"""Datalog scanning and report generation."""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from datalog_checker.coverage import CoverageResult

GAIN_NAMES = {"GAIN", "GAIN_I", "GAIN_Q"}
TEMPERATURE_NAME = "THERM_DIODE_HKADC_TEMP"
ROOM_TEMPERATURE_MIN = 25.0
ROOM_TEMPERATURE_MAX = 30.0
INPUT_PORT_COLUMNS = (
    "MCOND=RFIN_PORT",
    "MCOND=BBOUT_PATH",
)


@dataclass(frozen=True)
class NegativeGain:
    dut_sn: str
    csv_file: str
    sequence_path: str
    line_number: int
    metric: str
    value: float
    ports: tuple[tuple[str, str], ...]
    all_gain_values: tuple[float, ...]


@dataclass(frozen=True)
class PortFailure:
    dut_sn: str
    csv_file: str
    sequence_path: str
    input_port: str
    debug_port_path: str
    negative_rows: int
    worst_gain: float
    best_gain: float
    metrics: tuple[str, ...]


@dataclass(frozen=True)
class TemperatureMeasurement:
    dut_sn: str
    csv_file: str
    sequence_path: str
    line_number: int
    value: float
    setpoint: float | None
    expected_min: float
    expected_max: float

    @property
    def within_spec(self) -> bool:
        return self.expected_min <= self.value <= self.expected_max


@dataclass(frozen=True)
class TemperatureFailure:
    dut_sn: str
    csv_file: str
    sequence_path: str
    strict: bool
    measurements: tuple[TemperatureMeasurement, ...]
    out_of_spec: tuple[TemperatureMeasurement, ...]
    reason: str = ""


def format_input_port(port: str) -> str:
    shorthand = port.strip().upper()
    if len(shorthand) > 1 and shorthand.startswith("M") and shorthand[1] in {"D", "P", "U"}:
        return shorthand[1:]
    return shorthand


def _find_index(header: list[str], *names: str) -> int | None:
    positions = {name.strip().upper(): index for index, name in enumerate(header)}
    for name in names:
        index = positions.get(name.upper())
        if index is not None:
            return index
    return None


def _parse_number(value: str) -> float | None:
    cleaned = value.strip().replace(",", "")
    if not cleaned:
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def display_path(path: Path, manifest_path: Path) -> str:
    try:
        return str(path.relative_to(manifest_path.parent))
    except ValueError:
        return str(path)


def scan_file(
    path: Path,
    display_name: str,
    gain_threshold: float = 0.0,
) -> list[NegativeGain]:
    with path.open("r", newline="", encoding="utf-8-sig", errors="replace") as handle:
        reader = csv.reader(handle)

        header: list[str] | None = None
        sequence_path = ""
        for row in reader:
            if row and row[0].strip().upper().startswith("SEQUENCE_FILE="):
                sequence_path = row[0].split("=", 1)[1].strip()
            if row and row[0].strip().upper() == "STEP_NAME=":
                header = row
                break

        if header is None:
            raise ValueError("could not find the STEP_NAME data header")

        dut_index = _find_index(header, "TCOND=DUT_SN", "DUT_SN")
        name_index = _find_index(header, "TNAME", "NAME")
        data_index = _find_index(header, "TDATA", "DATA", "VALUE")
        if dut_index is None or name_index is None or data_index is None:
            raise ValueError("missing one of TCOND=DUT_SN, TNAME, or TDATA columns")

        port_indices = [
            (header_name, index)
            for header_name in INPUT_PORT_COLUMNS
            if (index := _find_index(header, header_name)) is not None
        ]

        low_gain_rows: list[NegativeGain] = []
        all_gain_values: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)
        for row in reader:
            required_index = max(dut_index, name_index, data_index)
            if len(row) <= required_index:
                continue

            metric = row[name_index].strip().upper()
            if metric not in GAIN_NAMES:
                continue

            value = _parse_number(row[data_index])
            if value is None:
                continue

            ports: list[tuple[str, str]] = []
            for port_type, index in port_indices:
                if index < len(row):
                    port = row[index].strip()
                    if port and (port_type, port) not in ports:
                        ports.append((port_type, port))

            dut_sn = row[dut_index].strip() or "UNKNOWN"
            port_key = tuple(ports)
            all_gain_values[(dut_sn, port_key)].append(value)
            if value >= gain_threshold:
                continue

            low_gain_rows.append(
                NegativeGain(
                    dut_sn=dut_sn,
                    csv_file=display_name,
                    sequence_path=sequence_path,
                    line_number=reader.line_num,
                    metric=metric,
                    value=value,
                    ports=port_key,
                    all_gain_values=(),
                )
            )

        return [
            NegativeGain(
                dut_sn=result.dut_sn,
                csv_file=result.csv_file,
                sequence_path=result.sequence_path,
                line_number=result.line_number,
                metric=result.metric,
                value=result.value,
                ports=result.ports,
                all_gain_values=tuple(all_gain_values[(result.dut_sn, result.ports)]),
            )
            for result in low_gain_rows
        ]


