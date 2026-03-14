import logging
from celery import Celery
from celery.signals import setup_logging
from src.api.config.settings import get_settings


def create_celery_app() -> Celery:
    """Create and configure Celery application."""
    settings = get_settings()

    celery_app = Celery(
        "tasks",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
        include=["src.api.tasks.jobs"],
    )

    # Celery configuration
    celery_app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_time_limit=settings.job_max_timeout,
        task_soft_time_limit=settings.job_max_timeout
        - 60,  # Soft limit 1 min before hard
        worker_prefetch_multiplier=1,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        result_expires=settings.redis_job_ttl,
        beat_schedule={
            "recover-stale-jobs": {
                "task": "src.api.tasks.jobs.recover_stale_jobs",
                "schedule": 300.0,  # every 5 minutes
            },
        },
    )

    return celery_app


@setup_logging.connect
def setup_celery_logging(**kwargs):
    """Configure logging for Celery workers."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


# Create the Celery app instance
celery_app = create_celery_app()
