"""
Pipeline for processing WhatsApp messages and routing to OpenCode jobs.

Simple model: One active job per user (sender), all messages go to same working directory.
Send "/new_project" to start fresh.
"""

import logging
from enum import Enum

from src.api.models.job import IncomingMessage, Reply, JobStatus
from src.api.services.job_service import get_job_service
from src.api.config.settings import get_settings
from src.api.tasks.jobs import run_coding_task, continue_coding_task

logger = logging.getLogger(__name__)


class PipelineAction(Enum):
    CONTINUE_SESSION = "continue_session"
    CREATE_FIRST_JOB = "create_first_job"
    REUSE_PENDING = "reuse_pending"
    CREATE_FOLLOW_UP = "create_follow_up"


def _determine_action(existing_job) -> PipelineAction:
    """Determine what pipeline action to take based on existing job state."""
    if not existing_job:
        return PipelineAction.CREATE_FIRST_JOB
    if existing_job.status == JobStatus.WAITING_FOR_INPUT:
        return PipelineAction.CONTINUE_SESSION
    if existing_job.status == JobStatus.PENDING:
        return PipelineAction.REUSE_PENDING
    return PipelineAction.CREATE_FOLLOW_UP


async def process_message(msg: IncomingMessage) -> Reply:
    """Process incoming WhatsApp message."""
    if not msg.type == "text" or not msg.text:
        return Reply(type="text", text="I only understand text messages for now.")

    prompt = msg.text.strip()
    sender = msg.sender

    if prompt.lower() == "/new_project":
        return await _start_new_project(sender)

    return await _add_to_project(prompt=prompt, sender=sender)


async def _start_new_project(sender: str) -> Reply:
    """Start a new project (clears old working directory reference)."""
    job_service = get_job_service()
    job_service.clear_active_job(sender)

    logger.info(f"[pipeline] New project for {sender}")

    return Reply(
        type="text",
        text="🆕 New project started!\n\n"
        "What would you like to build?\n\n"
        "Working directory will be reused for all your messages.",
    )


def _continue_session(prompt, sender, existing_job, job_service, settings):
    logger.info(f"[pipeline] Job for {sender} waiting for input, continuing session")
    job_service.add_conversation_message(existing_job.id, "user", prompt)
    continue_coding_task.delay(str(existing_job.id), prompt)
    return existing_job


def _create_first_job(prompt, sender, existing_job, job_service, settings):
    working_dir = f"{settings.vibe_repos_base}/user-{sender.replace('@', '_').replace(':', '_')[:50]}"
    job = job_service.create_job(
        prompt=prompt,
        session_id=f"whatsapp-{sender}",
        branch_name=f"user-{sender[:8]}",
        webhook_url=settings.whatsapp_callback_url,
        max_turns=settings.default_max_turns,
        working_dir=working_dir,
    )
    job_service.add_conversation_message(job.id, "user", prompt)
    job_service.set_active_job(sender, job.id)
    logger.info(f"[pipeline] First project for {sender}: {job.id}")
    run_coding_task.delay(str(job.id))
    return job


def _reuse_pending(prompt, sender, existing_job, job_service, settings):
    existing_job.prompt = prompt
    job_service.add_conversation_message(existing_job.id, "user", prompt)
    job_service.save_job(existing_job)
    logger.info(f"[pipeline] Reusing pending job for {sender}: {existing_job.id}")
    return existing_job


def _create_follow_up(prompt, sender, existing_job, job_service, settings):
    job = job_service.create_job(
        prompt=prompt,
        session_id=existing_job.session_id,
        branch_name=existing_job.branch_name,
        webhook_url=settings.whatsapp_callback_url,
        max_turns=settings.default_max_turns,
        working_dir=existing_job.working_dir,
    )
    job_service.add_conversation_message(job.id, "user", prompt)
    job_service.set_active_job(sender, job.id)
    logger.info(
        f"[pipeline] New job for {sender}: {job.id} "
        f"(prev {existing_job.id} was {existing_job.status.value})"
    )
    run_coding_task.delay(str(job.id))
    return job


_ACTION_HANDLERS = {
    PipelineAction.CONTINUE_SESSION: _continue_session,
    PipelineAction.CREATE_FIRST_JOB: _create_first_job,
    PipelineAction.REUSE_PENDING: _reuse_pending,
    PipelineAction.CREATE_FOLLOW_UP: _create_follow_up,
}


async def _add_to_project(prompt: str, sender: str) -> Reply:
    """Add a task to the user's existing project."""
    job_service = get_job_service()
    settings = get_settings()

    existing_job = job_service.get_active_job(sender)
    action = _determine_action(existing_job)
    handler = _ACTION_HANDLERS[action]
    job = handler(prompt, sender, existing_job, job_service, settings)

    return Reply(
        type="text",
        text=f"Got it! Working on: {prompt[:50]}{'...' if len(prompt) > 50 else ''}\n\n"
        f"Job ID: `{job.id}`\n"
        f"Working dir: `{job.working_dir}`\n"
        "I'll message you when done or if I need anything.",
    )
