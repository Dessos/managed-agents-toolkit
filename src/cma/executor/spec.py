"""Job specifications — operator-authored YAML files under
``.managed-agents/job_specs/`` that declare what each ``spec_ref`` means.

The spec defines a contract between the cloud agent (which sees only the
``spec_ref`` + allowed input parameters) and the local adapter (which
provides the actual handler).

Example spec file (``example-long-job.yaml``):

.. code-block:: yaml

    spec_ref: example-long-job
    job_type: compute
    description: Long-running chunked compute over a dataset.
    handler: example_long_job_handler        # key in JOB_HANDLERS dict
    allowed_inputs:
      dataset:
        type: string
        choices: ["dataset-alpha", "dataset-beta"]
      start:
        type: date
      end:
        type: date
      chunk_size_steps:
        type: integer
        min: 10
        max: 500
        default: 100
    output_summary:
      metric_a: number
      metric_b: number
      metric_c: number
      result_count: integer
    timeout_seconds: 1800
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Input schema — narrow, intentional
# ---------------------------------------------------------------------------


class InputSchema(BaseModel):
    """Allowed shape of one input parameter.

    Intentionally limited types — we want every parameter to be JSON-safe
    so the cloud agent can supply it through the MCP tool, and the
    constraint set must be checkable without arbitrary code.
    """

    type: Literal["string", "integer", "number", "boolean", "date"]
    choices: list[Any] | None = None
    min: float | int | None = None
    max: float | int | None = None
    default: Any | None = None
    description: str | None = None


# ---------------------------------------------------------------------------
# Job spec
# ---------------------------------------------------------------------------


_SPEC_REF_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class JobSpec(BaseModel):
    """One job specification.

    Loaded from a YAML file. The cloud agent submits jobs by ``spec_ref``;
    the executor resolves the ref to this spec, validates inputs, and
    dispatches to ``handler`` (a key in the operator's JOB_HANDLERS dict).
    """

    spec_ref: str = Field(..., description="Opaque slug, e.g. 'example-long-job'.")
    job_type: str = Field(..., description="Category for filtering, e.g. 'compute', 'research'.")
    handler: str = Field(..., description="Key in JOB_HANDLERS dict in the adapter file.")
    description: str | None = None
    allowed_inputs: dict[str, InputSchema] = Field(default_factory=dict)
    output_summary: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(
        default=1800,
        gt=0,
        description="Per-job wall-clock timeout. After this, executor SIGTERMs the worker.",
    )

    @field_validator("spec_ref")
    @classmethod
    def _slug_shape(cls, v: str) -> str:
        if not _SPEC_REF_RE.match(v):
            raise ValueError(
                f"spec_ref {v!r} must match {_SPEC_REF_RE.pattern} "
                "(lowercase alphanumerics, dashes, underscores; max 63 chars)."
            )
        return v

    # -----------------------------------------------------------------
    # Input validation
    # -----------------------------------------------------------------

    def validate_inputs(self, inputs: Mapping[str, Any]) -> dict[str, Any]:
        """Validate + coerce *inputs* against ``allowed_inputs``.

        Returns the validated input dict (with defaults applied). Raises
        :class:`ValueError` listing every failure if validation fails.

        Strictness rules:

        * Unknown input keys are REJECTED. Cloud agents can only pass
          parameters the operator pre-declared.
        * Each value must match its declared ``type``.
        * If ``choices`` is set, value must be in the list.
        * If ``min``/``max`` is set (numeric types only), value must be in range.
        * Missing required inputs (no default) are REJECTED.
        """
        errors: list[str] = []
        out: dict[str, Any] = {}
        unknown = set(inputs.keys()) - set(self.allowed_inputs.keys())
        if unknown:
            errors.append(
                f"Unknown input keys: {sorted(unknown)}. "
                f"Allowed: {sorted(self.allowed_inputs.keys())}."
            )

        for key, schema in self.allowed_inputs.items():
            if key in inputs:
                value = inputs[key]
                ok, coerced, msg = _validate_one(key, value, schema)
                if ok:
                    out[key] = coerced
                else:
                    errors.append(msg)
            elif schema.default is not None:
                out[key] = schema.default
            else:
                errors.append(f"Missing required input {key!r}.")

        if errors:
            raise ValueError("Input validation failed:\n  - " + "\n  - ".join(errors))
        return out


# ---------------------------------------------------------------------------
# Per-value validation
# ---------------------------------------------------------------------------


def _validate_one(
    name: str, value: Any, schema: InputSchema
) -> tuple[bool, Any, str]:
    """Return ``(ok, coerced_value, error_message)``."""
    # Type check + coerce
    expected = schema.type
    if expected == "string":
        if not isinstance(value, str):
            return False, None, f"{name}: expected string, got {type(value).__name__}"
        coerced: Any = value
    elif expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return False, None, f"{name}: expected integer, got {type(value).__name__}"
        coerced = value
    elif expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, None, f"{name}: expected number, got {type(value).__name__}"
        coerced = float(value)
    elif expected == "boolean":
        if not isinstance(value, bool):
            return False, None, f"{name}: expected boolean, got {type(value).__name__}"
        coerced = value
    elif expected == "date":
        if not isinstance(value, str):
            return False, None, f"{name}: expected date string, got {type(value).__name__}"
        # Accept ISO 8601 dates. Don't import dateutil; do a simple shape check.
        if not re.match(r"^\d{4}-\d{2}-\d{2}", value):
            return False, None, f"{name}: expected date 'YYYY-MM-DD...', got {value!r}"
        coerced = value
    else:  # pragma: no cover — Pydantic Literal catches this
        return False, None, f"{name}: unknown declared type {expected!r}"

    # choices
    if schema.choices is not None and coerced not in schema.choices:
        return False, None, f"{name}: value {coerced!r} not in choices {schema.choices}"

    # range
    if expected in {"integer", "number"}:
        if schema.min is not None and coerced < schema.min:
            return False, None, f"{name}: value {coerced} below min {schema.min}"
        if schema.max is not None and coerced > schema.max:
            return False, None, f"{name}: value {coerced} above max {schema.max}"

    return True, coerced, ""


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_job_spec(path: Path | str) -> JobSpec:
    """Load + validate a single job spec file."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"No job spec at {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if raw is None:
        raise ValueError(f"Empty job spec at {p}")
    return JobSpec.model_validate(raw)


def load_job_specs(directory: Path | str) -> dict[str, JobSpec]:
    """Load every ``*.yaml`` job spec under *directory*; key by ``spec_ref``.

    Raises :class:`ValueError` on duplicate spec_refs (operator authored two
    files with the same slug).
    """
    d = Path(directory)
    if not d.is_dir():
        return {}
    specs: dict[str, JobSpec] = {}
    for yaml_file in sorted(d.glob("*.yaml")):
        spec = load_job_spec(yaml_file)
        if spec.spec_ref in specs:
            raise ValueError(
                f"Duplicate spec_ref {spec.spec_ref!r} in {yaml_file} "
                f"(already defined elsewhere in {d})."
            )
        specs[spec.spec_ref] = spec
    return specs
