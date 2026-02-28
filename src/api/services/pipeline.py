"""
Pipeline for processing WhatsApp messages and routing to Vibe jobs.

This bridges the WhatsApp integration from main with our Vibe API:
- Receives messages from WhatsApp webhook
- Routes text directly to Vibe jobs
- Transcribes audio to text first, then creates job
- Returns job status/results back to WhatsApp
"""

from typing import Optional

from src.api.models.job import IncomingMessage, Reply
from src.api.services.job_service import get_job_service
from src.api.tasks.jobs import run_vibe_task


async def process_message(msg: IncomingMessage) -> Reply:
    """
    Process incoming WhatsApp message and create Vibe job.

    Pipeline:
      text  → Create Vibe job directly
      audio → Transcribe (if ElevenLabs available), then create job
      image → Future: describe image, then create job

    Returns job creation confirmation to user.
    """
    if msg.type == "text" and msg.text:
        # Create Vibe job from text
        return await _create_vibe_job(
            prompt=msg.text,
            sender=msg.sender,
            repo_url=None,  # Could extract from message
            branch_name=None,
        )

    elif msg.type == "audio" and msg.media_base64:
        # Try to transcribe if ElevenLabs is available
        try:
            from src.api.services.elevenlabs import stt

            transcribed = await stt.transcribe(msg.media_base64)
            return await _create_vibe_job(
                prompt=transcribed, sender=msg.sender, repo_url=None, branch_name=None
            )
        except ImportError:
            # ElevenLabs not configured, ask user to send text
            return Reply(
                type="text",
                text="Audio transcription not available. Please send your request as text.",
            )

    elif msg.type == "image":
        # Future: describe image and create job
        return Reply(
            type="text",
            text="Image processing coming soon. Please send your request as text for now.",
        )

    # Fallback
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
    Create a Vibe job and return confirmation to user.

    The job will be processed async by Celery. Results will be sent
    via webhook back to the WhatsApp service.
    """
    job_service = get_job_service()

    # Create job
    job = job_service.create_job(
        prompt=prompt,
        session_id=f"whatsapp-{sender}",
        repo_url=repo_url,
        branch_name=branch_name or f"whatsapp-{sender[:8]}",
        webhook_url=None,  # TODO: Configure webhook to WhatsApp service
        max_turns=50,
    )

    # Queue for processing
    run_vibe_task.delay(str(job.id))

    # Return confirmation to user
    return Reply(
        type="text",
        text=f"🚀 Got it! I'm working on: {prompt[:50]}{'...' if len(prompt) > 50 else ''}\n\n"
        f"Job ID: {job.id}\n"
        f"I'll let you know when it's done or if I have questions.",
    )
