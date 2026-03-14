"""Tests for pipeline state machine logic."""

from src.api.models.job import Job, JobStatus


def test_determine_action_no_existing_job():
    from src.api.services.pipeline import _determine_action, PipelineAction

    assert _determine_action(None) == PipelineAction.CREATE_FIRST_JOB


def test_determine_action_waiting_for_input():
    from src.api.services.pipeline import _determine_action, PipelineAction

    job = Job(prompt="test", status=JobStatus.WAITING_FOR_INPUT)
    assert _determine_action(job) == PipelineAction.CONTINUE_SESSION


def test_determine_action_pending():
    from src.api.services.pipeline import _determine_action, PipelineAction

    job = Job(prompt="test", status=JobStatus.PENDING)
    assert _determine_action(job) == PipelineAction.REUSE_PENDING


def test_determine_action_completed():
    from src.api.services.pipeline import _determine_action, PipelineAction

    job = Job(prompt="test", status=JobStatus.COMPLETED)
    assert _determine_action(job) == PipelineAction.CREATE_FOLLOW_UP


def test_determine_action_failed():
    from src.api.services.pipeline import _determine_action, PipelineAction

    job = Job(prompt="test", status=JobStatus.FAILED)
    assert _determine_action(job) == PipelineAction.CREATE_FOLLOW_UP
