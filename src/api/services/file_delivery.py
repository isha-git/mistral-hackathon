"""Send changed files or inline code blocks to users via notifier."""

import os
import mimetypes
import logging

from src.api.config.settings import get_settings
from src.api.models.job import Job
from src.api.services.code_extractor import extract_inline_code
from src.api.services.whatsapp_notifier import get_notifier

logger = logging.getLogger(__name__)

MIMETYPE_MAP = {
    ".py": "text/x-python",
    ".js": "application/javascript",
    ".ts": "application/typescript",
    ".html": "text/html",
    ".css": "text/css",
    ".json": "application/json",
}


def deliver_files(job: Job, changed_files: list[str], output: str) -> None:
    """Send changed files or inline code blocks to the user via notifier."""
    if changed_files:
        _send_changed_files(job, changed_files)
    else:
        _send_inline_code(job, output)


def _send_inline_code(job: Job, output: str) -> None:
    """Extract code blocks from agent output and send as file attachments."""
    notifier = get_notifier()
    blocks = extract_inline_code(output)

    for _lang, code, filename in blocks:
        ext = os.path.splitext(filename)[1]
        mimetype = MIMETYPE_MAP.get(ext, "text/plain")
        notifier.send_file(job, filename, code.encode("utf-8"), mimetype)


def _send_changed_files(job: Job, files_changed: list[str]) -> None:
    """Send changed files as document attachments."""
    settings = get_settings()
    notifier = get_notifier()

    for filepath in files_changed:
        if not os.path.isabs(filepath):
            filepath = os.path.join(job.working_dir, filepath)

        # Resolve symlinks and canonicalize, then verify the path stays
        # within the job's working directory to prevent path traversal.
        resolved = os.path.realpath(filepath)
        allowed_root = os.path.realpath(job.working_dir)
        if not resolved.startswith(allowed_root + os.sep) and resolved != allowed_root:
            logger.warning(f"[send_files] Path traversal blocked: {filepath}")
            continue

        if not os.path.isfile(resolved):
            logger.warning(f"[send_files] File not found: {resolved}")
            continue
        filepath = resolved

        if os.path.getsize(filepath) > settings.max_file_size_bytes:
            logger.info(f"[send_files] Skipping large file: {filepath}")
            continue

        filename = os.path.basename(filepath)
        if filename.startswith("."):
            continue

        try:
            with open(filepath, "rb") as f:
                file_data = f.read()

            mimetype_guess, _ = mimetypes.guess_type(filepath)
            notifier.send_file(
                job, filename, file_data, mimetype_guess or "application/octet-stream"
            )
        except Exception as e:
            logger.error(f"[send_files] Failed to send '{filename}': {e}")
