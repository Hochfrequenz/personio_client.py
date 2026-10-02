"""Custom exceptions for the Personio API client."""

from __future__ import annotations


class PersonioClientError(Exception):
    """Base exception for all Personio client errors."""


class PersonioAPIError(PersonioClientError):
    """Exception raised when the API returns an error response.

    Attributes:
        status_code: The HTTP status code of the error response.
        message: A human-readable error message.
        trace_id: The ID Personio assigned to the failed request, if the response contains one.
            Personio support asks for it.
    """

    def __init__(self, status_code: int, message: str, trace_id: str | None = None) -> None:
        """Initialize the API error.

        Args:
            status_code: The HTTP status code of the error response.
            message: A human-readable error message.
            trace_id: The ID Personio assigned to the failed request, if the response contains one.
        """
        self.status_code = status_code
        self.message = message
        self.trace_id = trace_id
        suffix = f" (trace ID: {trace_id})" if trace_id else ""
        super().__init__(f"API error {status_code}: {message}{suffix}")


class PersonioAuthenticationError(PersonioAPIError):
    """Exception raised when the authentication fails (401/403 or an error of the token endpoint)."""


class PersonioRateLimitError(PersonioAPIError):
    """Exception raised when the API still answers with 429 Too Many Requests after all retries.

    Attributes:
        retry_after: The delay in seconds the last response asked for, if it contains a Retry-After header.
    """

    def __init__(self, message: str, trace_id: str | None = None, retry_after: float | None = None) -> None:
        """Initialize the rate limit error.

        Args:
            message: A human-readable error message.
            trace_id: The ID Personio assigned to the failed request, if the response contains one.
            retry_after: The delay in seconds the last response asked for, if it contains a Retry-After header.
        """
        super().__init__(status_code=429, message=message, trace_id=trace_id)
        self.retry_after = retry_after
