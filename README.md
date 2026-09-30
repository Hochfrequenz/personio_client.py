# personio_client.py

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
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
> The client is under development; see the [implementation plan](docs/plans/2026-09-29-personio-v2-client.md).

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

For detailed information on the development setup (uv configuration, IDE setup, etc.), see the [Hochfrequenz Python Template Repository](https://github.com/Hochfrequenz/python_template_repository).

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
