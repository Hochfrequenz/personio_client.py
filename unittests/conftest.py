"""Pytest configuration and fixtures for the Personio client tests."""

from collections.abc import AsyncIterator, Iterator

import pytest
from aioresponses import aioresponses

import personio_client.client as client_module
from personio_client import PersonioClient
from unittests.helpers import BASE_URL, CLIENT_ID, CLIENT_SECRET, TOKEN_RESPONSE, TOKEN_URL, FakeClock


@pytest.fixture
def mock_aiohttp() -> Iterator[aioresponses]:
    """Fixture providing aioresponses mock."""
    with aioresponses() as m:
        yield m


@pytest.fixture
def mock_api(mock_aiohttp: aioresponses) -> aioresponses:
    """The aioresponses mock, answering every token request with the token example of the spec."""
    mock_aiohttp.post(TOKEN_URL, payload=TOKEN_RESPONSE, repeat=True)
    return mock_aiohttp


@pytest.fixture
async def client() -> AsyncIterator[PersonioClient]:
    """A client within its async context manager."""
    async with PersonioClient(client_id=CLIENT_ID, client_secret=CLIENT_SECRET, base_url=BASE_URL) as personio:
        yield personio


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """A fake of the clock that measures the lifetime of the access token."""
    fake = FakeClock()
    monkeypatch.setattr(client_module, "_now", fake)
    return fake


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The delays of the retries, recorded instead of waited for."""
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(client_module, "_sleep", fake_sleep)
    return delays
