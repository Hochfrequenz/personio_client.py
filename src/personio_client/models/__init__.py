"""
Pydantic models of the Personio API v2.

The models in `_generated` are generated from the OpenAPI specs in openapi/v2/;
see the "Development" section of the README for how to regenerate them.
"""

from personio_client.models._generated.auth import *  # noqa: F403
from personio_client.models._generated.employments import *  # noqa: F403

# Both specs define the `_meta` object (FieldMeta, FieldMetaLinks). The definitions are identical,
# which unittests/test_models.py checks, so it doesn't matter which one the star import keeps.
from personio_client.models._generated.persons import *  # type: ignore[assignment]  # noqa: F403
from personio_client.models._pagination import CursorPage as CursorPage
