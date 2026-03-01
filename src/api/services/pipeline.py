"""
Pipeline for processing WhatsApp messages and routing to Vibe jobs.

Simple model: One active job per user (sender), all messages go to same working directory.
Send "/new_project" to start fresh.
"""

import os
import shutil
from typing import Optional

import httpx

from src.api.models.job import IncomingMessage, Reply, JobStatus
from src.api.services.job_service import get_job_service
from src.api.services.elevenlabs.stt import transcribe
from src.api.config.settings import get_settings
from src.api.tasks.jobs import run_vibe_task, continue_vibe_task

_MAX_AUDIO_B64 = 10 * 1024 * 1024  # ~7.5 MB decoded


def _summarise_prompt(text: str) -> str:
    """Summarise a long prompt for the WhatsApp acknowledgement."""
    if len(text) <= 200:
        return text
    try:
        settings = get_settings()
        api_key = os.environ.get("MISTRAL_API_KEY") or settings.mistral_vibe_api_key
        resp = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "mistral-small-latest",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Summarise this task in under 200 characters. "
                            "Preserve any repo URLs or branch names exactly:\n\n"
                            f"{text}"
                        ),
                    }
                ],
                "max_tokens": 128,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        summary = resp.json()["choices"][0]["message"]["content"]
        return f"{summary}\n_(summarised)_"
    except Exception:
        return text[:200] + "..."


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
    """Start a new project (clears old job, working directory, and session history)."""
    job_service = get_job_service()
    settings = get_settings()

    # Clear the active job reference
    job_service.clear_active_job(sender)

    # Delete the user's working directory so the agent starts fresh
    safe_sender = sender.replace("@", "_").replace(":", "_")[:50]
    working_dir = f"/app/vibe_repos/user-{safe_sender}"
    if os.path.isdir(working_dir):
        shutil.rmtree(working_dir, ignore_errors=True)
        print(f"[pipeline] Deleted working directory: {working_dir}")

    print(f"[pipeline] New project for {sender}")

    return Reply(
        type="text",
        text="🆕 New project started!\n\n"
        "What would you like to build?\n\n",
    )


async def _add_to_project(prompt: str, sender: str) -> Reply:
    """Add a task to the user's existing project."""
    job_service = get_job_service()
    settings = get_settings()

    # Check if there's an active job for this user
    existing_job = job_service.get_active_job(sender)

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
        run_vibe_task.delay(str(job.id))
        print(f"[pipeline] First project for {sender}: {job.id}")
    elif existing_job.status == JobStatus.WAITING_FOR_INPUT:
        # Agent asked a question — use continue_vibe_task to preserve context
        job_service.clear_progress_events(str(existing_job.id))
        continue_vibe_task.delay(str(existing_job.id), prompt)
        job = existing_job
        print(f"[pipeline] Continuing job {job.id} with user response")
    elif existing_job.status == JobStatus.PROCESSING:
        # Previous task still running — queue the message, don't start a new run
        job_service.add_conversation_message(existing_job.id, "user", prompt)
        job = existing_job
        print(f"[pipeline] Job {job.id} still processing, queued message")
        display = _summarise_prompt(prompt)
        return Reply(
            type="text",
            text=f"🎙️ {display}\n\n"
                 "⏳ Still working on the previous task. I'll get to this next.",
        )
    else:
        # Job is completed/failed/pending — reuse for a new task
        existing_job.prompt = prompt
        existing_job.status = JobStatus.PENDING
        job_service.add_conversation_message(existing_job.id, "user", prompt)
        job_service.save_job(existing_job)
        job_service.clear_progress_events(str(existing_job.id))
        job = existing_job
        run_vibe_task.delay(str(job.id))
        print(f"[pipeline] Reusing project for {sender}: {job.id}")

    display = _summarise_prompt(prompt)
    progress_url = f"{settings.base_url}/jobs/{job.id}"

    # Send the progress link as a separate WhatsApp message so it's easy to copy
    if settings.whatsapp_callback_url:
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    settings.whatsapp_callback_url,
                    json={"to": sender, "message": progress_url},
                    timeout=5.0,
                )
        except Exception as e:
            print(f"[pipeline] Failed to send progress link: {e}")

    if existing_job and existing_job.status == JobStatus.WAITING_FOR_INPUT:
        return Reply(
            type="text",
            text=f"🎙️ {display}\n\nContinuing with your response...",
        )

    return Reply(
        type="text",
        text=f"🎙️ {display}\n\nI'll message you when done or if I need anything.",
    )
