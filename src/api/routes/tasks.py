import hmac

from fastapi import APIRouter, Depends, HTTPException, Header, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Annotated
from uuid import UUID

from src.api.config.settings import get_settings
from src.api.models.job import (
    Job,
    JobCreate,
    JobResponse,
    JobListResponse,
    UserResponse,
    JobStatus,
)
from src.api.services.job_service import get_job_service, JobService
from src.api.tasks.jobs import run_coding_task, continue_coding_task

router = APIRouter(prefix="/tasks", tags=["tasks"])
security = HTTPBearer(auto_error=False)


def job_to_response(job: Job) -> JobResponse:
    """Convert a Job model to a JobResponse."""
    return JobResponse(
        id=job.id,
        session_id=job.session_id,
        status=job.status,
        repo_url=job.repo_url,
        branch_name=job.branch_name,
        working_dir=job.working_dir,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        current_turn=job.current_turn,
        max_turns=job.max_turns,
        result=job.result,
        error_message=job.error_message,
        current_question=job.current_question,
        conversation=job.conversation,
        turn_history=job.turn_history,
    )


async def verify_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    x_api_key: Annotated[str | None, Header()] = None,
) -> str:
    """Verify API key from Authorization header or X-API-Key header."""
    settings = get_settings()

    # Try Bearer token first
    if credentials and credentials.credentials:
        api_key = credentials.credentials
    # Fall back to X-API-Key header
    elif x_api_key:
        api_key = x_api_key
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not hmac.compare_digest(api_key, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return api_key


@router.post(
    "",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new coding task",
)
async def create_task(
    job_create: JobCreate,
    job_service: Annotated[JobService, Depends(get_job_service)],
    api_key: Annotated[str, Depends(verify_api_key)],
):
    """
    Submit a new coding task to be processed by OpenCode.

    For stateful sessions:
    - Pass session_id to link related jobs
    - Pass working_dir to continue in existing directory
    - Or pass repo_url + branch_name for persistent repo directory
    """
    # Check if we should reuse existing working directory
    working_dir = job_create.working_dir
    if not working_dir and job_create.repo_url and job_create.branch_name:
        # Try to find existing working directory for this repo/branch
        existing_dir = job_service.find_working_dir_for_repo_branch(
            job_create.repo_url, job_create.branch_name
        )
        if existing_dir:
            working_dir = existing_dir

    # Create job
    job = job_service.create_job(
        prompt=job_create.prompt,
        session_id=job_create.session_id,
        working_dir=working_dir,
        repo_url=job_create.repo_url,
        branch_name=job_create.branch_name,
        webhook_url=job_create.webhook_url,
        max_turns=job_create.max_turns,
    )

    # Queue Celery task
    run_coding_task.delay(str(job.id))

    return job_to_response(job)


@router.get(
    "",
    response_model=JobListResponse,
    summary="List all tasks",
)
async def list_tasks(
    job_service: Annotated[JobService, Depends(get_job_service)],
    api_key: Annotated[str, Depends(verify_api_key)],
    page: int = 1,
    page_size: int = 20,
):
    """List all coding tasks with pagination."""
    jobs, total = job_service.list_jobs(page=page, page_size=page_size)

    return JobListResponse(
        jobs=[job_to_response(job) for job in jobs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Get task status",
)
async def get_task(
    job_id: UUID,
    job_service: Annotated[JobService, Depends(get_job_service)],
    api_key: Annotated[str, Depends(verify_api_key)],
):
    """Get the status and details of a specific task."""
    job = job_service.get_job(job_id)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )

    return job_to_response(job)


@router.post(
    "/{job_id}/continue",
    response_model=JobResponse,
    summary="Continue a task with user response",
)
async def continue_task(
    job_id: UUID,
    user_response: UserResponse,
    job_service: Annotated[JobService, Depends(get_job_service)],
    api_key: Annotated[str, Depends(verify_api_key)],
):
    """
    Submit a user response to continue a task that's waiting for input.

    Use this when the agent has asked a question and you have the user's answer.
    """
    job = job_service.get_job(job_id)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )

    if job.status != JobStatus.WAITING_FOR_INPUT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job {job_id} is not waiting for input (status: {job.status})",
        )

    if not job.working_dir:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job {job_id} has no working directory",
        )

    # Queue continuation task
    continue_coding_task.delay(str(job_id), user_response.response)

    # Return current state (will update async)
    job.status = JobStatus.PROCESSING
    return job_to_response(job)


@router.get(
    "/session/{session_id}",
    response_model=list[JobResponse],
    summary="Get all jobs in a session",
)
async def get_session_jobs(
    session_id: str,
    job_service: Annotated[JobService, Depends(get_job_service)],
    api_key: Annotated[str, Depends(verify_api_key)],
):
    """Get all jobs belonging to a session."""
    jobs = job_service.get_session_jobs(session_id)

    return [job_to_response(job) for job in jobs]
