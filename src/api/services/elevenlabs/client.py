import os

from elevenlabs import ElevenLabs

_client: ElevenLabs | None = None


def get_client() -> ElevenLabs:
    """Return a shared ElevenLabs client. Reads ELEVENLABS_API_KEY from env."""
    global _client
    if _client is None:
        api_key = os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            raise RuntimeError("ELEVENLABS_API_KEY environment variable is not set")
        _client = ElevenLabs(api_key=api_key)
    return _client
