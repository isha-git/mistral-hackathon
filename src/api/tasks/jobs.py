import httpx
import os
import tempfile
import re
from datetime import datetime
from celery.exceptions import SoftTimeLimitExceeded

from src.api.config.celery import celery_app
from src.api.config.settings import get_settings
from src.api.models.job import JobStatus, TurnResult
from src.api.services.job_service import get_job_service
from src.api.services.vibe_wrapper import VibeInteractiveWrapper


def _get_repo_name(repo_url: str) -> str:
    """Extract repo name from URL."""
    repo_name = repo_url.rstrip("/").split("/")[-1]
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]
    return repo_name


def _build_persistent_working_dir(repo_url: str, branch_name: str) -> str:
    """Build a persistent working directory path for repo/branch."""
    repo_name = _get_repo_name(repo_url)
    base_dir = os.path.join(os.getcwd(), "vibe_repos")
    work_dir = os.path.join(base_dir, f"{repo_name}_{branch_name}")
    return work_dir


@celery_app.task(bind=True, max_retries=3)
def run_vibe_task(self, job_id: str):
    """
    Celery task to run Mistral Vibe with stateful working directories.

    Key features:
    - Reuses working directory for same repo/branch
    - Detects when agent needs clarification
    - Maintains full conversation history
    """
    job_service = get_job_service()
    settings = get_settings()

    job = job_service.get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    try:
        # Update status to processing
        job_service.update_job_status(job_id, JobStatus.PROCESSING)

        # Determine working directory
        if job.working_dir:
            # Use explicitly provided working directory
            worktree_path = job.working_dir
        elif job.repo_url and job.branch_name:
            # Use persistent directory for repo/branch
            worktree_path = _build_persistent_working_dir(job.repo_url, job.branch_name)
            # Check if this directory already exists from previous job
            if os.path.exists(worktree_path):
                print(f"Reusing existing working directory: {worktree_path}")
        else:
            # Create temporary directory
            worktree_path = tempfile.mkdtemp(prefix="vibe_work_")

        # Ensure directory exists
        os.makedirs(worktree_path, exist_ok=True)

        # Save working directory
        job_service.set_working_directory(job_id, worktree_path)

        # Create vibe wrapper
        wrapper = VibeInteractiveWrapper(
            working_dir=worktree_path, max_turns=job.max_turns
        )

        # Build prompt with clear instructions about asking questions
        full_prompt = _build_prompt(job)

        # Run vibe session
        final_result = None
        for result in wrapper.start_task(full_prompt):
            # Store turn result
            turn_result = TurnResult(
                turn_number=job.current_turn + 1,
                prompt=full_prompt if job.current_turn == 0 else "Continue",
                output=result.output,
                success=result.success,
                files_changed=result.files_changed,
                timestamp=datetime.utcnow(),
            )
            job_service.add_turn_result(job_id, turn_result)

            # Add to conversation
            if result.output:
                job_service.add_conversation_message(job_id, "agent", result.output)

            # Check if vibe is asking for input (enhanced detection)
            question = _extract_question(result.output)
            if question:
                job_service.set_agent_question(job_id, question)
                _notify_webhook(
                    job,
                    {
                        "status": "needs_input",
                        "question": question,
                        "job_id": str(job_id),
                        "session_id": job.session_id,
                        "working_dir": worktree_path,
                        "current_turn": job.current_turn,
                        "repo_url": job.repo_url,
                        "branch_name": job.branch_name,
                    },
                )
                # Pause here - user needs to respond
                return {"status": "waiting_for_input", "job_id": job_id}

            # If failed, mark job as failed
            if not result.success:
                job_service.update_job_status(
                    job_id,
                    JobStatus.FAILED,
                    error_message=result.error or "Task failed",
                )
                _notify_webhook(
                    job,
                    {
                        "status": "failed",
                        "error": result.error or "Task failed",
                        "job_id": str(job_id),
                    },
                )
                return {"status": "failed", "job_id": job_id}

            final_result = result

        # All turns completed successfully
        final_output = (
            wrapper.session_history[-1]["output"]
            if wrapper.session_history
            else "Task completed"
        )

        job_service.update_job_status(job_id, JobStatus.COMPLETED, result=final_output)

        _notify_webhook(
            job,
            {
                "status": "completed",
                "result": final_output,
                "job_id": str(job_id),
                "session_id": job.session_id,
                "working_dir": worktree_path,
                "branch": job.branch_name,
                "total_turns": wrapper.current_turn,
                "files_changed": _get_all_files_changed(job),
            },
        )

        return {"status": "completed", "job_id": job_id}

    except SoftTimeLimitExceeded:
        job_service.update_job_status(
            job_id,
            JobStatus.TIMEOUT,
            error_message="Task exceeded maximum execution time",
        )
        _notify_webhook(job, {"status": "timeout", "job_id": str(job_id)})
        raise

    except Exception as e:
        if self.request.retries < settings.job_retry_count:
            raise self.retry(
                countdown=settings.job_retry_delay * (self.request.retries + 1)
            )

        job_service.update_job_status(job_id, JobStatus.FAILED, error_message=str(e))
        _notify_webhook(
            job, {"status": "failed", "error": str(e), "job_id": str(job_id)}
        )
        raise


