"""
Pipeline for processing WhatsApp messages and routing to Vibe jobs.

Flow:
  1. WhatsApp message arrives at /webhook
  2. Text/audio routed here, job created with callback URL
  3. Celery runs Vibe agent async
  4. If agent asks question → POST callback_url → WhatsApp service → user
  5. User replies → WhatsApp service → POST /tasks/{id}/continue
  6. Agent continues → completion → POST callback_url → user
"""

from typing import Optional

from src.api.models.job import IncomingMessage, Reply
from src.api.services.job_service import get_job_service
from src.api.config.settings import get_settings
from src.api.tasks.jobs import run_vibe_task


async def process_message(msg: IncomingMessage) -> Reply:
    """
    Process incoming WhatsApp message and create Vibe job.

    Pipeline:
      text  → Create Vibe job directly
      audio → Transcribe via ElevenLabs, then create job
      image → Future
    """
    if msg.type == "text" and msg.text:
        return await _create_vibe_job(prompt=msg.text, sender=msg.sender)

    elif msg.type == "audio" and msg.media_base64:
        try:
            from src.api.services.elevenlabs import stt

            transcribed = await stt.transcribe(msg.media_base64)
            return await _create_vibe_job(prompt=transcribed, sender=msg.sender)
        except ImportError:
            return Reply(
                type="text",
                text="Audio transcription not available. Please send your request as text.",
            )

    elif msg.type == "image":
        return Reply(
            type="text",
            text="Image processing coming soon. Please send your request as text for now.",
        )

    return Reply(
        type="text", text="I didn't understand that. Please send text or audio."
    )


async def _create_vibe_job(
    prompt: str,
    sender: str,
    repo_url: Optional[str] = None,
    branch_name: Optional[str] = None,
) -> Reply:
    """
    Create a Vibe job with the WhatsApp callback URL so the agent can
    send questions and results back to the user.
    """
    settings = get_settings()
    job_service = get_job_service()

    # whatsapp_callback_url is where Celery will POST when:
    # - agent has a question (status: needs_input)
    # - job completes (status: completed)
    # - job fails (status: failed)
    # The WhatsApp service at that URL then forwards to the user.
    job = job_service.create_job(
        prompt=prompt,
        session_id=f"whatsapp-{sender}",
        repo_url=repo_url,
        branch_name=branch_name or f"whatsapp-{sender[:8]}",
        webhook_url=settings.whatsapp_callback_url,
        max_turns=50,
    )

    run_vibe_task.delay(str(job.id))

    return Reply(
        type="text",
        text=f"Got it! Working on: {prompt[:50]}{'...' if len(prompt) > 50 else ''}\n\n"
        f"Job ID: `{job.id}`\n"
        f"I'll message you when done or if I need anything.",
    )
