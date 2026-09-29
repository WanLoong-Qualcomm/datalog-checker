from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from datalog_checker.config import CheckSettings, load_settings, parse_gain_threshold


class SettingsTests(unittest.TestCase):
    def write_settings(self, payload: dict[str, object]) -> Path:
        directory = Path(self.temp_directory.name)
        path = directory / "JUI.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_loads_nested_check_settings(self) -> None:
        path = self.write_settings(
            {
                "checks": {
                    "Gain": {"enabled": True, "threshold": -1.5},
                    "Temperature": {"enabled": False},
                },
                "files": ["input.csv"],
            }
        )

        settings = load_settings(path)

        self.assertEqual(settings.checks["Gain"], CheckSettings(True, -1.5))
        self.assertFalse(settings.checks["Temperature"].enabled)
        self.assertEqual(settings.gain_threshold, -1.5)
        self.assertEqual(settings.files, (path.parent / "input.csv",))

    def test_gain_threshold_defaults_to_zero(self) -> None:
        path = self.write_settings(
            {"checks": {"Gain": {"enabled": True}}, "files": []}
        )

        settings = load_settings(path)

        self.assertEqual(settings.gain_threshold, 0.0)

    def test_migrates_previous_flat_gain_configuration(self) -> None:
        path = self.write_settings(
            {
                "checks": {"Gain": "True", "Temperature": "False"},
                "gain_threshold": 2,
                "files": [],
            }
        )

        settings = load_settings(path)

        self.assertTrue(settings.checks["Gain"].enabled)
        self.assertEqual(settings.gain_threshold, 2.0)
        self.assertFalse(settings.checks["Temperature"].enabled)

    def test_rejects_threshold_on_non_gain_check(self) -> None:
        path = self.write_settings(
            {
                "checks": {"Temperature": {"enabled": True, "threshold": 1}},
                "files": [],
            }
        )

        with self.assertRaisesRegex(ValueError, "only the 'Gain' check"):
            load_settings(path)

    def test_rejects_non_finite_threshold(self) -> None:
        with self.assertRaises(ValueError):
            parse_gain_threshold("not-a-number")
        with self.assertRaises(ValueError):
            parse_gain_threshold(float("inf"))


if __name__ == "__main__":
    unittest.main()
