from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from datalog_checker.coverage import CoverageError, scan_coverage_file
from datalog_checker.coverage_config import TestNameMapping
from datalog_checker.core import write_csv_report, write_markdown_report


class CoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name)
        self.mapping = TestNameMapping(
            {"GAIN": ("GAIN_I", "GAIN_Q"), "LOCK": ("LOCK",)}
        )

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_coverage_ranking_uses_percentage_not_raw_count(self) -> None:
        datalog = self.write_case(
            [
                ("A", "KEY_A", "G0", "GAIN"),
                ("B", "KEY_B", "G0/G1", "GAIN"),
            ],
            [
                ("KEY_A", "G0", "GAIN_I"),
                ("KEY_A", "G0", "GAIN_Q"),
                ("KEY_B", "G0", "GAIN_I"),
                ("KEY_B", "G0", "GAIN_Q"),
                ("KEY_B", "G1", "GAIN_I"),
            ],
        )

        result = scan_coverage_file(datalog, "datalog.csv", self.mapping)

        self.assertEqual(result.selected_labels, ("A",))
        self.assertTrue(result.fully_covered)
        self.assertEqual(result.gaps, ())

    def test_equal_percentage_remains_tied_when_raw_counts_differ(self) -> None:
        datalog = self.write_case(
            [
                ("A", "KEY_A", "G0/G1", "GAIN"),
                ("B", "KEY_B", "G0", "GAIN"),
            ],
            [
                ("KEY_A", "G0", "GAIN_I"),
                ("KEY_A", "G0", "GAIN_Q"),
                ("KEY_B", "G0", "GAIN_I"),
            ],
        )

        result = scan_coverage_file(datalog, "datalog.csv", self.mapping)

        self.assertEqual(result.selected_labels, ("A", "B"))
        self.assertFalse(result.fully_covered)

    def test_ambiguous_partial_coverage_returns_all_candidate_labels(self) -> None:
        datalog = self.write_case(
            [
                ("A", "KEY_A", "G0/G1", "GAIN"),
                ("B", "KEY_B", "G0/G1", "GAIN"),
            ],
            [
                ("KEY_A", "G0", "GAIN_I"),
                ("KEY_A", "G0", "GAIN_Q"),
                ("KEY_B", "G0", "GAIN_I"),
                ("KEY_B", "G0", "GAIN_Q"),
            ],
        )

        result = scan_coverage_file(datalog, "datalog.csv", self.mapping)

        self.assertEqual(result.selected_labels, ("A", "B"))
        self.assertFalse(result.fully_covered)

    def test_tied_full_coverage_returns_all_candidate_labels(self) -> None:
        datalog = self.write_case(
            [
                ("A", "KEY_A", "G0", "GAIN"),
                ("B", "KEY_B", "G0", "GAIN"),
            ],
            [
                ("KEY_A", "G0", "GAIN_I"),
                ("KEY_A", "G0", "GAIN_Q"),
                ("KEY_B", "G0", "GAIN_I"),
                ("KEY_B", "G0", "GAIN_Q"),
            ],
        )

        result = scan_coverage_file(datalog, "datalog.csv", self.mapping)

        self.assertEqual(result.selected_labels, ("A", "B"))
        self.assertTrue(result.fully_covered)

    def test_missing_modes_are_recombined_for_reporting(self) -> None:
        datalog = self.write_case(
            [("LABEL", "KEY", "G0/G1", "GAIN")],
            [("KEY", "G0", "GAIN_I")],
        )

        result = scan_coverage_file(datalog, "datalog.csv", self.mapping)

        self.assertEqual(result.selected_labels, ("LABEL",))
        self.assertEqual(result.selected_evaluations[0].covered_count, 1)
        self.assertEqual(result.selected_evaluations[0].required_count, 4)
        self.assertEqual(result.gaps[0].gain_modes_text, "G0;G1")
        self.assertEqual(result.gaps[0].configuration_style, "(KEY, G0;G1, GAIN)")

    def test_missing_tests_are_recombined_for_reporting(self) -> None:
        datalog = self.write_case(
            [("LABEL", "KEY", "G0", "GAIN/LOCK")],
            [("KEY", "G0", "GAIN_I")],
        )

        result = scan_coverage_file(datalog, "datalog.csv", self.mapping)

        self.assertEqual(result.gaps[0].tests_text, "GAIN;LOCK")
        self.assertEqual(result.gaps[0].configuration_style, "(KEY, G0, GAIN;LOCK)")

    def test_duplicate_configuration_and_datalog_rows_do_not_change_coverage(self) -> None:
        datalog = self.write_case(
            [
                ("LABEL", " KEY ", " G0 / G0 ", " GAIN "),
                ("LABEL", "KEY", "G0", "GAIN"),
            ],
            [
                ("KEY", "G0", "GAIN_I"),
                ("KEY", "G0", "GAIN_I"),
                ("KEY", "G0", "GAIN_Q"),
            ],
        )

        result = scan_coverage_file(datalog, "datalog.csv", self.mapping)

        self.assertTrue(result.fully_covered)
        self.assertEqual(result.selected_evaluations[0].required_count, 2)

    def test_missing_sequence_configuration_is_an_error(self) -> None:
        datalog = self.root / "datalog.csv"
        self.write_datalog(datalog, [("KEY", "G0", "GAIN_I")], "missing.seq")

        with self.assertRaisesRegex(CoverageError, "not found"):
            scan_coverage_file(datalog, "datalog.csv", self.mapping)

    def test_coverage_gaps_are_written_to_combined_reports(self) -> None:
        datalog = self.write_case(
            [("LABEL", "KEY", "G0/G1", "GAIN")],
            [("KEY", "G0", "GAIN_I")],
        )
        result = scan_coverage_file(datalog, "datalog.csv", self.mapping)
        csv_report = self.root / "report.csv"
        markdown_report = self.root / "report.md"

        write_csv_report(csv_report, [], [], [result])
        write_markdown_report(
            markdown_report,
            Path("JUI.json"),
            [datalog],
            [],
            [],
            [],
            [],
            [result],
        )

        csv_text = csv_report.read_text(encoding="utf-8")
        markdown_text = markdown_report.read_text(encoding="utf-8")
        self.assertIn("Coverage", csv_text)
        self.assertIn("COVERAGE_GAIN_MODES", csv_text)
        self.assertIn("KEY,G0;G1,GAIN", markdown_text)

    def test_full_tied_labels_are_reported_as_all_candidates(self) -> None:
        datalog = self.write_case(
            [
                ("A", "KEY_A", "G0", "GAIN"),
                ("B", "KEY_B", "G0", "GAIN"),
            ],
            [
                ("KEY_A", "G0", "GAIN_I"),
                ("KEY_A", "G0", "GAIN_Q"),
                ("KEY_B", "G0", "GAIN_I"),
                ("KEY_B", "G0", "GAIN_Q"),
            ],
        )
        result = scan_coverage_file(datalog, "datalog.csv", self.mapping)
        csv_report = self.root / "pass.csv"
        markdown_report = self.root / "pass.md"

        write_csv_report(csv_report, [], [], [result])
        write_markdown_report(
            markdown_report,
            Path("JUI.json"),
            [datalog],
            [],
            [],
            [],
            [],
            [result],
        )

        self.assertIn("A", csv_report.read_text(encoding="utf-8"))
        self.assertIn("B", csv_report.read_text(encoding="utf-8"))
        with csv_report.open(newline="", encoding="utf-8") as handle:
            coverage_rows = [
                row for row in csv.DictReader(handle) if row["CHECK_TYPE"] == "Coverage"
            ]
        self.assertEqual(len(coverage_rows), 1)
        self.assertEqual(
            coverage_rows[0]["COVERAGE_WARNING"],
            "Multiple candidate labels tie at 100.0% coverage: A, B.",
        )
        markdown_text = markdown_report.read_text(encoding="utf-8")
        self.assertIn("Candidate label(s): A, B", markdown_text)
        self.assertIn(
            "Warning: Multiple candidate labels tie at 100.0% coverage: A, B.",
            markdown_text,
        )
        self.assertEqual(markdown_text.count("- Coverage:"), 1)
        self.assertNotIn("- A:", markdown_text)
        self.assertNotIn("- B:", markdown_text)

    def test_partial_tied_labels_are_reported_once_with_all_candidates(self) -> None:
        datalog = self.write_case(
            [
                ("A", "KEY", "G0/G1", "GAIN"),
                ("B", "KEY", "G0/G1", "GAIN"),
            ],
            [
                ("KEY", "G0", "GAIN_I"),
                ("KEY", "G0", "GAIN_Q"),
            ],
        )
        result = scan_coverage_file(datalog, "datalog.csv", self.mapping)
        markdown_report = self.root / "partial.md"

        write_markdown_report(
            markdown_report,
            Path("JUI.json"),
            [datalog],
            [],
            [],
            [],
            [],
            [result],
        )

        markdown_text = markdown_report.read_text(encoding="utf-8")
        self.assertEqual(markdown_text.count("Coverage failures:"), 1)
        self.assertEqual(
            markdown_text.count(
                "Warning: Multiple candidate labels tie at 50.0% coverage: A, B."
            ),
            1,
        )
        self.assertGreater(
            markdown_text.index("Warning: Multiple candidate labels"),
            markdown_text.index("Coverage failures:"),
        )
        self.assertNotIn("- Missing:", markdown_text)
        self.assertEqual(markdown_text.count("Candidate label(s): A, B"), 1)
        self.assertEqual(markdown_text.count("- Coverage:"), 1)

    def test_partial_tied_labels_with_different_gaps_are_reported_separately(
        self,
    ) -> None:
        datalog = self.write_case(
            [
                ("A", "KEY_A", "G0/G1", "GAIN"),
                ("B", "KEY_B", "G0/G1", "GAIN"),
            ],
            [
                ("KEY_A", "G0", "GAIN_I"),
                ("KEY_A", "G0", "GAIN_Q"),
                ("KEY_B", "G0", "GAIN_I"),
                ("KEY_B", "G0", "GAIN_Q"),
            ],
        )
        result = scan_coverage_file(datalog, "datalog.csv", self.mapping)
        markdown_report = self.root / "different.md"
        csv_report = self.root / "different.csv"

        self.assertEqual(result.selected_labels, ("A", "B"))
        self.assertEqual(len(result.selected_evaluation_groups), 2)

        write_markdown_report(
            markdown_report,
            Path("JUI.json"),
            [datalog],
            [],
            [],
            [],
            [],
            [result],
        )

        markdown_text = markdown_report.read_text(encoding="utf-8")
        self.assertEqual(markdown_text.count("Coverage failures:"), 2)
        self.assertEqual(markdown_text.count("Warning: Multiple candidate labels"), 1)
        self.assertIn("Candidate label(s): A", markdown_text)
        self.assertIn("Candidate label(s): B", markdown_text)
        self.assertNotIn("Candidate label(s): A, B", markdown_text)
        self.assertIn("KEY_A,G1,GAIN", markdown_text)
        self.assertIn("KEY_B,G1,GAIN", markdown_text)
        self.assertNotIn("(KEY_A, G1, GAIN)", markdown_text)
        self.assertNotIn("(KEY_B, G1, GAIN)", markdown_text)
        self.assertIn("Datalog path: `datalog.csv`", markdown_text)
        self.assertIn(
            f"SSF path: `{result.configuration_path}`",
            markdown_text,
        )
        self.assertEqual(markdown_text.count("SSF path:"), 1)
        self.assertNotIn("CSV path:", markdown_text)
        self.assertNotIn("Configuration CSV:", markdown_text)
        self.assertIn(
            "KEY_A,G1,GAIN\n\nCoverage failures:\n"
            "- Candidate label(s): B",
            markdown_text,
        )

        write_csv_report(csv_report, [], [], [result])
        with csv_report.open(newline="", encoding="utf-8") as handle:
            coverage_rows = [
                row for row in csv.DictReader(handle) if row["CHECK_TYPE"] == "Coverage"
            ]
        self.assertEqual(
            {row["COVERAGE_CANDIDATES"] for row in coverage_rows}, {"A", "B"}
        )

    def write_case(
        self,
        configuration_rows: list[tuple[str, str, str, str]],
        datalog_rows: list[tuple[str, str, str]],
    ) -> Path:
        configuration = self.root / "sequence.csv"
        with configuration.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Label", "Main_Script_Key", "Config_GainMode", "TESTS"])
            writer.writerows(configuration_rows)

        datalog = self.root / "datalog.csv"
        self.write_datalog(datalog, datalog_rows, "sequence.seq")
        return datalog

    def write_datalog(
        self,
        path: Path,
        rows: list[tuple[str, str, str]],
        sequence_name: str,
    ) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([f"SEQUENCE_FILE={sequence_name}"])
            writer.writerow(
                [
                    "STEP_NAME=",
                    "TCOND=DUT_SN",
                    "TNAME",
                    "TCOND=MAIN_SCRIPT_KEY",
                    "TCOND=CONFIG_GAINMODE_RX",
                ]
            )
            for main_script_key, gain_mode, tname in rows:
                writer.writerow(
                    ["row", "DUT-1", tname, main_script_key, gain_mode]
                )


if __name__ == "__main__":
    unittest.main()
