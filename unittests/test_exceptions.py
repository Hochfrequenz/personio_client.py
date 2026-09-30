"""Tests for the exceptions of the Personio client."""

import pytest

from personio_client import (
    PersonioAPIError,
    PersonioAuthenticationError,
    PersonioClientError,
    PersonioRateLimitError,
)


class TestPersonioAPIError:
    """Tests for the attributes and the message of PersonioAPIError."""

    def test_attributes_and_message(self) -> None:
        """Test that the error keeps the status code and the message."""
        error = PersonioAPIError(404, "Resource not found: There is no resource for the specified id.")

        assert error.status_code == 404
        assert error.message == "Resource not found: There is no resource for the specified id."
        assert error.trace_id is None
        assert str(error) == "API error 404: Resource not found: There is no resource for the specified id."

    def test_trace_id_is_part_of_the_message(self) -> None:
        """Test that the trace ID, which Personio support asks for, shows up in the message."""
        error = PersonioAPIError(400, "Bad request", trace_id="aswo3f-a202lfso-312123sld-1230ddd")

        assert error.trace_id == "aswo3f-a202lfso-312123sld-1230ddd"
        assert str(error) == "API error 400: Bad request (trace ID: aswo3f-a202lfso-312123sld-1230ddd)"


class TestPersonioRateLimitError:
    """Tests for PersonioRateLimitError."""

    def test_status_code_is_429(self) -> None:
        """Test that a rate limit error always has the status code 429 and keeps the requested delay."""
        error = PersonioRateLimitError("Too many requests", retry_after=30.0)

        assert error.status_code == 429
        assert error.retry_after == 30.0
        assert str(error) == "API error 429: Too many requests"

    def test_retry_after_is_optional(self) -> None:
        """Test that the delay is None if the response contains no Retry-After header."""
        assert PersonioRateLimitError("Too many requests").retry_after is None


class TestHierarchy:
    """Tests for the hierarchy of the exceptions."""

    @pytest.mark.parametrize("error_class", [PersonioAuthenticationError, PersonioRateLimitError])
    def test_specific_errors_are_api_errors(self, error_class: type[PersonioAPIError]) -> None:
        """Test that callers can catch every error response of the API as PersonioAPIError."""
        assert issubclass(error_class, PersonioAPIError)

    def test_api_error_is_client_error(self) -> None:
        """Test that callers can catch every error of the client as PersonioClientError."""
        assert issubclass(PersonioAPIError, PersonioClientError)
