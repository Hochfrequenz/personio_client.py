"""Async HTTP client for the Personio API v2."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Literal, TypeVar
from urllib.parse import quote

import aiohttp
from pydantic import AwareDatetime

from personio_client.exceptions import (
    PersonioAPIError,
    PersonioAuthenticationError,
    PersonioClientError,
    PersonioRateLimitError,
)
from personio_client.models import CursorPage, OAuth2Token, OAuth2TokenRequest, Person

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
    from types import TracebackType

ItemT = TypeVar("ItemT")

DEFAULT_BASE_URL = "https://api.personio.de"
DEFAULT_MAX_RETRIES = 3
# The maximum page size of the list endpoints; the iter_* methods use it to save requests.
MAX_PAGE_SIZE = 50
# Obtain a new access token this many seconds before the current one expires.
TOKEN_REFRESH_MARGIN_SECONDS = 60
# The lifetime of an access token if the token response doesn't state it (the documented default of one day).
DEFAULT_TOKEN_LIFETIME_SECONDS = 86400


def _now() -> float:
    """Return the time of the monotonic clock that measures the lifetime of the access token."""
    return time.monotonic()


async def _sleep(seconds: float) -> None:
    """Wait before retrying a request that was answered with 429."""
    await asyncio.sleep(seconds)


def _parse_retry_after(value: str | None) -> float | None:
    """Parse the value of a Retry-After header.

    Args:
        value: The header value: a delay in seconds or an HTTP date.

    Returns:
        The delay in seconds (never negative), or None if the value is missing or can't be parsed.
    """
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())


def _retry_delay(retry_after: str | None, attempt: int) -> float:
    """Return the delay before the next attempt of a request that was answered with 429.

    Args:
        retry_after: The value of the Retry-After header of the response, if there is one.
        attempt: The number of the attempt that was answered with 429, starting at 0.

    Returns:
        The delay the Retry-After header asks for, otherwise 1 s, doubling with each attempt.
    """
    delay = _parse_retry_after(retry_after)
    return float(2**attempt) if delay is None else delay


def _error_details(text: str) -> tuple[str | None, str | None]:
    """Extract the message and the trace ID from the body of an error response.

    The resource endpoints answer with `{"personio_trace_id": ..., "errors": [{"title": ..., "detail": ...}]}`,
    the token endpoint with `{"error": ..., "error_description": ..., "trace_id": ...}`.

    Args:
        text: The body of the error response.

    Returns:
        The message and the trace ID; each is None if the body doesn't contain it.
    """
    try:
        body = json.loads(text)
    except ValueError:
        return None, None
    if not isinstance(body, dict):
        return None, None
    messages = []
    for error in body.get("errors") or []:
        if isinstance(error, dict):
            parts = [str(error[key]) for key in ("title", "detail") if error.get(key)]
            if parts:
                messages.append(": ".join(parts))
    if not messages:
        parts = [str(body[key]) for key in ("error", "error_description") if body.get(key)]
        if parts:
            messages.append(": ".join(parts))
    trace_id = body.get("personio_trace_id") or body.get("trace_id")
    return "; ".join(messages) or None, str(trace_id) if trace_id else None


def _format_datetime(value: AwareDatetime) -> str:
    """Format a value for a query parameter of format "date-time" (ISO 8601 with UTC offset).

    Args:
        value: The timezone-aware datetime.

    Returns:
        The ISO 8601 timestamp including the UTC offset.

    Raises:
        ValueError: If the datetime is naive. The API would interpret it in a time zone the caller doesn't know.
    """
    if value.utcoffset() is None:
        raise ValueError(f"timezone-aware datetime required, got a naive datetime: {value!r}")
    return value.isoformat()


def _path_parameter(value: str) -> str:
    """Percent-encode a value for a path parameter, including slashes.

    Args:
        value: The value, e.g. an ID.

    Returns:
        The percent-encoded value.
    """
    return quote(value, safe="")


async def _iterate_pages(fetch_page: Callable[[str | None], Awaitable[CursorPage[ItemT]]]) -> AsyncIterator[ItemT]:
    """Yield the items of all pages of a list endpoint, following the cursors of the next links.

    Args:
        fetch_page: Returns the page for a cursor (None for the first page).

    Yields:
        The items of all pages.

    Raises:
        PersonioClientError: If the API returns a cursor twice, i.e. the pagination doesn't advance.
            A sync job must not continue with a partial result.
    """
    cursor: str | None = None
    seen_cursors: set[str] = set()
    while True:
        page = await fetch_page(cursor)
        for item in page.data:
            yield item
        cursor = page.next_cursor
        if cursor is None or not page.data:
            return
        if cursor in seen_cursors:
            raise PersonioClientError(f"The pagination doesn't advance: the API returned the cursor {cursor!r} twice")
        seen_cursors.add(cursor)


class PersonioClient:
    """Async client for the Personio API v2.

    Every public method except the `iter_*` methods wraps exactly one operation of the API; the "API Coverage"
    section of the README lists which operations are covered.
    The client obtains an access token with the client credentials when it sends its first request,
    and a new one shortly before the token expires. Requests answered with 429 Too Many Requests are retried.

    The client can be used as an async context manager to ensure proper cleanup of resources.

    Example:
        async with PersonioClient(client_id="papi-...", client_secret="papi-...") as client:
            token = await client.obtain_access_token()
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        scope: list[str] | None = None,
        app_id: str | None = None,
        partner_id: str | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        base_url: str = DEFAULT_BASE_URL,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the Personio client.

        Args:
            client_id: The client ID of the API credentials.
            client_secret: The client secret of the API credentials.
            scope: The scopes of the access tokens the client obtains. Defaults to all scopes of the credentials.
            app_id: Identifies your application towards Personio (header X-Personio-App-ID, e.g. "MY_APP").
                Personio strongly recommends it, so that it can support you with API issues.
            partner_id: Identifies your company if you are an integration partner of Personio
                (header X-Personio-Partner-ID).
            max_retries: How often a request answered with 429 Too Many Requests is retried. 0 disables the retries.
            base_url: The base URL of the API. Defaults to https://api.personio.de.
            session: An optional aiohttp ClientSession to use. If not provided,
                a new session will be created when entering the context manager.

        Raises:
            ValueError: If max_retries is negative.
        """
        if max_retries < 0:
            raise ValueError(f"max_retries must not be negative, got {max_retries}")
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._app_id = app_id
        self._partner_id = partner_id
        self._max_retries = max_retries
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._owns_session = session is None
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def __aenter__(self) -> PersonioClient:
        """Enter the async context manager.

        Creates a new aiohttp session if one was not provided in the constructor.

        Returns:
            The client instance.
        """
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the async context manager.

        Closes the aiohttp session if it was created by the client.

        Args:
            exc_type: The exception type, if any.
            exc_val: The exception value, if any.
            exc_tb: The exception traceback, if any.
        """
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    def _headers(self, access_token: str | None) -> dict[str, str]:
        """Get the headers for API requests.

        Args:
            access_token: The access token to authenticate the request with, or None for the token request.

        Returns:
            A dictionary of headers.
        """
        headers = {"Accept": "application/json"}
        if access_token is not None:
            headers["Authorization"] = f"Bearer {access_token}"
        if self._app_id is not None:
            headers["X-Personio-App-ID"] = self._app_id
        if self._partner_id is not None:
            headers["X-Personio-Partner-ID"] = self._partner_id
        return headers

    async def _ensure_access_token(self) -> str:
        """Return the cached access token, or obtain a new one if there is none or it is about to expire.

        The lock ensures that concurrent requests obtain only one token.

        Returns:
            A valid access token.

        Raises:
            PersonioAuthenticationError: If the credentials are rejected or the token response contains no token.
        """
        async with self._token_lock:
            if self._access_token is None or _now() >= self._access_token_expires_at:
                token = await self.obtain_access_token(scope=self._scope)
                if not token.access_token:
                    raise PersonioAuthenticationError(200, "The token response contains no access token")
                lifetime = DEFAULT_TOKEN_LIFETIME_SECONDS if token.expires_in is None else token.expires_in
                self._access_token = token.access_token
                self._access_token_expires_at = _now() + lifetime - TOKEN_REFRESH_MARGIN_SECONDS
            return self._access_token

    def _raise_for_status(self, status: int, text: str, retry_after: str | None, *, token_request: bool) -> None:
        """Raise the matching exception if the response indicates an error.

        Args:
            status: The HTTP status code of the response.
            text: The body of the response.
            retry_after: The value of the Retry-After header of the response, if there is one.
            token_request: Whether the response answers a request of an access token.

        Raises:
            PersonioRateLimitError: If the response status is 429.
            PersonioAuthenticationError: If the response status is 401 or 403, or any other 4xx of the token endpoint.
            PersonioAPIError: If the response status indicates any other error.
        """
        if status < 400:
            return
        message, trace_id = _error_details(text)
        message = message or text or f"Request failed with status {status}"
        if status == 429:
            raise PersonioRateLimitError(message, trace_id=trace_id, retry_after=_parse_retry_after(retry_after))
        if status in (401, 403) or (token_request and status < 500):
            raise PersonioAuthenticationError(status_code=status, message=message, trace_id=trace_id)
        raise PersonioAPIError(status_code=status, message=message, trace_id=trace_id)

    async def _send(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool,
        params: Mapping[str, str] | None = None,
        data: Mapping[str, str] | None = None,
    ) -> str:
        """Send a request to the API, retrying it while the API answers with 429 Too Many Requests.

        Args:
            method: The HTTP method.
            path: The API path (will be appended to base_url).
            authenticated: Whether to authenticate the request with an access token (False for the token request).
            params: Optional query parameters.
            data: Optional form fields to send as the request body.

        Returns:
            The response text.

        Raises:
            RuntimeError: If the client is not in a context manager.
        """
        if self._session is None:
            raise RuntimeError("Client must be used within an async context manager (async with)")

        url = f"{self._base_url}{path}"
        attempt = 0
        while True:
            # fetched for every attempt, in case the token expired while waiting for a retry
            access_token = await self._ensure_access_token() if authenticated else None
            async with self._session.request(
                method, url, headers=self._headers(access_token), params=params, data=data
            ) as response:
                status = response.status
                retry_after = response.headers.get("Retry-After")
                text = await response.text()
            if status == 429 and attempt < self._max_retries:
                await _sleep(_retry_delay(retry_after, attempt))
                attempt += 1
                continue
            if status == 401 and authenticated:
                # the token may have been revoked; the next request obtains a new one
                self._access_token = None
            self._raise_for_status(status, text, retry_after, token_request=not authenticated)
            return text

    async def _get(self, path: str, params: Mapping[str, str] | None = None) -> str:
        """Make an authenticated GET request to the API.

        Args:
            path: The API path (will be appended to base_url).
            params: Optional query parameters.

        Returns:
            The response text.
        """
        return await self._send("GET", path, authenticated=True, params=params)

    async def _post_form(self, path: str, form: Mapping[str, str]) -> str:
        """Make a POST request with a form body to the API, without an access token.

        Args:
            path: The API path (will be appended to base_url).
            form: The form fields to send as the request body (application/x-www-form-urlencoded).

        Returns:
            The response text.
        """
        return await self._send("POST", path, authenticated=False, data=form)

    # =========================================================================
    # Authentication
    # =========================================================================

    async def obtain_access_token(self, *, scope: list[str] | None = None) -> OAuth2Token:
        """Obtain an access token with the client credentials (OAuth 2.0 client credentials grant).

        The client calls this method itself whenever it needs a token, so you only need it to inspect a token,
        e.g. its scopes. Every call obtains a new token; Personio allows 150 token requests per minute.

        Args:
            scope: The scopes of the token. Defaults to all scopes of the credentials.

        Returns:
            The token, valid for `expires_in` seconds (one day by default).

        Raises:
            PersonioAuthenticationError: If Personio rejects the credentials or the requested scopes.
        """
        request = OAuth2TokenRequest(
            grant_type="client_credentials",
            client_id=self._client_id,
            client_secret=self._client_secret,
            scope=" ".join(scope) if scope else None,
        )
        response_text = await self._post_form("/v2/auth/token", request.model_dump(exclude_none=True))
        return OAuth2Token.model_validate_json(response_text)

    # =========================================================================
    # Persons
    # =========================================================================

    async def get_persons(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        id: list[str] | None = None,
        email: list[str] | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        preferred_name: str | None = None,
        created_at: AwareDatetime | None = None,
        created_at_gt: AwareDatetime | None = None,
        created_at_lt: AwareDatetime | None = None,
        updated_at: AwareDatetime | None = None,
        updated_at_gt: AwareDatetime | None = None,
        updated_at_lt: AwareDatetime | None = None,
        status: Literal["ACTIVE", "INACTIVE"] | None = None,
    ) -> CursorPage[Person]:
        """Get one page of persons.

        The filters are combined with a logical AND. Use iter_persons() to get the persons of all pages.
        The credentials need the scope personio:persons:read.

        Args:
            limit: The number of persons per page, from 1 to 50. Defaults to 10.
            cursor: The cursor of the page to return (`next_cursor` of the previous page). Defaults to the first page.
            id: Filter by the IDs of the persons.
            email: Filter by the e-mail addresses of the persons.
            first_name: Filter by the first name.
            last_name: Filter by the last name.
            preferred_name: Filter by the preferred name.
            created_at: Filter by the time of creation.
            created_at_gt: Return only persons created after this time (query parameter `created_at.gt`).
            created_at_lt: Return only persons created before this time (query parameter `created_at.lt`).
            updated_at: Filter by the time of the last update.
            updated_at_gt: Return only persons updated after this time (query parameter `updated_at.gt`).
            updated_at_lt: Return only persons updated before this time (query parameter `updated_at.lt`).
            status: Return only active persons (whose latest employment is active, on leave or onboarding)
                or inactive persons.

        Returns:
            A page of persons with the cursor of the next page.

        Raises:
            ValueError: If a date-time filter is a naive datetime.
        """
        params: dict[str, str] = {}
        if limit is not None:
            params["limit"] = str(limit)
        if cursor is not None:
            params["cursor"] = cursor
        if id is not None:
            params["id"] = ",".join(id)
        if email is not None:
            params["email"] = ",".join(email)
        if first_name is not None:
            params["first_name"] = first_name
        if last_name is not None:
            params["last_name"] = last_name
        if preferred_name is not None:
            params["preferred_name"] = preferred_name
        if created_at is not None:
            params["created_at"] = _format_datetime(created_at)
        if created_at_gt is not None:
            params["created_at.gt"] = _format_datetime(created_at_gt)
        if created_at_lt is not None:
            params["created_at.lt"] = _format_datetime(created_at_lt)
        if updated_at is not None:
            params["updated_at"] = _format_datetime(updated_at)
        if updated_at_gt is not None:
            params["updated_at.gt"] = _format_datetime(updated_at_gt)
        if updated_at_lt is not None:
            params["updated_at.lt"] = _format_datetime(updated_at_lt)
        if status is not None:
            params["status"] = status

        response_text = await self._get("/v2/persons", params)
        return CursorPage[Person].model_validate_json(response_text)

    async def get_person(self, person_id: str) -> Person:
        """Get a person.

        The credentials need the scope personio:persons:read.

        Args:
            person_id: The ID of the person.

        Returns:
            The person.

        Raises:
            PersonioAPIError: With the status code 404 if there is no person with this ID.
        """
        response_text = await self._get(f"/v2/persons/{_path_parameter(person_id)}")
        return Person.model_validate_json(response_text)

    async def iter_persons(
        self,
        *,
        limit: int = MAX_PAGE_SIZE,
        id: list[str] | None = None,
        email: list[str] | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        preferred_name: str | None = None,
        created_at: AwareDatetime | None = None,
        created_at_gt: AwareDatetime | None = None,
        created_at_lt: AwareDatetime | None = None,
        updated_at: AwareDatetime | None = None,
        updated_at_gt: AwareDatetime | None = None,
        updated_at_lt: AwareDatetime | None = None,
        status: Literal["ACTIVE", "INACTIVE"] | None = None,
    ) -> AsyncIterator[Person]:
        """Iterate over the persons of all pages.

        Calls get_persons() page by page with the same filters until there is no next page.
        See get_persons() for the filters.

        Args:
            limit: The number of persons per page, from 1 to 50. Defaults to 50, which saves requests.
            id: Filter by the IDs of the persons.
            email: Filter by the e-mail addresses of the persons.
            first_name: Filter by the first name.
            last_name: Filter by the last name.
            preferred_name: Filter by the preferred name.
            created_at: Filter by the time of creation.
            created_at_gt: Return only persons created after this time.
            created_at_lt: Return only persons created before this time.
            updated_at: Filter by the time of the last update.
            updated_at_gt: Return only persons updated after this time.
            updated_at_lt: Return only persons updated before this time.
            status: Return only active or inactive persons.

        Yields:
            The persons.

        Raises:
            PersonioClientError: If the API returns a cursor twice, i.e. the pagination doesn't advance.
        """

        async def fetch_page(cursor: str | None) -> CursorPage[Person]:
            return await self.get_persons(
                limit=limit,
                cursor=cursor,
                id=id,
                email=email,
                first_name=first_name,
                last_name=last_name,
                preferred_name=preferred_name,
                created_at=created_at,
                created_at_gt=created_at_gt,
                created_at_lt=created_at_lt,
                updated_at=updated_at,
                updated_at_gt=updated_at_gt,
                updated_at_lt=updated_at_lt,
                status=status,
            )

        async for person in _iterate_pages(fetch_page):
            yield person
