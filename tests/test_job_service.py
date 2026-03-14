"""Tests for JobService Redis operations."""

from src.api.models.job import JobStatus, TurnResult


def test_create_and_get_job(job_service):
    job = job_service.create_job(prompt="Build a todo app")
    assert job.prompt == "Build a todo app"
    assert job.status == JobStatus.PENDING

    retrieved = job_service.get_job(job.id)
    assert retrieved is not None
    assert retrieved.id == job.id
    assert retrieved.prompt == "Build a todo app"


def test_update_job_status(job_service):
    job = job_service.create_job(prompt="Test")
    job_service.update_job_status(job.id, JobStatus.PROCESSING)

    updated = job_service.get_job(job.id)
    assert updated.status == JobStatus.PROCESSING
    assert updated.started_at is not None


def test_list_jobs_pagination(job_service):
    for i in range(5):
        job_service.create_job(prompt=f"Task {i}")

    jobs, total = job_service.list_jobs(page=1, page_size=2)
    assert total == 5
    assert len(jobs) == 2

    jobs2, _ = job_service.list_jobs(page=2, page_size=2)
    assert len(jobs2) == 2


def test_delete_job(job_service):
    job = job_service.create_job(prompt="To delete")
    assert job_service.get_job(job.id) is not None

    job_service.delete_job(job.id)
    assert job_service.get_job(job.id) is None


def test_active_job(job_service):
    sender = "user123"
    job = job_service.create_job(prompt="Active test")
    job_service.set_active_job(sender, job.id)

    active = job_service.get_active_job(sender)
    assert active is not None
    assert active.id == job.id

    job_service.clear_active_job(sender)
    assert job_service.get_active_job(sender) is None


def test_add_turn_result(job_service):
    job = job_service.create_job(prompt="Turn test")
    turn = TurnResult(turn_number=1, prompt="test", output="result", success=True)
    job_service.add_turn_result(job.id, turn)

    updated = job_service.get_job(job.id)
    assert len(updated.turn_history) == 1
    assert updated.current_turn == 1


def test_session_jobs(job_service):
    session = "session-1"
    job_service.create_job(prompt="Task 1", session_id=session)
    job_service.create_job(prompt="Task 2", session_id=session)

    jobs = job_service.get_session_jobs(session)
    assert len(jobs) == 2


def test_opencode_session_storage(job_service):
    job_service.store_opencode_session("whatsapp-123", "oc-session-abc")
    assert job_service.get_opencode_session("whatsapp-123") == "oc-session-abc"
    assert job_service.get_opencode_session("nonexistent") is None
