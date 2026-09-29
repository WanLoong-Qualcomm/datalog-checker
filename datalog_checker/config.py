"""Configuration loading and validation for Datalog Checker."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

COVERAGE_CHECK_NAME = "Coverage (Lite)"
LEGACY_COVERAGE_CHECK_NAME = "Coverage"


@dataclass(frozen=True)
class CheckSettings:
    enabled: bool
    threshold: float | None = None
    strict: bool = False
    tolerance: float | None = None


@dataclass(frozen=True)
class Settings:
    checks: dict[str, CheckSettings]
    files: tuple[Path, ...]
    outputs_directory: Path | None = None

    @property
    def gain_threshold(self) -> float:
        gain_settings = self.checks.get("Gain")
        if gain_settings is None or gain_settings.threshold is None:
            return 0.0
        return gain_settings.threshold

    @property
    def temperature_strict(self) -> bool:
        temperature_settings = self.checks.get("Temperature")
        return temperature_settings.strict if temperature_settings else False

    @property
    def temperature_tolerance(self) -> float:
        temperature_settings = self.checks.get("Temperature")
        if temperature_settings is None or temperature_settings.tolerance is None:
            return 10.0
        return temperature_settings.tolerance


def default_check_settings() -> dict[str, CheckSettings]:
    return {
        "Gain": CheckSettings(enabled=True, threshold=0.0),
        "Temperature": CheckSettings(enabled=False, strict=False, tolerance=10.0),
        COVERAGE_CHECK_NAME: CheckSettings(enabled=False),
    }


def parse_gain_threshold(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("Gain threshold must be a finite number.")
    try:
        threshold = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Gain threshold must be a finite number.") from error
    if not math.isfinite(threshold):
        raise ValueError("Gain threshold must be a finite number.")
    return threshold


def parse_temperature_tolerance(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("Temperature tolerance must be a non-negative finite number.")
    try:
        tolerance = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "Temperature tolerance must be a non-negative finite number."
        ) from error
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("Temperature tolerance must be a non-negative finite number.")
    return tolerance


def _read_json(path: Path) -> Any:
    raw_content = path.read_text(encoding="utf-8-sig")
    try:
        return json.loads(raw_content)
    except json.JSONDecodeError as original_error:
        # Accept manifests produced with commas after standalone JSON
        # brackets, for example `{,` and `],`.
        repaired_lines = []
        for line in raw_content.splitlines():
            stripped = line.strip()
            trimmed = line.rstrip()
            if stripped in {"{,", "[,", "],", "},"}:
                line = trimmed[:-1]
            elif trimmed.endswith(("{,", "[,")):
                line = trimmed[:-1]
            repaired_lines.append(line)
        try:
            return json.loads("\n".join(repaired_lines))
        except json.JSONDecodeError:
            raise original_error


def _path_from_item(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("path", "file", "filename", "csv", "filepath"):
            value = item.get(key)
            if isinstance(value, str):
                return value
    return None


def _paths_from_manifest(manifest: Any, manifest_path: Path) -> list[Path]:
    if isinstance(manifest, list):
        items = manifest
    elif isinstance(manifest, dict):
        items = None
        for key in ("files", "paths", "csv_files", "input_files"):
            if key in manifest:
                items = manifest[key]
                break
        if items is None:
            raise ValueError(
                f"{manifest_path} must contain a 'files' list or be a JSON list."
            )
    else:
        raise ValueError(f"{manifest_path} must contain a JSON list or object.")

    if not isinstance(items, list):
        raise ValueError("The manifest file list must be a JSON array.")

    paths: list[Path] = []
    for item in items:
        value = _path_from_item(item)
        if not value:
            raise ValueError(f"Unsupported file entry in {manifest_path}: {item!r}")
        path = Path(value)
        if not path.is_absolute():
            path = manifest_path.parent / path
        paths.append(path)
    return paths


def load_manifest(manifest_path: Path) -> list[Path]:
    return _paths_from_manifest(_read_json(manifest_path), manifest_path)


def _load_outputs_directory(raw_settings: dict[str, Any], settings_path: Path) -> Path | None:
    raw_directory = raw_settings.get("outputs_directory")
    if raw_directory is None:
        # Accept the misspelled field from the previous working configuration
        # while using the corrected name for all new manifests.
        raw_directory = raw_settings.get("outputs_dirctory")
    if raw_directory is None or raw_directory == "":
        return None
    if not isinstance(raw_directory, str) or not raw_directory.strip():
        raise ValueError(f"{settings_path} 'outputs_directory' must be a path string.")

    output_path = Path(raw_directory)
    if not output_path.is_absolute():
        output_path = settings_path.parent / output_path
    return output_path


def _load_checks(
    raw_checks: Any,
    settings_path: Path,
    legacy_gain_threshold: float,
) -> dict[str, CheckSettings]:
    if isinstance(raw_checks, dict):
        check_items = list(raw_checks.items())
    elif isinstance(raw_checks, list):
        # Read the previous list-of-single-key-objects shape during migration.
        check_items = []
        for item in raw_checks:
            if not isinstance(item, dict) or len(item) != 1:
                raise ValueError(
                    f"{settings_path} legacy check entries must contain one item."
                )
            check_items.extend(item.items())
    else:
        raise ValueError(f"{settings_path} 'checks' must be a JSON object.")

    checks: dict[str, CheckSettings] = {}
    for raw_name, raw_check in check_items:
        name = (
            COVERAGE_CHECK_NAME
            if raw_name == LEGACY_COVERAGE_CHECK_NAME
            else raw_name
        )
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{settings_path} contains an invalid check name.")
        if name in checks:
            raise ValueError(f"{settings_path} contains a duplicate check name: {name}.")

        if isinstance(raw_check, str) and raw_check.strip().lower() in {"true", "false"}:
            raw_check = raw_check.strip().lower() == "true"
        if isinstance(raw_check, bool):
            checks[name] = CheckSettings(
                enabled=raw_check,
                threshold=legacy_gain_threshold if name == "Gain" else None,
                tolerance=10.0 if name == "Temperature" else None,
            )
            continue
        if not isinstance(raw_check, dict):
            raise ValueError(
                f"{settings_path} check '{name}' must be an object with an 'enabled' field."
            )

        enabled = raw_check.get("enabled")
        if isinstance(enabled, str) and enabled.strip().lower() in {"true", "false"}:
            enabled = enabled.strip().lower() == "true"
        if not isinstance(enabled, bool):
            raise ValueError(
                f"{settings_path} check '{name}' 'enabled' must be a JSON boolean."
            )

        threshold: float | None = None
        strict = False
        tolerance: float | None = None
        if name == "Gain":
            threshold = parse_gain_threshold(
                raw_check.get("threshold", legacy_gain_threshold)
            )
            if "strict" in raw_check or "tolerance" in raw_check:
                raise ValueError(
                    f"only the 'Temperature' check may define 'strict' or 'tolerance' in {settings_path}."
                )
        elif name == "Temperature":
            strict = raw_check.get("strict", False)
            if isinstance(strict, str) and strict.strip().lower() in {"true", "false"}:
                strict = strict.strip().lower() == "true"
            if not isinstance(strict, bool):
                raise ValueError(
                    f"{settings_path} check 'Temperature' 'strict' must be a JSON boolean."
                )
            tolerance = parse_temperature_tolerance(raw_check.get("tolerance", 10))
            if "threshold" in raw_check:
                raise ValueError(
                    f"{settings_path} only the 'Gain' check may define 'threshold'."
                )
        elif "threshold" in raw_check or "strict" in raw_check or "tolerance" in raw_check:
            raise ValueError(
                f"{settings_path} check '{name}' has unsupported options."
            )
        checks[name] = CheckSettings(
            enabled=enabled,
            threshold=threshold,
            strict=strict,
            tolerance=tolerance,
        )
    return checks


def load_settings(settings_path: Path) -> Settings:
    raw_settings = _read_json(settings_path)
    if isinstance(raw_settings, list):
        return Settings(
            checks={},
            files=tuple(_paths_from_manifest(raw_settings, settings_path)),
        )
    if not isinstance(raw_settings, dict):
        raise ValueError(f"{settings_path} must contain a JSON object.")

    legacy_gain_threshold = parse_gain_threshold(raw_settings.get("gain_threshold", 0))
    checks = _load_checks(
        raw_settings.get("checks", {}),
        settings_path,
        legacy_gain_threshold,
    )
    files = tuple(_paths_from_manifest(raw_settings, settings_path))
    outputs_directory = _load_outputs_directory(raw_settings, settings_path)
    return Settings(
        checks=checks,
        files=files,
        outputs_directory=outputs_directory,
    )