def _expected_temperature_range(
    setpoint: float | None,
    tolerance: float,
) -> tuple[float, float]:
    if setpoint is None:
        return (
            ROOM_TEMPERATURE_MIN - tolerance,
            ROOM_TEMPERATURE_MAX + tolerance,
        )
    return setpoint - tolerance, setpoint + tolerance


def scan_temperature_file(
    path: Path,
    display_name: str,
    tolerance: float = 10.0,
    strict: bool = False,
) -> list[TemperatureFailure]:
    with path.open("r", newline="", encoding="utf-8-sig", errors="replace") as handle:
        reader = csv.reader(handle)

        header: list[str] | None = None
        sequence_path = ""
        for row in reader:
            if row and row[0].strip().upper().startswith("SEQUENCE_FILE="):
                sequence_path = row[0].split("=", 1)[1].strip()
            if row and row[0].strip().upper() == "STEP_NAME=":
                header = row
                break

        if header is None:
            raise ValueError("could not find the STEP_NAME data header")

        dut_index = _find_index(header, "TCOND=DUT_SN", "DUT_SN")
        name_index = _find_index(header, "TNAME", "NAME")
        data_index = _find_index(header, "TDATA", "DATA", "VALUE")
        temperature_index = _find_index(header, "TCOND=TEMPERATURE")
        if dut_index is None or name_index is None or data_index is None:
            raise ValueError("missing one of TCOND=DUT_SN, TNAME, or TDATA columns")

        grouped: dict[tuple[str, str, str], list[TemperatureMeasurement]] = defaultdict(list)
        for row in reader:
            required_index = max(dut_index, name_index, data_index)
            if len(row) <= required_index:
                continue
            if row[name_index].strip().upper() != TEMPERATURE_NAME:
                continue

            value = _parse_number(row[data_index])
            if value is None:
                continue
            setpoint = (
                _parse_number(row[temperature_index])
                if temperature_index is not None and temperature_index < len(row)
                else None
            )
            expected_min, expected_max = _expected_temperature_range(
                setpoint, tolerance
            )
            dut_sn = row[dut_index].strip() or "UNKNOWN"
            measurement = TemperatureMeasurement(
                dut_sn=dut_sn,
                csv_file=display_name,
                sequence_path=sequence_path,
                line_number=reader.line_num,
                value=value,
                setpoint=setpoint,
                expected_min=expected_min,
                expected_max=expected_max,
            )
            grouped[(dut_sn, display_name, sequence_path)].append(measurement)

    if not grouped:
        return [
            TemperatureFailure(
                dut_sn="UNKNOWN",
                csv_file=display_name,
                sequence_path=sequence_path,
                strict=strict,
                measurements=(),
                out_of_spec=(),
                reason=f"No {TEMPERATURE_NAME} measurements were found.",
            )
        ]

    failures: list[TemperatureFailure] = []
    for (dut_sn, csv_file, sequence_path), measurements in grouped.items():
        out_of_spec = tuple(
            measurement for measurement in measurements if not measurement.within_spec
        )
        failed = bool(out_of_spec) if strict else len(out_of_spec) == len(measurements)
        if failed:
            failures.append(
                TemperatureFailure(
                    dut_sn=dut_sn,
                    csv_file=csv_file,
                    sequence_path=sequence_path,
                    strict=strict,
                    measurements=tuple(measurements),
                    out_of_spec=out_of_spec,
                )
            )
    return failures


