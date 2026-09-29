"""Configuration for mapping sequence TESTS values to datalog TNAME values."""

from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CoverageConfigError(ValueError):
    """Raised when the coverage mapping configuration is invalid."""


DEFAULT_TEMPERATURE_MEASUREMENTS = ("THERM_DIODE_HKADC_TEMP",)
_EXCLUSION_TOKEN_SEPARATOR = re.compile(r"[;/|,]+")


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


@dataclass(frozen=True)
class CoverageExclusion:
    """One set of SSF values for which coverage requirements are omitted."""

    temperatures: tuple[str, ...]
    main_script_keys: tuple[str, ...]
    gain_modes: tuple[str, ...]
    tests: tuple[str, ...]

    @staticmethod
    def _matches(patterns: tuple[str, ...], value: str) -> bool:
        normalized = value.strip().upper()
        return "*" in patterns or normalized in patterns

    def matches(
        self,
        temperature: str,
        main_script_key: str,
        gain_mode: str,
        tests: str,
    ) -> bool:
        return (
            self._matches(self.temperatures, temperature)
            and self._matches(self.main_script_keys, main_script_key)
            and self._matches(self.gain_modes, gain_mode)
            and self._matches(self.tests, tests)
        )


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


def _split_exclusion_values(value: str) -> tuple[str, ...]:
    return tuple(
        token.strip().upper()
        for token in _EXCLUSION_TOKEN_SEPARATOR.split(value)
        if token.strip()
    )


def _find_config_column(header: list[str], name: str) -> int | None:
    normalized_name = name.strip().upper()
    for index, value in enumerate(header):
        if value.strip().upper() == normalized_name:
            return index
    return None


def load_coverage_exclusions(
    path: Path | None = None,
) -> tuple[CoverageExclusion, ...]:
    """Load SSF rows that should be omitted from coverage requirements."""
    config_path = path or default_coverage_config_path()
    raw = _load_config(config_path)
    raw_exclusions_path = raw.get("coverage_exclusions_path")
    if raw_exclusions_path is None or raw_exclusions_path == "":
        return ()
    if not isinstance(raw_exclusions_path, str) or not raw_exclusions_path.strip():
        raise CoverageConfigError(
            f"{config_path} 'coverage_exclusions_path' must be a path string."
        )

    exclusions_path = Path(raw_exclusions_path)
    if not exclusions_path.is_absolute():
        exclusions_path = config_path.parent / exclusions_path

    try:
        with exclusions_path.open(
            "r", newline="", encoding="utf-8-sig", errors="replace"
        ) as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration as error:
                raise CoverageConfigError(
                    f"{exclusions_path} is empty."
                ) from error

            columns: dict[str, int] = {}
            missing: list[str] = []
            for name in (
                "Temperature",
                "Main_Script_Key",
                "Config_GainMode",
                "TESTS",
            ):
                index = _find_config_column(header, name)
                if index is None:
                    missing.append(name)
                else:
                    columns[name] = index
            if missing:
                raise CoverageConfigError(
                    f"{exclusions_path} is missing coverage exclusion column(s): "
                    f"{', '.join(missing)}."
                )

            exclusions: list[CoverageExclusion] = []
            for row in reader:
                if not row or not any(value.strip() for value in row):
                    continue
                if len(row) <= max(columns.values()):
                    raise CoverageConfigError(
                        f"{exclusions_path} contains an incomplete exclusion row."
                    )
                values = {
                    name: _split_exclusion_values(row[index])
                    for name, index in columns.items()
                }
                if any(not values[name] for name in columns):
                    raise CoverageConfigError(
                        f"{exclusions_path} contains an exclusion row with a blank field."
                    )
                exclusions.append(
                    CoverageExclusion(
                        temperatures=values["Temperature"],
                        main_script_keys=values["Main_Script_Key"],
                        gain_modes=values["Config_GainMode"],
                        tests=values["TESTS"],
                    )
                )
    except OSError as error:
        raise CoverageConfigError(
            f"Could not read coverage exclusions file '{exclusions_path}': {error}"
        ) from error

    return tuple(exclusions)
