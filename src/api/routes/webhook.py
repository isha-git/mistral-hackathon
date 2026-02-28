from fastapi import APIRouter

from api.models import IncomingMessage, Reply
from api.services.pipeline import process_message

router = APIRouter()


@router.post("/webhook")
async def webhook(msg: IncomingMessage) -> Reply:
    return await process_message(msg)
