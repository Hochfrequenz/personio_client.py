"""Constants and helpers shared by the tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aioresponses import aioresponses
from aioresponses.compat import normalize_url
from aioresponses.core import RequestCall

BASE_URL = "https://api.personio.de"
TOKEN_URL = f"{BASE_URL}/v2/auth/token"
CLIENT_ID = "papi-baaaaaad-c0de-fade-baad-00000000001d"
CLIENT_SECRET = "verY-Secret-p4ssw0rd"

SPEC_DIR = Path(__file__).parent.parent / "openapi" / "v2"
AUTH_SPEC = "credentials-service-api-v2"
PERSONS_SPEC = "persons-service-api-v2"
EMPLOYMENTS_SPEC = "employment-contract-v2"


def spec_example(spec: str, name: str) -> Any:
    """Return the value of an example of a spec in openapi/v2/, e.g. spec_example(PERSONS_SPEC, "PersonsResponse")."""
    content = json.loads((SPEC_DIR / f"{spec}.json").read_text(encoding="utf-8"))
    return content["components"]["examples"][name]["value"]


TOKEN_RESPONSE: dict[str, Any] = spec_example(AUTH_SPEC, "OAuth2TokenSuccessAccessToken")
ACCESS_TOKEN: str = TOKEN_RESPONSE["access_token"]


def requests_to(mock: aioresponses, method: str, url: str) -> list[RequestCall]:
    """Return the requests the aioresponses mock recorded for a method and a URL (including its query)."""
    return mock.requests.get((method, normalize_url(url)), [])


class FakeClock:
    """Replaces the monotonic clock of the client, so that tests can let time pass."""

    def __init__(self) -> None:
        """Start the clock at an arbitrary time."""
        self.now = 1000.0

    def __call__(self) -> float:
        """Return the current time."""
        return self.now
