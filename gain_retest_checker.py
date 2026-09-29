#!/usr/bin/env python3
"""Find negative gain measurements and summarize them by input port."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


GAIN_NAMES = {"GAIN", "GAIN_I", "GAIN_Q"}
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


def format_input_port(port: str) -> str:
    shorthand = port.strip().upper()
    if len(shorthand) > 1 and shorthand.startswith("M") and shorthand[1] in {"D", "P", "U"}:
        return shorthand[1:]
    return shorthand


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan CSV files from files.json and report negative gain values."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("files.json"),
        help="JSON file containing the CSV paths (default: files.json).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("gain_retest_report.md"),
        help="Markdown report path (default: gain_retest_report.md).",
    )
    parser.add_argument(
        "--csv-report",
        type=Path,
        default=Path("gain_retest_report.csv"),
        help="Flat CSV report path (default: gain_retest_report.csv).",
    )
    return parser.parse_args()


def _path_from_item(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("path", "file", "filename", "csv", "filepath"):
            value = item.get(key)
            if isinstance(value, str):
                return value
    return None


def load_manifest(manifest_path: Path) -> list[Path]:
    raw_manifest = manifest_path.read_text(encoding="utf-8-sig")
    try:
        manifest = json.loads(raw_manifest)
    except json.JSONDecodeError as original_error:
        # Accept manifests produced with commas after standalone JSON
        # brackets, for example `{,` and `],`. These are easy to repair
        # without changing commas that separate actual list entries.
        repaired_lines = []
        for line in raw_manifest.splitlines():
            stripped = line.strip()
            trimmed = line.rstrip()
            if stripped in {"{,", "[,", "],", "},"}:
                line = trimmed[:-1]
            elif trimmed.endswith(("{,", "[,")):
                line = trimmed[:-1]
            repaired_lines.append(line)
        try:
            manifest = json.loads("\n".join(repaired_lines))
        except json.JSONDecodeError:
            raise original_error

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


def _display_path(path: Path, manifest_path: Path) -> str:
    try:
        return str(path.relative_to(manifest_path.parent))
    except ValueError:
        return str(path)


def scan_file(path: Path, display_name: str) -> list[NegativeGain]:
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

        negative_rows: list[NegativeGain] = []
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
            if value >= 0:
                continue

            negative_rows.append(
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
            for result in negative_rows
        ]


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
            input_port = f"{format_input_port(raw_ports[0])} ({raw_ports[0]})"
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


def write_csv_report(path: Path, failures: list[PortFailure]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "DUT_SN",
                "RETEST_REQUIRED",
                "CSV_FILE",
                "SEQUENCE_FILE",
                "FAILURE_PORT",
                "DEBUG_PORT_PATH",
                "NEGATIVE_GAIN_ROWS",
                "WORST_GAIN_DB",
                "BEST_GAIN_DB",
                "GAIN_METRICS",
            ]
        )
        for failure in failures:
            writer.writerow(
                [
                    failure.dut_sn,
                    "YES",
                    failure.csv_file,
                    failure.sequence_path,
                    failure.input_port,
                    failure.debug_port_path,
                    failure.negative_rows,
                    failure.worst_gain,
                    failure.best_gain,
                    ", ".join(failure.metrics),
                ]
            )


def write_markdown_report(
    path: Path,
    manifest_path: Path,
    scanned_files: list[Path],
    results: list[NegativeGain],
    failures: list[PortFailure],
    errors: list[str],
) -> None:
    grouped = group_by_dut(results)
    file_groups: dict[tuple[str, str, str], list[PortFailure]] = defaultdict(list)
    for failure in failures:
        file_groups[(failure.dut_sn, failure.csv_file, failure.sequence_path)].append(failure)

    lines = [
        "# Gain Retest Report",
        "",
        f"Manifest: `{manifest_path}`",
        f"Files scanned: {len(scanned_files)}",
        f"DUTs requiring retest: {len(grouped)}",
        f"Negative gain rows: {len(results)}",
        f"Input port failure rows: {len(failures)}",
        "Gain ranges include all gain measurements for each failing port path.",
        "",
    ]

    if grouped:
        lines.append("## Cases requiring retest")
        lines.append("")
        dut_names = sorted(grouped)
        for dut_index, dut_sn in enumerate(dut_names):
            if dut_index:
                lines.extend(["---", ""])
            lines.append(f"DUT: {dut_sn}")
            lines.append("")
            dut_files = [
                (key, file_failures)
                for key, file_failures in file_groups.items()
                if key[0] == dut_sn
            ]
            for file_index, ((_, csv_file, sequence_path), file_failures) in enumerate(
                sorted(dut_files)
            ):
                if file_index:
                    lines.extend(["---", ""])
                lines.append(f"CSV path: `{csv_file}`")
                lines.append(f"Sequence path: `{sequence_path or 'Unavailable'}`")
                lines.append("")
                for failure in sorted(file_failures, key=lambda item: item.input_port):
                    lines.append(
                        f"Failure port: {failure.input_port} | "
                        f"Gain range: {failure.worst_gain:g} to {failure.best_gain:g} dB"
                    )
                lines.append("")
        lines.append("Each failing port has its own line. The CSV report contains the same summary in machine-readable form.")
        lines.append("")
    else:
        lines.extend(["No negative gain was found. No DUTs require retest.", ""])

    if errors:
        lines.extend(["## Files that could not be scanned", ""])
        for error in errors:
            lines.append(f"- {error}")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    try:
        file_paths = load_manifest(args.manifest)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"Error reading manifest: {error}", file=sys.stderr)
        return 2

    all_results: list[NegativeGain] = []
    scanned_files: list[Path] = []
    errors: list[str] = []
    for path in file_paths:
        display_name = _display_path(path, args.manifest)
        try:
            all_results.extend(scan_file(path, display_name))
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
    )
    write_csv_report(args.csv_report, port_failures)

    print(f"Scanned {len(scanned_files)} file(s).")
    print(f"Found {len(all_results)} negative gain row(s).")
    print(f"DUTs requiring retest: {len(group_by_dut(all_results))}")
    print(f"Input port failure rows: {len(port_failures)}")
    print(f"Markdown report: {args.report}")
    print(f"CSV report: {args.csv_report}")
    if errors:
        print(f"Files with errors: {len(errors)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
