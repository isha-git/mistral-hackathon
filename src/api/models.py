from typing import Literal

from pydantic import BaseModel


class IncomingMessage(BaseModel):
    sender: str
    type: Literal["text", "audio", "image"]
    text: str | None = None
    urls: list[str] | None = None
    media_base64: str | None = None
    mimetype: str | None = None


class Reply(BaseModel):
    type: Literal["text", "audio", "image"]
    text: str | None = None
    data_base64: str | None = None
    mimetype: str | None = None
    caption: str | None = None
