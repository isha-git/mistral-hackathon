"""Celery tasks for running coding jobs via OpenCode."""

import os
import logging
from celery.exceptions import SoftTimeLimitExceeded

from src.api.config.celery import celery_app
from src.api.config.settings import get_settings
from src.api.models.job import JobStatus, TurnResult
from src.api.services.job_service import get_job_service
from src.api.services.opencode_wrapper import run_opencode_task as run_opencode
from src.api.services.whatsapp_notifier import get_notifier
from src.api.services.file_detector import snapshot_files, detect_changed_files
from src.api.services.question_extractor import extract_question
from src.api.services.file_delivery import deliver_files

logger = logging.getLogger(__name__)


def _process_opencode_result(
    job_id: str,
    job,
    result,
    prompt: str,
    turn_number: int,
    files_before: dict[str, float],
    working_dir: str,
) -> dict:
    """Common post-processing after an OpenCode call."""
    settings = get_settings()
    job_service = get_job_service()
    notifier = get_notifier()

    # Detect changed files
    changed_files = result.files_changed
    if not changed_files:
        changed_files = detect_changed_files(files_before, working_dir)
        if changed_files:
            logger.info(
                f"[Job {job_id}] Detected {len(changed_files)} changed files via filesystem scan"
            )

    logger.info(f"[Job {job_id}] OpenCode completed with success: {result.success}")

    # Store turn result
    turn_result = TurnResult(
        turn_number=turn_number,
        prompt=prompt,
        output=result.output,
        success=result.success,
        files_changed=changed_files,
    )
    job_service.add_turn_result(job_id, turn_result)

    # Add agent response to conversation
    if result.output:
        job_service.add_conversation_message(
            job_id, "agent", result.output[: settings.conversation_message_max_chars]
        )

    # Check if agent is asking a question
    question = extract_question(result.output)
    if question:
        logger.info(f"[Job {job_id}] Agent is asking a question: {question}")
        job_service.set_agent_question(job_id, question)
        notifier.send_status(
            job,
            "needs_input",
            {
                "question": question,
                "job_id": str(job_id),
                "session_id": job.session_id,
                "working_dir": working_dir,
                "current_turn": turn_number,
            },
        )
        return {"status": "waiting_for_input", "job_id": job_id}

    if result.success:
        logger.info(f"[Job {job_id}] Marking job as COMPLETED")
        job_service.update_job_status(job_id, JobStatus.COMPLETED, result=result.output)
        notifier.send_status(
            job,
            "completed",
            {
                "result": result.output,
                "job_id": str(job_id),
                "files_changed": changed_files,
            },
        )
        deliver_files(job, changed_files, result.output)
        return {"status": "completed", "job_id": job_id, "result": result.output}
    else:
        error = result.error or "Task failed"
        logger.error(f"[Job {job_id}] Task failed: {error}")
        job_service.update_job_status(job_id, JobStatus.FAILED, error_message=error)
        notifier.send_status(
            job,
            "failed",
            {
                "error": error,
                "job_id": str(job_id),
            },
        )
        return {"status": "failed", "job_id": job_id, "error": error}


def _handle_task_exception(celery_task, job_id: str, job, exc: Exception) -> None:
    """Handle exceptions in Celery coding tasks with retry logic."""
    job_service = get_job_service()
    settings = get_settings()
    notifier = get_notifier()

    if isinstance(exc, SoftTimeLimitExceeded):
        logger.error(f"[Job {job_id}] Task exceeded soft time limit")
        job_service.update_job_status(
            job_id,
            JobStatus.TIMEOUT,
            error_message="Task exceeded maximum execution time",
        )
        notifier.send_status(job, "timeout", {"job_id": str(job_id)})
        raise exc

    logger.error(f"[Job {job_id}] Exception occurred: {str(exc)}")
    logger.exception(f"[Job {job_id}] Full exception details:")

    if celery_task.request.retries < settings.job_retry_count:
        retry_count = celery_task.request.retries + 1
        logger.info(
            f"[Job {job_id}] Retrying ({retry_count}/{settings.job_retry_count})"
        )
        raise celery_task.retry(countdown=settings.job_retry_delay * retry_count)

    logger.error(f"[Job {job_id}] All retries exhausted, marking as FAILED")
    job_service.update_job_status(job_id, JobStatus.FAILED, error_message=str(exc))
    notifier.send_status(job, "failed", {"error": str(exc), "job_id": str(job_id)})
    raise exc


