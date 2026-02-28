from api.models import IncomingMessage, Reply
from api.services.elevenlabs import stt


async def process_message(msg: IncomingMessage) -> Reply:
    """Route an incoming message through the processing pipeline.

    Current pipeline:
      audio → ElevenLabs STT → text reply
      text  → echo (Mistral integration later)
      image → echo (future)
    """
    if msg.type == "audio" and msg.media_base64:
        text = await stt.transcribe(msg.media_base64)
        # response = await mistral.chat(text)       # step 4 (later)
        # audio = await tts.speak(response)          # step 5 (later)
        return Reply(type="text", text=text)

    # Text and image: echo for now, Mistral integration later
    return Reply(type="text", text=msg.text or "(no text)")
