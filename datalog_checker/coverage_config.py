"""Configuration for mapping sequence TESTS values to datalog TNAME values."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CoverageConfigError(ValueError):
    """Raised when the coverage mapping configuration is invalid."""


DEFAULT_TEMPERATURE_MEASUREMENTS = ("THERM_DIODE_HKADC_TEMP",)


def _normalise_name(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CoverageConfigError(f"{field_name} must be a non-empty string.")
    return value.strip().upper()


def _load_config(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise CoverageConfigError(
            f"{path} is not valid JSON: {error.msg}."
        ) from error

    if not isinstance(raw, dict):
        raise CoverageConfigError(f"{path} must contain a JSON object.")
    return raw


@dataclass(frozen=True)
class TestNameMapping:
    """Normalized mapping from sequence TESTS names to datalog TNAME names."""

    tests_to_tnames: dict[str, tuple[str, ...]]

    def tnames_for(self, tests_name: str) -> tuple[str, ...]:
        normalized = _normalise_name(tests_name, "TESTS mapping key")
        try:
            return self.tests_to_tnames[normalized]
        except KeyError as error:
            raise CoverageConfigError(
                f"No TNAME mapping is configured for sequence TESTS '{tests_name}'."
            ) from error


def default_coverage_config_path() -> Path:
    """Return the source or bundled default coverage mapping path."""
    if getattr(sys, "frozen", False):
        executable_config = Path(sys.executable).resolve().parent / "config.json"
        if executable_config.exists():
            return executable_config
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return bundle_root / "config.json"
    return Path(__file__).resolve().parents[1] / "config.json"


def load_test_name_mapping(path: Path | None = None) -> TestNameMapping:
    """Load and validate the TESTS-to-TNAME mapping from ``config.json``."""
    config_path = path or default_coverage_config_path()
    raw = _load_config(config_path)
    raw_mapping = raw.get("tests_to_tnames")
    if not isinstance(raw_mapping, dict) or not raw_mapping:
        raise CoverageConfigError(
            f"{config_path} must contain a non-empty 'tests_to_tnames' object."
        )

    mapping: dict[str, tuple[str, ...]] = {}
    for raw_key, raw_values in raw_mapping.items():
        key = _normalise_name(raw_key, "TESTS mapping key")
        if key in mapping:
            raise CoverageConfigError(
                f"{config_path} contains duplicate TESTS mapping key '{raw_key}'."
            )
        if isinstance(raw_values, str):
            values = [raw_values]
        elif isinstance(raw_values, list):
            values = raw_values
        else:
            raise CoverageConfigError(
                f"Mapping for TESTS '{raw_key}' must be a string or an array of strings."
            )
        if not values:
            raise CoverageConfigError(
                f"Mapping for TESTS '{raw_key}' must not be empty."
            )

        normalized_values: list[str] = []
        for raw_value in values:
            value = _normalise_name(raw_value, f"TNAME mapping for '{raw_key}'")
            if value not in normalized_values:
                normalized_values.append(value)
        mapping[key] = tuple(normalized_values)

    return TestNameMapping(mapping)


def load_temperature_measurement_names(
    path: Path | None = None,
) -> tuple[str, ...]:
    """Load the TNAME values used by the temperature check.

    Older configuration files may omit this field; those files retain the
    original single-measurement behavior.
    """
    config_path = path or default_coverage_config_path()
    raw = _load_config(config_path)
    raw_names = raw.get("temperature_measurements")
    if raw_names is None:
        return DEFAULT_TEMPERATURE_MEASUREMENTS
    if not isinstance(raw_names, list) or not raw_names:
        raise CoverageConfigError(
            f"{config_path} must contain a non-empty 'temperature_measurements' list."
        )

    names: list[str] = []
    for raw_name in raw_names:
        name = _normalise_name(raw_name, "Temperature measurement name")
        if name not in names:
            names.append(name)
    return tuple(names)
