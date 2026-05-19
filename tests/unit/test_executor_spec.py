"""Tests for cma.executor.spec — job spec loading + input validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cma.executor.spec import (
    InputSchema,
    JobSpec,
    load_job_spec,
    load_job_specs,
)

# ---------------------------------------------------------------------------
# spec_ref shape
# ---------------------------------------------------------------------------


class TestSpecRefShape:
    def test_accepts_simple_slug(self) -> None:
        spec = JobSpec(spec_ref="my-job", job_type="t", handler="h")
        assert spec.spec_ref == "my-job"

    def test_accepts_underscores(self) -> None:
        JobSpec(spec_ref="my_job_v3", job_type="t", handler="h")

    def test_rejects_uppercase(self) -> None:
        with pytest.raises(ValueError, match="spec_ref"):
            JobSpec(spec_ref="MyJob", job_type="t", handler="h")

    def test_rejects_slash(self) -> None:
        with pytest.raises(ValueError, match="spec_ref"):
            JobSpec(spec_ref="bad/slash", job_type="t", handler="h")

    def test_rejects_leading_dash(self) -> None:
        with pytest.raises(ValueError, match="spec_ref"):
            JobSpec(spec_ref="-bad", job_type="t", handler="h")


# ---------------------------------------------------------------------------
# validate_inputs
# ---------------------------------------------------------------------------


def _spec_with_inputs(allowed: dict[str, dict]) -> JobSpec:
    return JobSpec(
        spec_ref="test",
        job_type="t",
        handler="h",
        allowed_inputs={k: InputSchema(**v) for k, v in allowed.items()},
    )


class TestValidateInputs:
    def test_accepts_well_typed_inputs(self) -> None:
        spec = _spec_with_inputs(
            {
                "dataset": {"type": "string", "choices": ["dataset-alpha"]},
                "days": {"type": "integer", "min": 1, "max": 1000},
            }
        )
        out = spec.validate_inputs({"dataset": "dataset-alpha", "days": 30})
        assert out == {"dataset": "dataset-alpha", "days": 30}

    def test_applies_defaults(self) -> None:
        spec = _spec_with_inputs(
            {"days": {"type": "integer", "default": 100, "min": 1, "max": 1000}}
        )
        out = spec.validate_inputs({})
        assert out == {"days": 100}

    def test_rejects_unknown_keys(self) -> None:
        spec = _spec_with_inputs({"x": {"type": "integer", "default": 1}})
        with pytest.raises(ValueError, match="Unknown input keys"):
            spec.validate_inputs({"x": 1, "rogue_key": "leak"})

    def test_rejects_wrong_type_string(self) -> None:
        spec = _spec_with_inputs({"x": {"type": "string"}})
        with pytest.raises(ValueError, match="expected string"):
            spec.validate_inputs({"x": 42})

    def test_rejects_wrong_type_integer(self) -> None:
        spec = _spec_with_inputs({"x": {"type": "integer"}})
        with pytest.raises(ValueError, match="expected integer"):
            spec.validate_inputs({"x": "not an int"})

    def test_boolean_not_treated_as_integer(self) -> None:
        # Pythonism: True is instance of int. We explicitly reject bool here.
        spec = _spec_with_inputs({"x": {"type": "integer"}})
        with pytest.raises(ValueError, match="expected integer"):
            spec.validate_inputs({"x": True})

    def test_rejects_value_outside_min(self) -> None:
        spec = _spec_with_inputs({"x": {"type": "integer", "min": 10}})
        with pytest.raises(ValueError, match="below min"):
            spec.validate_inputs({"x": 5})

    def test_rejects_value_outside_max(self) -> None:
        spec = _spec_with_inputs({"x": {"type": "integer", "max": 10}})
        with pytest.raises(ValueError, match="above max"):
            spec.validate_inputs({"x": 15})

    def test_rejects_value_not_in_choices(self) -> None:
        spec = _spec_with_inputs({"x": {"type": "string", "choices": ["a", "b"]}})
        with pytest.raises(ValueError, match="not in choices"):
            spec.validate_inputs({"x": "c"})

    def test_rejects_missing_required(self) -> None:
        spec = _spec_with_inputs({"required_field": {"type": "integer"}})
        with pytest.raises(ValueError, match="Missing required"):
            spec.validate_inputs({})

    def test_accepts_date_iso(self) -> None:
        spec = _spec_with_inputs({"d": {"type": "date"}})
        out = spec.validate_inputs({"d": "2025-01-15"})
        assert out == {"d": "2025-01-15"}

    def test_rejects_date_non_iso(self) -> None:
        spec = _spec_with_inputs({"d": {"type": "date"}})
        with pytest.raises(ValueError, match="date"):
            spec.validate_inputs({"d": "January 15"})

    def test_collects_multiple_errors(self) -> None:
        spec = _spec_with_inputs(
            {
                "a": {"type": "integer"},
                "b": {"type": "string"},
            }
        )
        with pytest.raises(ValueError) as exc_info:
            spec.validate_inputs({"a": "wrong", "b": 42})
        msg = str(exc_info.value)
        assert "a:" in msg
        assert "b:" in msg


# ---------------------------------------------------------------------------
# load_job_spec / load_job_specs
# ---------------------------------------------------------------------------


def _write_spec(tmp_path: Path, **fields) -> Path:
    p = tmp_path / f"{fields['spec_ref']}.yaml"
    p.write_text(yaml.safe_dump(fields), encoding="utf-8")
    return p


class TestLoadJobSpec:
    def test_loads_minimal(self, tmp_path: Path) -> None:
        path = _write_spec(tmp_path, spec_ref="hello", job_type="t", handler="h")
        spec = load_job_spec(path)
        assert spec.spec_ref == "hello"
        assert spec.timeout_seconds == 1800  # default

    def test_loads_with_inputs(self, tmp_path: Path) -> None:
        path = _write_spec(
            tmp_path,
            spec_ref="with_inputs",
            job_type="t",
            handler="h",
            allowed_inputs={
                "x": {"type": "integer", "min": 0, "max": 10},
            },
        )
        spec = load_job_spec(path)
        assert "x" in spec.allowed_inputs
        assert spec.allowed_inputs["x"].max == 10

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_job_spec(tmp_path / "nope.yaml")

    def test_empty_file_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.yaml"
        p.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="Empty"):
            load_job_spec(p)


class TestLoadJobSpecs:
    def test_returns_empty_for_missing_dir(self, tmp_path: Path) -> None:
        assert load_job_specs(tmp_path / "does-not-exist") == {}

    def test_loads_multiple(self, tmp_path: Path) -> None:
        _write_spec(tmp_path, spec_ref="a", job_type="t", handler="h")
        _write_spec(tmp_path, spec_ref="b", job_type="t", handler="h")
        specs = load_job_specs(tmp_path)
        assert set(specs.keys()) == {"a", "b"}

    def test_rejects_duplicate_spec_ref(self, tmp_path: Path) -> None:
        _write_spec(tmp_path, spec_ref="dup", job_type="t", handler="h")
        # Manually write a second file with same spec_ref.
        p2 = tmp_path / "second.yaml"
        p2.write_text(
            yaml.safe_dump({"spec_ref": "dup", "job_type": "t", "handler": "h"}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Duplicate spec_ref"):
            load_job_specs(tmp_path)
