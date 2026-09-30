from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from datalog_checker.coverage import CoverageError, scan_coverage_file
from datalog_checker.coverage_config import CoverageExclusion, TestNameMapping
from datalog_checker.core import write_markdown_report


class CoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name)
        self.mapping = TestNameMapping(
            {"GAIN": ("GAIN_I", "GAIN_Q"), "LOCK": ("LOCK",)}
        )

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_uses_datalog_label_instead_of_percentage_ranking(self) -> None:
        datalog = self.write_case(
            [
                ("A", "KEY_A", "G0/G1", "GAIN"),
                ("B", "KEY_B", "G0", "GAIN"),
            ],
            [
                ("KEY_A", "G0", "GAIN_I"),
                ("KEY_A", "G0", "GAIN_Q"),
                ("KEY_A", "G1", "GAIN_I"),
                ("KEY_A", "G1", "GAIN_Q"),
                ("KEY_B", "G0", "GAIN_I"),
            ],
            datalog_label="B",
        )

        result = scan_coverage_file(datalog, "datalog.csv", self.mapping)

        self.assertEqual(result.selected_labels, ("B",))
        self.assertEqual(
            tuple(evaluation.label for evaluation in result.evaluations), ("B",)
        )
        self.assertFalse(result.fully_covered)
        self.assertEqual(result.gaps[0].configuration_style, "(KEY_B, G0, GAIN)")
        self.assertIsNone(result.ambiguity_warning)

    def test_missing_datalog_label_is_an_error(self) -> None:
        datalog = self.root / "datalog.csv"
        self.write_datalog(
            datalog,
            [("KEY", "G0", "GAIN_I")],
            "sequence.seq",
            label="",
        )

        with self.assertRaisesRegex(CoverageError, "CONFIG_SEQ_FROM_CSV"):
            scan_coverage_file(datalog, "datalog.csv", self.mapping)

    def test_conflicting_datalog_labels_are_an_error(self) -> None:
        datalog = self.root / "datalog.csv"
        self.write_datalog(
            datalog,
            [("KEY", "G0", "GAIN_I"), ("KEY", "G0", "GAIN_Q")],
            "sequence.seq",
            labels=["A", "B"],
        )

        with self.assertRaisesRegex(CoverageError, "multiple.*labels"):
            scan_coverage_file(datalog, "datalog.csv", self.mapping)

    def test_unknown_datalog_label_is_an_error(self) -> None:
        datalog = self.write_case(
            [("A", "KEY", "G0", "GAIN")],
            [("KEY", "G0", "GAIN_I")],
            datalog_label="UNKNOWN",
        )

        with self.assertRaisesRegex(CoverageError, "was not found"):
            scan_coverage_file(datalog, "datalog.csv", self.mapping)

    def test_missing_modes_are_recombined_for_reporting(self) -> None:
        datalog = self.write_case(
            [("LABEL", "KEY", "G0/G1", "GAIN")],
            [("KEY", "G0", "GAIN_I")],
        )

        result = scan_coverage_file(datalog, "datalog.csv", self.mapping)

        self.assertEqual(result.selected_evaluations[0].covered_count, 1)
        self.assertEqual(result.selected_evaluations[0].required_count, 8)
        self.assertEqual(result.gaps[0].gain_modes_text, "G0/G1")
        self.assertEqual(result.gaps[0].configuration_style, "(KEY, G0/G1, GAIN)")

    def test_missing_tests_are_recombined_for_reporting(self) -> None:
        datalog = self.write_case(
            [("LABEL", "KEY", "G0", "GAIN/LOCK")],
            [("KEY", "G0", "GAIN_I")],
        )

        result = scan_coverage_file(datalog, "datalog.csv", self.mapping)

        self.assertEqual(result.gaps[0].tests_text, "GAIN;LOCK")
        self.assertEqual(result.gaps[0].configuration_style, "(KEY, G0, GAIN;LOCK)")

    def test_coverage_exclusions_remove_matching_ssf_tests(self) -> None:
        configuration = self.root / "sequence.csv"
        with configuration.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["Label", "Temperature", "Main_Script_Key", "Config_GainMode", "TESTS"]
            )
            writer.writerow(["LABEL", "110", "KEY", "G0", "IP2ACS"])
            writer.writerow(["LABEL", "110", "KEY", "G0", "IP3ACS"])
            writer.writerow(["LABEL", "110", "KEY", "G0", "GAIN"])

        datalog = self.root / "datalog.csv"
        with datalog.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["SEQUENCE_FILE=sequence.seq"])
            writer.writerow(
                [
                    "STEP_NAME=",
                    "TCOND=DUT_SN",
                    "TCOND=CONFIG_SEQ_FROM_CSV",
                    "TNAME",
                    "TCOND=MAIN_SCRIPT_KEY",
                    "TCOND=CONFIG_GAINMODE_RX",
                ]
            )
            for _ in range(2):
                for tname in ("IP2ACS", "IP3ACS", "GAIN_I", "GAIN_Q"):
                    writer.writerow(["row", "DUT-1", "LABEL", tname, "KEY", "G0"])

        mapping = TestNameMapping(
            {
                "GAIN": ("GAIN_I", "GAIN_Q"),
                "IP2ACS": ("IP2ACS",),
                "IP3ACS": ("IP3ACS",),
            }
        )
        exclusions = (
            CoverageExclusion(
                temperatures=("110",),
                main_script_keys=("*",),
                gain_modes=("*",),
                tests=("IP2ACS", "IP3ACS"),
            ),
        )

        result = scan_coverage_file(datalog, "datalog.csv", mapping, exclusions)

        evaluation = result.selected_evaluations[0]
        self.assertEqual(evaluation.required_count, 4)
        self.assertEqual(evaluation.covered_count, 4)
        self.assertTrue(result.fully_covered)

    def test_duplicate_configuration_rows_are_deduplicated_but_datalog_rows_count(self) -> None:
        datalog = self.write_case(
            [
                ("LABEL", " KEY ", " G0 / G0 ", " GAIN "),
                ("LABEL", "KEY", "G0", "GAIN"),
            ],
            [
                ("KEY", "G0", "GAIN_I"),
                ("KEY", "G0", "GAIN_I"),
                ("KEY", "G0", "GAIN_Q"),
                ("KEY", "G0", "GAIN_Q"),
            ],
        )

        result = scan_coverage_file(datalog, "datalog.csv", self.mapping)

        self.assertTrue(result.fully_covered)
        self.assertEqual(result.selected_evaluations[0].required_count, 4)

    def test_coverage_multiplicity_uses_signal_path_count(self) -> None:
        one_path = self.write_case(
            [("LABEL", "KEY_RP0-R0", "G0", "GAIN")],
            [
                ("KEY_RP0-R0", "G0", "GAIN_I"),
                ("KEY_RP0-R0", "G0", "GAIN_I"),
                ("KEY_RP0-R0", "G0", "GAIN_Q"),
                ("KEY_RP0-R0", "G0", "GAIN_Q"),
            ],
        )
        one_path_result = scan_coverage_file(one_path, "one.csv", self.mapping)

        self.assertTrue(one_path_result.fully_covered)
        self.assertEqual(one_path_result.selected_evaluations[0].required_count, 4)
        self.assertEqual(one_path_result.selected_evaluations[0].covered_count, 4)

        two_path = self.write_case(
            [("LABEL", "KEY_RP0-R0_RP1-R1", "G0", "GAIN")],
            [
                ("KEY_RP0-R0_RP1-R1", "G0", "GAIN_I"),
                ("KEY_RP0-R0_RP1-R1", "G0", "GAIN_I"),
                ("KEY_RP0-R0_RP1-R1", "G0", "GAIN_I"),
                ("KEY_RP0-R0_RP1-R1", "G0", "GAIN_I"),
                ("KEY_RP0-R0_RP1-R1", "G0", "GAIN_Q"),
                ("KEY_RP0-R0_RP1-R1", "G0", "GAIN_Q"),
                ("KEY_RP0-R0_RP1-R1", "G0", "GAIN_Q"),
                ("KEY_RP0-R0_RP1-R1", "G0", "GAIN_Q"),
            ],
        )
        two_path_result = scan_coverage_file(two_path, "two.csv", self.mapping)

        self.assertTrue(two_path_result.fully_covered)
        self.assertEqual(two_path_result.selected_evaluations[0].required_count, 8)
        self.assertEqual(two_path_result.selected_evaluations[0].covered_count, 8)

    def test_missing_sequence_configuration_is_an_error(self) -> None:
        datalog = self.root / "datalog.csv"
        self.write_datalog(
            datalog,
            [("KEY", "G0", "GAIN_I")],
            "missing.seq",
            label="LABEL",
        )

        with self.assertRaisesRegex(CoverageError, "not found"):
            scan_coverage_file(datalog, "datalog.csv", self.mapping)

    def test_coverage_gaps_are_written_to_markdown_report(self) -> None:
        datalog = self.write_case(
            [("LABEL", "KEY", "G0/G1", "GAIN")],
            [("KEY", "G0", "GAIN_I")],
        )
        result = scan_coverage_file(datalog, "datalog.csv", self.mapping)
        markdown_report = self.root / "report.md"

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
        self.assertIn("KEY,G0/G1,GAIN", markdown_text)
        self.assertIn("- Label: LABEL", markdown_text)
        self.assertNotIn("Candidate label", markdown_text)
        self.assertNotIn("Warning:", markdown_text)

    def test_full_coverage_is_not_written_to_markdown_report(self) -> None:
        datalog = self.write_case(
            [
                ("A", "KEY_A", "G0", "GAIN"),
                ("B", "KEY_B", "G0", "GAIN"),
            ],
            [
                ("KEY_B", "G0", "GAIN_I"),
                ("KEY_B", "G0", "GAIN_Q"),
                ("KEY_B", "G0", "GAIN_I"),
                ("KEY_B", "G0", "GAIN_Q"),
            ],
            datalog_label="B",
        )
        result = scan_coverage_file(datalog, "datalog.csv", self.mapping)
        markdown_report = self.root / "pass.md"

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
        self.assertNotIn("Label: B", markdown_text)
        self.assertNotIn("[FULL]", markdown_text)
        self.assertNotIn("Coverage assessments", markdown_text)
        self.assertNotIn("Candidate label", markdown_text)
        self.assertNotIn("Warning:", markdown_text)

    def write_case(
        self,
        configuration_rows: list[tuple[str, str, str, str]],
        datalog_rows: list[tuple[str, str, str]],
        datalog_label: str | None = None,
    ) -> Path:
        configuration = self.root / "sequence.csv"
        with configuration.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Label", "Main_Script_Key", "Config_GainMode", "TESTS"])
            writer.writerows(configuration_rows)

        datalog = self.root / "datalog.csv"
        self.write_datalog(
            datalog,
            datalog_rows,
            "sequence.seq",
            label=datalog_label or configuration_rows[0][0],
        )
        return datalog

    def write_datalog(
        self,
        path: Path,
        rows: list[tuple[str, str, str]],
        sequence_name: str,
        label: str = "LABEL",
        labels: list[str] | None = None,
    ) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([f"SEQUENCE_FILE={sequence_name}"])
            writer.writerow(
                [
                    "STEP_NAME=",
                    "TCOND=DUT_SN",
                    "TCOND=CONFIG_SEQ_FROM_CSV",
                    "TNAME",
                    "TCOND=MAIN_SCRIPT_KEY",
                    "TCOND=CONFIG_GAINMODE_RX",
                ]
            )
            for index, (main_script_key, gain_mode, tname) in enumerate(rows):
                row_label = labels[index] if labels is not None else label
                writer.writerow(
                    ["row", "DUT-1", row_label, tname, main_script_key, gain_mode]
                )


if __name__ == "__main__":
    unittest.main()
