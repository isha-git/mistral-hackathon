from uuid import UUID
from datetime import datetime
import re

from src.api.config.redis import get_redis_client
from src.api.config.settings import get_settings
from src.api.models.job import Job, JobStatus, ConversationMessage, TurnResult


class JobService:
    """Service for managing jobs in Redis."""

    def __init__(self):
        self.redis = get_redis_client()
        self.settings = get_settings()

    def _get_job_key(self, job_id: UUID | str) -> str:
        """Generate Redis key for a job."""
        return f"job:{str(job_id)}"

    def _get_session_key(self, session_id: str) -> str:
        """Generate Redis key for session tracking."""
        return f"session:{session_id}"

    def _get_repo_working_dir_key(self, repo_url: str, branch_name: str) -> str:
        """Generate Redis key for repo+branch working directory tracking."""
        # Sanitize repo_url for use as key
        repo_key = re.sub(r"[^a-zA-Z0-9]", "_", repo_url)
        return f"repo_workdir:{repo_key}:{branch_name}"

    def _serialize_job(self, job: Job) -> str:
        """Serialize job to JSON string."""
        return job.model_dump_json()

    def _deserialize_job(self, data: str) -> Job:
        """Deserialize JSON string to Job."""
        return Job.model_validate_json(data)

    def find_working_dir_for_repo_branch(
        self, repo_url: str, branch_name: str
    ) -> str | None:
        """Find existing working directory for a repo/branch combination."""
        key = self._get_repo_working_dir_key(repo_url, branch_name)
        return self.redis.get(key)

    def set_working_dir_for_repo_branch(
        self, repo_url: str, branch_name: str, working_dir: str
    ) -> None:
        """Store working directory for a repo/branch combination."""
        key = self._get_repo_working_dir_key(repo_url, branch_name)
        # Store with long TTL (30 days)
        self.redis.setex(key, 2592000, working_dir)

    def _get_active_job_key(self, sender: str) -> str:
        """Generate Redis key for user's active job."""
        return f"active_job:{sender}"

    def get_active_job(self, sender: str) -> Job | None:
        """Get the active job for a user (the one we keep updating)."""
        key = self._get_active_job_key(sender)
        job_id = self.redis.get(key)
        if job_id:
            return self.get_job(job_id)
        return None

    def set_active_job(self, sender: str, job_id: str | UUID) -> None:
        """Set the active job for a user."""
        key = self._get_active_job_key(sender)
        self.redis.setex(key, self.settings.redis_job_ttl, str(job_id))

    def clear_active_job(self, sender: str) -> None:
        """Clear the active job for a user (when starting new project)."""
        key = self._get_active_job_key(sender)
        self.redis.delete(key)

    def create_job(
        self,
        prompt: str,
        session_id: str | None = None,
        working_dir: str | None = None,
        repo_url: str | None = None,
        branch_name: str | None = None,
        webhook_url: str | None = None,
        max_turns: int = 50,
    ) -> Job:
        """Create a new job and store it in Redis."""
        job = Job(
            session_id=session_id,
            prompt=prompt,
            repo_url=repo_url,
            branch_name=branch_name,
            working_dir=working_dir,
            webhook_url=webhook_url,
            max_turns=max_turns,
        )

        # Store in Redis with TTL
        job_key = self._get_job_key(job.id)
        self.redis.setex(job_key, self.settings.redis_job_ttl, self._serialize_job(job))

        # If session_id provided, add to session index
        if session_id:
            session_key = self._get_session_key(session_id)
            self.redis.sadd(session_key, str(job.id))
            self.redis.expire(session_key, self.settings.redis_job_ttl)

        return job

    def get_job(self, job_id: UUID | str) -> Job | None:
        """Retrieve a job from Redis."""
        job_key = self._get_job_key(job_id)
        data = self.redis.get(job_key)

        if data is None:
            return None

        return self._deserialize_job(data)

    def save_job(self, job: Job) -> None:
        """Save job to Redis."""
        job_key = self._get_job_key(job.id)
        self.redis.setex(job_key, self.settings.redis_job_ttl, self._serialize_job(job))

    def update_job_status(
        self,
        job_id: UUID | str,
        status: JobStatus,
        result: str | None = None,
        error_message: str | None = None,
    ) -> Job | None:
        """Update job status and store in Redis."""
        job = self.get_job(job_id)
        if job is None:
            return None

        job.status = status

        if status == JobStatus.PROCESSING and job.started_at is None:
            job.started_at = datetime.utcnow()
        elif status == JobStatus.COMPLETED:
            job.completed_at = datetime.utcnow()
            if result:
                job.result = result
        elif status in (JobStatus.FAILED, JobStatus.TIMEOUT):
            if error_message:
                job.error_message = error_message

        self.save_job(job)
        return job

    def add_turn_result(
        self,
        job_id: UUID | str,
        turn_result: TurnResult,
    ) -> Job | None:
        """Add a turn result to the job history."""
        job = self.get_job(job_id)
        if job is None:
            return None

        job.turn_history.append(turn_result)
        job.current_turn = turn_result.turn_number

        self.save_job(job)
        return job

    def add_conversation_message(
        self,
        job_id: UUID | str,
        role: str,
        content: str,
    ) -> Job | None:
        """Add a message to the conversation history."""
        job = self.get_job(job_id)
        if job is None:
            return None

        message = ConversationMessage(role=role, content=content)
        job.conversation.append(message)

        self.save_job(job)
        return job

    def set_working_directory(
        self,
        job_id: UUID | str,
        working_dir: str,
    ) -> Job | None:
        """Set the working directory for a job."""
        job = self.get_job(job_id)
        if job is None:
            return None

        job.working_dir = working_dir

        # Also store for repo/branch if applicable
        if job.repo_url and job.branch_name:
            self.set_working_dir_for_repo_branch(
                job.repo_url, job.branch_name, working_dir
            )

        self.save_job(job)
        return job

    def set_agent_question(
        self,
        job_id: UUID | str,
        question: str,
    ) -> Job | None:
        """Set the current question agent is asking."""
        job = self.get_job(job_id)
        if job is None:
            return None

        job.current_question = question
        job.status = JobStatus.WAITING_FOR_INPUT

        # Add to conversation
        self.add_conversation_message(job_id, "agent", question)

        self.save_job(job)
        return job

    def submit_user_response(
        self,
        job_id: UUID | str,
        response: str,
    ) -> Job | None:
        """Submit user response and continue processing."""
        job = self.get_job(job_id)
        if job is None:
            return None

        # Add user response to conversation
        self.add_conversation_message(job_id, "user", response)

        # Clear current question and update status
        job.current_question = None
        job.status = JobStatus.PROCESSING

        self.save_job(job)
        return job

    def get_session_jobs(self, session_id: str) -> list[Job]:
        """Get all jobs in a session."""
        session_key = self._get_session_key(session_id)
        job_ids = self.redis.smembers(session_key)

        jobs = []
        for job_id in job_ids:
            job = self.get_job(job_id)
            if job:
                jobs.append(job)

        # Sort by creation time
        jobs.sort(key=lambda j: j.created_at)
        return jobs

    def list_jobs(self, page: int = 1, page_size: int = 20) -> tuple[list[Job], int]:
        """List jobs with pagination. Returns (jobs, total_count)."""
        job_keys = self.redis.keys("job:*")
        total = len(job_keys)

        if total == 0:
            return [], 0

        jobs_data = self.redis.mget(job_keys)
        jobs = [self._deserialize_job(data) for data in jobs_data if data is not None]

        # Sort by created_at descending
        jobs.sort(key=lambda j: j.created_at, reverse=True)

        # Paginate
        start = (page - 1) * page_size
        end = start + page_size
        paginated_jobs = jobs[start:end]

        return paginated_jobs, total

    def get_opencode_session(self, session_id: str | None) -> str | None:
        """Retrieve stored OpenCode session ID from Redis for session continuity."""
        if not session_id:
            return None
        return self.redis.get(f"opencode_session:{session_id}")

    def store_opencode_session(self, session_id: str, opencode_session_id: str) -> None:
        """Store OpenCode session ID in Redis."""
        self.redis.setex(f"opencode_session:{session_id}", 86400, opencode_session_id)

    def delete_job(self, job_id: UUID | str) -> bool:
        """Delete a job from Redis."""
        job_key = self._get_job_key(job_id)
        result = self.redis.delete(job_key)
        return result > 0


def get_job_service() -> JobService:
    """Get JobService instance."""
    return JobService()
