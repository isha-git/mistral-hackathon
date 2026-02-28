"""
Celery tasks for running Mistral Vibe via direct Python API.
"""

import os
import re
import logging
from datetime import datetime
from celery.exceptions import SoftTimeLimitExceeded

import httpx

from src.api.config.celery import celery_app
from src.api.config.settings import get_settings
from src.api.models.job import JobStatus, TurnResult
from src.api.services.job_service import get_job_service
from src.api.services.vibe_wrapper import run_vibe_task as run_vibe

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3)
def run_vibe_task(self, job_id: str):
    """
    Celery task to run Mistral Vibe job.

    Uses direct Python API - no subprocess, no TUI.
    """
    logger.info(f"[Job {job_id}] Starting vibe task")
    job_service = get_job_service()
    settings = get_settings()

    job = job_service.get_job(job_id)
    if not job:
        logger.error(f"[Job {job_id}] Job not found")
        raise ValueError(f"Job {job_id} not found")

    logger.info(f"[Job {job_id}] Loaded job with prompt: {job.prompt[:100]}...")
    logger.info(f"[Job {job_id}] Session ID: {job.session_id}")

    try:
        # Update status to processing
        logger.info(f"[Job {job_id}] Updating status to PROCESSING")
        job_service.update_job_status(job_id, JobStatus.PROCESSING)

        # Determine working directory
        if job.working_dir:
            worktree_path = job.working_dir
        else:
            worktree_path = os.path.join(os.getcwd(), "vibe_work", job_id)
            os.makedirs(worktree_path, exist_ok=True)
            job_service.set_working_directory(job_id, worktree_path)

        logger.info(f"[Job {job_id}] Working directory: {worktree_path}")

        # Vibe loads previous messages automatically from sessions/ directory
        logger.info(
            f"[Job {job_id}] Running vibe task (will load session from {worktree_path})"
        )
        result = run_vibe(
            prompt=job.prompt,
            working_dir=worktree_path,
            max_turns=job.max_turns,
        )

        logger.info(f"[Job {job_id}] Vibe completed with success: {result.success}")
        logger.info(f"[Job {job_id}] Output preview: {result.output[:200]}...")

        # Store turn result
        turn_result = TurnResult(
            turn_number=1,
            prompt=job.prompt,
            output=result.output,
            success=result.success,
            files_changed=result.files_changed,
        )
        job_service.add_turn_result(job_id, turn_result)

        # Add agent response to conversation for continuity
        if result.output:
            job_service.add_conversation_message(
                job_id, "agent", result.output[:1000]
            )  # Limit size

        # Check if agent is asking a question
        question = _extract_question(result.output)
        if question:
            logger.info(f"[Job {job_id}] Agent is asking a question: {question}")
            job_service.set_agent_question(job_id, question)
            _notify_webhook(
                job,
                {
                    "status": "needs_input",
                    "question": question,
                    "job_id": str(job_id),
                    "session_id": job.session_id,
                    "working_dir": worktree_path,
                    "current_turn": 1,
                },
            )
            return {"status": "waiting_for_input", "job_id": job_id}

        # Task completed successfully
        if result.success:
            logger.info(f"[Job {job_id}] Marking job as COMPLETED")
            job_service.update_job_status(
                job_id, JobStatus.COMPLETED, result=result.output
            )
            _notify_webhook(
                job,
                {
                    "status": "completed",
                    "result": result.output,
                    "job_id": str(job_id),
                    "files_changed": result.files_changed,
                },
            )
            return {"status": "completed", "job_id": job_id, "result": result.output}
        else:
            # Task failed
            logger.error(f"[Job {job_id}] Task failed: {result.error}")
            job_service.update_job_status(
                job_id, JobStatus.FAILED, error_message=result.error or "Task failed"
            )
            _notify_webhook(
                job,
                {
                    "status": "failed",
                    "error": result.error or "Task failed",
                    "job_id": str(job_id),
                },
            )
            return {"status": "failed", "job_id": job_id, "error": result.error}

    except SoftTimeLimitExceeded:
        logger.error(f"[Job {job_id}] Task exceeded soft time limit")
        job_service.update_job_status(
            job_id,
            JobStatus.TIMEOUT,
            error_message="Task exceeded maximum execution time",
        )
        _notify_webhook(job, {"status": "timeout", "job_id": str(job_id)})
        raise

    except Exception as e:
        logger.error(f"[Job {job_id}] Exception occurred: {str(e)}")
        logger.exception(f"[Job {job_id}] Full exception details:")
        if self.request.retries < settings.job_retry_count:
            retry_count = self.request.retries + 1
            logger.info(
                f"[Job {job_id}] Retrying ({retry_count}/{settings.job_retry_count})"
            )
            raise self.retry(countdown=settings.job_retry_delay * retry_count)

        logger.error(f"[Job {job_id}] All retries exhausted, marking as FAILED")
        job_service.update_job_status(job_id, JobStatus.FAILED, error_message=str(e))
        _notify_webhook(
            job, {"status": "failed", "error": str(e), "job_id": str(job_id)}
        )
        raise


