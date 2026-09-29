from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from datalog_checker.coverage_config import (
    CoverageConfigError,
    load_test_name_mapping,
)


class CoverageConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_directory.name) / "config.json"

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def write_config(self, payload: object) -> None:
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def test_loads_and_normalizes_mapping(self) -> None:
        self.write_config(
            {
                "tests_to_tnames": {
                    " gain ": ["gain_i", " GAIN_Q ", "GAIN_I"],
                }
            }
        )

        mapping = load_test_name_mapping(self.path)

        self.assertEqual(mapping.tnames_for("GAIN"), ("GAIN_I", "GAIN_Q"))

    def test_accepts_single_string_target(self) -> None:
        self.write_config({"tests_to_tnames": {"LOCK": "LOCKDET_RXPLL0"}})

        mapping = load_test_name_mapping(self.path)

        self.assertEqual(mapping.tnames_for("lock"), ("LOCKDET_RXPLL0",))

    def test_rejects_missing_mapping_object(self) -> None:
        self.write_config({})

        with self.assertRaises(CoverageConfigError):
            load_test_name_mapping(self.path)

    def test_rejects_empty_target_list(self) -> None:
        self.write_config({"tests_to_tnames": {"GAIN": []}})

        with self.assertRaises(CoverageConfigError):
            load_test_name_mapping(self.path)

    def test_rejects_blank_target(self) -> None:
        self.write_config({"tests_to_tnames": {"GAIN": [" "]}})

        with self.assertRaises(CoverageConfigError):
            load_test_name_mapping(self.path)


if __name__ == "__main__":
    unittest.main()
