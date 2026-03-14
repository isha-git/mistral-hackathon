"""Notification interface for sending job status updates and files to users."""

from abc import ABC, abstractmethod

from src.api.models.job import Job


class Notifier(ABC):
    """Abstract base class for sending notifications."""

    @abstractmethod
    def send_status(self, job: Job, status: str, data: dict) -> None: ...

    @abstractmethod
    def send_file(
        self, job: Job, filename: str, data: bytes, mimetype: str
    ) -> None: ...


class NullNotifier(Notifier):
    """No-op notifier for when no callback is configured."""

    def send_status(self, job: Job, status: str, data: dict) -> None:
        pass

    def send_file(self, job: Job, filename: str, data: bytes, mimetype: str) -> None:
        pass