def group_by_dut(results: Iterable[NegativeGain]) -> dict[str, list[NegativeGain]]:
    grouped: dict[str, list[NegativeGain]] = defaultdict(list)
    for result in results:
        grouped[result.dut_sn].append(result)
    return dict(sorted(grouped.items()))


def summarize_port_failures(results: Iterable[NegativeGain]) -> list[PortFailure]:
    grouped: dict[tuple[str, str, str, str], list[NegativeGain]] = defaultdict(list)
    for result in results:
        raw_ports: list[str] = []
        for _, raw_port in result.ports:
            raw_port = raw_port.strip().upper()
            if raw_port not in raw_ports:
                raw_ports.append(raw_port)
        if raw_ports:
            debug_port_path = ", ".join(raw_ports)
            key = (result.dut_sn, result.csv_file, result.sequence_path, debug_port_path)
            grouped[key].append(result)

    failures: list[PortFailure] = []
    for (dut_sn, csv_file, sequence_path, debug_port_path), port_results in grouped.items():
        raw_rfin_port = debug_port_path.split(", ", 1)[0]
        input_port = f"{format_input_port(raw_rfin_port)} ({raw_rfin_port})"
        failures.append(
            PortFailure(
                dut_sn=dut_sn,
                csv_file=csv_file,
                sequence_path=sequence_path,
                input_port=input_port,
                debug_port_path=debug_port_path,
                negative_rows=len(port_results),
                worst_gain=min(
                    value
                    for result in port_results
                    for value in result.all_gain_values
                ),
                best_gain=max(
                    value
                    for result in port_results
                    for value in result.all_gain_values
                ),
                metrics=tuple(sorted({result.metric for result in port_results})),
            )
        )
    return sorted(
        failures,
        key=lambda failure: (
            failure.dut_sn,
            failure.csv_file,
            failure.input_port,
        ),
    )


def flagged_dut_names(
    results: Iterable[NegativeGain],
    temperature_failures: Iterable[TemperatureFailure] = (),
    coverage_results: Iterable[CoverageResult] = (),
) -> set[str]:
    names = {result.dut_sn for result in results}
    names.update(failure.dut_sn for failure in temperature_failures)
    names.update(result.dut_sn for result in coverage_results if result.gaps)
    return names


def _coverage_report_results(
    results: Iterable[CoverageResult],
) -> Iterable[CoverageResult]:
    """Expand tied coverage results when their coverage details differ."""
    for result in results:
        for group in result.selected_evaluation_groups:
            yield CoverageResult(
                dut_sn=result.dut_sn,
                csv_file=result.csv_file,
                sequence_path=result.sequence_path,
                configuration_path=result.configuration_path,
                selected_labels=group.labels,
                evaluations=(group.evaluation,),
            )


