from fastapi import APIRouter

from src.api.models.job import IncomingMessage, Reply
from src.api.services.pipeline import process_message

router = APIRouter()


@router.post("/webhook")
async def webhook(msg: IncomingMessage) -> Reply:
    return await process_message(msg)
