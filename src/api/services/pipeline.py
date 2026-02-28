"""
Pipeline for processing WhatsApp messages and routing to Vibe jobs.

Simple model: One active job per user (sender), all messages go to same working directory.
Send "/new_project" to start fresh.
"""

from typing import Optional

from src.api.models.job import IncomingMessage, Reply, JobStatus
from src.api.services.job_service import get_job_service
from src.api.services.elevenlabs.stt import transcribe
from src.api.config.settings import get_settings
from src.api.tasks.jobs import run_vibe_task

_MAX_AUDIO_B64 = 10 * 1024 * 1024  # ~7.5 MB decoded


async def process_message(msg: IncomingMessage) -> Reply:
    """
    Process incoming WhatsApp message.

    Simple model:
    - One job per user (sender) tracked via active_job:<sender> in Redis
    - All messages go to same working directory
    - Send "/new_project" to start fresh
    """
    if msg.type == "audio" and msg.media_base64:
        if len(msg.media_base64) > _MAX_AUDIO_B64:
            return Reply(type="text", text="Audio is too large. Please send a shorter message.")
        try:
            transcribed = await transcribe(msg.media_base64)
            prompt = transcribed.strip()
            if not prompt:
                return Reply(type="text", text="I couldn't understand the audio. Could you try again?")
        except Exception as e:
            print(f"[pipeline] STT failed: {e}")
            return Reply(type="text", text=f"Failed to transcribe audio: {e}")
    elif msg.type == "text" and msg.text:
        prompt = msg.text.strip()
    else:
        return Reply(type="text", text="I only understand text and voice messages for now.")
    sender = msg.sender

    # Check for new project command
    if prompt.lower() == "/new_project":
        return await _start_new_project(sender)

    # Otherwise, add to existing project
    return await _add_to_project(prompt=prompt, sender=sender)


async def _start_new_project(sender: str) -> Reply:
    """Start a new project (clears old working directory reference)."""
    job_service = get_job_service()
    settings = get_settings()

    # Clear the active job reference
    job_service.clear_active_job(sender)

    print(f"[pipeline] New project for {sender}")

    return Reply(
        type="text",
        text="🆕 New project started!\n\n"
        "What would you like to build?\n\n"
        "Working directory will be reused for all your messages.",
    )


async def _add_to_project(prompt: str, sender: str) -> Reply:
    """Add a task to the user's existing project."""
    job_service = get_job_service()
    settings = get_settings()

    # Check if there's an active job for this user
    existing_job = job_service.get_active_job(sender)

    if existing_job and existing_job.status == JobStatus.WAITING_FOR_INPUT:
        # User is responding to a question
        print(f"[pipeline] Job for {sender} waiting for input, treating as new task")

    if not existing_job:
        # First time user - create initial job in host-accessible directory
        working_dir = (
            f"/app/vibe_repos/user-{sender.replace('@', '_').replace(':', '_')[:50]}"
        )
        job = job_service.create_job(
            prompt=prompt,
            session_id=f"whatsapp-{sender}",
            branch_name=f"user-{sender[:8]}",
            webhook_url=settings.whatsapp_callback_url,
            max_turns=50,
            working_dir=working_dir,
        )
        # Add user's message to conversation
        job_service.add_conversation_message(job.id, "user", prompt)
        # Set as active job
        job_service.set_active_job(sender, job.id)
        print(f"[pipeline] First project for {sender}: {job.id}")
    else:
        # Reuse existing job - update prompt, reset status, add message
        existing_job.prompt = prompt
        existing_job.status = JobStatus.PENDING
        job_service.add_conversation_message(existing_job.id, "user", prompt)
        job_service.save_job(existing_job)
        # Clear stale progress events from previous run
        job_service.clear_progress_events(str(existing_job.id))
        job = existing_job
        print(f"[pipeline] Reusing project for {sender}: {job.id}")

    run_vibe_task.delay(str(job.id))

    return Reply(
        type="text",
        text=f"Got it! Working on: {prompt[:50]}{'...' if len(prompt) > 50 else ''}\n\n"
        f"Track progress: {settings.base_url}/jobs/{job.id}\n\n"
        "I'll message you when done or if I need anything.",
    )
