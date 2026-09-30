# Personio API v2 Client: Implementation Plan

**Goal:** An async Python client, `personio_client`, for the Personio API v2 that obtains access tokens and reads persons and employments.
It is built like the Import Client of [decidalo_client.py](https://github.com/Hochfrequenz/decidalo_client.py):
aiohttp, pydantic models generated from the OpenAPI specs, one method per API operation,
and tests that keep models, client and README in sync with the specs.

**Status:** Planned on 2026-09-29, open questions reviewed on 2026-09-30; nothing is implemented yet.
Repository: [Hochfrequenz/personio_client.py](https://github.com/Hochfrequenz/personio_client.py), branch `feat/personio-v2-client`.

**In scope:**

| Operation | Spec | Method |
| --- | --- | --- |
| `POST /v2/auth/token` | `credentials-service-api-v2` | `obtain_access_token()` |
| `GET /v2/persons` | `persons-service-api-v2` | `get_persons()` |
| `GET /v2/persons/{id}` | `persons-service-api-v2` | `get_person()` |
| `GET /v2/persons/{person_id}/employments` | `employment-contract-v2` | `get_employments()` |
| `GET /v2/persons/{person_id}/employments/{id}` | `employment-contract-v2` | `get_employment()` |

Plus the convenience methods `iter_persons()` and `iter_employments()`, which walk through all pages of a list.

**Out of scope:** `POST /v2/auth/revoke` (part of the auth spec, listed as "not implemented"),
the write endpoints of persons and employments (`person-write-api-v2`, `employment-write-api-v2`),
all other v2 APIs (absences, attendances, documents, org units, ...), the v1 API, a synchronous client,
and retries on server errors (see [Follow-ups](#follow-ups)). Rate limits (429) are retried automatically (decision 17).

---

## Sources

- Model repository: [Hochfrequenz/decidalo_client.py](https://github.com/Hochfrequenz/decidalo_client.py) (state of 2026-09-29), in particular
  - `src/decidalo_client/client.py` and `exceptions.py`: session handling, request helpers, error mapping, query formatting
  - `src/decidalo_app_client/client.py`: token refresh guarded by an `asyncio.Lock`
  - `unittests/test_models.py` and `unittests/test_api_coverage.py`: drift tests against the spec
  - `docs/plans/2026-09-23-import-api-sync.md`: the decisions and the 1:1 mapping rule this plan builds on
- Personio Developer Hub:
  - [OpenAPI files](https://developer.personio.de/openapi), downloadable as `https://developer.personio.de/openapi/<name>.yaml`
  - [Obtain Access Token](https://developer.personio.de/reference/post_v2-auth-token), [Authentication](https://developer.personio.de/reference/authentication)
  - [List persons](https://developer.personio.de/reference/get_v2-persons), [Retrieve a person](https://developer.personio.de/reference/get_v2-persons-id)
  - [List employments](https://developer.personio.de/reference/get_v2-persons-person-id-employments), [Retrieve an employment](https://developer.personio.de/reference/get_v2-persons-person-id-employments-id)
  - [Include our headers in your requests](https://developer.personio.de/reference/include-our-headers-in-your-requests)
- PyPI: the project list of the [Simple API](https://pypi.org/simple/) and the existing Personio packages
  (see [Existing Python Packages](#existing-python-packages))

## Analysis

### Specs

Personio publishes one OpenAPI file per API area. Three of them cover the scope:

| File | Title, version | Operations | Component schemas |
| --- | --- | --- | --- |
| `credentials-service-api-v2` | Authentication 2.0.0 (OpenAPI 3.0.3) | `POST /v2/auth/token`, `POST /v2/auth/revoke` | `OAuth2TokenRequest`, `OAuth2Token`, `OAuth2TokenErrorResponse`, `OAuth2TokenRevocationRequest`, `V1AuthTokenRequest`, `V1AuthenticationTokenResponse` |
| `persons-service-api-v2` | Person 2.0.0 (OpenAPI 3.0.0) | `GET /v2/persons`, `GET /v2/persons/{id}` | `Person`, `PersonEmploymentResponse` |
| `employment-contract-v2` | Employment 2.0.0 (OpenAPI 3.0.0) | `GET /v2/persons/{person_id}/employments`, `GET /v2/persons/{person_id}/employments/{id}` | `Employment`, `Termination`, `OrgUnit`, `CostCenter` |

### Authentication

- `POST /v2/auth/token` with `application/x-www-form-urlencoded`: `grant_type=client_credentials` (the only supported grant),
  `client_id`, `client_secret` and an optional space-delimited `scope` (without it, the token gets all scopes of the credentials).
- Success (200): `access_token` (prefix `papi-`), `token_type` (`Bearer`), `expires_in` (default 86400 s = 24 h) and `scope`.
  A token can be used for any number of calls during its lifetime.
- Failure (400, `application/problem+json`): `OAuth2TokenErrorResponse` with an OAuth2 `error` code
  (`invalid_request`, `invalid_client`, `invalid_grant`, ...), `error_description`, `error_uri`, `timestamp` and `trace_id`.
- The token endpoint allows 150 requests per minute; beyond that, requests are throttled to 1 per second for 60 seconds.
- Resource endpoints expect `Authorization: Bearer <access_token>`.
  Both persons endpoints require the scope `personio:persons:read`. The employment read endpoints name no scope,
  but updating an employment (`employment-write-api-v2`) requires `personio:persons:write`,
  and none of the 17 v2 specs defines an employments scope; all scopes use plural resource names (`persons`, `absences`, `jobs`, ...).
  So reading employments most likely needs `personio:persons:read` as well (open question 1).
- Personio strongly recommends the headers `X-Personio-App-ID` (customers) and `X-Personio-Partner-ID` (integration partners),
  with values in UPPER_SNAKE_CASE; without them, Personio may be unable to support API issues.

### Resource Endpoints

- `GET /v2/persons`: query parameters `limit` (1-50, default 10), `cursor`, `id` and `email` (comma-separated lists),
  `first_name`, `last_name`, `preferred_name`, the date-time filters `created_at`, `created_at.gt`, `created_at.lt`,
  `updated_at`, `updated_at.gt`, `updated_at.lt`, and `status` (`ACTIVE`, which includes persons whose latest employment
  is active, on leave or onboarding, or `INACTIVE`).
- `GET /v2/persons/{person_id}/employments`: `limit`, `cursor`, `id` (comma-separated), `updated_at`, `updated_at.gt`, `updated_at.lt`;
  the most recent employments come first.
- `GET /v2/persons/{id}` and `GET /v2/persons/{person_id}/employments/{id}` return the `Person` or `Employment` object itself.
- List responses have the form `{"_data": [...], "_meta": {"links": {"self": {"href": ...}, "next": {"href": ".../v2/persons?cursor=cur_..."}}}}`.
  The pagination is cursor-based; the cursor of the next page is only available inside the `next` link.
- The specs don't say whether filters have to be resent with a cursor. The `next` links of the persons and employments examples
  only contain the cursor, even for filtered requests. Other v2 APIs differ: the reports API fails if filters are sent along with a cursor,
  the `next` links of the org units API repeat the filters, and some cursors are opaque base64 values that carry offset and limit.
- A [forum thread](https://developer.personio.de/discuss/66ed43cf97e5610010204acb) (2024) reports a `next` link of `/v2/persons`
  that pointed to its own page, so the pagination never advanced.
  The attendance periods API used relative `next` links until a [changelog](https://developer.personio.de/changelog/absolute-urls-in-pagination-links-for-attendance-periods-api-v2)
  switched them to absolute URLs.
- Errors (400, 404) are `application/problem+json`:
  `{"personio_trace_id": ..., "timestamp": ..., "errors": [{"title": ..., "detail": ..., "type": ..., "_meta": {...}}]}`.
- A person references its employments only by ID (`employments: [{"id": ...}]`), so reading the employments of all persons takes one request per person.

### Rate Limits

- The persons and employments specs document no rate limits, no 429 response and no rate limit headers.
  Of all 17 v2 specs, only `jobs-v2` documents a 429 ("The rate limit has been exceeded"), without headers such as `Retry-After`.
- Documented limits exist only for the token endpoint (150 requests per minute, see [Authentication](#authentication))
  and, in a [v1 changelog](https://developer.personio.de/changelog/rate-limits-on-get-employees-endpoint-may-6-2024),
  for `GET /company/employees` (300 requests per minute, bursts of 15 per second).
- Forum threads report ["significant rate limiting on the persons endpoint"](https://developer.personio.de/discuss/6901cd6289d055814a6f57a0)
  (about 2025-10, "it feels like we are mostly guessing them") and
  [429 responses despite the recommended timing](https://developer.personio.de/discuss/67ad33de7de5140018892fb0); neither got an answer from Personio.
- Reading the employments of 500 persons takes about 510 requests, so hitting a rate limit is likely.

### Spec Quirks

A trial generation of the models (datamodel-code-generator 0.71.0) and a validation of all 15 response examples of the three specs revealed:

1. The files are served with the extension `.yaml`, but contain minified JSON.
2. The list responses (`_data`/`_meta`) and the error response of the resource endpoints are inline schemas, not component schemas.
   With the default scope they are not generated; with `--openapi-scopes paths` they get names like `V2PersonsGetResponse`
   and `V2PersonsGetResponse1` (the error).
3. With the default naming, class names collide across and within the specs: `Status` and `Type` exist in the persons
   and in the employments spec with different values, the inline `person` object of an employment becomes a second `Person`,
   and the employment spec gets `Type` and `Type1`.
4. The examples contradict the enums: all five person examples send `custom_attributes[].type` as `"string"`,
   while the enum only allows `STRING`, `INT`, `DOUBLE`, `DATE`, `BOOLEAN`, `STRING_LIST` and `UNSPECIFIED`.
   With generated enums, 5 of the 15 examples fail to validate.
   The scope in the token example (`personio:person:write personio:employment:read`) doesn't match the documented `personio:persons:read` either.
5. `Person.email` has `format: email`, which becomes `EmailStr`. That needs the extra dependency `email-validator`
   and rejects addresses such as `test@localhost`, so a single unusual address would make a whole page unparsable.
6. `Employment.sub_company` is deprecated, which becomes `Field(deprecated=True)` and needs pydantic >= 2.7.
7. `Person` and `Employment` declare no required fields, so every field is optional (`X | None = None`).
8. The employment dates `probation_end_date`, `employment_start_date`, `employment_end_date` and `contract_end_date`
   are plain strings (no `format: date`; all examples are `YYYY-MM-DD` or `null`), while the dates of `termination` are `date`,
   and the write specs (`person-write-api-v2`, `employment-write-api-v2`) define the same fields as `format: date`.
9. Mismatches that don't break parsing: `employment_end_date: null` although the field isn't nullable,
   `termination.last_working_date` in the example versus `last_working_day` in the schema,
   and the `next` link of the employment examples points to `/v2/employments?cursor=...`.
10. The auth spec contains two unused v1 schemas (`V1AuthTokenRequest`, `V1AuthenticationTokenResponse`).
11. The value of a custom attribute is typed as `str | list[str] | list[dict[str, Any]]` ("an arbitrary value that can be represented as a string"),
    although Personio knows the attribute types `INT`, `DOUBLE`, `BOOLEAN` and `DATE`.
    A number or boolean would fail to validate ("Input should be a valid string") and make the whole page unparsable.
12. Two formats become strict types: `Employment.job.id` (`format: uuid`) becomes `UUID`, which rejects values like `""` or `"47"`,
    although all other IDs are plain strings (even the employment ID, which is a UUID in the examples);
    the Jobs API (`jobs-v2`) documents the job ID as a UUID, too.
    The links in the `_meta` of a person or employment (`format: uri`) become `AnyUrl`, which rejects relative links
    and re-encodes characters like `<` and `>`.

With the flags of [decision 5](#decisions), all 15 examples validate, and the generated code passes `ruff format`,
`mypy --strict` (with the pydantic plugin) and `codespell`; `ruff check` only reports long description lines (E501).

### Existing Python Packages

Only four of the 902,475 projects on PyPI (checked on 2026-09-30) have "personio" in their name, and none of them covers the scope of this plan:

| Package | Import package | Personio API | Scope | Style | Latest release on PyPI |
| --- | --- | --- | --- | --- | --- |
| [`personio-py`](https://pypi.org/project/personio-py/) | `personio_py` | v1 | employees, attendances, absences, projects | synchronous (requests) | 0.2.3 (2023-05) |
| [`personio-api-client`](https://pypi.org/project/personio-api-client/) | `personio_api_client` | v1; v2 only on GitHub | v1: employees, time-offs; v2 (GitHub releases 0.2.0 and 0.2.1, not on PyPI): projects, attendance periods | synchronous (httpx), also has a class `PersonioClient` | 0.1.0 (2026-01) |
| [`personio-client-api`](https://pypi.org/project/personio-client-api/) | `personio_client_api` | v1 | token and employees (raw dicts, no pagination) | synchronous (requests) | 0.1.2 (2024-08) |
| [`dlt-source-personio`](https://pypi.org/project/dlt-source-personio/) | `dlt_source_personio` | v1 and v2 | loads all persons and their employments into a [dlt](https://dlthub.com) pipeline; no filters, no retrieval by ID | pipeline source, not a client library | 0.0.4 (2025-12) |

- No project name collides with `personio-client`: none is identical after PEP 503 normalization,
  none after removing the separators and folding confusable characters (`0`/`o`, `1`/`l`/`i`),
  and none is within an edit distance of 2. None of the four packages installs the import package `personio_client`
  (checked in their wheels via the [PyPI inspector](https://inspector.pypi.io/)).
- `dlt-source-personio` generates its models from an unofficial v2 spec derived from the docs.
  It solved the same problems as this plan: lowercase custom attribute types with values typed by attribute type
  (numbers, booleans, dates), employment dates as dates, lenient `_meta` links, and a retry strategy added later.
  This supports decisions 5 and 17, but is no proof.

## Target Design

### Package Layout

```text
openapi/v2/
  credentials-service-api-v2.json   # the three specs, pretty-printed
  persons-service-api-v2.json
  employment-contract-v2.json
scripts/
  generate_models.py                # downloads the specs (--download) and generates the models
src/personio_client/
  __init__.py                       # PersonioClient, CursorPage, Person, Employment, OAuth2Token, exceptions
  py.typed
  client.py                         # PersonioClient
  exceptions.py                     # PersonioClientError, PersonioAPIError, PersonioAuthenticationError, PersonioRateLimitError
  models/
    __init__.py                     # re-exports the generated models and CursorPage
    _pagination.py                  # CursorPage[T], hand-written (list responses are inline in the spec)
    _generated/
      __init__.py
      auth.py                       # generated from credentials-service-api-v2.json
      persons.py                    # generated from persons-service-api-v2.json
      employments.py                # generated from employment-contract-v2.json
unittests/
  conftest.py                       # aioresponses fixture, loader for the examples of the specs
  test_exceptions.py
  test_client.py                    # context manager, headers, token handling, error mapping
  test_persons.py
  test_employments.py
  test_models.py
  test_api_coverage.py
```

Generated model classes (with the flags of decision 5):

- `auth.py`: `OAuth2TokenRequest`, `OAuth2Token`, `OAuth2TokenErrorResponse`, `OAuth2TokenRevocationRequest`,
  `V1AuthTokenRequest`, `V1AuthenticationTokenResponse` and two nested classes of the latter
- `persons.py`: `Person`, `PersonCustomAttribute`, `PersonProfilePicture`, `PersonEmploymentResponse`, `FieldMeta`, `FieldMetaLinks`
- `employments.py`: `Employment`, `EmploymentPosition`, `EmploymentSupervisor`, `EmploymentOffice`, `EmploymentPerson`,
  `EmploymentLegalEntity`, `EmploymentJob`, `EmploymentSubCompany`, `Termination`, `OrgUnit`, `CostCenter`, `FieldMeta`, `FieldMetaLinks`

`FieldMeta` and `FieldMetaLinks` (the `_meta` object of a resource) are the only names defined in two modules; both definitions are identical.

### Public API

```python
class PersonioClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        scope: list[str] | None = None,  # default: all scopes of the credentials
        app_id: str | None = None,  # sent as X-Personio-App-ID
        partner_id: str | None = None,  # sent as X-Personio-Partner-ID
        max_retries: int = 3,  # retries of a request answered with 429; 0 disables them
        base_url: str = "https://api.personio.de",
        session: aiohttp.ClientSession | None = None,
    ) -> None: ...

    # Authentication
    async def obtain_access_token(self, *, scope: list[str] | None = None) -> OAuth2Token: ...

    # Persons
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
    ) -> CursorPage[Person]: ...
    async def get_person(self, person_id: str) -> Person: ...
    async def iter_persons(self, *, limit: int = 50, id: list[str] | None = None, ...) -> AsyncIterator[Person]: ...

    # Employments
    async def get_employments(
        self,
        person_id: str,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        id: list[str] | None = None,
        updated_at: AwareDatetime | None = None,
        updated_at_gt: AwareDatetime | None = None,
        updated_at_lt: AwareDatetime | None = None,
    ) -> CursorPage[Employment]: ...
    async def get_employment(self, person_id: str, employment_id: str) -> Employment: ...
    async def iter_employments(self, person_id: str, *, limit: int = 50, ...) -> AsyncIterator[Employment]: ...
```

The `iter_*` methods accept the filters of their list method except `cursor`.

### Usage

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


asyncio.run(main())
```

## Decisions

1. **Names:** distribution and package `personio_client` (confirmed in the review of the open questions):
   the PyPI name `personio-client` is free and collides with no other project or import package (see [Existing Python Packages](#existing-python-packages)).
   Class `PersonioClient`, exceptions `Personio...Error`, as in `decidalo_client`. Async only (aiohttp), as in the model.
2. **Scope:** "the GET endpoints of persons and employments" is read as list and retrieve for both (four operations),
   plus the token endpoint. `POST /v2/auth/revoke` stays unwrapped, but shows up as "not implemented" in the API coverage of the README.
3. **Specs in the repository:** the three specs are stored under `openapi/v2/` with the names Personio uses, as pretty-printed `.json` files.
   The content is JSON despite the `.yaml` URL (quirk 1), and pretty-printing makes the spec diff of a sync reviewable.
   The write specs are not stored.
4. **One generated module per spec** (`models/_generated/{auth,persons,employments}.py`),
   re-exported by `models/__init__.py` via star imports as in the model.
   Alternatives: merging the specs before generating (needs a preprocessing script, which the model avoided on purpose);
   hand-written models (no drift detection against the spec).
5. **Generator flags:** the flags of the model, with these changes:
   - `--naming-strategy full-path`: collision-free, readable names such as `EmploymentPerson` and `PersonCustomAttribute` (quirk 3).
   - `--ignore-enum-constraints` instead of `--enum-field-as-literal one` and `--set-default-enum-member`:
     enum fields are plain `str`; the allowed values stay visible in the field descriptions (quirk 4).
     Alternative: generated enums as in the model. Rejected because the official examples already fail with them,
     and every value Personio adds later (e.g. a new termination type) would make a whole page of persons or employments unparsable.
     Trade-off: no enum classes; comparisons with string literals work the same (`employment.status == "ACTIVE"`).
   - `--type-mappings email=string uuid=string uri=string`: e-mail addresses, the job ID and the `_meta` links are plain `str`
     (quirks 5 and 12; `uuid` and `uri` agreed in the review of the open questions).
     They are opaque values the client only passes on, so a validation adds little, but could make a whole page unparsable;
     all IDs are `str` then, and relative links work. No `email-validator` dependency is needed.
     `date` and `AwareDatetime` stay typed, because they are useful for incremental syncs (`updated_at.gt`).
   - `--type-overrides` for the only fields that deviate from the spec (agreed in the review of the open questions):
     - `PersonCustomAttribute.value` → `pydantic.JsonValue`: the value of a custom attribute accepts any JSON value (quirk 11);
       users have to distinguish its types with `isinstance` anyway.
     - `Employment.probation_end_date`, `employment_start_date`, `employment_end_date` and `contract_end_date` → `datetime.date` (quirk 8):
       the write specs define them as dates, all examples are `YYYY-MM-DD` or `null`, and the dates of `termination` are typed, too.
       An empty string or a format like `17.01.2024` would fail to validate; the smoke test checks the real values.

     The overrides don't change the other specs, but datamodel-codegen silently ignores an override whose key doesn't match
     (e.g. after a renaming), so a unit test guards them.
   - `--disable-timestamp`: regenerating an unchanged spec produces no diff.
   - `--formatters builtin`, followed by `ruff format` and `ruff check --select I --fix` as in the model
     (avoids the deprecation warning of the default formatters black and isort).

   Unchanged: `--extra-fields ignore` (fields that Personio adds are ignored instead of breaking the parsing,
   decision 1 of the import sync plan), `--strict-nullable`, `--use-annotated`, `--field-constraints`, `--collapse-root-models`,
   `--use-standard-collections`, `--use-double-quotes`, `--target-python-version 3.11`.
6. **Generator script instead of README commands:** three specs, each downloaded, pretty-printed and generated with the same long list of flags,
   would be about ten commands in the README. `scripts/generate_models.py` keeps the flags and the mapping from spec to module
   in one place and runs on Windows and Linux: `uv run --group codegen python scripts/generate_models.py [--download]`.
7. **The generated code is linted and type-checked** (the model excludes its generated module):
   it passes today; only E501 needs a per-file ignore. If a future spec produces findings that flags can't fix,
   the generated package gets an exclusion as in the model.
8. **List responses return `CursorPage[T]`**, a small hand-written generic model with the fields `data` (alias `_data`)
   and `meta` (alias `_meta`) and a property `next_cursor`, which reads the `cursor` query parameter of `_meta.links.next.href`
   (`None` on the last page). It works with absolute and relative links and ignores the path of the link,
   which is wrong in the employment examples (quirk 9).
   Alternative: generating the inline schemas with `--openapi-scopes paths`, which would make names like `V2PersonsGetResponse`
   public return types (quirk 2).
9. **`iter_persons()` and `iter_employments()`** are async generators that call their list method page by page
   until there is no `next_cursor` or a page is empty.
   Their page size defaults to the maximum of 50 to save requests. They are the only public methods outside the 1:1 rule.
   - They resend the same filters and `limit` with each cursor (agreed in the review of the open questions):
     the result is either correct or a loud error (e.g. a 400, if the API rejects filters next to a cursor), never silently wrong.
     Alternatives: sending only the cursor, or taking the query of the `next` link;
     both would silently return unfiltered pages if the cursor doesn't carry the filters.
   - If a cursor repeats, they raise `PersonioClientError` instead of looping forever or silently returning a partial result
     (the forum bug, see [Resource Endpoints](#resource-endpoints)); a sync job must not continue with incomplete data.
   - Alternatives to the iterators: `get_all_*()` returning lists (whole result in memory, no early exit);
     a generic `paginate(method, **filters)` (typable via `ParamSpec`, but harder to discover).
10. **Authentication:** the client receives the credentials and obtains a token lazily with the first request.
    It caches the token and obtains a new one 60 s before `expires_in` runs out (24 h if `expires_in` is missing);
    the expiry is measured with `time.monotonic()`.
    An `asyncio.Lock` ensures that concurrent requests obtain only one token (similar to `DecidaloAppClient._ensure_fresh`).
    A 401 of a resource endpoint discards the cached token, so the next call obtains a new one; there is no automatic retry.
    `obtain_access_token()` is public (1:1 with the token operation) and has no side effects; the caching lives in the private `_ensure_access_token()`.
    Alternatives: obtaining the token in `__aenter__` (a network call on entering the context, harder to test);
    accepting a token obtained elsewhere (not needed yet).
11. **Errors:** `PersonioClientError` ← `PersonioAPIError(status_code, message, trace_id)` ← `PersonioAuthenticationError`.
    - 401 and 403 raise `PersonioAuthenticationError`, as in the model.
    - A 4xx of the token endpoint raises `PersonioAuthenticationError` with the OAuth2 `error` and `error_description` as message.
    - Other errors raise `PersonioAPIError`; the message is built from `errors[].title` and `errors[].detail` of the
      problem+json body, falling back to the raw body.
    - `trace_id` (from `personio_trace_id` or `trace_id`) is kept because Personio support asks for it.
    - A 429 raises `PersonioRateLimitError` (a `PersonioAPIError` with `retry_after`) once the retries are exhausted (decision 17).
12. **Headers:** `app_id` and `partner_id` are optional and sent as `X-Personio-App-ID` and `X-Personio-Partner-ID`
    with every request, including the token request.
13. **Parameter types:**
    - `id` and `email` are `list[str]`, sent as one comma-separated value (the documented format,
      not repeated keys like the array parameters of decidalo). `scope` is a `list[str]`, sent space-delimited.
    - The `status` filter is `Literal["ACTIVE", "INACTIVE"]`, checked statically only; responses stay lenient (decision 5).
    - Date-time filters are `AwareDatetime`, sent as ISO 8601; a naive datetime raises `ValueError`
      (decision 8 of the import sync plan; aiohttp encodes the `+` of an offset as `%2B`).
    - Path parameters are percent-encoded (`quote(value, safe="")`).
14. **Dependencies:** `aiohttp>=3.10,<3.14` (upper bound from the model; check whether aioresponses works with aiohttp 3.14
    and drop the bound if it does) and `pydantic>=2.7` (quirk 6).
    Dependency groups as in the template, plus `pytest-asyncio` and `aioresponses` in `tests`
    and a `codegen` group with `datamodel-code-generator==0.71.0` (the version of the trial generation) and the linting group.
15. **Template:** pre-commit and the workflows of the template stay; only the package paths change.
    Job names stay unchanged because they are required status checks.
16. **No specific scope by default** (agreed in the review of the open questions):
    the client requests no scope (`scope=None`), so the token gets all scopes of the credentials.
    This keeps working if Personio renames or adds scopes. Least privilege belongs to the credentials:
    the README names the required scope `personio:persons:read` and recommends credentials with only this scope.
    Alternative: requesting `personio:persons:read` by default, rejected because it breaks if the employment endpoints
    need another scope or Personio renames it.
17. **Automatic retries on 429** (agreed in the review of the open questions; see [Rate Limits](#rate-limits)):
    - The constructor takes `max_retries` (default 3; 0 disables the retries).
    - A request answered with 429 is repeated after the delay of the `Retry-After` header (seconds or HTTP date) if there is one,
      otherwise after 1 s, 2 s and 4 s. Each attempt fetches the access token again, in case it expired while waiting.
    - Once the retries are exhausted, the client raises `PersonioRateLimitError` with `retry_after`.
    - The retries live in the shared request helper, so they apply to all requests, including the token request and the iterators.
    - Retries on server errors (5xx) are a follow-up.
    Alternatives: no retries (every user would have to write their own for the main use case, reading the employments of all persons);
    client-side throttling (not sensible while the limits are unknown).
18. **An own client instead of contributing to `personio-api-client`** (agreed on 2026-09-30):
    none of the existing packages offers the v2 persons and employments endpoints as a library (see [Existing Python Packages](#existing-python-packages)).
    Contributing them to `personio-api-client` would mean writing the same code in its style (synchronous, hand-written models)
    instead of following this plan (async like `decidalo_client`, models generated from the specs with drift tests, 1:1 mapping, retries),
    and its releases depend on a single maintainer (its GitHub releases 0.2.0 and 0.2.1 haven't reached PyPI).
    The README explains how this client differs from the similarly named packages, so that nobody confuses them
    (in particular `personio-api-client`, which also has a class `PersonioClient`),
    and says that we may approach the maintainer of `personio-api-client` (see [Follow-ups](#follow-ups)).

## The 1:1 Mapping Rule

Adapted from the import sync plan of the model:

- One public method of `PersonioClient` wraps exactly one operation of the specs (checked by `unittests/test_api_coverage.py`).
  Exception: the `iter_*` methods, which each delegate to exactly one list method and accept the same filters except `cursor`.
- Path, query parameters, request body and return type follow the spec; list responses return `CursorPage[T]` (decision 8).
- A keyword argument is the snake_case form of the spec name, with dots replaced by underscores (`created_at.gt` → `created_at_gt`);
  the query key is sent in the exact spelling of the spec; the arguments follow the order of the spec.
- Filters are keyword-only, path parameters are positional.
  A path parameter the spec calls `id` is named after its resource (`person_id`, `employment_id`),
  because `get_employment(person_id, id)` would be ambiguous.
- Types: `int`, `str`, `Literal[...]` for enum parameters, `list[str]` for comma-separated lists,
  `AwareDatetime` for date-time parameters (decision 13).
- The request body is built from the request model of the spec (`OAuth2TokenRequest`).

## Implementation

Commits in order, each green on its own (tests, ruff, mypy, codespell, coverage >= 80 %).

### Task 1: Plan

- Create: `docs/plans/2026-09-29-personio-v2-client.md` (this document)
- Commit: `docs: add the implementation plan for the Personio v2 client`

### Task 2: Project Setup

- Modify `pyproject.toml`:
  - Metadata: name `personio_client`, description "An async Python client for the Personio API v2 (persons and employments)",
    authors as in the model, URLs pointing to `https://github.com/Hochfrequenz/personio_client.py`, keywords.
  - Dependencies (decision 14) and dependency groups: `tests` + `pytest-asyncio==1.4.0`, `aioresponses==0.7.9`;
    new `codegen` = `datamodel-code-generator==0.71.0` + linting; `dev` + `codegen`.
  - `[tool.ruff]`: `target-version = "py311"`;
    `[tool.ruff.lint.per-file-ignores]`: `"src/personio_client/models/_generated/*" = ["E501"]` and `"unittests/**"` as in the model.
  - `[tool.mypy]`: `plugins = ["pydantic.mypy"]`, plus an `ignore_missing_imports` override for `aioresponses` if it ships no type information.
  - `[tool.hatch.build.hooks.vcs]`: `version-file = "src/_personio_client_version.py"`.
  - `[tool.pytest.ini_options]`: `asyncio_mode = "auto"`, `asyncio_default_fixture_loop_scope = "function"`.
- Replace `src/mypackage/` by `src/personio_client/` (`__init__.py`, `py.typed`); delete `unittests/test_myclass.py`.
- Create `src/personio_client/exceptions.py` (decisions 11 and 17) and `unittests/test_exceptions.py`, so that the test suite isn't empty.
- `.github/workflows/pythonlint.yml`: `src/mypackage` → `src/personio_client`; add `scripts` to ruff and mypy.
- `.pre-commit-config.yaml`: `files: ^(src/personio_client|unittests|scripts)/`.
- `.gitignore`: `/src/_personio_client_version.py` and `try_api.py` (local smoke test, Task 8).
- `domain-specific-terms.txt`: add the words codespell flags (e.g. `personio`).
- Create `LICENSE` (MIT, as declared in `pyproject.toml`; copyright holder as in the model).
- `README.md`: replace the template text by a short description of the project, including the disclaimer
  that this is a community project and not an official Personio client, and a section on related packages (decision 18):
  the similarly named `personio-api-client`, `personio-client-api`, `personio-py` and `dlt-source-personio`,
  how this client differs (API v2 persons and employments, async, models generated from the official specs),
  and that we may approach the maintainer of `personio-api-client`.
  The repository is public already, so this section comes first; the rest of the README follows in Task 7.
- Run `uv lock`.
- Commit: `chore: turn the template into the personio_client project`

### Task 3: Specs and Models

- Create `scripts/generate_models.py`:

  ```python
  SPEC_URL = "https://developer.personio.de/openapi/{name}.yaml"  # served as .yaml, contains JSON
  SPECS = {  # spec name -> generated module
      "credentials-service-api-v2": "auth",
      "persons-service-api-v2": "persons",
      "employment-contract-v2": "employments",
  }
  TYPE_OVERRIDES = {  # the only deviations from the specs, see decision 5
      "PersonCustomAttribute.value": "pydantic.JsonValue",
      "Employment.probation_end_date": "datetime.date",
      "Employment.employment_start_date": "datetime.date",
      "Employment.employment_end_date": "datetime.date",
      "Employment.contract_end_date": "datetime.date",
  }
  CODEGEN_FLAGS = [
      "--input-file-type", "openapi",
      "--output-model-type", "pydantic_v2.BaseModel",
      "--target-python-version", "3.11",
      "--use-annotated", "--use-double-quotes", "--collapse-root-models", "--field-constraints",
      "--strict-nullable", "--use-standard-collections", "--extra-fields", "ignore",
      "--ignore-enum-constraints", "--naming-strategy", "full-path",
      "--type-mappings", "email=string", "uuid=string", "uri=string",
      "--type-overrides", json.dumps(TYPE_OVERRIDES),
      "--disable-timestamp", "--formatters", "builtin",
  ]
  ```

  - With `--download`: fetch each spec (with an explicit `User-Agent`), parse it as JSON and write it to
    `openapi/v2/<name>.json` with `indent=2`, `ensure_ascii=False` and a trailing newline.
  - Always: run `datamodel-codegen --input openapi/v2/<name>.json --output src/personio_client/models/_generated/<module>.py <flags>`
    for each spec, then `ruff format` and `ruff check --select I --fix` on the generated package.
- Run `uv run --group codegen python scripts/generate_models.py --download`.
- Create `src/personio_client/models/_generated/__init__.py` (docstring only) and
  `src/personio_client/models/__init__.py` (star imports of the three modules, and `CursorPage`).
- Create `src/personio_client/models/_pagination.py`:

  ```python
  class PageLink(BaseModel):
      model_config = ConfigDict(extra="ignore")
      href: str | None = None


  class PageMeta(BaseModel):
      model_config = ConfigDict(extra="ignore")
      links: dict[str, PageLink] = Field(default_factory=dict)


  class CursorPage(BaseModel, Generic[ItemT]):
      """One page of a cursor-paginated list response."""

      model_config = ConfigDict(extra="ignore")
      data: Annotated[list[ItemT], Field(alias="_data", default_factory=list)]
      meta: Annotated[PageMeta, Field(alias="_meta", default_factory=PageMeta)]

      @property
      def next_cursor(self) -> str | None:
          """The cursor of the next page, taken from the next link, or None on the last page."""
          link = self.meta.links.get("next")
          if link is None or link.href is None:
              return None
          for parameter in urlsplit(link.href).query.split("&"):
              key, _, value = parameter.partition("=")
              if unquote(key) == "cursor":
                  return unquote(value)  # unlike parse_qs, keeps a "+" (cursors may be base64 values)
          return None
  ```

- Create `unittests/test_models.py` (see [Tests](#tests)).
- Commit: `build: add the Personio v2 specs and generate the models`

### Task 4: Client Core and Authentication

- Create `src/personio_client/client.py`:
  - Constants `DEFAULT_BASE_URL`, `MAX_PAGE_SIZE = 50`, `TOKEN_REFRESH_MARGIN_SECONDS = 60`, `DEFAULT_TOKEN_LIFETIME_SECONDS = 86400`,
    `DEFAULT_MAX_RETRIES = 3`.
  - `_format_datetime()` as in the model.
  - `__init__`, `__aenter__` and `__aexit__` as in `DecidaloClient` (an external session is used, but not closed).
  - `_ensure_access_token()` (decision 10):

    ```python
    async def _ensure_access_token(self) -> str:
        """Return the cached access token, or obtain a new one if there is none or it is about to expire."""
        async with self._token_lock:
            if self._access_token is None or time.monotonic() >= self._access_token_expires_at:
                token = await self.obtain_access_token(scope=self._scope)
                if token.access_token is None:
                    raise PersonioAuthenticationError(200, "The token response contains no access token")
                lifetime = DEFAULT_TOKEN_LIFETIME_SECONDS if token.expires_in is None else token.expires_in
                self._access_token = token.access_token
                self._access_token_expires_at = time.monotonic() + lifetime - TOKEN_REFRESH_MARGIN_SECONDS
            return self._access_token
    ```

  - `_handle_response()` (error mapping, decision 11), `_get(path, params)` (Bearer token; discards the token on 401)
    and `_post_form(path, form)` (no token; errors of the token endpoint become `PersonioAuthenticationError`).
    Both send via a shared `_send()`, which retries a 429 (decision 17): it waits as long as `Retry-After` says,
    otherwise 1 s, doubling with each retry, and raises `PersonioRateLimitError` when the retries are exhausted.
  - `obtain_access_token()`: form body from `OAuth2TokenRequest(grant_type="client_credentials", ...).model_dump(exclude_none=True)`,
    response parsed as `OAuth2Token`.
- `src/personio_client/__init__.py`: exports.
- Create `unittests/conftest.py` and `unittests/test_client.py`.
- Commit: `feat: add PersonioClient with client credentials authentication`

### Task 5: Persons

- `get_persons()`, `get_person()` and `iter_persons()`:

  ```python
  async def iter_persons(self, *, limit: int = MAX_PAGE_SIZE, id: list[str] | None = None, ...) -> AsyncIterator[Person]:
      cursor: str | None = None
      seen_cursors: set[str] = set()
      while True:
          page = await self.get_persons(limit=limit, cursor=cursor, id=id, ...)
          for person in page.data:
              yield person
          cursor = page.next_cursor
          if cursor is None or not page.data:
              return
          if cursor in seen_cursors:
              raise PersonioClientError(f"The pagination doesn't advance: the cursor {cursor!r} was returned twice")
          seen_cursors.add(cursor)
  ```

- Create `unittests/test_persons.py`.
- Commit: `feat: wrap the persons endpoints`

### Task 6: Employments

- `get_employments()`, `get_employment()` and `iter_employments()`, analogous to Task 5.
- Create `unittests/test_employments.py`.
- Commit: `feat: wrap the employments endpoints`

### Task 7: API Coverage and README

- Create `unittests/test_api_coverage.py`, adapted from the model: it reads all specs in `openapi/v2/`,
  knows the request helpers `_get` (GET) and `_post_form` (POST), and checks the `iter_*` exception of the 1:1 rule.
- `README.md`, completing the version of Task 2:
  - badges, installation, usage (as above)
  - authentication: credentials, the required scope `personio:persons:read`, `app_id`
  - pagination, custom attributes (compare `type` case-insensitively; `value` is any JSON value), error handling
  - rate limits: automatic retries, `max_retries`, and limiting concurrent employment requests (e.g. with an `asyncio.Semaphore`)
  - the API coverage section between `<!-- api-coverage:start -->` and `<!-- api-coverage:end -->`
    (printed by `PYTHONPATH=src uv run --group tests python unittests/test_api_coverage.py`)
  - development (generator script) and license
- Commits: `test: check the API coverage against the specs`, `docs: document usage and API coverage in the README`

### Task 8: Smoke Test Against the Live API

Not part of CI; needs real, read-only credentials:

- A local, git-ignored `try_api.py` (as in the model) reads `PERSONIO_CLIENT_ID` and `PERSONIO_CLIENT_SECRET` from the environment.
- It uses credentials that only have the scope `personio:persons:read` (open question 1).
- It obtains a token and prints the granted scopes, iterates the persons with `limit=5` over several pages with and without filters,
  reads the employments of some persons, and retrieves a single person and a single employment.
- It runs `iter_persons(status="ACTIVE", limit=5)` over at least three pages and checks that every person matches the filter
  and that the cursor advances (open question 2).
- It prints the `type` and the JSON type of the `value` of every kind of custom attribute it sees (open question 3).
- It prints the response headers of a normal request (are there rate limit headers?) and those of any 429 (open question 5).
- It reports values that contradict the spec (see [Open Questions](#open-questions)).
- The results go into this plan; required changes (e.g. to decision 9) get their own commits.

### Task 9: Pull Request

- PR `feat/personio-v2-client` → `main`; the description lists the decisions and the results of the smoke test.
- Releasing v0.1.0 needs the publishing setup (see [Follow-ups](#follow-ups)).

## Tests

All HTTP calls are mocked with aioresponses, which matches the full URL including the query,
so the mocks check the exact query keys.
The mock responses are the official examples of the specs (loaded by a helper in `conftest.py`) instead of hand-written payloads,
so the tests use the format Personio documents.

- `test_client.py`
  - The context manager creates and closes its own session and leaves an external session open;
    methods called outside `async with` raise `RuntimeError`; a trailing slash of `base_url` is stripped.
  - `obtain_access_token()` sends a form body with `grant_type=client_credentials`, the credentials and the space-delimited `scope`,
    and no `Authorization` header; the response is parsed as `OAuth2Token`.
  - Token errors: the `invalid_client` example raises `PersonioAuthenticationError` with message and `trace_id`; a 500 raises `PersonioAPIError`.
  - Token handling: two requests obtain one token; concurrent requests (`asyncio.gather`) obtain one token;
    an expired token (patched `time.monotonic`) is replaced; a missing `expires_in` means 24 h; a 401 discards the token.
  - Errors of resource endpoints: the `ResourceNotFound` example raises `PersonioAPIError` with message and `trace_id`;
    401 and 403 raise `PersonioAuthenticationError`; a non-JSON body becomes the message.
  - Retries (decision 17): a 429 followed by a 200 returns the result; the delay follows `Retry-After` (seconds and HTTP date)
    or doubles from 1 s (checked via a patched `asyncio.sleep`); after `max_retries` a `PersonioRateLimitError` with `retry_after`
    is raised; `max_retries=0` raises at once; the token request is retried, too.
  - Headers: `Authorization: Bearer ...` and `Accept: application/json`; `X-Personio-App-ID` and `X-Personio-Partner-ID` only if configured.
  - Date-time parameters: a naive datetime raises `ValueError`; an offset is sent URL-encoded (`%2B02:00`);
    no parameter is annotated as a plain `datetime` (as in the model).
- `test_persons.py` and `test_employments.py`
  - The list method without arguments requests the plain path and returns a `CursorPage` whose `next_cursor`
    is taken from the example (`cur_234ls0f02lalfdd` for persons).
  - One test per list method passes all parameters and checks the exact URL (`id=1,2`, `created_at.gt=...`, `status=ACTIVE`, ...).
  - The retrieve method parses the example; `get_person()` uses the example with the lowercase custom attribute type
    (regression test for decision 5); path parameters are percent-encoded.
  - The `iter_*` method walks through three mocked pages in order, sends `limit=50`, resends the filters with each cursor,
    stops without a `next` link and on an empty page, and raises `PersonioClientError` if a cursor repeats.
  - The `iter_*` method accepts the same filters as its list method, except `cursor` (compared via `inspect.signature`).
- `test_models.py`
  - Every component schema of each spec has a generated class of the same name in the corresponding module.
  - No model forbids extra fields; an unknown field is ignored.
  - No enum class is generated (guard for `--ignore-enum-constraints`).
  - `PersonCustomAttribute.value` accepts numbers and booleans, and the four employment dates are parsed as `date`
    (guards for the type overrides).
  - No generated field uses `UUID`, `AnyUrl` or `EmailStr` (guard for the type mappings).
  - All response examples of the specs validate against the models.
  - Class names defined in more than one generated module have identical JSON schemas (currently `FieldMeta` and `FieldMetaLinks`),
    so the star imports can't shadow a different model.
  - `CursorPage.next_cursor` with and without `next` link, with a relative link, and with a link without cursor.
- `test_api_coverage.py`: every method calls a documented operation, no operation is wrapped twice,
  the `iter_*` methods delegate to exactly one list method, and the API coverage section of the README is up to date.

## Definition of Done

```bash
uv sync --group dev
uv run pytest
uv run ruff check src/personio_client unittests scripts
uv run ruff format --check .
uv run mypy --show-error-codes src/personio_client --strict
uv run mypy --show-error-codes unittests --strict
uv run mypy --show-error-codes scripts --strict
uv run codespell --ignore-words=domain-specific-terms.txt src README.md
uv build
```

Coverage (run in `unittests/` as in the coverage workflow) is at least 80 % as in the template, with a target above 95 %.
All GitHub workflows are green, the README is complete, and the smoke test is done or explicitly deferred in the PR.

## Open Questions

Reviewed on 2026-09-30; all points are resolved or decided. The smoke test (Task 8) confirms the assumptions behind points 1 to 6.

1. **Resolved:** Which scope does the employments endpoint need?
   Most likely `personio:persons:read`, like the persons endpoints (see [Authentication](#authentication));
   the client requests no specific scope anyway (decision 16).
   The smoke test confirms it with credentials that only have `personio:persons:read`.
2. **Decided:** Does the API accept the filters together with a cursor, and does the cursor keep them?
   Not documented for persons and employments, and other v2 APIs differ (see [Resource Endpoints](#resource-endpoints)).
   The iterators resend the filters (decision 9). The smoke test checks that filtered pages keep the filter and that the cursor advances;
   if the API answers a filter next to a cursor with a 400, the iterators switch to taking the query of the `next` link.
3. **Decided:** Custom attributes: is `type` sent in upper or lower case, and which JSON types does `value` have for `INT`, `DOUBLE`, `BOOLEAN` and `DATE`?
   Neither breaks the parsing anymore: `type` is a plain `str`, and `value` accepts any JSON value (decision 5).
   The smoke test records the actual casing and value types, so that the README can describe them.
4. **Decided:** Is `job.id` always a UUID?
   Most likely (the Jobs API documents it as a UUID, too), but it doesn't matter anymore: the job ID is a plain `str` (decision 5).
5. **Decided:** What are the rate limits of the persons and employments endpoints, and which headers come with a 429?
   They aren't documented (see [Rate Limits](#rate-limits)), so the client retries a 429 automatically (decision 17).
   The smoke test records the rate limit headers, if there are any.
6. **Decided:** Are the plain-string employment dates always `YYYY-MM-DD`?
   Most likely (the write specs define them as dates, and all examples match), so they are generated as `date` (decision 5).
   The smoke test checks that all real employments parse.
7. **Resolved:** Where does the repository live, and is `personio-client` the PyPI name?
   The repository was transferred to [Hochfrequenz/personio_client.py](https://github.com/Hochfrequenz/personio_client.py) (public) on 2026-09-29,
   and the name `personio-client` stays (decision 1; no collisions, see [Existing Python Packages](#existing-python-packages)).

## Follow-ups

- Retries on server errors (502, 503, 504), and client-side throttling if the smoke test finds rate limit headers.
- `POST /v2/auth/revoke` as `revoke_access_token()`.
- More v2 APIs (e.g. org units, cost centers, legal entities, absences) and the write endpoints of persons and employments.
- Activate the publishing workflow (GitHub environment `release`, PyPI trusted publisher) and release v0.1.0.
- Dependabot ecosystem `uv` instead of `pip`, as in the model.
- A scheduled workflow that downloads the specs and reports changes.
- Possibly approach the maintainer of `personio-api-client` (decision 18), e.g. to link the READMEs to each other
  and to point out that its GitHub releases 0.2.0 and 0.2.1 haven't reached PyPI;
  0.2.1 adapts the v1 authentication to Personio rejecting credentials in the query string from 2026-12-01.

## How to Sync the Specs

```bash
uv run --group codegen python scripts/generate_models.py --download
git diff openapi/
uv run pytest
```

`unittests/test_models.py` and `unittests/test_api_coverage.py` point out what has to be updated (models, methods, README section);
the expected README section is printed by `PYTHONPATH=src uv run --group tests python unittests/test_api_coverage.py`.
