from api.models import IncomingMessage, Reply


async def process_message(msg: IncomingMessage) -> Reply:
    """Process an incoming WhatsApp message and return a reply.

    Stub: echoes back what was received. Replace with ElevenLabs integration.
    """
    return Reply(type="text", text=f"Received {msg.type}: {msg.text or '(media)'}")
