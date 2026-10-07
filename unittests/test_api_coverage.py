"""Tests that the API coverage section of the README matches the OpenAPI specs and the client.

To print the expected README section (e.g. after a spec sync), run:
    uv run python unittests/test_api_coverage.py
"""

from __future__ import annotations

import ast
import inspect
import json
import re
from collections.abc import Collection
from pathlib import Path
from typing import Any

import personio_client.client as client_module

REPO_ROOT = Path(__file__).parent.parent
README_PATH = REPO_ROOT / "README.md"
SPEC_DIR = REPO_ROOT / "openapi" / "v2"
START_MARKER = "<!-- api-coverage:start -->"
END_MARKER = "<!-- api-coverage:end -->"
HTTP_METHODS = ("get", "head", "post", "put", "patch", "delete")
REQUEST_HELPERS = {"_get": "GET", "_post_form": "POST"}


def normalize_path(path: str) -> str:
    """Replace path parameters by '{}' so that spec and client paths can be compared."""
    return re.sub(r"\{[^}]*\}", "{}", path)


def spec_operations() -> dict[tuple[str, str], dict[str, Any]]:
    """Return all operations of the specs, keyed by (HTTP method, path)."""
    operations: dict[tuple[str, str], dict[str, Any]] = {}
    for spec_path in sorted(SPEC_DIR.glob("*.json")):
        spec: dict[str, Any] = json.loads(spec_path.read_text(encoding="utf-8"))
        for path, item in spec["paths"].items():
            for method, operation in item.items():
                if method in HTTP_METHODS:
                    operations[method.upper(), path] = operation
    return operations


def public_methods() -> list[ast.AsyncFunctionDef]:
    """Return the public async methods of PersonioClient."""
    tree = ast.parse(inspect.getsource(client_module))
    client_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PersonioClient")
    return [
        node for node in client_class.body if isinstance(node, ast.AsyncFunctionDef) and not node.name.startswith("_")
    ]


def method_calls(node: ast.AST, names: Collection[str]) -> list[ast.Call]:
    """Return the calls of methods with one of the names in the body of a node, e.g. self._get(...)."""
    return [
        call
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr in names
    ]


def client_operations() -> dict[str, tuple[str, str]]:
    """Return the operation that each public PersonioClient method calls, keyed by method name.

    The operation is taken from the request helper call (self._get/_post_form) in the method body.
    The iter_* methods don't call a request helper; they walk through the pages of a list method.
    """
    operations: dict[str, tuple[str, str]] = {}
    for node in public_methods():
        if node.name.startswith("iter_"):
            continue
        calls = method_calls(node, REQUEST_HELPERS)
        assert len(calls) == 1, f"{node.name} must call exactly one request helper"
        helper = calls[0].func
        assert isinstance(helper, ast.Attribute)
        path_node = calls[0].args[0]
        if isinstance(path_node, ast.Constant) and isinstance(path_node.value, str):
            path = path_node.value
        elif isinstance(path_node, ast.JoinedStr):
            path = "".join(str(part.value) if isinstance(part, ast.Constant) else "{}" for part in path_node.values)
        else:
            raise AssertionError(f"{node.name} must pass the path as a string literal")
        operations[node.name] = (REQUEST_HELPERS[helper.attr], normalize_path(path))
    return operations


def expected_coverage_section() -> str:
    """Render the API coverage section of the README from the specs and the client."""
    operations = spec_operations()
    implemented = {operation: name for name, operation in client_operations().items()}
    by_tag: dict[str, list[tuple[str, str]]] = {}
    for method, path in operations:
        by_tag.setdefault(operations[method, path]["tags"][0], []).append((method, path))

    lines = [f"**{len(implemented)} of {len(operations)}** operations are implemented.", ""]
    for tag in sorted(by_tag, key=str.lower):
        lines += [f"#### {tag}", "", "| Endpoint | Method |", "| --- | --- |"]
        for method, path in sorted(by_tag[tag], key=lambda op: (op[1], HTTP_METHODS.index(op[0].lower()))):
            name = implemented.get((method, normalize_path(path)))
            if name is not None:
                cell = f"`{name}()`"
            elif operations[method, path].get("deprecated"):
                cell = "not implemented (deprecated)"
            else:
                cell = "not implemented"
            lines.append(f"| `{method} {path}` | {cell} |")
        lines.append("")
    return "\n".join(lines).strip()


class TestApiCoverage:
    """Tests for the mapping between client methods and API operations."""

    def test_every_client_method_calls_a_documented_operation(self) -> None:
        """Test that no method calls an endpoint that is missing from the specs (e.g. a mistyped path)."""
        documented = {(method, normalize_path(path)) for method, path in spec_operations()}

        undocumented = {
            name: operation for name, operation in client_operations().items() if operation not in documented
        }

        assert undocumented == {}

    def test_no_two_methods_wrap_the_same_operation(self) -> None:
        """Test that every operation is wrapped by at most one method."""
        operations = list(client_operations().values())

        assert len(operations) == len(set(operations))

    def test_iter_methods_delegate_to_their_list_method(self) -> None:
        """Test that each iter_* method calls exactly one method: the list method of the same resource."""
        public_names = {node.name for node in public_methods()}
        iter_methods = [node for node in public_methods() if node.name.startswith("iter_")]

        assert [node.name for node in iter_methods] == ["iter_persons", "iter_employments"]
        for node in iter_methods:
            calls = [
                call.func.attr for call in method_calls(node, public_names) if isinstance(call.func, ast.Attribute)
            ]
            assert calls == [node.name.replace("iter_", "get_", 1)], node.name

    def test_readme_coverage_section_is_up_to_date(self) -> None:
        """Test that the API coverage section of the README matches the specs and the client.

        If this fails after a spec sync or after adding a method, replace the README section
        between the markers with the output of this module (see the module docstring).
        """
        readme = README_PATH.read_text(encoding="utf-8")
        start = readme.index(START_MARKER) + len(START_MARKER)
        end = readme.index(END_MARKER)

        assert readme[start:end].strip() == expected_coverage_section()


if __name__ == "__main__":
    print(expected_coverage_section())
