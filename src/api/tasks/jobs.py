"""
Celery tasks for running coding jobs via OpenCode SDK.
"""

import os
import re
import base64
import mimetypes
import logging
from celery.exceptions import SoftTimeLimitExceeded

import httpx

from src.api.config.celery import celery_app
from src.api.config.settings import get_settings
from src.api.models.job import JobStatus, TurnResult
from src.api.services.job_service import get_job_service
from src.api.services.opencode_wrapper import run_opencode_task as run_opencode

logger = logging.getLogger(__name__)


def _snapshot_files(directory: str) -> dict[str, float]:
    """Take a snapshot of file modification times in a directory."""
    snapshot = {}
    if not directory or not os.path.isdir(directory):
        return snapshot
    for root, dirs, files in os.walk(directory):
        # Skip hidden directories like .git, sessions
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "sessions"]
        for f in files:
            if f.startswith("."):
                continue
            path = os.path.join(root, f)
            try:
                snapshot[path] = os.path.getmtime(path)
            except OSError:
                pass
    return snapshot


def _detect_changed_files(
    before: dict[str, float], directory: str
) -> list[str]:
    """Compare current files against a snapshot to find new/modified files."""
    changed = []
    if not directory or not os.path.isdir(directory):
        return changed
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "sessions"]
        for f in files:
            if f.startswith("."):
                continue
            path = os.path.join(root, f)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if path not in before or mtime > before[path]:
                changed.append(path)
    return changed


@celery_app.task(bind=True, max_retries=3)
def run_coding_task(self, job_id: str):
    """
    Celery task to run an OpenCode job.
    """
    logger.info(f"[Job {job_id}] Starting coding task")
    job_service = get_job_service()
    settings = get_settings()

    job = job_service.get_job(job_id)
    if not job:
        logger.error(f"[Job {job_id}] Job not found")
        raise ValueError(f"Job {job_id} not found")

    # Stale retry guard: only execute if job is still PENDING
    if job.status != JobStatus.PENDING:
        logger.warning(
            f"[Job {job_id}] Skipping stale task — status is {job.status.value}, expected PENDING"
        )
        return {"status": "skipped", "job_id": job_id, "reason": "stale"}

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
            import os

            worktree_path = os.path.join(os.getcwd(), "vibe_repos", job_id)
            os.makedirs(worktree_path, exist_ok=True)
            job_service.set_working_directory(job_id, worktree_path)

        logger.info(f"[Job {job_id}] Working directory: {worktree_path}")

        # Snapshot files before running so we can detect changes
        files_before = _snapshot_files(worktree_path)

        # Retrieve existing OpenCode session ID from Redis if continuing
        opencode_session_id = job_service.get_opencode_session(job.session_id)

        logger.info(f"[Job {job_id}] Running OpenCode task")
        result = run_opencode(
            prompt=job.prompt,
            working_dir=worktree_path,
            session_id=opencode_session_id,
        )

        # Store OpenCode session ID for future continuity
        if result.session_id and job.session_id:
            job_service.store_opencode_session(job.session_id, result.session_id)

        # Detect changed files — use OpenCode's list, fall back to filesystem diff
        changed_files = result.files_changed
        if not changed_files:
            changed_files = _detect_changed_files(files_before, worktree_path)
            if changed_files:
                logger.info(f"[Job {job_id}] Detected {len(changed_files)} changed files via filesystem scan")

        logger.info(f"[Job {job_id}] OpenCode completed with success: {result.success}")
        logger.info(f"[Job {job_id}] Output preview: {result.output[:200]}...")

        # Store turn result
        turn_result = TurnResult(
            turn_number=1,
            prompt=job.prompt,
            output=result.output,
            success=result.success,
            files_changed=changed_files,
        )
        job_service.add_turn_result(job_id, turn_result)

        # Add agent response to conversation for continuity
        if result.output:
            job_service.add_conversation_message(job_id, "agent", result.output[:1000])

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
                    "files_changed": changed_files,
                },
            )
            # Send changed files as WhatsApp attachments
            if changed_files:
                _send_changed_files(job, changed_files)
            else:
                _send_inline_code(job, result.output)
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
    """Extract question from output if agent is explicitly asking for clarification.

    Only triggers on explicit markers — NOT on casual conversational questions
    like 'How can I help you?' or 'Would you like me to modify the code?'
    """
    # Look for QUESTION: prefix — this is the reliable signal
    if "QUESTION:" in output:
        match = re.search(r"QUESTION:\s*(.+?)(?:\n|$)", output, re.IGNORECASE)
        if match:
            return match.group(1).strip()

    # Only match explicit clarification phrases
    clarification_patterns = [
        r"please clarify",
        r"need more information",
        r"missing details",
        r"before I (?:can |)proceed",
        r"could you (?:please )?(?:provide|specify|clarify)",
    ]

    for pattern in clarification_patterns:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            # Return the sentence containing the match
            for line in output.split("\n"):
                if re.search(pattern, line, re.IGNORECASE):
                    return line.strip()
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


