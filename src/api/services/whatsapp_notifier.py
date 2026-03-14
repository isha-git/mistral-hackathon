"""WhatsApp-specific notification implementation."""

import base64
import logging

import httpx

from src.api.config.settings import get_settings
from src.api.models.job import Job
from src.api.services.notifier import Notifier, NullNotifier
from src.api.services.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)

# Module-level circuit breaker shared across all notifications
_circuit_breaker = CircuitBreaker(failure_threshold=5, reset_timeout=60.0)


def _extract_recipient(job: Job) -> str | None:
    """Extract WhatsApp recipient number from job session_id."""
    recipient = job.session_id
    if recipient and recipient.startswith("whatsapp-"):
        return recipient.removeprefix("whatsapp-")
    return recipient


class WhatsAppNotifier(Notifier):
    """Send notifications via the WhatsApp bridge."""

    def __init__(self, callback_url: str):
        self.callback_url = callback_url
        self.send_doc_url = callback_url.replace("/send", "/send-document")
        self.settings = get_settings()

    def send_status(self, job: Job, status: str, data: dict) -> None:
        recipient = _extract_recipient(job)
        if not recipient:
            logger.warning(f"No recipient found for job {job.id}")
            return

        message = self._format_message(status, data)
        payload = {"to": recipient, "message": message}

        try:
            _circuit_breaker.call(
                httpx.post,
                self.callback_url,
                json=payload,
                timeout=self.settings.webhook_send_timeout,
            )
            logger.info(f"WhatsApp notification sent to {recipient} for job {job.id}")
        except CircuitOpenError:
            logger.warning(
                f"Circuit breaker open, skipping notification for job {job.id}"
            )
        except Exception as e:
            logger.error(f"Failed to send WhatsApp notification: {e}")

    def send_file(self, job: Job, filename: str, data: bytes, mimetype: str) -> None:
        recipient = _extract_recipient(job)
        if not recipient:
            return

        payload = {
            "to": recipient,
            "data_base64": base64.b64encode(data).decode("utf-8"),
            "filename": filename,
            "mimetype": mimetype,
            "caption": f"📎 {filename}",
        }

        try:
            _circuit_breaker.call(
                httpx.post,
                self.send_doc_url,
                json=payload,
                timeout=self.settings.webhook_document_timeout,
            )
            logger.info(f"Sent file '{filename}' to {recipient}")
        except CircuitOpenError:
            logger.warning(f"Circuit breaker open, skipping file send for {filename}")
        except Exception as e:
            logger.error(f"Failed to send file '{filename}': {e}")

    @staticmethod
    def _format_message(status: str, data: dict) -> str:
        settings = get_settings()
        if status == "needs_input":
            return f"❓ {data.get('question', 'I have a question for you')}"
        elif status == "completed":
            result = data.get("result", "Task completed")
            max_chars = settings.notification_result_max_chars
            return f"✅ Done!\n\n{result[:max_chars]}{'...' if len(result) > max_chars else ''}"
        elif status == "failed":
            error = data.get("error", "Task failed")
            max_chars = settings.notification_error_max_chars
            return f"❌ Failed: {error[:max_chars]}"
        elif status == "timeout":
            return "⏱️ Task timed out. Please try again."
        return f"Status: {status}"


def get_notifier() -> Notifier:
    """Factory: return WhatsAppNotifier if callback URL is configured, else NullNotifier."""
    settings = get_settings()
    if settings.whatsapp_callback_url:
        return WhatsAppNotifier(settings.whatsapp_callback_url)
    return NullNotifier()
