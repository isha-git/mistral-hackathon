from uuid import UUID
from datetime import datetime, timedelta
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
        return f"job:{str(job_id)}"

    def _get_session_key(self, session_id: str) -> str:
        return f"session:{session_id}"

    def _get_repo_working_dir_key(self, repo_url: str, branch_name: str) -> str:
        repo_key = re.sub(r"[^a-zA-Z0-9]", "_", repo_url)
        return f"repo_workdir:{repo_key}:{branch_name}"

    def _serialize_job(self, job: Job) -> str:
        return job.model_dump_json()

    def _deserialize_job(self, data: str) -> Job:
        return Job.model_validate_json(data)

    def find_working_dir_for_repo_branch(
        self, repo_url: str, branch_name: str
    ) -> str | None:
        key = self._get_repo_working_dir_key(repo_url, branch_name)
        return self.redis.get(key)

    def set_working_dir_for_repo_branch(
        self, repo_url: str, branch_name: str, working_dir: str
    ) -> None:
        key = self._get_repo_working_dir_key(repo_url, branch_name)
        self.redis.setex(key, self.settings.repo_workdir_ttl, working_dir)

    def _get_active_job_key(self, sender: str) -> str:
        return f"active_job:{sender}"

    def get_active_job(self, sender: str) -> Job | None:
        key = self._get_active_job_key(sender)
        job_id = self.redis.get(key)
        if job_id:
            return self.get_job(job_id)
        return None

    def set_active_job(self, sender: str, job_id: str | UUID) -> None:
        key = self._get_active_job_key(sender)
        self.redis.setex(key, self.settings.redis_job_ttl, str(job_id))

    def clear_active_job(self, sender: str) -> None:
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
        job = Job(
            session_id=session_id,
            prompt=prompt,
            repo_url=repo_url,
            branch_name=branch_name,
            working_dir=working_dir,
            webhook_url=webhook_url,
            max_turns=max_turns,
        )

        job_key = self._get_job_key(job.id)
        self.redis.setex(job_key, self.settings.redis_job_ttl, self._serialize_job(job))

        # Add to sorted index for efficient pagination
        self.redis.zadd("job_index", {str(job.id): job.created_at.timestamp()})

        if session_id:
            session_key = self._get_session_key(session_id)
            self.redis.sadd(session_key, str(job.id))
            self.redis.expire(session_key, self.settings.redis_job_ttl)

        return job

    def get_job(self, job_id: UUID | str) -> Job | None:
        job_key = self._get_job_key(job_id)
        data = self.redis.get(job_key)
        if data is None:
            return None
        return self._deserialize_job(data)

    def save_job(self, job: Job) -> None:
        job_key = self._get_job_key(job.id)
        self.redis.setex(job_key, self.settings.redis_job_ttl, self._serialize_job(job))

    def update_job_status(
        self,
        job_id: UUID | str,
        status: JobStatus,
        result: str | None = None,
        error_message: str | None = None,
    ) -> Job | None:
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
        job = self.get_job(job_id)
        if job is None:
            return None

        job.working_dir = working_dir

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
        job = self.get_job(job_id)
        if job is None:
            return None

        job.current_question = question
        job.status = JobStatus.WAITING_FOR_INPUT

        self.add_conversation_message(job_id, "agent", question)

        self.save_job(job)
        return job

    def submit_user_response(
        self,
        job_id: UUID | str,
        response: str,
    ) -> Job | None:
        job = self.get_job(job_id)
        if job is None:
            return None

        self.add_conversation_message(job_id, "user", response)

        job.current_question = None
        job.status = JobStatus.PROCESSING

        self.save_job(job)
        return job

    def get_session_jobs(self, session_id: str) -> list[Job]:
        session_key = self._get_session_key(session_id)
        job_ids = self.redis.smembers(session_key)

        jobs = []
        for job_id in job_ids:
            job = self.get_job(job_id)
            if job:
                jobs.append(job)

        jobs.sort(key=lambda j: j.created_at)
        return jobs

    def list_jobs(self, page: int = 1, page_size: int = 20) -> tuple[list[Job], int]:
        """List jobs with pagination using sorted set index."""
        total = self.redis.zcard("job_index")
        if total == 0:
            return [], 0

        start = (page - 1) * page_size
        end = start + page_size - 1
        job_ids = self.redis.zrevrange("job_index", start, end)

        if not job_ids:
            return [], total

        job_keys = [self._get_job_key(jid) for jid in job_ids]
        jobs_data = self.redis.mget(job_keys)
        jobs = [self._deserialize_job(data) for data in jobs_data if data is not None]

        return jobs, total

    def get_opencode_session(self, session_id: str | None) -> str | None:
        if not session_id:
            return None
        return self.redis.get(f"opencode_session:{session_id}")

    def store_opencode_session(self, session_id: str, opencode_session_id: str) -> None:
        self.redis.setex(
            f"opencode_session:{session_id}",
            self.settings.opencode_session_ttl,
            opencode_session_id,
        )

    def find_stale_processing_jobs(self, stale_threshold_seconds: int) -> list[Job]:
        """Find jobs stuck in PROCESSING longer than the threshold.

        Scans the job index and returns jobs whose started_at timestamp
        is older than now - stale_threshold_seconds.
        """
        cutoff = datetime.utcnow() - timedelta(seconds=stale_threshold_seconds)
        all_job_ids = self.redis.zrange("job_index", 0, -1)
        stale_jobs: list[Job] = []

        for job_id in all_job_ids:
            job = self.get_job(job_id)
            if job is None:
                continue
            if job.status != JobStatus.PROCESSING:
                continue
            if job.started_at and job.started_at < cutoff:
                stale_jobs.append(job)

        return stale_jobs

    def delete_job(self, job_id: UUID | str) -> bool:
        job_key = self._get_job_key(job_id)
        self.redis.zrem("job_index", str(job_id))
        result = self.redis.delete(job_key)
        return result > 0


def get_job_service() -> JobService:
    """Get JobService instance."""
    return JobService()
