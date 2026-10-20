"""Public-safe tests for the local baseline evidence gate."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from baseline_readiness import (
    REQUIRED_BEHAVIOR, ENGINE_READONLY_FILES, assess, main,
    read_local_report, verify_engine_package_identity,
)


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
        "dataset_count": 1460,
        "dataset_sha256": "f" * 64,
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
            "selected_locally_mapped_list_count": 8,
            "selected_without_bundled_asset_count": 2,
            "selected_remote_only_list_count": 1,
            "selected_user_defined_list_count": 1,
            "selected_unresolved_list_count": 0,
            "simple_host_rules_in_shard": 365,
            "request_count": 1460,
            "dataset_sha256": "f" * 64,
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


def package_evidence(folder, default, memory):
    source = folder / "stock.xpi"
    common = {
        "js/static-net-filtering.js": b"// public fixture engine\n",
        **{name: b"// public fixture module\n" for name in ENGINE_READONLY_FILES},
    }
    probe = Path(__file__).with_name("engine_buffer_probe.js").read_text(
        encoding="utf-8"
    )
    suffix = ("\n" + probe).replace("\n", "\r\n").encode("utf-8")
    with ZipFile(source, "w") as archive:
        for name, payload in common.items():
            archive.writestr(name, payload)
    memory["source_package_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    packages = {}
    locations = {}
    for role in ("upstream", "meblock"):
        location = folder / (role + ".xpi")
        with ZipFile(location, "w") as archive:
            for name, payload in common.items():
                if name == "js/static-net-filtering.js":
                    payload += suffix
                archive.writestr(name, payload)
        packages[role] = {"sha256": hashlib.sha256(location.read_bytes()).hexdigest()}
        locations[role] = location
    default["packages"] = packages
    return source, locations


class BaselineReadinessTests(unittest.TestCase):
    def test_fully_pinned_controllable_evidence_passes_without_claiming_gain(self):
        reports = evidence()
        result = assess(*reports, engine_verified=True)
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
            (1, lambda value: value["rule_derived_probe_coverage"].update(
                selected_unresolved_list_count=1),
             "selected_list_source_provenance"),
            (1, lambda value: value.update(dataset_count=1459),
             "selected_list_source_provenance"),
            (1, lambda value: value["rule_derived_probe_coverage"].update(
                selected_remote_only_list_count=2),
             "selected_list_source_provenance"),
            (1, lambda value: value["rule_derived_probe_coverage"].update(
                selected_locally_mapped_list_count=9),
             "selected_list_source_provenance"),
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
                result = assess(*reports, engine_verified=True)
                self.assertFalse(result["ready_for_optimization"])
                self.assertIn(failed, result["unresolved"])

    def test_missing_data_fails_closed_without_echoing_private_fields(self):
        reports = list(evidence())
        reports[2]["raw_file"] = "private/path/browser-profile.json.gz"
        result = assess(*reports, engine_verified=True)
        self.assertNotIn("private/path", json.dumps(result))
        reports[2] = {}
        self.assertFalse(assess(*reports, engine_verified=True)["ready_for_optimization"])

    def test_package_verification_rejects_engine_drift_and_missing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            _, default, memory = evidence()
            stock, paths = package_evidence(folder, default, memory)
            self.assertTrue(verify_engine_package_identity(
                default, memory, stock, paths
            ))
            bad = folder / "different.xpi"
            with ZipFile(paths["upstream"]) as original, ZipFile(bad, "w") as changed:
                for name in original.namelist():
                    body = original.read(name)
                    if name == "js/redirect-engine.js":
                        body += b"// differing code"
                    changed.writestr(name, body)
            broken = dict(paths)
            broken["upstream"] = bad
            default["packages"]["upstream"]["sha256"] = hashlib.sha256(
                bad.read_bytes()
            ).hexdigest()
            self.assertFalse(verify_engine_package_identity(
                default, memory, stock, broken
            ))
            self.assertFalse(assess(*evidence())["ready_for_optimization"])

    def test_real_xpi_byte_identity_rejects_modified_engine_and_modified_report(self):
        stock_engine = b"export function matchRequest() { return 1; }\n"
        from baseline_readiness import ENGINE_READONLY_FILES
        suffix = ("\n" + (
            Path(__file__).with_name("engine_buffer_probe.js").read_text(
                encoding="utf-8"
            )
        )).encode("utf-8").replace(b"\n", b"\r\n")

        def make_xpi(path, *, instrumented, override=None):
            with ZipFile(path, "w") as archive:
                archive.writestr(
                    "js/static-net-filtering.js",
                    override if override is not None else (
                        stock_engine + suffix if instrumented else stock_engine
                    ),
                )
                for filename in ENGINE_READONLY_FILES:
                    archive.writestr(filename, b"known-public-test-content")

        def digest(path):
            return hashlib.sha256(path.read_bytes()).hexdigest()

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            stock = folder / "stock.xpi"
            upstream = folder / "upstream.xpi"
            meblock = folder / "meblock.xpi"
            make_xpi(stock, instrumented=False)
            make_xpi(upstream, instrumented=True)
            make_xpi(meblock, instrumented=True)
            default, memory = evidence()[1:]
            default["packages"] = {
                "upstream": {"sha256": digest(upstream)},
                "meblock": {"sha256": digest(meblock)},
            }
            memory["source_package_sha256"] = digest(stock)
            packages = {"upstream": upstream, "meblock": meblock}

            self.assertTrue(verify_engine_package_identity(
                default, memory, stock, packages,
            ))
            self.assertFalse(assess(
                *evidence(), engine_verified=False
            )["ready_for_optimization"])
            memory["source_package_sha256"] = "a" * 64
            self.assertFalse(verify_engine_package_identity(
                default, memory, stock, packages,
            ))
            memory["source_package_sha256"] = digest(stock)
            make_xpi(meblock, instrumented=True, override=b"tampered engine")
            default["packages"]["meblock"]["sha256"] = digest(meblock)
            self.assertFalse(verify_engine_package_identity(
                default, memory, stock, packages,
            ))
            make_xpi(meblock, instrumented=True)
            with ZipFile(upstream, "w") as archive:
                archive.writestr("js/static-net-filtering.js", stock_engine + suffix)
                for filename in ENGINE_READONLY_FILES:
                    archive.writestr(
                        filename, b"changed-content"
                        if filename == "js/redirect-engine.js"
                        else b"known-public-test-content"
                    )
            default["packages"]["upstream"]["sha256"] = digest(upstream)
            self.assertFalse(verify_engine_package_identity(
                default, memory, stock, packages,
            ))
            self.assertFalse(verify_engine_package_identity(
                default, memory, stock, {
                    "upstream": folder / "missing.xpi",
                    "meblock": meblock,
                },
            ))

    def test_report_loading_is_local_size_limited_and_cli_redacts_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            reports = list(evidence())
            stock, paths = package_evidence(folder, reports[1], reports[2])
            for name, report in zip(("correctness", "default", "memory"), reports):
                (folder / (name + ".json")).write_text(
                    json.dumps(report), encoding="utf-8"
                )
            args = [
                "--correctness-report", str(folder / "correctness.json"),
                "--default-report", str(folder / "default.json"),
                "--memory-report", str(folder / "memory.json"),
                "--memory-xpi", str(stock),
                "--upstream-xpi", str(paths["upstream"]),
                "--meblock-xpi", str(paths["meblock"]),
            ]
            self.assertEqual(main(args), 0)
            path = folder / "correctness.json"
            path.write_text("{bad private payload", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_local_report(path)
            self.assertEqual(main(args), 2)
            path.write_text('{"cpu": NaN}', encoding="utf-8")
            with self.assertRaises(ValueError):
                read_local_report(path)


if __name__ == "__main__":
    unittest.main()