def _extract_question(output: str) -> str | None:
    """Extract question from vibe output if agent is asking for clarification."""
    # Look for QUESTION: prefix
    if "QUESTION:" in output:
        match = re.search(r"QUESTION:\s*(.+?)(?:\n|$)", output, re.IGNORECASE)
        if match:
            return match.group(1).strip()

    # Check for question patterns
    question_patterns = [
        r"(?:what|which|how|where|when|why|who|can you|could you).+\?",
        r"please clarify",
        r"need more information",
        r"missing details",
    ]

    for pattern in question_patterns:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            return match.group(0).strip()

    return None


def _notify_webhook(job, data: dict):
    """Send notification to webhook (WhatsApp) if configured."""
    settings = get_settings()
    if not settings.whatsapp_callback_url:
        return

    try:
        # Extract WhatsApp number from session_id (format: "whatsapp-<number>")
        recipient = job.session_id
        if recipient and recipient.startswith("whatsapp-"):
            recipient = recipient.replace("whatsapp-", "")

        if not recipient:
            logger.warning(f"No recipient found for job {job.id}")
            return

        # Build message based on status
        status = data.get("status")
        if status == "needs_input":
            message = f"❓ {data.get('question', 'I have a question for you')}"
        elif status == "completed":
            result = data.get("result", "Task completed")
            message = f"✅ Done!\n\n{result[:500]}{'...' if len(result) > 500 else ''}"
        elif status == "failed":
            error = data.get("error", "Task failed")
            message = f"❌ Failed: {error[:200]}"
        elif status == "timeout":
            message = "⏱️ Task timed out. Please try again."
        else:
            message = f"Status: {status}"

        # WhatsApp bridge expects: {"to": "<number>", "message": "<text>"}
        payload = {
            "to": recipient,
            "message": message,
        }

        httpx.post(
            settings.whatsapp_callback_url,
            json=payload,
            timeout=10.0,
        )
        logger.info(f"WhatsApp notification sent to {recipient} for job {job.id}")
    except Exception as e:
        logger.error(f"Failed to send WhatsApp notification: {e}")


@celery_app.task(bind=True, max_retries=3)
def continue_vibe_task(self, job_id: str, user_response: str):
    """
    Continue a vibe task with user input.

    Simplified version - just runs a new task with the response.
    """
    logger.info(f"[Job {job_id}] Continuing with user response")
    job_service = get_job_service()
    settings = get_settings()

    job = job_service.get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    try:
        # Update status
        job_service.update_job_status(job_id, JobStatus.PROCESSING)
        job_service.submit_user_response(job_id, user_response)

        # Build combined prompt
        combined_prompt = (
            f"Previous context: {job.prompt}\n\nUser response: {user_response}"
        )

        # Run vibe
        result = run_vibe(
            prompt=combined_prompt,
            working_dir=job.working_dir,
            max_turns=job.max_turns,
        )

        # Store result
        turn_result = TurnResult(
            turn_number=job.current_turn + 1,
            prompt=user_response,
            output=result.output,
            success=result.success,
            files_changed=result.files_changed,
        )
        job_service.add_turn_result(job_id, turn_result)

        # Check for questions
        question = _extract_question(result.output)
        if question:
            job_service.set_agent_question(job_id, question)
            _notify_webhook(
                job,
                {
                    "status": "needs_input",
                    "question": question,
                    "job_id": str(job_id),
                },
            )
            return {"status": "waiting_for_input", "job_id": job_id}

        # Complete or fail
        if result.success:
            job_service.update_job_status(
                job_id, JobStatus.COMPLETED, result=result.output
            )
            _notify_webhook(
                job,
                {
                    "status": "completed",
                    "result": result.output,
                    "job_id": str(job_id),
                },
            )
            return {"status": "completed", "job_id": job_id, "result": result.output}
        else:
            job_service.update_job_status(
                job_id, JobStatus.FAILED, error_message=result.error or "Task failed"
            )
            _notify_webhook(
                job,
                {
                    "status": "failed",
                    "error": result.error or "Task failed",
                    "job_id": str(job_id),
                },
            )
            return {"status": "failed", "job_id": job_id, "error": result.error}

    except SoftTimeLimitExceeded:
        logger.error(f"[Job {job_id}] Task exceeded soft time limit")
        job_service.update_job_status(
            job_id,
            JobStatus.TIMEOUT,
            error_message="Task exceeded maximum execution time",
        )
        _notify_webhook(job, {"status": "timeout", "job_id": str(job_id)})
        raise

    except Exception as e:
        logger.error(f"[Job {job_id}] Exception: {str(e)}")
        logger.exception(f"[Job {job_id}] Full traceback:")
        if self.request.retries < settings.job_retry_count:
            retry_count = self.request.retries + 1
            logger.info(
                f"[Job {job_id}] Retrying ({retry_count}/{settings.job_retry_count})"
            )
            raise self.retry(countdown=settings.job_retry_delay * retry_count)

        logger.error(f"[Job {job_id}] All retries exhausted")
        job_service.update_job_status(job_id, JobStatus.FAILED, error_message=str(e))
        _notify_webhook(
            job, {"status": "failed", "error": str(e), "job_id": str(job_id)}
        )
        raise
