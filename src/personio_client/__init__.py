"""
personio_client - An async Python client for the Personio API v2.
"""

from personio_client.exceptions import (
    PersonioAPIError,
    PersonioAuthenticationError,
    PersonioClientError,
    PersonioRateLimitError,
)

__all__ = [
    "PersonioAPIError",
    "PersonioAuthenticationError",
    "PersonioClientError",
    "PersonioRateLimitError",
]
