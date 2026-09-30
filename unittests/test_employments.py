"""Tests for the employments endpoints of PersonioClient."""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime
from typing import Any

import pytest
from aioresponses import aioresponses

from personio_client import CursorPage, Employment, PersonioClient, PersonioClientError
from unittests.helpers import BASE_URL, EMPLOYMENTS_SPEC, requests_to, spec_example

EMPLOYMENTS_URL = f"{BASE_URL}/v2/persons/5005/employments"
EMPLOYMENT_ID = "2306b77a-35fb-4b1d-8044-3b14f7f2ca65"


def employments_page(ids: list[str], next_cursor: str | None) -> dict[str, Any]:
    """Return a list response with a copy of the example employment of the spec per ID, and a next link."""
    employment = spec_example(EMPLOYMENTS_SPEC, "EmploymentResponse")
    links: dict[str, Any] = {"self": {"href": EMPLOYMENTS_URL}}
    if next_cursor is not None:
        links["next"] = {"href": f"{EMPLOYMENTS_URL}?cursor={next_cursor}"}
    return {"_data": [{**employment, "id": employment_id} for employment_id in ids], "_meta": {"links": links}}


class TestGetEmployments:
    """Tests for get_employments (GET /v2/persons/{person_id}/employments)."""

    async def test_without_parameters(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that the list example of the spec is parsed, although its next link has a wrong path."""
        example = spec_example(EMPLOYMENTS_SPEC, "EmploymentsListInMultiplePagesResponse")
        mock_api.get(EMPLOYMENTS_URL, payload=example)

        page = await client.get_employments("5005")

        assert isinstance(page, CursorPage)
        assert page.data == [Employment.model_validate(item) for item in example["_data"]]
        assert page.next_cursor == "cur_42sQwR2eZvKilo2"
        assert requests_to(mock_api, "GET", EMPLOYMENTS_URL)[0].kwargs["params"] == {}

    async def test_all_parameters(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that every filter is sent under the query key of the spec."""
        url = (
            f"{EMPLOYMENTS_URL}?limit=50&cursor=cur_82sQwR2eZvKilo2&id={EMPLOYMENT_ID},employment-2"
            "&updated_at=2023-06-17T14:07:17%2B00:00&updated_at.gt=2023-01-01T00:00:00%2B00:00"
            "&updated_at.lt=2024-01-01T00:00:00%2B00:00"
        )
        mock_api.get(url, payload=spec_example(EMPLOYMENTS_SPEC, "EmploymentsWithParametersAndSinglePageResponse"))

        page = await client.get_employments(
            "5005",
            limit=50,
            cursor="cur_82sQwR2eZvKilo2",
            id=[EMPLOYMENT_ID, "employment-2"],
            updated_at=datetime(2023, 6, 17, 14, 7, 17, tzinfo=UTC),
            updated_at_gt=datetime(2023, 1, 1, tzinfo=UTC),
            updated_at_lt=datetime(2024, 1, 1, tzinfo=UTC),
        )

        assert page.next_cursor is None
        assert requests_to(mock_api, "GET", url)[0].kwargs["params"] == {
            "limit": "50",
            "cursor": "cur_82sQwR2eZvKilo2",
            "id": f"{EMPLOYMENT_ID},employment-2",
            "updated_at": "2023-06-17T14:07:17+00:00",
            "updated_at.gt": "2023-01-01T00:00:00+00:00",
            "updated_at.lt": "2024-01-01T00:00:00+00:00",
        }

    async def test_naive_datetime_is_rejected(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that a naive datetime raises ValueError, because the API would assume an unknown time zone."""
        with pytest.raises(ValueError, match="timezone-aware datetime required"):
            await client.get_employments("5005", updated_at=datetime(2024, 1, 1))

        assert requests_to(mock_api, "GET", EMPLOYMENTS_URL) == []

    async def test_person_id_is_percent_encoded(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that a slash in the person ID can't change the path."""
        mock_api.get(f"{BASE_URL}/v2/persons/a%2Fb/employments", payload={"_data": []})

        page = await client.get_employments("a/b")

        assert page.data == []


class TestGetEmployment:
    """Tests for get_employment (GET /v2/persons/{person_id}/employments/{id})."""

    async def test_get_employment(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that the employment example of the spec is parsed, with dates as date and the job ID as str."""
        mock_api.get(f"{EMPLOYMENTS_URL}/{EMPLOYMENT_ID}", payload=spec_example(EMPLOYMENTS_SPEC, "EmploymentResponse"))

        employment = await client.get_employment("5005", EMPLOYMENT_ID)

        assert employment.id == EMPLOYMENT_ID
        assert employment.status == "ACTIVE"
        assert employment.type == "INTERNAL"
        assert employment.employment_start_date == date(2023, 1, 1)
        assert employment.probation_end_date == date(2023, 7, 1)
        assert employment.contract_end_date == date(2024, 1, 1)
        assert employment.employment_end_date is None
        assert employment.created_at == datetime(2023, 1, 1, 14, 7, 17, tzinfo=UTC)
        assert employment.position is not None
        assert employment.position.title == "Software Engineer"
        assert employment.person is not None
        assert employment.person.id == "5005"
        assert employment.job is not None
        assert employment.job.id == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        assert employment.org_units is not None
        assert [(unit.type, unit.id) for unit in employment.org_units] == [("team", "4502"), ("department", "401")]
        assert employment.termination is not None
        assert employment.termination.termination_date == date(2022, 12, 12)
        assert employment.termination.type == "QUIT"

    async def test_deprecated_sub_company_warns(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that the deprecated sub_company is still parsed, but accessing it warns (use legal_entity instead)."""
        mock_api.get(f"{EMPLOYMENTS_URL}/{EMPLOYMENT_ID}", payload=spec_example(EMPLOYMENTS_SPEC, "EmploymentResponse"))
        employment = await client.get_employment("5005", EMPLOYMENT_ID)

        with pytest.warns(DeprecationWarning, match="deprecated"):
            sub_company = employment.sub_company

        assert sub_company is not None
        assert sub_company.id == "47"

    async def test_ids_are_percent_encoded(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that slashes in the IDs can't change the path."""
        mock_api.get(f"{BASE_URL}/v2/persons/a%2Fb/employments/c%2Fd", payload={"id": "c/d"})

        employment = await client.get_employment("a/b", "c/d")

        assert employment.id == "c/d"


class TestIterEmployments:
    """Tests for iter_employments, which walks through all pages of get_employments."""

    async def test_walks_through_all_pages(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that the employments of all pages are yielded, requesting 50 per page and resending the filters."""
        filters = "limit=50&updated_at.gt=2023-01-01T00:00:00%2B00:00"
        mock_api.get(f"{EMPLOYMENTS_URL}?{filters}", payload=employments_page(["1", "2"], "cursor_2"))
        mock_api.get(f"{EMPLOYMENTS_URL}?{filters}&cursor=cursor_2", payload=employments_page(["3"], None))

        employments = [
            employment
            async for employment in client.iter_employments("5005", updated_at_gt=datetime(2023, 1, 1, tzinfo=UTC))
        ]

        assert [employment.id for employment in employments] == ["1", "2", "3"]

    async def test_stops_on_an_empty_page(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that an empty page ends the iteration even if it has a next link."""
        mock_api.get(f"{EMPLOYMENTS_URL}?limit=50", payload=employments_page([], "cursor_2"))

        employments = [employment async for employment in client.iter_employments("5005")]

        assert employments == []

    async def test_repeated_cursor_raises(self, mock_api: aioresponses, client: PersonioClient) -> None:
        """Test that a cursor the API returns twice raises instead of looping forever."""
        mock_api.get(f"{EMPLOYMENTS_URL}?limit=50", payload=employments_page(["1"], "cursor_2"))
        mock_api.get(f"{EMPLOYMENTS_URL}?limit=50&cursor=cursor_2", payload=employments_page(["2"], "cursor_2"))

        with pytest.raises(PersonioClientError, match="pagination doesn't advance"):
            async for _ in client.iter_employments("5005"):
                pass

    def test_accepts_the_filters_of_get_employments(self) -> None:
        """Test that iter_employments accepts the same parameters as get_employments, except the cursor."""
        get_parameters = list(inspect.signature(PersonioClient.get_employments).parameters)
        iter_parameters = list(inspect.signature(PersonioClient.iter_employments).parameters)

        assert iter_parameters == [parameter for parameter in get_parameters if parameter != "cursor"]
