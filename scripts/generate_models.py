"""Download the OpenAPI specs of the Personio API v2 and generate the pydantic models from them.

Run it from the repository root:
    uv run --group codegen python scripts/generate_models.py              # regenerate the models from openapi/v2/
    uv run --group codegen python scripts/generate_models.py --download   # download the specs first

The choice of the flags is explained in decision 5 of docs/plans/2026-09-29-personio-v2-client.md.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_DIR = REPO_ROOT / "openapi" / "v2"
MODEL_DIR = REPO_ROOT / "src" / "personio_client" / "models" / "_generated"

# Personio serves the specs with the extension .yaml, but they contain JSON
SPEC_URL = "https://developer.personio.de/openapi/{name}.yaml"

# spec name -> generated module
SPECS = {
    "credentials-service-api-v2": "auth",
    "persons-service-api-v2": "persons",
    "employment-contract-v2": "employments",
}

# The only deviations from the specs. datamodel-codegen silently ignores an override whose key doesn't match
# a generated field, so unittests/test_models.py checks that they are applied.
TYPE_OVERRIDES = {
    "PersonCustomAttribute.value": "pydantic.JsonValue",
    "Employment.probation_end_date": "datetime.date",
    "Employment.employment_start_date": "datetime.date",
    "Employment.employment_end_date": "datetime.date",
    "Employment.contract_end_date": "datetime.date",
}

CODEGEN_FLAGS = [
    "--input-file-type=openapi",
    "--output-model-type=pydantic_v2.BaseModel",
    "--target-python-version=3.11",
    "--use-annotated",
    "--use-double-quotes",
    "--collapse-root-models",
    "--field-constraints",
    "--strict-nullable",
    "--use-standard-collections",
    "--extra-fields=ignore",
    "--ignore-enum-constraints",
    "--naming-strategy=full-path",
    f"--type-overrides={json.dumps(TYPE_OVERRIDES)}",
    "--disable-timestamp",
    "--formatters=builtin",
    "--type-mappings",
    "email=string",
    "uuid=string",
    "uri=string",
]


def download_specs() -> None:
    """Download the specs and store them pretty-printed, so that the diff of a sync is reviewable."""
    SPEC_DIR.mkdir(parents=True, exist_ok=True)
    for name in SPECS:
        request = urllib.request.Request(SPEC_URL.format(name=name), headers={"User-Agent": "personio_client-codegen"})
        with urllib.request.urlopen(request, timeout=60) as response:
            spec = json.load(response)
        path = SPEC_DIR / f"{name}.json"
        path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Downloaded {path.relative_to(REPO_ROOT)}")


def generate_models() -> None:
    """Generate one module of pydantic models per spec and format the modules with ruff."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for name, module in SPECS.items():
        output = MODEL_DIR / f"{module}.py"
        spec = SPEC_DIR / f"{name}.json"
        subprocess.run(["datamodel-codegen", "--input", str(spec), "--output", str(output), *CODEGEN_FLAGS], check=True)
        print(f"Generated {output.relative_to(REPO_ROOT)}")
    subprocess.run(["ruff", "format", str(MODEL_DIR)], check=True)
    subprocess.run(["ruff", "check", "--select", "I", "--fix", str(MODEL_DIR)], check=True)


def main() -> None:
    """Parse the command line and run the requested steps."""
    parser = argparse.ArgumentParser(description="Generate the pydantic models from the Personio OpenAPI specs.")
    parser.add_argument("--download", action="store_true", help="download the specs before generating the models")
    args = parser.parse_args()
    if args.download:
        download_specs()
    generate_models()


if __name__ == "__main__":
    main()
