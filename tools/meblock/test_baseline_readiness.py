"""Public-safe tests for the local baseline evidence gate."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from baseline_readiness import REQUIRED_BEHAVIOR, assess, main, read_local_report


def evidence():
    pins = {"upstream": "a" * 40, "meblock": "b" * 40}
    assets = {"main": "c" * 40, "prod": "d" * 40}
    probe_hashes = {
        name: "e" * 64 for name in (
            "browser_probe.js", "browser_fixtures.py", "state_probe.js",
            "engine_buffer_probe.js",
        )
    }
    correctness = {
        "harness_files_sha256": dict(probe_hashes),
        "refs": pins,
        "assets": assets,
        "status": "complete",
        "fixture_version": 8,
        "default_enabled_lists_only": False,
        "injected_filter_rules": 147,
        "comparison": {
            "verdict": "CONTROL: matching builds",
            "correctness": {name: True for name in REQUIRED_BEHAVIOR},
        },
    }
    default = {
        "harness_files_sha256": dict(probe_hashes),
        "refs": dict(pins),
        "assets": dict(assets),
        "status": "complete",
        "default_enabled_lists_only": True,
        "injected_filter_rules": 0,
        "comparison": {
            "builds_identical": True,
            "default_enabled_lists_only": True,
            "injected_filter_rules": 0,
            "verdict": "CONTROL: matching builds",
            "correctness": {name: True for name in REQUIRED_BEHAVIOR},
            "engine_buffer_footprint": {
                "upstream": {"compiled_network_filters": 252243},
            },
            "fixture_navigation": {
                name: {
                    "median_run_p95_allowed_script_resource_duration_ms": 9.2
                }
                for name in ("upstream", "meblock")
            },
            "batched_matcher": {
                name: {
                    "schema": "meblock-batched-context-match-summary-v1",
                    "runs": 3,
                    "calibrated_for_amortized_screening": True,
                    "median_run_p95_amortized_context_us": 2.3,
                    "range_run_p95_amortized_context_us": [2.1, 2.5],
                }
                for name in ("upstream", "meblock")
            },
        },
        "rule_derived_probe_coverage": {
            "selected_list_count": 10,
            "selected_list_keys_sha256": "public-key-hash",
        },
        "unfiltered_fixture_control": {
            "navigation": [
                {
                    "load_event_end_ms": 30,
                    "dom_content_loaded_end_ms": 20,
                    "allowed_script_resource_duration_ms": 5,
                }
                for _ in range(20)
            ]
        },
    }
    memory = {
        "comparison_mode": "build",
        "identical_xpi_negative_control": True,
        "workload": "default-enabled-filter-lists",
        "extra_synthetic_rules": 0,
        "pairs": 3,
        "minimum_settle_seconds": 300,
        "verdict": "CONTROL: same XPI",
        "idle_firefox_cpu": {
            "pairs": 3,
            "minimum_observed_seconds": 300.2,
            "paired_deltas_one_core_percent": [0.1, -0.1, 0.0],
            "screening_gate_passed": True,
            "process_pid_churn_observed": False,
            "verdict": "CONTROL: same XPI",
        },
        "pinned_profile_identity": {
            "network_filters": 252243,
            "selected_list_keys_sha256": "public-key-hash",
            "selected_list_count": 10,
        },
    }
    return correctness, default, memory


class BaselineReadinessTests(unittest.TestCase):
    def test_fully_pinned_controllable_evidence_passes_without_claiming_gain(self):
        reports = evidence()
        result = assess(*reports)
        self.assertTrue(result["ready_for_optimization"])
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(result["unresolved"], [])
        self.assertIn("does not prove", result["scope"])
        self.assertNotIn("http", json.dumps(result))
        self.assertNotIn("username", json.dumps(result))

    def test_incomplete_and_exploratory_evidence_fail_independently(self):
        mutations = (
            (0, lambda value: value["comparison"]["correctness"].update(
                per_request_match=False), "independent_correctness_fixture"),
            (0, lambda value: value.update(fixture_version=6),
             "independent_correctness_fixture"),
            (1, lambda value: value.update(injected_filter_rules=1),
             "unmodified_default_list_parity"),
            (1, lambda value: value["comparison"]["correctness"].update(
                browser_page_match=False), "unmodified_default_list_parity"),
            (1, lambda value: value["comparison"]["fixture_navigation"][
                "upstream"].update(
                    median_run_p95_allowed_script_resource_duration_ms=None),
             "browser_local_resource_p95"),
            (1, lambda value: value["unfiltered_fixture_control"].update(
                navigation=[{}] * 19), "browser_local_resource_p95"),
            (1, lambda value: value["unfiltered_fixture_control"][
                "navigation"][0].update(
                    allowed_script_resource_duration_ms=0),
             "browser_local_resource_p95"),
            (1, lambda value: value["comparison"]["batched_matcher"][
                "meblock"].update(
                    calibrated_for_amortized_screening=False),
             "amortized_matcher_context_p95_precision"),
            (1, lambda value: value["comparison"]["batched_matcher"][
                "meblock"].update(runs=1),
             "amortized_matcher_context_p95_precision"),
            (1, lambda value: value["comparison"]["batched_matcher"][
                "upstream"].update(
                    range_run_p95_amortized_context_us=[1.0, 4.0]),
             "amortized_matcher_context_p95_precision"),
            (2, lambda value: value.update(pairs=1),
             "paired_cpu_memory_null_control"),
            (2, lambda value: value["idle_firefox_cpu"].update(
                minimum_observed_seconds=2),
             "paired_cpu_memory_null_control"),
            (2, lambda value: value.update(minimum_settle_seconds=3),
             "paired_cpu_memory_null_control"),
            (2, lambda value: value["idle_firefox_cpu"].update(
                process_pid_churn_observed=True),
             "paired_cpu_memory_null_control"),
            (1, lambda value: value["refs"].update(upstream="other"),
             "pinned_runtime_inputs"),
            (1, lambda value: value["harness_files_sha256"].update(
                **{"browser_probe.js": "bad"}),
             "pinned_runtime_inputs"),
            (2, lambda value: value["pinned_profile_identity"].update(
                network_filters=252244), "pinned_runtime_inputs"),
            (2, lambda value: value["pinned_profile_identity"].update(
                selected_list_keys_sha256="different"), "pinned_runtime_inputs"),
        )
        for index, mutation, failed in mutations:
            with self.subTest(failed=failed, index=index):
                reports = list(deepcopy(evidence()))
                mutation(reports[index])
                result = assess(*reports)
                self.assertFalse(result["ready_for_optimization"])
                self.assertIn(failed, result["unresolved"])

    def test_missing_data_fails_closed_without_echoing_private_fields(self):
        reports = list(evidence())
        reports[2]["raw_file"] = "private/path/browser-profile.json.gz"
        result = assess(*reports)
        self.assertNotIn("private/path", json.dumps(result))
        reports[2] = {}
        self.assertFalse(assess(*reports)["ready_for_optimization"])

    def test_report_loading_is_local_size_limited_and_cli_redacts_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name, report in zip(("correctness", "default", "memory"), evidence()):
                (folder / (name + ".json")).write_text(json.dumps(report), encoding="utf-8")
            with self.subTest("valid"):
                self.assertEqual(main([
                    "--correctness-report", str(folder / "correctness.json"),
                    "--default-report", str(folder / "default.json"),
                    "--memory-report", str(folder / "memory.json"),
                ]), 0)
            path = folder / "correctness.json"
            path.write_text("{bad private payload", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_local_report(path)
            self.assertEqual(main([
                "--correctness-report", str(path),
                "--default-report", str(folder / "default.json"),
                "--memory-report", str(folder / "memory.json"),
            ]), 2)


if __name__ == "__main__":
    unittest.main()