def write_csv_report(
    path: Path,
    failures: list[PortFailure],
    temperature_failures: list[TemperatureFailure] | None = None,
    coverage_results: list[CoverageResult] | None = None,
) -> None:
    """Write gain, temperature, and coverage findings to one CSV report."""
    temperature_failures = temperature_failures or []
    coverage_results = coverage_results or []
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[tuple[str, ...], list[object]]] = []

    for failure in failures:
        rows.append(
            (
                (
                    failure.dut_sn,
                    failure.csv_file,
                    failure.sequence_path,
                    "Gain",
                    failure.input_port,
                ),
                [
                    failure.dut_sn,
                    "Gain",
                    "YES",
                    failure.csv_file,
                    failure.sequence_path,
                    failure.input_port,
                    failure.debug_port_path,
                    failure.negative_rows,
                    failure.worst_gain,
                    failure.best_gain,
                    ", ".join(failure.metrics),
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                ],
            )
        )

    for failure in temperature_failures:
        if not failure.measurements:
            rows.append(
                (
                    (
                        failure.dut_sn,
                        failure.csv_file,
                        failure.sequence_path,
                        "Temperature",
                        "",
                    ),
                    [
                        failure.dut_sn,
                        "Temperature",
                        "YES",
                        failure.csv_file,
                        failure.sequence_path,
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "YES" if failure.strict else "NO",
                        "",
                        "",
                        "",
                        "",
                        "NO",
                        failure.reason,
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                    ],
                )
            )
            continue

        for measurement in failure.measurements:
            rows.append(
                (
                    (
                        failure.dut_sn,
                        failure.csv_file,
                        failure.sequence_path,
                        "Temperature",
                        str(measurement.line_number),
                    ),
                    [
                        failure.dut_sn,
                        "Temperature",
                        "YES",
                        failure.csv_file,
                        failure.sequence_path,
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "YES" if failure.strict else "NO",
                        measurement.value,
                        measurement.setpoint if measurement.setpoint is not None else "",
                        measurement.expected_min,
                        measurement.expected_max,
                        "YES" if measurement.within_spec else "NO",
                        failure.reason,
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                    ],
                )
            )

    for result in _coverage_report_results(coverage_results):
        selected_evaluations = result.selected_evaluations
        if not selected_evaluations:
            continue
        evaluation = selected_evaluations[0]
        candidate_labels = "; ".join(result.selected_labels)
        coverage_warning = result.ambiguity_warning or ""
        report_gaps = result.gaps or (None,)
        for gap in report_gaps:
            rows.append(
                (
                    (
                        result.dut_sn,
                        result.csv_file,
                        result.sequence_path,
                        "Coverage",
                        gap.configuration_style if gap else "PASS",
                    ),
                    [
                        result.dut_sn,
                        "Coverage",
                        "YES" if gap else "NO",
                        result.csv_file,
                        result.sequence_path,
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "Coverage gap" if gap else "Coverage complete",
                        result.configuration_path,
                        candidate_labels,
                        candidate_labels,
                        evaluation.covered_count,
                        evaluation.required_count,
                        f"{float(evaluation.coverage_ratio):.6f}",
                        gap.main_script_key if gap else "",
                        gap.gain_modes_text if gap else "",
                        gap.tests_text if gap else "",
                        coverage_warning,
                    ],
                )
            )

    rows.sort(key=lambda item: item[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "DUT_SN",
                "CHECK_TYPE",
                "CASE_FLAGGED",
                "CSV_FILE",
                "SEQUENCE_FILE",
                "FAILURE_PORT",
                "DEBUG_PORT_PATH",
                "LOW_GAIN_ROWS",
                "WORST_GAIN_DB",
                "BEST_GAIN_DB",
                "GAIN_METRICS",
                "STRICT",
                "MEASURED_TEMPERATURE_C",
                "SETPOINT_C",
                "EXPECTED_MIN_C",
                "EXPECTED_MAX_C",
                "WITHIN_SPEC",
                "REASON",
                "COVERAGE_CONFIG_FILE",
                "COVERAGE_LABEL",
                "COVERAGE_CANDIDATES",
                "COVERAGE_COVERED",
                "COVERAGE_REQUIRED",
                "COVERAGE_RATIO",
                "COVERAGE_MAIN_SCRIPT_KEY",
                "COVERAGE_GAIN_MODES",
                "COVERAGE_TESTS",
                "COVERAGE_WARNING",
            ]
        )
        writer.writerows(row for _, row in rows)


