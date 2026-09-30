from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from datalog_checker.core import (
    NegativeGain,
    scan_file,
    scan_temperature_file,
    summarize_port_failures,
    timestamped_report_path,
    write_markdown_report,
)


class ScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.csv_path = Path(self.temp_directory.name) / "sample.csv"
        with self.csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["SEQUENCE_FILE=C:/sequence.seq"])
            writer.writerow(
                [
                    "STEP_NAME=",
                    "TCOND=DUT_SN",
                    "TNAME",
                    "TDATA",
                    "MCOND=RFIN_PORT",
                    "MCOND=BBOUT_PATH",
                ]
            )
            writer.writerow(["row", "DUT-1", "GAIN", "-0.5", "MD1", "BB1"])
            writer.writerow(["row", "DUT-1", "GAIN", "0.25", "MD1", "BB1"])
            writer.writerow(["row", "DUT-1", "GAIN_I", "1.5", "MD1", "BB1"])
            writer.writerow(["row", "DUT-1", "OTHER", "-10", "MD1", "BB1"])

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_default_threshold_flags_only_negative_gain(self) -> None:
        results = scan_file(self.csv_path, "sample.csv")

        self.assertEqual([result.value for result in results], [-0.5])
        self.assertEqual(results[0].all_gain_values, (-0.5, 0.25, 1.5))

    def test_custom_threshold_flags_values_below_threshold(self) -> None:
        results = scan_file(self.csv_path, "sample.csv", 1.0)

        self.assertEqual([result.value for result in results], [-0.5, 0.25])

    def test_port_failure_summary_uses_all_gain_values(self) -> None:
        results = scan_file(self.csv_path, "sample.csv", 1.0)
        failures = summarize_port_failures(results)

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].negative_rows, 2)
        self.assertEqual(failures[0].worst_gain, -0.5)
        self.assertEqual(failures[0].best_gain, 1.5)

    def test_timestamped_report_path_prefixes_report_name(self) -> None:
        report_path = timestamped_report_path(Path("datalog_report.md"))

        self.assertRegex(report_path.name, r"^\d{8}_\d{6}_datalog_report\.md$")

    def test_markdown_report_groups_gain_and_temperature_by_dut(self) -> None:
        results = scan_file(self.csv_path, "sample.csv", 1.0)
        temperature_path = self._write_temperature_csv(
            header_extra=[],
            values=[99.0, 37.5, 39.9, 38.9],
        )
        temperature_failures = scan_temperature_file(
            temperature_path,
            "sample.csv",
            tolerance=10,
            strict=True,
        )
        report_path = Path(self.temp_directory.name) / "report.md"

        write_markdown_report(
            report_path,
            Path("JUI.json"),
            [self.csv_path, temperature_path],
            results,
            summarize_port_failures(results),
            [],
            temperature_failures,
        )

        report = report_path.read_text(encoding="utf-8")
        self.assertIn("DUTs flagged: 1", report)
        self.assertIn("## Cases flagged", report)
        self.assertIn("=" * 80 + "\n\n## DUT: DUT-1", report)
        self.assertIn(
            "## DUT: DUT-1\n\n"
            + "-" * 80
            + "\n\nDatalog path: `sample.csv`",
            report,
        )
        self.assertIn("Gain failures:", report)
        self.assertIn("Temperature failures:", report)
        self.assertIn("dB\n\nTemperature failures:", report)
        self.assertNotIn("## Temperature failures", report)
        self.assertNotIn("Coverage assessments", report)

    def test_markdown_report_separates_multiple_duts(self) -> None:
        first_results = scan_file(self.csv_path, "first.csv", 1.0)
        second_result = NegativeGain(
            dut_sn="DUT-2",
            csv_file="second.csv",
            sequence_path="C:/sequence.seq",
            line_number=1,
            metric="GAIN",
            value=-1.0,
            ports=(),
            all_gain_values=(-1.0,),
        )
        report_path = Path(self.temp_directory.name) / "report.md"

        write_markdown_report(
            report_path,
            Path("JUI.json"),
            [self.csv_path],
            [*first_results, second_result],
            [],
            [],
        )

        report = report_path.read_text(encoding="utf-8")
        self.assertIn("=" * 80 + "\n\n## DUT: DUT-1", report)
        self.assertIn("=" * 80 + "\n\n## DUT: DUT-2", report)
        self.assertNotIn("-" * 80 + "\n\n## DUT: DUT-2", report)

    def test_temperature_non_strict_accepts_best_case_at_room_temperature(self) -> None:
        path = self._write_temperature_csv(
            header_extra=[],
            values=[99.0, 37.5, 39.9, 38.9],
        )

        failures = scan_temperature_file(path, "room.csv", tolerance=10, strict=False)

        self.assertEqual(failures, [])

    def test_temperature_strict_requires_all_room_measurements(self) -> None:
        path = self._write_temperature_csv(
            header_extra=[],
            values=[99.0, 37.5, 39.9, 38.9],
        )

        failures = scan_temperature_file(path, "room.csv", tolerance=10, strict=True)

        self.assertEqual(len(failures), 1)
        self.assertEqual(len(failures[0].out_of_spec), 1)

    def test_temperature_uses_explicit_setpoint(self) -> None:
        path = self._write_temperature_csv(
            header_extra=["TCOND=TEMPERATURE"],
            values=[107.0, 108.0, 114.0, 114.5],
            setpoints=["110"] * 4,
        )

        non_strict_failures = scan_temperature_file(
            path, "hot.csv", tolerance=3, strict=False
        )
        strict_failures = scan_temperature_file(
            path, "hot.csv", tolerance=3, strict=True
        )

        self.assertEqual(non_strict_failures, [])
        self.assertEqual(len(strict_failures), 1)
        self.assertEqual(len(strict_failures[0].out_of_spec), 2)

    def test_temperature_uses_configured_measurement_names(self) -> None:
        path = self._write_temperature_csv(
            header_extra=[],
            values=[40.0],
            measurement_name="THERM_AUX_TEMP",
        )

        failures = scan_temperature_file(
            path,
            "aux.csv",
            tolerance=10,
            strict=True,
            measurement_names=["THERM_AUX_TEMP"],
        )

        self.assertEqual(failures, [])

    def _write_temperature_csv(
        self,
        header_extra: list[str],
        values: list[float],
        setpoints: list[str] | None = None,
        measurement_name: str = "THERM_DIODE_HKADC_TEMP",
    ) -> Path:
        path = Path(self.temp_directory.name) / "temperature.csv"
        header = [
            "STEP_NAME=",
            "TCOND=DUT_SN",
            "TNAME",
            "TDATA",
            *header_extra,
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["SEQUENCE_FILE=C:/sequence.seq"])
            writer.writerow(header)
            for index, value in enumerate(values):
                row = ["row", "DUT-1", measurement_name, str(value)]
                if setpoints is not None:
                    row.extend([setpoints[index]])
                writer.writerow(row)
        return path


if __name__ == "__main__":
    unittest.main()
