"""Command-line entry point for Datalog Checker."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from datalog_checker.config import load_settings
from datalog_checker.core import (
    NegativeGain,
    TemperatureFailure,
    display_path,
    flagged_dut_names,
    scan_file,
    scan_temperature_file,
    summarize_port_failures,
    write_csv_report,
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
        default=Path("datalog_report.md"),
        help="Markdown report path (default: datalog_report.md).",
    )
    parser.add_argument(
        "--csv-report",
        type=Path,
        default=Path("datalog_report.csv"),
        help="Flat CSV report path (default: datalog_report.csv).",
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
    all_results: list[NegativeGain] = []
    scanned_files: list[Path] = []
    errors: list[str] = []
    temperature_failures: list[TemperatureFailure] = []
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
                    )
                )
            scanned_files.append(path)
        except (OSError, ValueError) as error:
            errors.append(f"`{display_name}`: {error}")

    all_results.sort(key=lambda result: (result.dut_sn, result.csv_file, result.line_number))
    port_failures = summarize_port_failures(all_results)
    write_markdown_report(
        args.report,
        args.manifest,
        scanned_files,
        all_results,
        port_failures,
        errors,
        temperature_failures,
    )
    write_csv_report(args.csv_report, port_failures, temperature_failures)

    print(f"Scanned {len(scanned_files)} file(s).")
    print(f"Found {len(all_results)} low gain row(s).")
    print(
        f"DUTs flagged: "
        f"{len(flagged_dut_names(all_results, temperature_failures))}"
    )
    print(f"Input port failure rows: {len(port_failures)}")
    print(f"Temperature failure cases: {len(temperature_failures)}")
    print(f"Markdown report: {args.report}")
    print(f"CSV report: {args.csv_report}")
    if errors:
        print(f"Files with errors: {len(errors)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
