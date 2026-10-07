# personio_client.py

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python Versions (officially) supported](https://img.shields.io/pypi/pyversions/personio-client.svg)
![Pypi status badge](https://img.shields.io/pypi/v/personio-client)

![Unittests status badge](https://github.com/Hochfrequenz/personio_client.py/workflows/Unittests/badge.svg)
![Coverage status badge](https://github.com/Hochfrequenz/personio_client.py/workflows/Coverage/badge.svg)
![Linting status badge](https://github.com/Hochfrequenz/personio_client.py/workflows/Linting/badge.svg)
![Formatting status badge](https://github.com/Hochfrequenz/personio_client.py/workflows/Formatting/badge.svg)

An async Python client for the [Personio API v2](https://developer.personio.de/reference/introduction).
It obtains access tokens and reads persons and employments.

> [!IMPORTANT]
> This is a community project and is NOT an official Personio client.
> It is not affiliated with or endorsed by Personio SE & Co. KG.

> [!NOTE]
> Besides the unit tests with the examples of the OpenAPI specs, the client was checked against the live API
> with read-only credentials on 2026-09-30 (see task 8 of the [implementation plan](docs/plans/2026-09-29-personio-v2-client.md)).

## Installation

```bash
pip install personio-client
```

## Usage

```python
import asyncio

from personio_client import PersonioClient


async def main() -> None:
    async with PersonioClient(client_id="papi-...", client_secret="papi-...", app_id="MY_APP") as client:
        async for person in client.iter_persons(status="ACTIVE"):
            print(person.first_name, person.last_name, person.email)
            if person.id is not None:  # the spec declares no required fields
                async for employment in client.iter_employments(person.id):
                    print("  ", employment.status, employment.employment_start_date)


if __name__ == "__main__":
    asyncio.run(main())
```

### Authentication

- The client authenticates with API credentials of Personio
  (see [Generate and manage API credentials](https://support.personio.de/hc/en-us/articles/4404623630993-Generate-and-manage-API-credentials)).
  They need the scope `personio:persons:read`, for the persons and the employment endpoints.
  We recommend credentials that have only this scope.
- The client obtains an access token when it sends its first request, and a new one shortly before the token expires
  (tokens are valid for one day). By default, the token gets all scopes of the credentials; pass `scope=[...]` to restrict it.
  `obtain_access_token()` requests a token explicitly, e.g. to check its scopes.
- Pass `app_id` (e.g. `"MY_APP"`) and, if you are an integration partner of Personio, `partner_id`.
  Personio strongly recommends these headers (`X-Personio-App-ID`, `X-Personio-Partner-ID`), so that it can support you with API issues.

### Pagination

`get_persons()` and `get_employments()` return one page, a `CursorPage` with the items in `data` and the cursor of the next page in `next_cursor`
(`None` on the last page):

```python
page = await client.get_persons(limit=50, updated_at_gt=datetime(2026, 1, 1, tzinfo=UTC))
while True:
    for person in page.data:
        ...
    if page.next_cursor is None:
        break
    page = await client.get_persons(limit=50, updated_at_gt=datetime(2026, 1, 1, tzinfo=UTC), cursor=page.next_cursor)
```

`iter_persons()` and `iter_employments()` do this for you: they request 50 items per page and resend the filters with each page.
If the API returns a cursor twice, they raise `PersonioClientError` instead of looping forever or returning a partial result.

Filters are keyword arguments named after the query parameters of the spec, with dots replaced by underscores
(e.g. `updated_at_gt` for `updated_at.gt`). Lists like `id` and `email` are sent as one comma-separated value.
Date-time filters take a timezone-aware `datetime.datetime`; a naive datetime raises `ValueError`.

### Finding New Persons

To find persons that exist in Personio but are not yet known to a target system, compare their Personio IDs with the IDs already stored in the target system:

```python
async def find_new_persons(
    client: PersonioClient,
    known_ids: set[str],
) -> list[Person]:
    """Persons in Personio that the target system doesn't know yet."""
    return [
        person
        async for person in client.iter_persons()
        if person.id and person.id not in known_ids
    ]
```

Use the Personio ID as the key in the target system, not the e-mail address, because e-mail addresses can change.

When using `status="ACTIVE"`, note that persons in onboarding are included even if their start date has not yet been reached. If only persons who have already started should be synchronized, check `employment_start_date` via `iter_employments()`.

A rehire is a new employment, not a new person. Therefore, identify new persons by their Personio ID and read their employments only after identifying the new persons.

Reading employments requires one request per person, so limit concurrent requests, for example with an `asyncio.Semaphore` as shown in the [Rate Limits](#rate-limits) section.

### Models

The models are generated from the OpenAPI specs of Personio with [pydantic](https://docs.pydantic.dev/).
Fields the API adds are ignored. Because the spec declares no required fields, every field is optional.
To keep an unexpected value from breaking the parsing of a whole page, the models deviate from the specs in a few places:

- Enums are plain strings. Personio isn't consistent in their casing: the API sends the custom attribute types
  in lower case (`string`, `date`), while the spec lists `STRING` and `DATE`; the statuses are upper case (`ACTIVE`).
  Compare them case-insensitively.
- E-mail addresses, IDs and links are plain strings.
- The `value` of a custom attribute is any JSON value (a string, a number, a boolean, a list, ...).
- The employment dates (`employment_start_date`, `employment_end_date`, `probation_end_date`, `contract_end_date`)
  are `datetime.date` values, as in the write specs of Personio (the read spec types them as plain strings).

### Rate Limits

Personio doesn't document the rate limits of these endpoints.
Its responses carry the headers of a token bucket (`x-ratelimit-burst-capacity`, `x-ratelimit-replenish-rate`, `x-ratelimit-remaining`);
in our test, the bucket held 100 requests.
The client retries a request answered with 429 Too Many Requests up to `max_retries` times (default 3),
waiting as long as the `Retry-After` header says, otherwise 1 s, 2 s and 4 s.
Then it raises `PersonioRateLimitError`. Pass `max_retries=0` to disable the retries.

Reading the employments of all persons takes one request per person, so don't send too many requests at once,
e.g. by limiting them with an `asyncio.Semaphore`:

```python
semaphore = asyncio.Semaphore(5)


async def employments_of(client: PersonioClient, person_id: str) -> list[Employment]:
    async with semaphore:
        return [employment async for employment in client.iter_employments(person_id)]
```

### Error Handling

```python
from personio_client import PersonioAPIError, PersonioAuthenticationError, PersonioRateLimitError

try:
    person = await client.get_person("3003")
except PersonioAuthenticationError as error:
    print(f"Authentication failed: {error.message}")
except PersonioRateLimitError as error:
    print(f"Rate limit exceeded; retry after {error.retry_after} seconds")
except PersonioAPIError as error:
    print(f"API error {error.status_code}: {error.message} (trace ID: {error.trace_id})")
```

`PersonioAuthenticationError` and `PersonioRateLimitError` are subclasses of `PersonioAPIError`, which is a subclass of `PersonioClientError`.
Personio support asks for the `trace_id` of a failed request.

### API Coverage

The client covers the Personio API v2 as described by the specs in [`openapi/v2/`](openapi/v2),
synced from [developer.personio.de/openapi](https://developer.personio.de/openapi) on 2026-09-30.
The tables are grouped by the tags of the specs.
Every method wraps exactly one operation, except `iter_persons()` and `iter_employments()`, which walk through all pages.

<!-- api-coverage:start -->
**5 of 6** operations are implemented.

#### Authentication

| Endpoint | Method |
| --- | --- |
| `POST /v2/auth/revoke` | not implemented |
| `POST /v2/auth/token` | `obtain_access_token()` |

#### Employments

| Endpoint | Method |
| --- | --- |
| `GET /v2/persons/{person_id}/employments` | `get_employments()` |
| `GET /v2/persons/{person_id}/employments/{id}` | `get_employment()` |

#### Persons

| Endpoint | Method |
| --- | --- |
| `GET /v2/persons` | `get_persons()` |
| `GET /v2/persons/{id}` | `get_person()` |
<!-- api-coverage:end -->

## Related Packages

Other Python packages for Personio have similar names.
To avoid confusion, this is how they differ from `personio-client` (as of September 2026):

| Package (import name) | What it is |
| --- | --- |
| [`personio-api-client`](https://pypi.org/project/personio-api-client/) (`personio_api_client`) | A synchronous client for the Personio API v1 (employees, time-offs). Its [GitHub repository](https://github.com/dkd-dobberkau/personio-api-client) also contains a v2 client for projects and attendance periods. It has a class `PersonioClient`, too. |
| [`personio-client-api`](https://pypi.org/project/personio-client-api/) (`personio_client_api`) | A minimal synchronous client for the Personio API v1 (employees). |
| [`personio-py`](https://pypi.org/project/personio-py/) (`personio_py`) | A synchronous client for the Personio API v1 (employees, attendances, absences, projects). |
| [`dlt-source-personio`](https://pypi.org/project/dlt-source-personio/) (`dlt_source_personio`) | A [dlt](https://dlthub.com) source that loads persons and employments into a data pipeline; not a client library. |

`personio-client` (import name `personio_client`) is a client library for the persons and employments endpoints of the Personio API v2,
which none of the packages above offers.
It is async (aiohttp), and its models are generated from the official OpenAPI specs of Personio,
like the [decidalo client](https://github.com/Hochfrequenz/decidalo_client.py) of Hochfrequenz.
Because of these different goals, we build this client instead of contributing to `personio-api-client`, which comes closest.
We may approach its maintainer, e.g. to link the two projects to each other.

## Development

Clone the repository and install the development environment:

```bash
git clone https://github.com/Hochfrequenz/personio_client.py.git
cd personio_client.py
uv sync --group dev
uv run pytest
```

To sync the client with the current API, download the specs and regenerate the models from them:

```bash
uv run --group codegen python scripts/generate_models.py --download
```

Review the changes of the specs with `git diff openapi/`, then run the tests:
`unittests/test_models.py` checks the models against the specs, and `unittests/test_api_coverage.py` checks the [API Coverage](#api-coverage) section of this README against the specs and the client.
To print the expected content of that section, run:

```bash
uv run python unittests/test_api_coverage.py
```

The design decisions are documented in the [implementation plan](docs/plans/2026-09-29-personio-v2-client.md).
For detailed information on the development setup (uv configuration, IDE setup, etc.), see the [Hochfrequenz Python Template Repository](https://github.com/Hochfrequenz/python_template_repository).

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