@celery_app.task(bind=True, max_retries=3)
def continue_vibe_task(self, job_id: str, user_response: str):
    """
    Continue a vibe session with user input in the same working directory.
    """
    job_service = get_job_service()
    settings = get_settings()

    job = job_service.get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    if not job.working_dir:
        raise ValueError(f"Job {job_id} has no working directory")

    worktree_path = job.working_dir

    # Create wrapper with existing working directory
    wrapper = VibeInteractiveWrapper(working_dir=worktree_path, max_turns=job.max_turns)

    # Restore session history
    wrapper.session_history = [
        {
            "turn": t.turn_number,
            "prompt": t.prompt,
            "output": t.output,
            "success": t.success,
        }
        for t in job.turn_history
    ]
    wrapper.current_turn = job.current_turn

    try:
        # Submit user response
        job_service.submit_user_response(job_id, user_response)

        # Continue the session
        for result in wrapper.continue_task(user_response):
            # Store turn result
            turn_result = TurnResult(
                turn_number=wrapper.current_turn,
                prompt=f"[User]: {user_response}",
                output=result.output,
                success=result.success,
                files_changed=result.files_changed,
                timestamp=datetime.utcnow(),
            )
            job_service.add_turn_result(job_id, turn_result)

            # Add to conversation
            if result.output:
                job_service.add_conversation_message(job_id, "agent", result.output)

            # Check if vibe is asking for more input
            question = _extract_question(result.output)
            if question:
                job_service.set_agent_question(job_id, question)
                _notify_webhook(
                    job,
                    {
                        "status": "needs_input",
                        "question": question,
                        "job_id": str(job_id),
                        "session_id": job.session_id,
                        "working_dir": worktree_path,
                        "current_turn": wrapper.current_turn,
                    },
                )
                return {"status": "waiting_for_input", "job_id": job_id}

            # If failed
            if not result.success:
                job_service.update_job_status(
                    job_id,
                    JobStatus.FAILED,
                    error_message=result.error or "Task failed",
                )
                _notify_webhook(
                    job,
                    {
                        "status": "failed",
                        "error": result.error or "Task failed",
                        "job_id": str(job_id),
                    },
                )
                return {"status": "failed", "job_id": job_id}

        # Session completed
        final_output = (
            wrapper.session_history[-1]["output"]
            if wrapper.session_history
            else "Task completed"
        )

        job_service.update_job_status(job_id, JobStatus.COMPLETED, result=final_output)

        _notify_webhook(
            job,
            {
                "status": "completed",
                "result": final_output,
                "job_id": str(job_id),
                "session_id": job.session_id,
                "working_dir": worktree_path,
                "branch": job.branch_name,
                "total_turns": wrapper.current_turn,
                "files_changed": _get_all_files_changed(job),
            },
        )

        return {"status": "completed", "job_id": job_id}

    except Exception as e:
        if self.request.retries < settings.job_retry_count:
            raise self.retry(
                countdown=settings.job_retry_delay * (self.request.retries + 1)
            )

        job_service.update_job_status(job_id, JobStatus.FAILED, error_message=str(e))
        _notify_webhook(
            job, {"status": "failed", "error": str(e), "job_id": str(job_id)}
        )
        raise


def _build_prompt(job) -> str:
    """Build the full prompt with instructions to ask questions when needed."""
    prompt_parts = []

    if job.repo_url:
        prompt_parts.append(f"Clone the repository from {job.repo_url}")
        if job.branch_name:
            prompt_parts.append(
                f"Create and switch to a branch named '{job.branch_name}'"
            )
        prompt_parts.append("Then:")

    prompt_parts.append(job.prompt)

    # Add instructions about asking questions
    prompt_parts.append(
        "\n\nIMPORTANT: If my request is unclear or missing details, DO NOT proceed with assumptions."
    )
    prompt_parts.append(
        "Instead, ask me specific questions to clarify what you should do."
    )
    prompt_parts.append(
        "Start your response with 'QUESTION:' followed by what you need to know."
    )

    return "\n".join(prompt_parts)


def _extract_question(output: str) -> str | None:
    """Extract question from agent output. Looks for 'QUESTION:' prefix."""
    if not output:
        return None

    match = re.search(r"QUESTION:\s*(.+?)(?:\n|$)", output, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()

    return None


def _notify_webhook(job, payload: dict):
    """
    Send notification to WhatsApp send server.

    Translates internal job payloads into {to, message} format
    expected by the WhatsApp bridge's POST /send endpoint.
    """
    if not job.webhook_url:
        return

    # Extract the sender from the session_id (format: "whatsapp-{sender}")
    sender = None
    if job.session_id and job.session_id.startswith("whatsapp-"):
        sender = job.session_id[len("whatsapp-") :]

    if not sender:
        return

    # Build the message text based on status
    status = payload.get("status")
    if status == "needs_input":
        message = f"Question: {payload.get('question', '')}"
    elif status == "completed":
        result = payload.get("result", "Task completed.")
        message = f"Done! {result[:1000]}"
    elif status == "failed":
        message = (
            f"Sorry, something went wrong: {payload.get('error', 'Unknown error')}"
        )
    elif status == "timeout":
        message = "Sorry, the task timed out. Please try again."
    else:
        return

    try:
        httpx.post(
            job.webhook_url,
            json={"to": sender, "message": message},
            timeout=10.0,
        )
    except Exception:
        pass


def _get_all_files_changed(job) -> list[str]:
    """Get all files changed across all turns."""
    files = set()
    for turn in job.turn_history:
        files.update(turn.files_changed)
    return list(files)