def _extract_inline_code(output: str) -> list[tuple[str, str, str]]:
    """Extract fenced code blocks from agent output.

    Returns list of (language, code, filename) tuples.
    """
    blocks = []
    # Match ```language ... ``` blocks
    pattern = r"```(\w+)?\s*\n(.*?)```"
    for match in re.finditer(pattern, output, re.DOTALL):
        lang = (match.group(1) or "").strip().lower()
        code = match.group(2).strip()
        if not code or len(code) < 10:
            continue

        # Map language to file extension
        ext_map = {
            "python": ".py", "py": ".py",
            "javascript": ".js", "js": ".js",
            "typescript": ".ts", "ts": ".ts",
            "html": ".html", "css": ".css",
            "java": ".java", "go": ".go",
            "rust": ".rs", "ruby": ".rb",
            "shell": ".sh", "bash": ".sh",
            "sql": ".sql", "json": ".json",
            "yaml": ".yaml", "yml": ".yaml",
            "xml": ".xml", "c": ".c", "cpp": ".cpp",
        }
        ext = ext_map.get(lang, ".txt")
        filename = f"code{ext}" if len(blocks) == 0 else f"code_{len(blocks)}{ext}"
        blocks.append((lang, code, filename))

    return blocks


def _send_inline_code(job, output: str):
    """Extract code blocks from agent output and send as file attachments."""
    settings = get_settings()
    if not settings.whatsapp_callback_url:
        return

    recipient = job.session_id
    if recipient and recipient.startswith("whatsapp-"):
        recipient = recipient.replace("whatsapp-", "")
    if not recipient:
        return

    blocks = _extract_inline_code(output)
    if not blocks:
        return

    send_doc_url = settings.whatsapp_callback_url.replace("/send", "/send-document")

    for lang, code, filename in blocks:
        try:
            mimetype_map = {
                ".py": "text/x-python",
                ".js": "application/javascript",
                ".ts": "application/typescript",
                ".html": "text/html",
                ".css": "text/css",
                ".json": "application/json",
            }
            ext = os.path.splitext(filename)[1]
            mimetype = mimetype_map.get(ext, "text/plain")

            payload = {
                "to": recipient,
                "data_base64": base64.b64encode(code.encode("utf-8")).decode("utf-8"),
                "filename": filename,
                "mimetype": mimetype,
                "caption": f"📎 {filename}",
            }

            httpx.post(send_doc_url, json=payload, timeout=30.0)
            logger.info(f"[send_inline] Sent inline code as '{filename}' to {recipient}")
        except Exception as e:
            logger.error(f"[send_inline] Failed to send '{filename}': {e}")


def _send_changed_files(job, files_changed: list[str]):
    """Send changed files as WhatsApp document attachments."""
    settings = get_settings()
    if not settings.whatsapp_callback_url:
        return

    recipient = job.session_id
    if recipient and recipient.startswith("whatsapp-"):
        recipient = recipient.replace("whatsapp-", "")
    if not recipient:
        return

    # Build the document send URL (same host as callback, different path)
    send_doc_url = settings.whatsapp_callback_url.replace("/send", "/send-document")

    for filepath in files_changed:
        # Resolve file path relative to working_dir
        if not os.path.isabs(filepath):
            filepath = os.path.join(job.working_dir, filepath)

        if not os.path.isfile(filepath):
            logger.warning(f"[send_files] File not found: {filepath}")
            continue

        # Skip large files (> 5MB)
        if os.path.getsize(filepath) > 5 * 1024 * 1024:
            logger.info(f"[send_files] Skipping large file: {filepath}")
            continue

        # Skip binary/non-text files that aren't useful to send
        filename = os.path.basename(filepath)
        if filename.startswith("."):
            continue

        try:
            with open(filepath, "rb") as f:
                file_data = f.read()

            mimetype_guess, _ = mimetypes.guess_type(filepath)

            payload = {
                "to": recipient,
                "data_base64": base64.b64encode(file_data).decode("utf-8"),
                "filename": filename,
                "mimetype": mimetype_guess or "application/octet-stream",
                "caption": f"📎 {filename}",
            }

            httpx.post(send_doc_url, json=payload, timeout=30.0)
            logger.info(f"[send_files] Sent file '{filename}' to {recipient}")
        except Exception as e:
            logger.error(f"[send_files] Failed to send '{filename}': {e}")


@celery_app.task(bind=True, max_retries=3)
def continue_coding_task(self, job_id: str, user_response: str):
    """
    Continue a coding task with user input.
    Sends the response to the same OpenCode session for continuity.
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

        # Snapshot files before running so we can detect changes
        files_before = _snapshot_files(job.working_dir)

        # Get the existing OpenCode session for continuity
        opencode_session_id = job_service.get_opencode_session(job.session_id)

        # Send follow-up to same session
        result = run_opencode(
            prompt=user_response,
            working_dir=job.working_dir,
            session_id=opencode_session_id,
        )

        # Update stored session ID if it changed
        if result.session_id and job.session_id:
            job_service.store_opencode_session(job.session_id, result.session_id)

        # Detect changed files — use OpenCode's list, fall back to filesystem diff
        changed_files = result.files_changed
        if not changed_files:
            changed_files = _detect_changed_files(files_before, job.working_dir)
            if changed_files:
                logger.info(f"[Job {job_id}] Detected {len(changed_files)} changed files via filesystem scan")

        # Store result
        turn_result = TurnResult(
            turn_number=job.current_turn + 1,
            prompt=user_response,
            output=result.output,
            success=result.success,
            files_changed=changed_files,
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
            # Send changed files as WhatsApp attachments
            if changed_files:
                _send_changed_files(job, changed_files)
            else:
                _send_inline_code(job, result.output)
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