def write_temperature_csv_report(
    path: Path,
    failures: list[TemperatureFailure],
) -> None:
    """Backward-compatible wrapper for writing temperature-only findings."""
    write_csv_report(path, [], failures)


def write_markdown_report(
    path: Path,
    manifest_path: Path,
    scanned_files: list[Path],
    results: list[NegativeGain],
    failures: list[PortFailure],
    errors: list[str],
    temperature_failures: list[TemperatureFailure] | None = None,
    coverage_results: list[CoverageResult] | None = None,
) -> None:
    temperature_failures = temperature_failures or []
    coverage_results = coverage_results or []
    gain_groups: dict[tuple[str, str, str], list[PortFailure]] = defaultdict(list)
    for failure in failures:
        gain_groups[(failure.dut_sn, failure.csv_file, failure.sequence_path)].append(
            failure
        )
    temperature_groups: dict[
        tuple[str, str, str], list[TemperatureFailure]
    ] = defaultdict(list)
    for failure in temperature_failures:
        temperature_groups[
            (failure.dut_sn, failure.csv_file, failure.sequence_path)
        ].append(failure)
    gain_result_groups: dict[tuple[str, str, str], list[NegativeGain]] = defaultdict(list)
    for result in results:
        gain_result_groups[(result.dut_sn, result.csv_file, result.sequence_path)].append(
            result
        )
    coverage_groups: dict[tuple[str, str, str], list[CoverageResult]] = defaultdict(list)
    for result in coverage_results:
        if result.gaps:
            coverage_groups[(result.dut_sn, result.csv_file, result.sequence_path)].append(
                result
            )
    coverage_pass_groups: dict[str, list[CoverageResult]] = defaultdict(list)
    for result in coverage_results:
        if not result.gaps:
            coverage_pass_groups[result.dut_sn].append(result)

    dut_names = sorted(
        flagged_dut_names(results, temperature_failures, coverage_results)
    )

    lines = [
        "# Datalog Checker Report",
        "",
        f"Manifest: `{manifest_path}`",
        f"Files scanned: {len(scanned_files)}",
        f"DUTs flagged: {len(dut_names)}",
        f"Low gain rows: {len(results)}",
        f"Input port failure rows: {len(failures)}",
        f"Temperature failure cases: {len(temperature_failures)}",
        f"Coverage failure cases: {sum(bool(result.gaps) for result in coverage_results)}",
        "Gain ranges include all gain measurements for each failing port path.",
        "",
    ]

    if dut_names:
        lines.append("## Cases flagged")
        lines.append("")
        for dut_sn in dut_names:
            lines.append(f"### DUT: {dut_sn}")
            lines.append("")
            file_keys = {
                key
                for key in (
                    set(gain_groups)
                    | set(temperature_groups)
                    | set(gain_result_groups)
                    | set(coverage_groups)
                )
                if key[0] == dut_sn
            }
            for _, csv_file, sequence_path in sorted(file_keys):
                file_key = (dut_sn, csv_file, sequence_path)
                file_coverage_results = coverage_groups.get(file_key, [])
                ssf_paths = sorted(
                    {
                        result.configuration_path
                        for result in file_coverage_results
                        if result.configuration_path
                    }
                )
                lines.append(f"Datalog path: `{csv_file}`")
                lines.append(f"Sequence path: `{sequence_path or 'Unavailable'}`")
                for ssf_path in ssf_paths:
                    lines.append(f"SSF path: `{ssf_path}`")
                lines.append("")

                file_gain_failures = gain_groups.get(file_key, [])
                file_gain_results = gain_result_groups.get(file_key, [])
                if file_gain_failures:
                    lines.append("Gain failures:")
                    for failure in sorted(
                        file_gain_failures, key=lambda item: item.input_port
                    ):
                        lines.append(
                            f"- Failure port: {failure.input_port} | "
                            f"Gain range: {failure.worst_gain:g} to "
                            f"{failure.best_gain:g} dB"
                        )
                elif file_gain_results:
                    lines.append(f"Gain rows flagged: {len(file_gain_results)}")

                temperature_file_failures = temperature_groups.get(file_key, [])
                if file_gain_failures or file_gain_results:
                    if temperature_file_failures:
                        lines.append("")
                for failure in temperature_file_failures:
                    lines.append("Temperature failures:")
                    lines.append(f"- Strict mode: {'yes' if failure.strict else 'no'}")
                    if failure.reason:
                        lines.append(f"- Reason: {failure.reason}")
                    for measurement in failure.measurements:
                        expected = (
                            f"{measurement.expected_min:g} to "
                            f"{measurement.expected_max:g} C"
                        )
                        status = "PASS" if measurement.within_spec else "FAIL"
                        lines.append(
                            f"- Line {measurement.line_number}: "
                            f"{measurement.value:g} C (expected {expected}) [{status}]"
                        )
                if (
                    file_gain_failures
                    or file_gain_results
                    or temperature_file_failures
                ) and file_coverage_results:
                    lines.append("")
                for coverage_result in file_coverage_results:
                    report_coverage_results = list(
                        _coverage_report_results([coverage_result])
                    )
                    for index, result in enumerate(report_coverage_results):
                        selected_evaluations = result.selected_evaluations
                        if not selected_evaluations:
                            continue
                        evaluation = selected_evaluations[0]
                        lines.append("Coverage failures:")
                        if index == 0 and coverage_result.ambiguity_warning:
                            lines.append(
                                f"Warning: {coverage_result.ambiguity_warning}"
                            )
                        lines.append(
                            f"- Candidate label(s): {', '.join(result.selected_labels)}"
                        )
                        lines.append(
                            f"- Coverage: {evaluation.covered_count}/"
                            f"{evaluation.required_count} "
                            f"({float(evaluation.coverage_ratio):.1%})"
                        )
                        for gap in result.gaps:
                            lines.append(gap.copy_text)
                        if index + 1 < len(report_coverage_results):
                            lines.append("")
                lines.append("")
        lines.append("The combined CSV report contains the same findings in machine-readable form.")
        lines.append("")
    else:
        lines.extend(
            [
                "No gain, temperature, or coverage failures were found. "
                "No DUTs were flagged.",
                "",
            ]
        )

    if coverage_pass_groups:
        lines.extend(["## Coverage assessments", ""])
        for dut_sn in sorted(coverage_pass_groups):
            lines.append(f"### DUT: {dut_sn}")
            lines.append("")
            for coverage_result in sorted(
                coverage_pass_groups[dut_sn],
                key=lambda item: (item.csv_file, item.sequence_path),
            ):
                lines.append(f"Datalog path: `{coverage_result.csv_file}`")
                lines.append(
                    f"Sequence path: `{coverage_result.sequence_path or 'Unavailable'}`"
                )
                lines.append(
                    f"SSF path: `"
                    f"{coverage_result.configuration_path}`"
                )
                lines.append("")
                for result in _coverage_report_results([coverage_result]):
                    lines.append(
                        f"Candidate label(s): {', '.join(result.selected_labels)}"
                    )
                    if coverage_result.ambiguity_warning:
                        lines.append(
                            f"Warning: {coverage_result.ambiguity_warning}"
                        )
                    selected_evaluations = result.selected_evaluations
                    if selected_evaluations:
                        evaluation = selected_evaluations[0]
                        lines.append(
                            f"- Coverage: {evaluation.covered_count}/"
                            f"{evaluation.required_count} "
                            f"({float(evaluation.coverage_ratio):.1%}) [FULL]"
                        )
                    lines.append("")

    if errors:
        lines.extend(["## Files that could not be scanned", ""])
        for error in errors:
            lines.append(f"- {error}")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
