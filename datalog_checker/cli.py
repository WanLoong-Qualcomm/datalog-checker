"""Command-line entry point for Datalog Checker."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from datalog_checker.config import COVERAGE_CHECK_NAME, load_settings
from datalog_checker.coverage import CoverageResult, scan_coverage_file
from datalog_checker.coverage_config import (
    load_coverage_exclusions,
    load_temperature_measurement_names,
    load_test_name_mapping,
)
from datalog_checker.core import (
    NegativeGain,
    TemperatureFailure,
    display_path,
    flagged_dut_names,
    scan_file,
    scan_temperature_file,
    summarize_port_failures,
    timestamped_report_path,
    write_markdown_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan CSV files from JUI.json and report flagged cases."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("JUI.json"),
        help="JSON user interface file containing checks and CSV paths (default: JUI.json).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help=(
            "Markdown report base path. The generated filename is prefixed with "
            "a timestamp."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        settings = load_settings(args.manifest)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"Error reading manifest: {error}", file=sys.stderr)
        return 2

    file_paths = list(settings.files)
    gain_settings = settings.checks.get("Gain")
    gain_enabled = gain_settings.enabled if gain_settings is not None else True
    temperature_settings = settings.checks.get("Temperature")
    temperature_enabled = (
        temperature_settings is not None and temperature_settings.enabled
    )
    coverage_settings = settings.checks.get(COVERAGE_CHECK_NAME)
    coverage_enabled = coverage_settings is not None and coverage_settings.enabled
    temperature_measurement_names: tuple[str, ...] | None = None
    try:
        coverage_mapping = load_test_name_mapping() if coverage_enabled else None
        coverage_exclusions = load_coverage_exclusions() if coverage_enabled else ()
        if temperature_enabled:
            temperature_measurement_names = load_temperature_measurement_names()
    except (OSError, ValueError) as error:
        print(f"Error reading runtime config: {error}", file=sys.stderr)
        return 2

    all_results: list[NegativeGain] = []
    scanned_files: list[Path] = []
    errors: list[str] = []
    temperature_failures: list[TemperatureFailure] = []
    coverage_results: list[CoverageResult] = []
    for path in file_paths:
        display_name = display_path(path, args.manifest)
        try:
            if gain_enabled:
                all_results.extend(scan_file(path, display_name, settings.gain_threshold))
            if temperature_enabled:
                temperature_failures.extend(
                    scan_temperature_file(
                        path,
                        display_name,
                        settings.temperature_tolerance,
                        settings.temperature_strict,
                        temperature_measurement_names,
                    )
                )
            if coverage_enabled and coverage_mapping is not None:
                coverage_results.append(
                    scan_coverage_file(
                        path,
                        display_name,
                        coverage_mapping,
                        coverage_exclusions,
                    )
                )
            scanned_files.append(path)
        except (OSError, ValueError) as error:
            errors.append(f"`{display_name}`: {error}")

    all_results.sort(key=lambda result: (result.dut_sn, result.csv_file, result.line_number))
    port_failures = summarize_port_failures(all_results)
    report_base = args.report
    if report_base is None:
        output_directory = settings.outputs_directory or Path(".")
        report_base = output_directory / "datalog_report.md"
    report_path = timestamped_report_path(report_base)
    write_markdown_report(
        report_path,
        args.manifest,
        scanned_files,
        all_results,
        port_failures,
        errors,
        temperature_failures,
        coverage_results,
    )

    print(f"Scanned {len(scanned_files)} file(s).")
    print(f"Found {len(all_results)} low gain row(s).")
    print(
        f"DUTs flagged: "
        f"{len(flagged_dut_names(all_results, temperature_failures, coverage_results))}"
    )
    print(f"Input port failure rows: {len(port_failures)}")
    print(f"Temperature failure cases: {len(temperature_failures)}")
    print(
        f"Coverage failure cases: "
        f"{sum(bool(result.gaps) for result in coverage_results)}"
    )
    print(f"Markdown report: {report_path}")
    if errors:
        print(f"Files with errors: {len(errors)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
