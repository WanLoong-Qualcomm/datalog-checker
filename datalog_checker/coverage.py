"""Lite sequence-to-datalog coverage analysis."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Iterable

from datalog_checker.coverage_config import TestNameMapping


class CoverageError(ValueError):
    """Raised when coverage input files cannot be interpreted."""


_TOKEN_SEPARATOR = re.compile(r"[;/|,]+")


def _canonical_header(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", value.strip().upper()).strip("_")


def _normalise_value(value: str) -> str:
    return value.strip().upper()


def _split_tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in (_normalise_value(item) for item in _TOKEN_SEPARATOR.split(value))
        if token
    )


def _find_column(header: list[str], *names: str) -> int | None:
    positions = {_canonical_header(name): index for index, name in enumerate(header)}
    for name in names:
        index = positions.get(_canonical_header(name))
        if index is not None:
            return index
    return None


def _require_columns(
    header: list[str], required: dict[str, tuple[str, ...]], source: Path
) -> dict[str, int]:
    columns: dict[str, int] = {}
    missing: list[str] = []
    for name, aliases in required.items():
        index = _find_column(header, *aliases)
        if index is None:
            missing.append(name)
        else:
            columns[name] = index
    if missing:
        raise CoverageError(
            f"{source} is missing coverage column(s): {', '.join(missing)}."
        )
    return columns


@dataclass(frozen=True)
class CoverageGap:
    """A missing sequence configuration combination."""

    main_script_key: str
    gain_modes: tuple[str, ...]
    tests: tuple[str, ...]

    @property
    def gain_modes_text(self) -> str:
        return ";".join(self.gain_modes)

    @property
    def tests_text(self) -> str:
        return ";".join(self.tests)

    @property
    def configuration_style(self) -> str:
        return f"({self.main_script_key}, {self.gain_modes_text}, {self.tests_text})"

    @property
    def copy_text(self) -> str:
        """Return the gap as clean comma-separated configuration fields."""
        return f"{self.main_script_key},{self.gain_modes_text},{self.tests_text}"


@dataclass(frozen=True)
class CoverageLabelResult:
    """Coverage totals and gaps for one sequence configuration label."""

    label: str
    required_count: int
    covered_count: int
    gaps: tuple[CoverageGap, ...]

    @property
    def fully_covered(self) -> bool:
        return self.required_count > 0 and not self.gaps

    @property
    def coverage_ratio(self) -> Fraction:
        if self.required_count == 0:
            return Fraction(0, 1)
        return Fraction(self.covered_count, self.required_count)


@dataclass(frozen=True)
class CoverageEvaluationGroup:
    """Tied labels that have the same coverage result."""

    labels: tuple[str, ...]
    evaluation: CoverageLabelResult


@dataclass(frozen=True)
class CoverageResult:
    """Coverage analysis for one datalog file."""

    dut_sn: str
    csv_file: str
    sequence_path: str
    configuration_path: str
    selected_labels: tuple[str, ...]
    evaluations: tuple[CoverageLabelResult, ...]

    @property
    def selected_evaluations(self) -> tuple[CoverageLabelResult, ...]:
        selected = set(self.selected_labels)
        return tuple(result for result in self.evaluations if result.label in selected)

    @property
    def selected_evaluation_groups(self) -> tuple[CoverageEvaluationGroup, ...]:
        """Group selected labels only when their coverage results are identical."""
        selected = set(self.selected_labels)
        grouped: dict[
            tuple[int, int, tuple[CoverageGap, ...]],
            tuple[list[str], CoverageLabelResult],
        ] = {}
        for evaluation in self.evaluations:
            if evaluation.label not in selected:
                continue
            key = (
                evaluation.required_count,
                evaluation.covered_count,
                evaluation.gaps,
            )
            if key not in grouped:
                grouped[key] = ([], evaluation)
            grouped[key][0].append(evaluation.label)

        return tuple(
            CoverageEvaluationGroup(labels=tuple(labels), evaluation=evaluation)
            for labels, evaluation in grouped.values()
        )

    @property
    def gaps(self) -> tuple[CoverageGap, ...]:
        unique_gaps: dict[CoverageGap, None] = {}
        for evaluation in self.selected_evaluations:
            for gap in evaluation.gaps:
                unique_gaps.setdefault(gap, None)
        return tuple(unique_gaps)

    @property
    def fully_covered(self) -> bool:
        return all(evaluation.fully_covered for evaluation in self.selected_evaluations)

    @property
    def ambiguity_warning(self) -> str | None:
        """Return a warning when multiple labels share the best percentage."""
        if len(self.selected_labels) < 2:
            return None
        selected_evaluations = self.selected_evaluations
        if not selected_evaluations:
            return None
        coverage = float(selected_evaluations[0].coverage_ratio)
        labels = ", ".join(self.selected_labels)
        return (
            f"Multiple candidate labels tie at {coverage:.1%} coverage: "
            f"{labels}."
        )


@dataclass(frozen=True)
class _Requirement:
    main_script_key: str
    gain_mode: str
    tests: str
    tname: str


def _read_sequence_configuration(
    path: Path,
    mapping: TestNameMapping,
) -> dict[str, tuple[_Requirement, ...]]:
    with path.open(
        "r", newline="", encoding="utf-8-sig", errors="replace"
    ) as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as error:
            raise CoverageError(f"{path} is empty.") from error

        columns = _require_columns(
            header,
            {
                "Label": ("Label",),
                "Main_Script_Key": ("Main_Script_Key", "Main Script Key"),
                "Config_GainMode": ("Config_GainMode",),
                "TESTS": ("TESTS",),
            },
            path,
        )
        requirements: dict[str, list[_Requirement]] = {}
        seen: dict[str, set[tuple[str, str, str, str]]] = {}
        for row in reader:
            max_index = max(columns.values())
            if len(row) <= max_index:
                continue
            label = row[columns["Label"]].strip()
            main_script_key = row[columns["Main_Script_Key"]].strip()
            raw_gain_modes = row[columns["Config_GainMode"]].strip()
            raw_tests = row[columns["TESTS"]].strip()
            if not label and not any((main_script_key, raw_gain_modes, raw_tests)):
                continue
            if not label:
                raise CoverageError(f"{path} has a configuration row without Label.")
            if not main_script_key or not raw_gain_modes or not raw_tests:
                raise CoverageError(
                    f"{path} has an incomplete coverage row for label '{label}'."
                )

            label_key = _normalise_value(label)
            requirements.setdefault(label_key, [])
            seen.setdefault(label_key, set())
            gain_modes = _split_tokens(raw_gain_modes)
            tests_names = _split_tokens(raw_tests)
            if not gain_modes or not tests_names:
                raise CoverageError(
                    f"{path} has an incomplete coverage row for label '{label}'."
                )
            for tests_name in tests_names:
                tnames = mapping.tnames_for(tests_name)
                for gain_mode in gain_modes:
                    for tname in tnames:
                        requirement_key = (
                            _normalise_value(main_script_key),
                            gain_mode,
                            tests_name,
                            tname,
                        )
                        if requirement_key in seen[label_key]:
                            continue
                        seen[label_key].add(requirement_key)
                        requirements[label_key].append(
                            _Requirement(
                                main_script_key=main_script_key,
                                gain_mode=gain_mode,
                                tests=tests_name,
                                tname=tname,
                            )
                        )

    if not requirements:
        raise CoverageError(f"{path} contains no coverage configuration rows.")
    return {label: tuple(items) for label, items in requirements.items()}


def _read_datalog(
    path: Path,
) -> tuple[str, str, set[tuple[str, str, str]]]:
    with path.open(
        "r", newline="", encoding="utf-8-sig", errors="replace"
    ) as handle:
        reader = csv.reader(handle)
        header: list[str] | None = None
        sequence_path = ""
        for row in reader:
            if row and row[0].strip().upper().startswith("SEQUENCE_FILE="):
                sequence_path = row[0].split("=", 1)[1].strip().strip('"')
            if row and row[0].strip().upper() == "STEP_NAME=":
                header = row
                break
        if header is None:
            raise CoverageError(f"{path} does not contain a STEP_NAME data header.")

        columns = _require_columns(
            header,
            {
                "Main_Script_Key": ("TCOND=MAIN_SCRIPT_KEY",),
                "Config_GainMode": ("TCOND=CONFIG_GAINMODE_RX",),
                "TNAME": ("TNAME",),
                "DUT_SN": ("TCOND=DUT_SN", "DUT_SN"),
            },
            path,
        )
        seen: set[tuple[str, str, str]] = set()
        dut_names: list[str] = []
        required_index = max(columns.values())
        for row in reader:
            if len(row) <= required_index:
                continue
            dut_sn = row[columns["DUT_SN"]].strip()
            if dut_sn and dut_sn not in dut_names:
                dut_names.append(dut_sn)
            main_script_key = _normalise_value(row[columns["Main_Script_Key"]])
            gain_mode = _normalise_value(row[columns["Config_GainMode"]])
            tname = _normalise_value(row[columns["TNAME"]])
            if main_script_key and gain_mode and tname:
                seen.add((main_script_key, gain_mode, tname))

    dut_sn = dut_names[0] if dut_names else "UNKNOWN"
    if len(dut_names) > 1:
        dut_sn = "; ".join(dut_names)
    if not sequence_path:
        raise CoverageError(f"{path} does not declare a SEQUENCE_FILE path.")
    return dut_sn, sequence_path, seen


def _resolve_configuration_path(datalog_path: Path, sequence_path: str) -> Path:
    sequence = Path(sequence_path)
    candidates: list[Path] = []
    if sequence.is_absolute():
        candidates.append(sequence.with_suffix(".csv"))
    else:
        candidates.append((datalog_path.parent / sequence).with_suffix(".csv"))
    candidates.append(datalog_path.parent / f"{sequence.stem}.csv")

    checked: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in checked:
            continue
        checked.append(resolved)
        if resolved.is_file():
            return resolved
    expected = ", ".join(str(path) for path in checked)
    raise CoverageError(
        f"Sequence configuration CSV for '{sequence_path}' was not found. "
        f"Expected one of: {expected}."
    )


def _evaluate_label(
    label: str,
    requirements: tuple[_Requirement, ...],
    seen: set[tuple[str, str, str]],
) -> CoverageLabelResult:
    covered_count = sum(
        (
            _normalise_value(requirement.main_script_key),
            requirement.gain_mode,
            requirement.tname,
        )
        in seen
        for requirement in requirements
    )
    missing_modes: dict[tuple[str, str], list[str]] = {}
    display_values: dict[tuple[str, str], tuple[str, str]] = {}
    for requirement in requirements:
        requirement_key = (
            _normalise_value(requirement.main_script_key),
            requirement.gain_mode,
            requirement.tname,
        )
        if requirement_key in seen:
            continue
        group_key = (
            _normalise_value(requirement.main_script_key),
            requirement.tests,
        )
        display_values.setdefault(
            group_key, (requirement.main_script_key, requirement.tests)
        )
        modes = missing_modes.setdefault(group_key, [])
        if requirement.gain_mode not in modes:
            modes.append(requirement.gain_mode)

    recombined_tests: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    recombined_display_values: dict[
        tuple[str, tuple[str, ...]], tuple[str, tuple[str, ...]]
    ] = {}
    for group_key in sorted(missing_modes):
        main_script_key, tests_name = display_values[group_key]
        missing_gain_modes = tuple(missing_modes[group_key])
        recombination_key = (
            _normalise_value(main_script_key),
            missing_gain_modes,
        )
        recombined_tests.setdefault(recombination_key, []).append(tests_name)
        recombined_display_values.setdefault(
            recombination_key,
            (main_script_key, missing_gain_modes),
        )

    gaps = tuple(
        CoverageGap(
            main_script_key=recombined_display_values[key][0],
            gain_modes=recombined_display_values[key][1],
            tests=tuple(recombined_tests[key]),
        )
        for key in sorted(recombined_tests)
    )
    return CoverageLabelResult(
        label=label,
        required_count=len(requirements),
        covered_count=covered_count,
        gaps=gaps,
    )


def _select_labels(evaluations: tuple[CoverageLabelResult, ...]) -> tuple[str, ...]:
    if not evaluations:
        return ()
    best_ratio = max(evaluation.coverage_ratio for evaluation in evaluations)
    return tuple(
        evaluation.label
        for evaluation in evaluations
        if evaluation.coverage_ratio == best_ratio
    )


def scan_coverage_file(
    path: Path,
    display_name: str,
    mapping: TestNameMapping,
) -> CoverageResult:
    """Compare one datalog against every label in its sequence configuration."""
    dut_sn, sequence_path, seen = _read_datalog(path)
    configuration_path = _resolve_configuration_path(path, sequence_path)
    requirements_by_label = _read_sequence_configuration(configuration_path, mapping)
    evaluations = tuple(
        _evaluate_label(label, requirements, seen)
        for label, requirements in sorted(requirements_by_label.items())
    )
    selected_labels = _select_labels(evaluations)
    return CoverageResult(
        dut_sn=dut_sn,
        csv_file=display_name,
        sequence_path=sequence_path,
        configuration_path=str(configuration_path),
        selected_labels=selected_labels,
        evaluations=evaluations,
    )


def coverage_failures(
    results: Iterable[CoverageResult],
) -> list[CoverageResult]:
    """Return only coverage assessments with gaps for report generation."""
    return [result for result in results if result.gaps]
