"""Tests for the core of PersonioClient: session handling, authentication, error mapping and retries."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import aiohttp
import pytest
from aioresponses import aioresponses

import personio_client.client as client_module
from personio_client import (
    OAuth2Token,
    PersonioAPIError,
    PersonioAuthenticationError,
    PersonioClient,
    PersonioRateLimitError,
)
from unittests.helpers import (
    ACCESS_TOKEN,
    AUTH_SPEC,
    BASE_URL,
    CLIENT_ID,
    CLIENT_SECRET,
    PERSONS_SPEC,
    TOKEN_RESPONSE,
    TOKEN_URL,
    FakeClock,
    requests_to,
    spec_example,
)

PERSON_PATH = "/v2/persons/3003"
PERSON_URL = f"{BASE_URL}{PERSON_PATH}"
PERSON_RESPONSE = spec_example(PERSONS_SPEC, "PersonsResponse")


# =============================================================================
# Context Manager
# =============================================================================


class TestContextManager:
    """Tests for the async context manager and the session handling."""

    async def test_aenter_creates_session_and_aexit_closes_it(self) -> None:
        """Test that the client creates its own session and closes it again."""
        client = PersonioClient(client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
        assert client._session is None

        async with client as entered:
            assert entered is client
            session = client._session
            assert session is not None

        assert client._session is None
        assert session.closed

    async def test_external_session_is_not_closed(self) -> None:
        """Test that a session passed to the constructor is used, but left open."""
        async with aiohttp.ClientSession() as session:
            async with PersonioClient(client_id=CLIENT_ID, client_secret=CLIENT_SECRET, session=session) as client:
                assert client._session is session

            assert not session.closed

    async def test_request_without_context_raises_error(self) -> None:
        """Test that making requests without context manager raises RuntimeError."""
        client = PersonioClient(client_id=CLIENT_ID, client_secret=CLIENT_SECRET)

        with pytest.raises(RuntimeError, match="must be used within an async context manager"):
            await client.obtain_access_token()

    async def test_trailing_slash_of_base_url_is_stripped(self, mock_aiohttp: aioresponses) -> None:
        """Test that a custom base URL with a trailing slash works."""
        mock_aiohttp.post("https://personio.example.com/v2/auth/token", payload=TOKEN_RESPONSE)

        async with PersonioClient(
            client_id=CLIENT_ID, client_secret=CLIENT_SECRET, base_url="https://personio.example.com/"
        ) as client:
            token = await client.obtain_access_token()

        assert token.access_token == ACCESS_TOKEN

    def test_negative_max_retries_is_rejected(self) -> None:
        """Test that max_retries must not be negative."""
        with pytest.raises(ValueError, match="max_retries"):
            PersonioClient(client_id=CLIENT_ID, client_secret=CLIENT_SECRET, max_retries=-1)


class TestSignatures:
    """Tests for the signatures of the public methods."""

    def test_no_parameter_is_annotated_as_plain_datetime(self) -> None:
        """Test that date-time parameters are annotated as AwareDatetime to show that they need a timezone."""
        plain_datetime_parameters = [
            f"{name}({parameter.name})"
            for name, method in inspect.getmembers(PersonioClient, inspect.isfunction)
            if not name.startswith("_")
            for parameter in inspect.signature(method).parameters.values()
            if re.search(r"\bdatetime\b", str(parameter.annotation))
        ]

        assert plain_datetime_parameters == []


# =============================================================================
# Authentication
# =============================================================================


class TestObtainAccessToken:
    """Tests for POST /v2/auth/token."""

    async def test_form_body_and_token(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that the client credentials are sent as form fields without an access token."""
        token = await client.obtain_access_token()

        assert token == OAuth2Token.model_validate(TOKEN_RESPONSE)
        assert token.expires_in == 86400
        request = requests_to(mock_api, "POST", TOKEN_URL)[0]
        assert request.kwargs["data"] == {
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        }
        assert "Authorization" not in request.kwargs["headers"]

    async def test_scope_is_sent_space_delimited(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that the requested scopes are sent as one space-delimited value."""
        await client.obtain_access_token(scope=["personio:persons:read", "personio:persons:write"])

        request = requests_to(mock_api, "POST", TOKEN_URL)[0]
        assert request.kwargs["data"]["scope"] == "personio:persons:read personio:persons:write"

    async def test_invalid_client_raises_authentication_error(
        self, mock_aiohttp: aioresponses, client: PersonioClient
    ) -> None:
        """Test that rejected credentials raise PersonioAuthenticationError with the OAuth2 error and the trace ID."""
        mock_aiohttp.post(TOKEN_URL, status=400, payload=spec_example(AUTH_SPEC, "OAuth2TokenInvalidClient"))

        with pytest.raises(PersonioAuthenticationError) as exc_info:
            await client.obtain_access_token()

        assert exc_info.value.status_code == 400
        assert exc_info.value.message.startswith("invalid_client: Client authentication failed")
        assert exc_info.value.trace_id == "baaaaaad-c0de-fade-baad-c000000000de"

    async def test_server_error_raises_api_error(self, mock_aiohttp: aioresponses, client: PersonioClient) -> None:
        """Test that a server error of the token endpoint is no authentication error."""
        mock_aiohttp.post(TOKEN_URL, status=500, body="Internal Server Error")

        with pytest.raises(PersonioAPIError) as exc_info:
            await client.obtain_access_token()

        assert type(exc_info.value) is PersonioAPIError
        assert exc_info.value.message == "Internal Server Error"


class TestTokenHandling:
    """Tests for the access token the client obtains and caches itself."""

    async def test_token_is_obtained_once_and_sent_as_bearer(
        self, mock_api: aioresponses, client: PersonioClient
    ) -> None:
        """Test that the client obtains a token with the first request and reuses it."""
        mock_api.get(PERSON_URL, payload=PERSON_RESPONSE, repeat=True)

        await client._get(PERSON_PATH)
        await client._get(PERSON_PATH)

        assert len(requests_to(mock_api, "POST", TOKEN_URL)) == 1
        for request in requests_to(mock_api, "GET", PERSON_URL):
            assert request.kwargs["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}"
            assert request.kwargs["headers"]["Accept"] == "application/json"

    async def test_concurrent_requests_obtain_one_token(
        self, mock_aiohttp: aioresponses, client: PersonioClient
    ) -> None:
        """Test that concurrent requests wait for the same token instead of obtaining one each."""
        mock_aiohttp.post(TOKEN_URL, payload=TOKEN_RESPONSE)  # a second token request would fail
        mock_aiohttp.get(PERSON_URL, payload=PERSON_RESPONSE, repeat=True)

        await asyncio.gather(*(client._get(PERSON_PATH) for _ in range(3)))

        assert len(requests_to(mock_aiohttp, "GET", PERSON_URL)) == 3

    async def test_token_is_renewed_before_it_expires(
        self, mock_api: aioresponses, client: PersonioClient, clock: FakeClock
    ) -> None:
        """Test that the client obtains a new token 60 seconds before `expires_in` runs out."""
        mock_api.get(PERSON_URL, payload=PERSON_RESPONSE, repeat=True)
        await client._get(PERSON_PATH)

        clock.now += 86400 - 61
        await client._get(PERSON_PATH)
        assert len(requests_to(mock_api, "POST", TOKEN_URL)) == 1

        clock.now += 1
        await client._get(PERSON_PATH)
        assert len(requests_to(mock_api, "POST", TOKEN_URL)) == 2

    async def test_missing_expires_in_means_one_day(
        self, mock_aiohttp: aioresponses, client: PersonioClient, clock: FakeClock
    ) -> None:
        """Test that a token without `expires_in` is used for the documented default lifetime of one day."""
        mock_aiohttp.post(TOKEN_URL, payload={"access_token": ACCESS_TOKEN, "token_type": "Bearer"}, repeat=True)
        mock_aiohttp.get(PERSON_URL, payload=PERSON_RESPONSE, repeat=True)
        await client._get(PERSON_PATH)

        clock.now += 86400 - 61
        await client._get(PERSON_PATH)
        assert len(requests_to(mock_aiohttp, "POST", TOKEN_URL)) == 1

        clock.now += 1
        await client._get(PERSON_PATH)
        assert len(requests_to(mock_aiohttp, "POST", TOKEN_URL)) == 2

    async def test_401_discards_the_token(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that the client obtains a new token after a 401, e.g. because the token was revoked."""
        mock_api.get(PERSON_URL, status=401, body="Unauthorized")
        mock_api.get(PERSON_URL, payload=PERSON_RESPONSE)

        with pytest.raises(PersonioAuthenticationError):
            await client._get(PERSON_PATH)
        await client._get(PERSON_PATH)

        assert len(requests_to(mock_api, "POST", TOKEN_URL)) == 2

    async def test_token_response_without_access_token(
        self, mock_aiohttp: aioresponses, client: PersonioClient
    ) -> None:
        """Test that a token response without a token raises PersonioAuthenticationError."""
        mock_aiohttp.post(TOKEN_URL, payload={"token_type": "Bearer"})

        with pytest.raises(PersonioAuthenticationError, match="contains no access token"):
            await client._get(PERSON_PATH)

    async def test_app_and_partner_headers(self, mock_api: aioresponses) -> None:
        """Test that the recommended headers are sent with every request, including the token request."""
        mock_api.get(PERSON_URL, payload=PERSON_RESPONSE)

        async with PersonioClient(
            client_id=CLIENT_ID, client_secret=CLIENT_SECRET, app_id="MY_APP", partner_id="ACME"
        ) as client:
            await client._get(PERSON_PATH)

        for request in requests_to(mock_api, "POST", TOKEN_URL) + requests_to(mock_api, "GET", PERSON_URL):
            assert request.kwargs["headers"]["X-Personio-App-ID"] == "MY_APP"
            assert request.kwargs["headers"]["X-Personio-Partner-ID"] == "ACME"

    async def test_no_app_and_partner_headers_by_default(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that the recommended headers are only sent if they are configured."""
        mock_api.get(PERSON_URL, payload=PERSON_RESPONSE)

        await client._get(PERSON_PATH)

        headers = requests_to(mock_api, "GET", PERSON_URL)[0].kwargs["headers"]
        assert "X-Personio-App-ID" not in headers
        assert "X-Personio-Partner-ID" not in headers


# =============================================================================
# Errors
# =============================================================================


class TestErrors:
    """Tests for the mapping of error responses to exceptions."""

    async def test_resource_not_found(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that the problem+json body of Personio becomes the message, including the trace ID."""
        mock_api.get(PERSON_URL, status=404, payload=spec_example(PERSONS_SPEC, "ResourceNotFound"))

        with pytest.raises(PersonioAPIError) as exc_info:
            await client._get(PERSON_PATH)

        assert type(exc_info.value) is PersonioAPIError
        assert exc_info.value.status_code == 404
        assert exc_info.value.message == "Resource not found: There is no resource for the specified id."
        assert exc_info.value.trace_id == "aswo3f-a202lfso-312123sld-1230ddd"

    async def test_several_errors_are_joined(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that all errors of a problem+json body end up in the message."""
        body = {
            "personio_trace_id": "trace-1",
            "errors": [
                {"title": "Bad request", "detail": "Provided request parameters are invalid."},
                {"title": "Invalid limit"},
            ],
        }
        mock_api.get(PERSON_URL, status=400, payload=body)

        with pytest.raises(PersonioAPIError) as exc_info:
            await client._get(PERSON_PATH)

        assert exc_info.value.message == "Bad request: Provided request parameters are invalid.; Invalid limit"

    @pytest.mark.parametrize("status", [401, 403])
    async def test_authentication_errors(self, mock_api: aioresponses, client: PersonioClient, status: int) -> None:
        """Test that 401 and 403 raise PersonioAuthenticationError."""
        mock_api.get(PERSON_URL, status=status, body="Forbidden")

        with pytest.raises(PersonioAuthenticationError) as exc_info:
            await client._get(PERSON_PATH)

        assert exc_info.value.status_code == status

    @pytest.mark.parametrize(
        ("body", "message"),
        [
            ("Bad Gateway", "Bad Gateway"),
            ('["not", "an", "object"]', '["not", "an", "object"]'),
            ('{"errors": "not a list"}', '{"errors": "not a list"}'),
            ("", "Request failed with status 502"),
        ],
    )
    async def test_unexpected_error_bodies(
        self, mock_api: aioresponses, client: PersonioClient, body: str, message: str
    ) -> None:
        """Test that an error body in an unexpected format becomes the message as it is."""
        mock_api.get(PERSON_URL, status=502, body=body)

        with pytest.raises(PersonioAPIError) as exc_info:
            await client._get(PERSON_PATH)

        assert exc_info.value.message == message
        assert exc_info.value.trace_id is None


# =============================================================================
# Retries
# =============================================================================


class TestRetries:
    """Tests for the retries of requests answered with 429 Too Many Requests."""

    async def test_429_is_retried(self, mock_api: aioresponses, client: PersonioClient, sleeps: list[float]) -> None:
        """Test that a request answered with 429 is repeated after one second."""
        mock_api.get(PERSON_URL, status=429)
        mock_api.get(PERSON_URL, payload=PERSON_RESPONSE)

        text = await client._get(PERSON_PATH)

        assert json.loads(text)["id"] == "3003"
        assert sleeps == [1.0]

    async def test_backoff_doubles(self, mock_api: aioresponses, client: PersonioClient, sleeps: list[float]) -> None:
        """Test that the delay doubles with each retry if the response has no Retry-After header."""
        for _ in range(3):
            mock_api.get(PERSON_URL, status=429)
        mock_api.get(PERSON_URL, payload=PERSON_RESPONSE)

        await client._get(PERSON_PATH)

        assert sleeps == [1.0, 2.0, 4.0]

    async def test_retry_after_seconds(
        self, mock_api: aioresponses, client: PersonioClient, sleeps: list[float]
    ) -> None:
        """Test that the delay of a Retry-After header in seconds is honored."""
        mock_api.get(PERSON_URL, status=429, headers={"Retry-After": "7"})
        mock_api.get(PERSON_URL, payload=PERSON_RESPONSE)

        await client._get(PERSON_PATH)

        assert sleeps == [7.0]

    async def test_retry_after_http_date(
        self, mock_api: aioresponses, client: PersonioClient, sleeps: list[float]
    ) -> None:
        """Test that a Retry-After header with an HTTP date is honored."""
        retry_at = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
        mock_api.get(PERSON_URL, status=429, headers={"Retry-After": retry_at})
        mock_api.get(PERSON_URL, payload=PERSON_RESPONSE)

        await client._get(PERSON_PATH)

        assert len(sleeps) == 1
        assert 25 <= sleeps[0] <= 30

    @pytest.mark.parametrize("retry_after", ["soon", "Sun, 31 Feb"])
    async def test_unparsable_retry_after_falls_back_to_backoff(
        self, mock_api: aioresponses, client: PersonioClient, sleeps: list[float], retry_after: str
    ) -> None:
        """Test that an unparsable Retry-After header is ignored."""
        mock_api.get(PERSON_URL, status=429, headers={"Retry-After": retry_after})
        mock_api.get(PERSON_URL, payload=PERSON_RESPONSE)

        await client._get(PERSON_PATH)

        assert sleeps == [1.0]

    async def test_exhausted_retries_raise_rate_limit_error(
        self, mock_api: aioresponses, client: PersonioClient, sleeps: list[float]
    ) -> None:
        """Test that the client gives up after max_retries and raises PersonioRateLimitError."""
        for _ in range(4):
            mock_api.get(PERSON_URL, status=429, headers={"Retry-After": "12"}, body="Too Many Requests")

        with pytest.raises(PersonioRateLimitError) as exc_info:
            await client._get(PERSON_PATH)

        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == 12.0
        assert exc_info.value.message == "Too Many Requests"
        assert sleeps == [12.0, 12.0, 12.0]

    async def test_no_retries_with_max_retries_zero(self, mock_api: aioresponses, sleeps: list[float]) -> None:
        """Test that max_retries=0 disables the retries."""
        mock_api.get(PERSON_URL, status=429)

        async with PersonioClient(client_id=CLIENT_ID, client_secret=CLIENT_SECRET, max_retries=0) as client:
            with pytest.raises(PersonioRateLimitError) as exc_info:
                await client._get(PERSON_PATH)

        assert exc_info.value.retry_after is None
        assert exc_info.value.message == "Request failed with status 429"
        assert sleeps == []

    async def test_token_request_is_retried(
        self, mock_aiohttp: aioresponses, client: PersonioClient, sleeps: list[float]
    ) -> None:
        """Test that the token request is retried, too."""
        mock_aiohttp.post(TOKEN_URL, status=429)
        mock_aiohttp.post(TOKEN_URL, payload=TOKEN_RESPONSE)

        token = await client.obtain_access_token()

        assert token.access_token == ACCESS_TOKEN
        assert sleeps == [1.0]

    def test_http_date_without_time_zone_is_utc(self) -> None:
        """Test that a Retry-After date with the zone -0000, which Python parses as naive datetime, is taken as UTC."""
        retry_at = format_datetime(datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=30))
        assert retry_at.endswith("-0000")

        delay = client_module._parse_retry_after(retry_at)

        assert delay is not None
        assert 25 <= delay <= 30

    @pytest.mark.parametrize("retry_after", ["Wed, 21 Oct 2015 07:28:00 GMT", "-5"])
    def test_retry_after_in_the_past_means_no_delay(self, retry_after: str) -> None:
        """Test that a Retry-After date in the past or a negative delay doesn't lead to a negative delay."""
        assert client_module._parse_retry_after(retry_after) == 0.0

    async def test_sleep_waits_with_asyncio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that the real delay function, which the other tests replace, waits via asyncio.sleep."""
        delays: list[float] = []

        async def fake_asyncio_sleep(seconds: float) -> None:
            delays.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_asyncio_sleep)
        await client_module._sleep(2.5)

        assert delays == [2.5]

    async def test_token_is_fetched_again_for_each_attempt(
        self, mock_api: aioresponses, client: PersonioClient, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that a retry uses a new token if the token expired while waiting."""
        mock_api.get(PERSON_URL, payload=PERSON_RESPONSE)
        await client._get(PERSON_PATH)  # obtains the first token
        mock_api.get(PERSON_URL, status=429)
        mock_api.get(PERSON_URL, payload=PERSON_RESPONSE)

        async def sleep_past_expiry(seconds: float) -> None:
            clock.now += 86400

        monkeypatch.setattr(client_module, "_sleep", sleep_past_expiry)
        await client._get(PERSON_PATH)

        assert len(requests_to(mock_api, "POST", TOKEN_URL)) == 2
