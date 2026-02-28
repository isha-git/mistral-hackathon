"""Text-to-speech via ElevenLabs. Placeholder for future use."""

# from api.services.elevenlabs.client import get_client
#
# async def speak(text: str, voice_id: str = "default") -> bytes:
#     """Convert text to audio using ElevenLabs TTS."""
#     client = get_client()
#     audio = client.text_to_speech.convert(
#         text=text,
#         voice_id=voice_id,
#         model_id="eleven_multilingual_v2",
#     )
#     return b"".join(audio)
