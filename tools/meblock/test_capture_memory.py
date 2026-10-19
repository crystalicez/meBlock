"""Offline tests for disposable XPI memory workloads; no Firefox profile access."""

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from capture_memory import (
    BUILT_IN_LIST, extension_process_explicit_mib, synthetic_xpi,
    validated_engine_state, wait_for_stable_engine,
)


class CaptureMemoryTests(unittest.TestCase):
    def test_disposable_package_preserves_source_and_injects_only_fixture_rules(self):
        with tempfile.TemporaryDirectory() as root:
            original = Path(root) / "original.xpi"
            disposable = Path(root) / "disposable.xpi"
            with ZipFile(original, "w") as archive:
                archive.writestr(BUILT_IN_LIST, "||fixed.example.invalid^\n")
                archive.writestr("manifest.json", '{"version":"1"}')
            synthetic_xpi(original, disposable, 3)
            repeat = Path(root) / "repeat.xpi"
            synthetic_xpi(original, repeat, 3)
            self.assertEqual(
                hashlib.sha256(disposable.read_bytes()).digest(),
                hashlib.sha256(repeat.read_bytes()).digest(),
            )
            with ZipFile(original) as archive:
                self.assertEqual(archive.read(BUILT_IN_LIST), b"||fixed.example.invalid^\n")
            with ZipFile(disposable) as archive:
                self.assertEqual(
                    archive.read(BUILT_IN_LIST),
                    b"||fixed.example.invalid^\n\n"
                    b"||mb-memory-0000000.example.invalid^\n"
                    b"||mb-memory-0000001.example.invalid^\n"
                    b"||mb-memory-0000002.example.invalid^\n",
                )
                self.assertEqual(archive.read("manifest.json"), b'{"version":"1"}')

    def test_missing_bundled_list_fails(self):
        with tempfile.TemporaryDirectory() as root:
            original = Path(root) / "original.xpi"
            with ZipFile(original, "w") as archive:
                archive.writestr("manifest.json", "{}")
            with self.assertRaisesRegex(ValueError, "bundled"):
                synthetic_xpi(original, Path(root) / "copy.xpi", 1)

    def test_extension_process_attribution_is_explicitly_not_addon_only(self):
        data = [
            {"process": "Extensions (pid 123)", "path": "explicit", "units": 0, "amount": 20 * 1024 * 1024},
            {"process": "Extensions (pid 123)", "path": "explicit/js", "units": 0, "amount": 12 * 1024 * 1024},
            {"process": "Extensions (pid 123)", "path": "explicit/native", "units": 0, "amount": 8 * 1024 * 1024},
            {"process": "Main Process", "path": "explicit", "units": 0, "amount": 900 * 1024 * 1024},
        ]
        self.assertEqual(extension_process_explicit_mib(data), 20.0)
        self.assertIsNone(extension_process_explicit_mib([]))

    def test_engine_readiness_and_exact_filter_count(self):
        report = {
            "network_filters": 852243,
            "cosmetic_filters": 44192,
            "selected_list_keys": [f"public-{i}" for i in range(10)],
            "updating": False,
            "auto_update": False,
        }
        validated = validated_engine_state(report, 600000, 852243)
        self.assertEqual(validated["network_filters"], 852243)
        self.assertEqual(validated["selected_lists"], 10)
        self.assertEqual(len(validated["selected_lists_sha256"]), 64)
        self.assertNotIn("selected_list_keys", validated)
        shuffled = {**report, "selected_list_keys": list(reversed(report["selected_list_keys"]))}
        self.assertEqual(
            validated_engine_state(shuffled, 600000, 852243)["selected_lists_sha256"],
            validated["selected_lists_sha256"],
        )
        different = {**report, "selected_list_keys": ["different-public"] * 10}
        with self.assertRaises(RuntimeError):
            validated_engine_state(different, 600000, 852243)
        for values in (
            {**report, "network_filters": 599999},
            {**report, "updating": True},
            {**report, "selected_list_keys": []},
            {**report, "network_filters": "852243"},
            {**report, "network_filters": 852242},
            {"error": "filter-engine-state-read-failed"},
        ):
            with self.subTest(observed=values):
                with self.assertRaises(RuntimeError):
                    validated_engine_state(values, 600000, 852243)

    def test_stability_gate_catches_late_filter_load(self):
        counts = [252243, 252243, 252243, 262232, 262232, 262232]
        states = [
            {"network_filters": value, "cosmetic_filters": 44192,
             "selected_lists": 1, "selected_lists_sha256": "a" * 64,
             "updating": False}
            for value in counts
        ]
        with patch("capture_memory.read_engine_state", side_effect=states) as reader:
            with patch("capture_memory.time.sleep"):
                observed = wait_for_stable_engine(None, "public-test-addon", 1, 262232,
                                                  attempts=6, interval_seconds=0)
        self.assertEqual(reader.call_count, 6)
        self.assertEqual(observed["network_filters"], 262232)

    def test_stability_gate_rejects_oscillating_filter_counts(self):
        states = [
            {"network_filters": 200 + index % 2,
             "cosmetic_filters": 44, "selected_lists": 1,
             "selected_lists_sha256": "a" * 64, "updating": False}
            for index in range(8)
        ]
        with patch("capture_memory.read_engine_state", side_effect=states):
            with patch("capture_memory.time.sleep"):
                with self.assertRaisesRegex(RuntimeError, "did not stabilize"):
                    wait_for_stable_engine(None, "public-test-addon", 0, None,
                                           attempts=8, interval_seconds=0)

    def test_stability_gate_rejects_filter_list_drift(self):
        stable = {
            "network_filters": 200,
            "cosmetic_filters": 40,
            "selected_lists": 2,
            "updating": False,
        }
        states = [
            {**stable, "selected_lists_sha256": "a" * 64 if index % 2 else "b" * 64}
            for index in range(8)
        ]
        with patch("capture_memory.read_engine_state", side_effect=states):
            with patch("capture_memory.time.sleep"):
                with self.assertRaisesRegex(RuntimeError, "did not stabilize"):
                    wait_for_stable_engine(None, "public-addon", 0, None,
                                           attempts=8, interval_seconds=0)

    def test_negative_synthetic_rule_count_fails(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            synthetic_xpi(Path("missing"), Path("unused"), 0)


if __name__ == "__main__":
    unittest.main()
