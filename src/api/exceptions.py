"""Application-specific exceptions with HTTP status codes."""


class AppError(Exception):
    """Base application error with HTTP status code."""

    status_code: int = 500
    detail: str = "Internal server error"

    def __init__(self, detail: str | None = None):
        if detail:
            self.detail = detail
        super().__init__(self.detail)


class JobNotFoundError(AppError):
    status_code = 404

    def __init__(self, job_id: str):
        super().__init__(f"Job {job_id} not found")


class JobInvalidStateError(AppError):
    status_code = 400

    def __init__(self, job_id: str, current_status: str, expected_status: str):
        super().__init__(
            f"Job {job_id} is {current_status}, expected {expected_status}"
        )


class OpenCodeError(AppError):
    status_code = 502

    def __init__(self, message: str):
        super().__init__(f"OpenCode service error: {message}")


class RateLimitError(AppError):
    status_code = 429
    detail = "Rate limit exceeded"
