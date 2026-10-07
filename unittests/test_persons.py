"""Tests for the persons endpoints of PersonioClient."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from aioresponses import aioresponses

from personio_client import CursorPage, Person, PersonioClient, PersonioClientError
from unittests.helpers import BASE_URL, PERSONS_SPEC, requests_to, spec_example

PERSONS_URL = f"{BASE_URL}/v2/persons"


def persons_page(ids: list[str], next_cursor: str | None) -> dict[str, Any]:
    """Return a list response with a copy of the example person of the spec per ID, and a next link with the cursor."""
    person = spec_example(PERSONS_SPEC, "PersonsResponse")
    links: dict[str, Any] = {"self": {"href": PERSONS_URL}}
    if next_cursor is not None:
        links["next"] = {"href": f"{PERSONS_URL}?cursor={next_cursor}"}
    return {"_data": [{**person, "id": person_id} for person_id in ids], "_meta": {"links": links}}


class TestGetPersons:
    """Tests for get_persons (GET /v2/persons)."""

    async def test_without_parameters(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that the plain path is requested and the list example of the spec is parsed."""
        example = spec_example(PERSONS_SPEC, "PersonsListInMultiplePagesResponse")
        mock_api.get(PERSONS_URL, payload=example)

        page = await client.get_persons()

        assert isinstance(page, CursorPage)
        assert page.data == [Person.model_validate(item) for item in example["_data"]]
        assert page.next_cursor == "cur_234ls0f02lalfdd"
        assert requests_to(mock_api, "GET", PERSONS_URL)[0].kwargs["params"] == {}

    async def test_all_parameters(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that every filter is sent under the query key of the spec, lists as one comma-separated value."""
        url = (
            f"{PERSONS_URL}?limit=50&cursor=cur_82sQwR2eZvKilo2&id=3003,3004"
            "&email=jen.doe@personio.de,john.doe@personio.de&first_name=John&last_name=Smith&preferred_name=John%20Smith"
            "&created_at=2023-01-01T12:00:00%2B00:00&created_at.gt=2023-01-01T00:00:00%2B00:00"
            "&created_at.lt=2023-12-31T00:00:00%2B00:00&updated_at=2024-01-01T12:00:00%2B02:00"
            "&updated_at.gt=2024-01-01T00:00:00%2B02:00&updated_at.lt=2024-12-31T00:00:00%2B02:00&status=ACTIVE"
        )
        mock_api.get(url, payload=spec_example(PERSONS_SPEC, "PersonsWithParametersAndSinglePageResponse"))
        cest = timezone(timedelta(hours=2))

        page = await client.get_persons(
            limit=50,
            cursor="cur_82sQwR2eZvKilo2",
            id=["3003", "3004"],
            email=["jen.doe@personio.de", "john.doe@personio.de"],
            first_name="John",
            last_name="Smith",
            preferred_name="John Smith",
            created_at=datetime(2023, 1, 1, 12, tzinfo=UTC),
            created_at_gt=datetime(2023, 1, 1, tzinfo=UTC),
            created_at_lt=datetime(2023, 12, 31, tzinfo=UTC),
            updated_at=datetime(2024, 1, 1, 12, tzinfo=cest),
            updated_at_gt=datetime(2024, 1, 1, tzinfo=cest),
            updated_at_lt=datetime(2024, 12, 31, tzinfo=cest),
            status="ACTIVE",
        )

        assert page.next_cursor is None
        assert requests_to(mock_api, "GET", url)[0].kwargs["params"] == {
            "limit": "50",
            "cursor": "cur_82sQwR2eZvKilo2",
            "id": "3003,3004",
            "email": "jen.doe@personio.de,john.doe@personio.de",
            "first_name": "John",
            "last_name": "Smith",
            "preferred_name": "John Smith",
            "created_at": "2023-01-01T12:00:00+00:00",
            "created_at.gt": "2023-01-01T00:00:00+00:00",
            "created_at.lt": "2023-12-31T00:00:00+00:00",
            "updated_at": "2024-01-01T12:00:00+02:00",
            "updated_at.gt": "2024-01-01T00:00:00+02:00",
            "updated_at.lt": "2024-12-31T00:00:00+02:00",
            "status": "ACTIVE",
        }

    async def test_naive_datetime_is_rejected(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that a naive datetime raises ValueError, because the API would assume an unknown time zone."""
        with pytest.raises(ValueError, match="timezone-aware datetime required"):
            await client.get_persons(updated_at_gt=datetime(2024, 1, 1))

        assert requests_to(mock_api, "GET", PERSONS_URL) == []


class TestGetPerson:
    """Tests for get_person (GET /v2/persons/{id})."""

    async def test_get_person(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that the person example of the spec is parsed, including its lowercase custom attribute types."""
        mock_api.get(f"{PERSONS_URL}/3003", payload=spec_example(PERSONS_SPEC, "PersonsResponse"))

        person = await client.get_person("3003")

        assert person.id == "3003"
        assert person.email == "jen.doe@personio.de"
        assert person.status == "ACTIVE"
        assert person.custom_attributes is not None
        assert [(attribute.type, attribute.value) for attribute in person.custom_attributes] == [
            ("string", "Red"),
            ("string", "DExx-xxff-ss-os02"),
        ]
        assert person.employments is not None
        assert [employment.id for employment in person.employments] == ["c85d978e-1104-4762-be94-c868525eef45"]

    async def test_person_id_is_percent_encoded(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that a slash in the ID can't change the path."""
        mock_api.get(f"{PERSONS_URL}/a%2Fb%20c", payload={"id": "a/b c"})

        person = await client.get_person("a/b c")

        assert person.id == "a/b c"


class TestIterPersons:
    """Tests for iter_persons, which walks through all pages of get_persons."""

    async def test_walks_through_all_pages(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that the persons of all pages are yielded, requesting 50 per page and resending the filters."""
        mock_api.get(f"{PERSONS_URL}?limit=50&status=ACTIVE", payload=persons_page(["1", "2"], "cursor_2"))
        mock_api.get(f"{PERSONS_URL}?limit=50&status=ACTIVE&cursor=cursor_2", payload=persons_page(["3"], "cursor_3"))
        mock_api.get(f"{PERSONS_URL}?limit=50&status=ACTIVE&cursor=cursor_3", payload=persons_page(["4"], None))

        persons = [person async for person in client.iter_persons(status="ACTIVE")]

        assert [person.id for person in persons] == ["1", "2", "3", "4"]

    async def test_stops_on_an_empty_page(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that an empty page ends the iteration even if it has a next link."""
        mock_api.get(f"{PERSONS_URL}?limit=50", payload=persons_page([], "cursor_2"))

        persons = [person async for person in client.iter_persons()]

        assert persons == []
        assert len(requests_to(mock_api, "GET", f"{PERSONS_URL}?limit=50")) == 1

    async def test_repeated_cursor_raises(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that a cursor the API returns twice raises instead of looping forever or returning a partial result."""
        mock_api.get(f"{PERSONS_URL}?limit=50", payload=persons_page(["1"], "cursor_2"))
        mock_api.get(f"{PERSONS_URL}?limit=50&cursor=cursor_2", payload=persons_page(["2"], "cursor_2"))
        persons = []

        with pytest.raises(PersonioClientError, match="pagination doesn't advance"):
            async for person in client.iter_persons():
                persons.append(person)

        assert [person.id for person in persons] == ["1", "2"]

    def test_accepts_the_filters_of_get_persons(self) -> None:
        """Test that iter_persons accepts the same parameters as get_persons, except the cursor."""
        get_parameters = list(inspect.signature(PersonioClient.get_persons).parameters)
        iter_parameters = list(inspect.signature(PersonioClient.iter_persons).parameters)

        assert iter_parameters == [parameter for parameter in get_parameters if parameter != "cursor"]
