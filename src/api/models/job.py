from enum import Enum
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4
from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    """Job status enumeration."""

    PENDING = "pending"
    PROCESSING = "processing"
    WAITING_FOR_INPUT = "waiting_for_input"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class ConversationMessage(BaseModel):
    """A message in the conversation between agent and user."""

    role: str  # "agent" or "user"
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class TurnResult(BaseModel):
    """Result from a single turn with vibe."""

    turn_number: int
    prompt: str
    output: str
    success: bool
    files_changed: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class Job(BaseModel):
    """Job model representing a coding task with full session history."""

    id: UUID = Field(default_factory=uuid4)
    session_id: str | None = Field(None, description="Session ID to link related jobs")
    prompt: str = Field(..., min_length=1, description="The coding task prompt")
    status: JobStatus = Field(default=JobStatus.PENDING)

    # Repository information
    repo_url: str | None = Field(None, description="Git repository URL to work on")
    branch_name: str | None = Field(None, description="Branch name for changes")
    working_dir: str | None = Field(None, description="Working directory for this job")

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # Conversation and turn history
    conversation: list[ConversationMessage] = Field(default_factory=list)
    turn_history: list[TurnResult] = Field(default_factory=list)
    current_turn: int = Field(default=0)
    max_turns: int = Field(default=50)

    # Current state when waiting for input
    current_question: str | None = None

    # Result/Error
    result: str | None = None
    error_message: str | None = None

    # External references
    webhook_url: str | None = Field(None, description="URL to notify on status changes")

    class Config:
        json_encoders = {
            UUID: str,
            datetime: lambda v: v.isoformat(),
        }


class JobCreate(BaseModel):
    """Model for creating a new job."""

    prompt: str = Field(..., min_length=1, description="The coding task prompt")
    session_id: str | None = Field(
        None, description="Session ID to link with previous jobs"
    )
    working_dir: str | None = Field(
        None,
        description="Existing working directory to reuse (for continuing sessions)",
    )
    repo_url: str | None = Field(
        None, description="Git repository URL to clone and work on"
    )
    branch_name: str | None = Field(
        None, description="Branch name to create (auto-generated if not provided)"
    )
    webhook_url: str | None = Field(None, description="URL to notify on status changes")
    max_turns: int = Field(default=50, description="Maximum turns for this session")


class UserResponse(BaseModel):
    """Model for user's response to agent question."""

    response: str = Field(..., min_length=1, description="User's response to the agent")


class JobResponse(BaseModel):
    """Model for job response."""

    id: UUID
    session_id: str | None = None
    status: JobStatus
    repo_url: str | None = None
    branch_name: str | None = None
    working_dir: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    current_turn: int
    max_turns: int
    result: str | None = None
    error_message: str | None = None
    current_question: str | None = None
    conversation: list[ConversationMessage] = Field(default_factory=list)
    turn_history: list[TurnResult] = Field(default_factory=list)

    class Config:
        json_encoders = {
            UUID: str,
            datetime: lambda v: v.isoformat(),
        }


class JobListResponse(BaseModel):
    """Model for job list response."""

    jobs: list[JobResponse]
    total: int
    page: int
    page_size: int


# WhatsApp Integration Models
class IncomingMessage(BaseModel):
    """Message received from WhatsApp."""

    sender: str
    type: Literal["text", "audio", "image"]
    text: str | None = None
    urls: list[str] | None = None
    media_base64: str | None = None
    mimetype: str | None = None


class Reply(BaseModel):
    """Reply to send back to WhatsApp."""

    type: Literal["text", "audio", "image"]
    text: str | None = None
    data_base64: str | None = None
    mimetype: str | None = None
    caption: str | None = None
