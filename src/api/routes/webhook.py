import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, status
from typing import Annotated

from src.api.config.settings import get_settings
from src.api.models.job import IncomingMessage, Reply
from src.api.services.pipeline import process_message

router = APIRouter()


async def verify_webhook_secret(
    x_webhook_secret: Annotated[str | None, Header()] = None,
) -> None:
    """Verify the shared webhook secret if configured."""
    settings = get_settings()
    if not settings.webhook_secret:
        return
    if not x_webhook_secret or not hmac.compare_digest(
        x_webhook_secret, settings.webhook_secret
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing webhook secret",
        )


@router.post("/webhook")
async def webhook(
    msg: IncomingMessage,
    _auth: Annotated[None, Depends(verify_webhook_secret)],
) -> Reply:
    return await process_message(msg)
