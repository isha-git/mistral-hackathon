import base64

from src.api.services.elevenlabs.client import get_client


async def transcribe(audio_base64: str, language_code: str = "eng") -> str:
    """Convert audio (base64-encoded) to text using ElevenLabs Scribe v2."""
    audio_bytes = base64.b64decode(audio_base64)

    client = get_client()
    result = client.speech_to_text.convert(
        file=audio_bytes,
        model_id="scribe_v2",
        language_code=language_code,
    )

    return result.text
