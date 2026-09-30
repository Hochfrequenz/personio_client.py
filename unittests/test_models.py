"""Tests for the pydantic models, most of them generated from the OpenAPI specs in openapi/v2/."""

from __future__ import annotations

import inspect
import json
from collections.abc import Iterator
from datetime import date
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any, get_args
from uuid import UUID

import pytest
from pydantic import AnyUrl, BaseModel, EmailStr

from personio_client.models import CursorPage, Employment, Person
from personio_client.models._generated import auth, employments, persons

SPEC_DIR = Path(__file__).parent.parent / "openapi" / "v2"
# spec name -> generated module, as in scripts/generate_models.py
SPECS: dict[str, ModuleType] = {
    "credentials-service-api-v2": auth,
    "persons-service-api-v2": persons,
    "employment-contract-v2": employments,
}
HTTP_METHODS = ("get", "head", "post", "put", "patch", "delete")
# the models of the list responses, keyed by the schema of their items (the specs describe list responses inline)
PAGE_MODELS: dict[str, type[BaseModel]] = {"Person": CursorPage[Person], "Employment": CursorPage[Employment]}


def load_spec(name: str) -> dict[str, Any]:
    """Load a spec from openapi/v2/."""
    spec: dict[str, Any] = json.loads((SPEC_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return spec


def generated_classes(module: ModuleType) -> dict[str, type]:
    """Return the classes defined in a generated module, keyed by name."""
    return {name: obj for name, obj in inspect.getmembers(module, inspect.isclass) if obj.__module__ == module.__name__}


GENERATED_MODELS = [
    obj for module in SPECS.values() for obj in generated_classes(module).values() if issubclass(obj, BaseModel)
]


def response_model(module: ModuleType, schema: dict[str, Any]) -> type[BaseModel] | None:
    """Return the model a response is parsed with, or None if the response schema is inline without a model.

    The errors of the resource endpoints are inline schemas without a model.
    """
    ref: str = schema.get("$ref", "")
    items_ref: str = schema.get("properties", {}).get("_data", {}).get("items", {}).get("$ref", "")
    if ref.startswith("#/components/schemas/"):
        model: type[BaseModel] = getattr(module, ref.rsplit("/", 1)[1])
        return model
    if items_ref.startswith("#/components/schemas/"):
        return PAGE_MODELS[items_ref.rsplit("/", 1)[1]]
    return None


def response_contents() -> Iterator[tuple[str, ModuleType, dict[str, Any], dict[str, Any]]]:
    """Yield the operation, the module, the content and the component examples of every response of the specs."""
    for name, module in SPECS.items():
        spec = load_spec(name)
        component_examples = spec["components"].get("examples", {})
        for path, item in spec["paths"].items():
            for method, operation in item.items():
                if method not in HTTP_METHODS:
                    continue
                for status, response in operation["responses"].items():
                    for content in response.get("content", {}).values():
                        yield f"{method.upper()} {path} {status}", module, content, component_examples


def response_examples() -> list[Any]:
    """Return a pytest parameter (model, example) for every response example of the specs that has a model."""
    params = []
    for operation, module, content, component_examples in response_contents():
        model = response_model(module, content["schema"])
        if model is None:
            continue
        for example_name, example in content.get("examples", {}).items():
            resolved = component_examples[example["$ref"].rsplit("/", 1)[1]] if "$ref" in example else example
            params.append(pytest.param(model, resolved["value"], id=f"{operation} {example_name}"))
    return params


def annotation_types(annotation: Any) -> list[Any]:
    """Return an annotation and all types nested in it, e.g. the members of a union."""
    types = [annotation]
    for arg in get_args(annotation):
        types += annotation_types(arg)
    return types


class TestModelsMatchSpecs:
    """Tests that the generated models are in sync with the specs in the repository."""

    def test_generated_models_are_collected(self) -> None:
        """Test that the model collection used by the other tests is not empty."""
        assert len(GENERATED_MODELS) > 20

    @pytest.mark.parametrize("name", list(SPECS))
    def test_every_schema_has_a_model(self, name: str) -> None:
        """Test that the models were regenerated after the last spec update (see the README)."""
        schemas = set(load_spec(name)["components"]["schemas"])

        assert schemas <= set(generated_classes(SPECS[name]))

    @pytest.mark.parametrize(("model", "example"), response_examples())
    def test_response_example_validates(self, model: type[BaseModel], example: Any) -> None:
        """Test that every response example of the specs validates against its model."""
        model.model_validate(example)

    def test_response_examples_are_collected(self) -> None:
        """Test that the examples of all five response models are checked."""
        models = {param.values[0] for param in response_examples()}

        assert models == {
            PAGE_MODELS["Person"],
            PAGE_MODELS["Employment"],
            persons.Person,
            employments.Employment,
            auth.OAuth2Token,
            auth.OAuth2TokenErrorResponse,
        }

    def test_names_defined_in_several_modules_are_identical(self) -> None:
        """Test that the star imports in personio_client.models can't shadow a different model.

        The persons and the employments spec both define the `_meta` object (FieldMeta and FieldMetaLinks).
        """
        classes_by_name: dict[str, list[type[BaseModel]]] = {}
        for module in SPECS.values():
            for name, cls in generated_classes(module).items():
                classes_by_name.setdefault(name, []).append(cls)
        shared = {name: classes for name, classes in classes_by_name.items() if len(classes) > 1}

        assert "FieldMeta" in shared
        for name, classes in shared.items():
            schemas = [cls.model_json_schema() for cls in classes]
            assert all(schema == schemas[0] for schema in schemas), name


class TestLenientModels:
    """Tests that unexpected values don't break the parsing of a whole page (decision 5 of the plan)."""

    def test_no_model_forbids_extra_fields(self) -> None:
        """Test that fields Personio adds to a response are ignored instead of failing the validation."""
        forbidding = [model.__name__ for model in GENERATED_MODELS if model.model_config.get("extra") == "forbid"]

        assert forbidding == []

    def test_unknown_field_is_ignored(self) -> None:
        """Test that a response containing a field unknown to the spec is parsed and the field dropped."""
        person = Person.model_validate({"id": "3003", "field_from_the_future": 42})

        assert person.id == "3003"
        assert "field_from_the_future" not in person.model_dump()

    def test_no_enum_class_is_generated(self) -> None:
        """Test that enum fields are plain strings (--ignore-enum-constraints).

        The examples of Personio contradict its enums, e.g. the custom attribute type "string" versus "STRING".
        """
        enums = [
            cls.__name__
            for module in SPECS.values()
            for cls in generated_classes(module).values()
            if issubclass(cls, Enum)
        ]

        assert enums == []

    def test_value_outside_an_enum_is_accepted(self) -> None:
        """Test that a value the enum of the spec doesn't list is kept as it is."""
        employment = Employment.model_validate({"status": "SOMETHING_NEW", "termination": {"type": "NEW_TYPE"}})

        assert employment.status == "SOMETHING_NEW"
        assert employment.termination is not None
        assert employment.termination.type == "NEW_TYPE"

    @pytest.mark.parametrize("value", ["Red", ["Munich", "Berlin"], [{"id": 1}], 42, 3.5, True, None])
    def test_custom_attribute_value_accepts_any_json_value(self, value: Any) -> None:
        """Test the type override of PersonCustomAttribute.value: numbers and booleans don't break the parsing."""
        person = Person.model_validate({"custom_attributes": [{"id": "dynamic_305", "type": "int", "value": value}]})

        assert person.custom_attributes is not None
        assert person.custom_attributes[0].value == value

    def test_employment_dates_are_dates(self) -> None:
        """Test the type overrides of the employment dates, which the read spec types as plain strings."""
        employment = Employment.model_validate(
            {
                "probation_end_date": "2023-07-01",
                "employment_start_date": "2023-01-01",
                "employment_end_date": None,
                "contract_end_date": "2024-01-01",
            }
        )

        assert employment.probation_end_date == date(2023, 7, 1)
        assert employment.employment_start_date == date(2023, 1, 1)
        assert employment.employment_end_date is None
        assert employment.contract_end_date == date(2024, 1, 1)

    def test_no_field_uses_a_strict_string_type(self) -> None:
        """Test that e-mail addresses, UUIDs and URLs are plain strings (--type-mappings)."""
        strict_types = (UUID, AnyUrl, EmailStr)
        offenders = [
            f"{model.__name__}.{name}"
            for model in GENERATED_MODELS
            for name, field in model.model_fields.items()
            if any(member in strict_types for member in annotation_types(field.annotation))
        ]

        assert offenders == []


class TestCursorPage:
    """Tests for the hand-written model of the list responses."""

    def test_data_and_next_cursor(self) -> None:
        """Test that a page contains the items and the cursor of the next link."""
        page = CursorPage[Person].model_validate(
            {
                "_data": [{"id": "3003"}],
                "_meta": {
                    "links": {
                        "self": {"href": "https://api.personio.de/v2/persons"},
                        "next": {"href": "https://api.personio.de/v2/persons?cursor=cur_82sQwR2eZvKilo2"},
                    }
                },
            }
        )

        assert [person.id for person in page.data] == ["3003"]
        assert page.next_cursor == "cur_82sQwR2eZvKilo2"

    def test_next_cursor_of_a_relative_link(self) -> None:
        """Test that a relative next link works, too (Personio used them in the attendance periods API)."""
        page = CursorPage[Person].model_validate(
            {
                "_data": [],
                "_meta": {"links": {"next": {"href": "/v2/persons?limit=5&cursor=cur_b2Zmc2V0PTEmbGltaXQ9MQ%3D%3D"}}},
            }
        )

        assert page.next_cursor == "cur_b2Zmc2V0PTEmbGltaXQ9MQ=="

    @pytest.mark.parametrize("href", ["/v2/persons?cursor=a+b/c==", "/v2/persons?cursor=a%2Bb%2Fc%3D%3D"])
    def test_plus_in_the_cursor_stays_a_plus(self, href: str) -> None:
        """Test that a `+` of a base64 cursor isn't turned into a space, whether it is percent-encoded or not."""
        page = CursorPage[Person].model_validate({"_meta": {"links": {"next": {"href": href}}}})

        assert page.next_cursor == "a+b/c=="

    def test_last_page_has_no_next_cursor(self) -> None:
        """Test that the last page, which has no next link, has no next cursor."""
        page = CursorPage[Person].model_validate({"_data": [], "_meta": {"links": {"self": {"href": "/v2/persons"}}}})

        assert page.next_cursor is None

    def test_next_link_without_cursor(self) -> None:
        """Test that a next link without a cursor parameter yields no next cursor."""
        page = CursorPage[Person].model_validate({"_meta": {"links": {"next": {"href": "/v2/persons?limit=5"}}}})

        assert page.next_cursor is None

    def test_missing_data_and_meta(self) -> None:
        """Test that a list response without `_data` and `_meta` is an empty last page (the spec requires neither)."""
        page = CursorPage[Person].model_validate({})

        assert page.data == []
        assert page.next_cursor is None