@celery_app.task(bind=True, max_retries=3)
def run_coding_task(self, job_id: str):
    """Celery task to run an OpenCode job."""
    logger.info(f"[Job {job_id}] Starting coding task")
    job_service = get_job_service()

    job = job_service.get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    if job.status != JobStatus.PENDING:
        logger.warning(
            f"[Job {job_id}] Skipping stale task — status is {job.status.value}"
        )
        return {"status": "skipped", "job_id": job_id, "reason": "stale"}

    try:
        job_service.update_job_status(job_id, JobStatus.PROCESSING)

        if job.working_dir:
            worktree_path = job.working_dir
        else:
            worktree_path = os.path.join(os.getcwd(), "vibe_repos", job_id)
            os.makedirs(worktree_path, exist_ok=True)
            job_service.set_working_directory(job_id, worktree_path)

        files_before = snapshot_files(worktree_path)
        opencode_session_id = job_service.get_opencode_session(job.session_id)

        result = run_opencode(
            prompt=job.prompt,
            working_dir=worktree_path,
            session_id=opencode_session_id,
        )

        if result.session_id and job.session_id:
            job_service.store_opencode_session(job.session_id, result.session_id)

        return _process_opencode_result(
            job_id, job, result, job.prompt, 1, files_before, worktree_path
        )

    except Exception as e:
        _handle_task_exception(self, job_id, job, e)


@celery_app.task()
def recover_stale_jobs():
    """Periodic task: find jobs stuck in PROCESSING and mark them FAILED.

    Runs via Celery Beat. Any job in PROCESSING state whose started_at
    is older than job_max_timeout + a 5-minute grace period is considered
    orphaned (worker crash, OOM, etc.) and is failed out so the user
    isn't left waiting forever.
    """
    settings = get_settings()
    job_service = get_job_service()
    notifier = get_notifier()

    grace_seconds = 300  # 5 minutes on top of the hard timeout
    threshold = settings.job_max_timeout + grace_seconds

    stale_jobs = job_service.find_stale_processing_jobs(threshold)
    if not stale_jobs:
        return {"recovered": 0}

    recovered = 0
    for job in stale_jobs:
        job_id = str(job.id)
        logger.warning(f"[Recovery] Failing orphaned job {job_id} (started {job.started_at})")
        job_service.update_job_status(
            job_id,
            JobStatus.FAILED,
            error_message="Job timed out — worker may have crashed. Please retry.",
        )
        notifier.send_status(
            job, "failed", {"error": "Job timed out unexpectedly", "job_id": job_id}
        )
        recovered += 1

    logger.info(f"[Recovery] Recovered {recovered} stale job(s)")
    return {"recovered": recovered}


@celery_app.task(bind=True, max_retries=3)
def continue_coding_task(self, job_id: str, user_response: str):
    """Continue a coding task with user input."""
    logger.info(f"[Job {job_id}] Continuing with user response")
    job_service = get_job_service()

    job = job_service.get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    if not job.working_dir:
        raise ValueError(
            f"Job {job_id} has no working directory — cannot continue task"
        )

    try:
        job_service.update_job_status(job_id, JobStatus.PROCESSING)
        job_service.submit_user_response(job_id, user_response)

        files_before = snapshot_files(job.working_dir)
        opencode_session_id = job_service.get_opencode_session(job.session_id)

        result = run_opencode(
            prompt=user_response,
            working_dir=job.working_dir,
            session_id=opencode_session_id,
        )

        if result.session_id and job.session_id:
            job_service.store_opencode_session(job.session_id, result.session_id)

        return _process_opencode_result(
            job_id,
            job,
            result,
            user_response,
            job.current_turn + 1,
            files_before,
            job.working_dir,
        )

    except Exception as e:
        _handle_task_exception(self, job_id, job, e)
