"""Public-safe tests for the local baseline evidence gate."""

from copy import deepcopy
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from baseline_readiness import (
    REQUIRED_BEHAVIOR, ENGINE_READONLY_FILES, assess, main,
    read_local_report, verify_engine_package_identity, verify_report_replay,
)


def evidence():
    pins = {"upstream": "a" * 40, "meblock": "b" * 40}
    assets = {"main": "c" * 40, "prod": "d" * 40}
    probe_hashes = {
        name: hashlib.sha256(
            Path(__file__).with_name(name).read_bytes()
        ).hexdigest()
        for name in (
            "compare_original.py", "browser_probe.js", "engine_buffer_probe.js",
            "state_probe.js", "browser_fixtures.py", "comparison_stats.py",
            "benchmark_firefox.py", "filtering_benchmark.py",
        )
    }
    correctness = {
        "harness_files_sha256": dict(probe_hashes),
        "refs": pins,
        "assets": assets,
        "status": "complete",
        "fixture_version": 9,
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
                    "schema": "meblock-batched-context-match-summary-v2",
                    "runs": 3,
                    "by_request_type": {
                        kind: {
                            "contexts_per_pass": count,
                            "samples_per_run": count * 3,
                            "calibrated_for_amortized_screening": True,
                            "stable_three_profile_null_control": True,
                            "median_run_p95_amortized_context_us": 2.3,
                            "relative_run_p95_span": 0.01,
                        }
                        for kind, count in (
                            ("script", 360), ("image", 360),
                            ("xmlhttprequest", 360), ("stylesheet", 380),
                        )
                    },
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
            "request_type_request_counts": {
                "script": 360, "image": 360,
                "xmlhttprequest": 360, "stylesheet": 380,
            },
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
        "background.html": b"<body>original</body>",
        "js/background.js": b"autoUpdate: true",
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
                elif name in ("background.html", "js/background.js"):
                    payload += b" test-only change"
                archive.writestr(name, payload)
            for added in (
                "assets/meblock-requests.json",
                "js/meblock-probe.js", "js/meblock-state-probe.js",
            ):
                archive.writestr(added, b"test-only data")
        packages[role] = {"sha256": hashlib.sha256(location.read_bytes()).hexdigest()}
        locations[role] = location
    default["packages"] = packages
    return source, locations


class BaselineReadinessTests(unittest.TestCase):
    def test_fully_pinned_controllable_evidence_passes_without_claiming_gain(self):
        reports = evidence()
        result = assess(*reports, engine_verified=True, evidence_replayed=True)
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
            (0, lambda value: value.update(fixture_version=8),
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
                request_type_request_counts={"script": 1460}),
             "default_request_type_breadth"),
            (1, lambda value: value["rule_derived_probe_coverage"].update(
                request_type_request_counts={
                    "script": 1160, "image": 100,
                    "xmlhttprequest": 100, "stylesheet": 100,
                }),
             "default_request_type_breadth"),
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
                result = assess(*reports, engine_verified=True, evidence_replayed=True)
                self.assertFalse(result["ready_for_optimization"])
                self.assertIn(failed, result["unresolved"])

    def test_two_matching_stale_harness_hashes_fail_against_current_files(self):
        reports = list(evidence())
        for report in reports[:2]:
            report["harness_files_sha256"]["browser_fixtures.py"] = "0" * 64
        status = assess(*reports, engine_verified=True, evidence_replayed=True)
        self.assertFalse(status["ready_for_optimization"])
        self.assertFalse(status["checks"]["pinned_runtime_inputs"])

    def test_one_unstable_request_type_rejects_aggregate_p95_pass(self):
        reports = list(evidence())
        good = assess(*reports, engine_verified=True, evidence_replayed=True)
        self.assertTrue(good["ready_for_optimization"])
        changed = reports[1]["comparison"]["batched_matcher"]["meblock"]
        changed["by_request_type"]["image"]["stable_three_profile_null_control"] = False
        bad = assess(*reports, engine_verified=True, evidence_replayed=True)
        self.assertFalse(bad["ready_for_optimization"])
        self.assertTrue(bad["checks"]["amortized_matcher_context_p95_precision"])
        self.assertFalse(bad["checks"]["per_request_type_amortized_p95_precision"])
        changed["by_request_type"]["image"]["stable_three_profile_null_control"] = True
        changed["by_request_type"].pop("stylesheet")
        missing = assess(*reports, engine_verified=True, evidence_replayed=True)
        self.assertFalse(missing["ready_for_optimization"])
        self.assertFalse(missing["checks"]["per_request_type_amortized_p95_precision"])

    def test_missing_data_fails_closed_without_echoing_private_fields(self):
        reports = list(evidence())
        reports[2]["raw_file"] = "private/path/browser-profile.json.gz"
        result = assess(*reports, engine_verified=True, evidence_replayed=True)
        self.assertNotIn("private/path", json.dumps(result))
        reports[2] = {}
        self.assertFalse(assess(*reports, engine_verified=True, evidence_replayed=True)["ready_for_optimization"])

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
                archive.writestr(
                    "background.html",
                    b"<body>modified</body>" if instrumented else b"<body>original</body>",
                )
                archive.writestr(
                    "js/background.js",
                    b"autoUpdate: false" if instrumented else b"autoUpdate: true",
                )
                if instrumented:
                    for added in (
                        "assets/meblock-requests.json",
                        "js/meblock-probe.js", "js/meblock-state-probe.js",
                    ):
                        archive.writestr(added, b"test-only fixture data")

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
                archive.writestr("background.html", b"<body>modified</body>")
                archive.writestr("js/background.js", b"autoUpdate: false")
                for added in (
                    "assets/meblock-requests.json",
                    "js/meblock-probe.js", "js/meblock-state-probe.js",
                ):
                    archive.writestr(added, b"test-only fixture data")
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

    def test_report_replay_rejects_changed_summary_run_and_dataset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset_path = root / "public-requests.json"
            requests = [{
                "frameUrl": "https://reader.example.invalid/",
                "url": "https://asset.example.invalid/test.js",
                "cpt": "script",
            }]
            raw = json.dumps(requests).encode("utf-8")
            dataset_path.write_bytes(raw)
            report_path = root / "report.json"
            for role in ("upstream", "meblock"):
                (root / f"{role}-run-1.json").write_text(
                    json.dumps({"test_value": 1}), encoding="utf-8",
                )

            def replay_comparison(data, rows, **kwargs):
                self.assertEqual(len(rows), 1)
                self.assertFalse(kwargs["default_lists"])
                return {
                    "recomputed_value": sum(
                        item["test_value"]
                        for role in ("upstream", "meblock")
                        for item in data[role]
                    ),
                }

            report = {
                "runs_per_build": 1,
                "default_enabled_lists_only": False,
                "dataset_count": 1,
                "dataset_sha256": hashlib.sha256(raw).hexdigest(),
                "rule_derived_probe_coverage": None,
                "comparison": {"recomputed_value": 2},
            }
            with patch("compare_original.compare", side_effect=replay_comparison):
                self.assertTrue(verify_report_replay(
                    report_path, dataset_path, report, default_lists=False,
                ))
                changed = deepcopy(report)
                changed["comparison"]["recomputed_value"] = 3
                self.assertFalse(verify_report_replay(
                    report_path, dataset_path, changed, default_lists=False,
                ))
                (root / "upstream-run-1.json").write_text(
                    json.dumps({"test_value": 2}), encoding="utf-8",
                )
                self.assertFalse(verify_report_replay(
                    report_path, dataset_path, report, default_lists=False,
                ))
                (root / "upstream-run-1.json").unlink()
                self.assertFalse(verify_report_replay(
                    report_path, dataset_path, report, default_lists=False,
                ))
            dataset_path.write_text("[]", encoding="utf-8")
            self.assertFalse(verify_report_replay(
                report_path, dataset_path, report, default_lists=False,
            ))

    def test_summary_is_not_enough_without_replay_gate(self):
        result = assess(*evidence(), engine_verified=True)
        self.assertFalse(result["ready_for_optimization"])
        self.assertFalse(result["checks"]["per_profile_evidence_replay"])

    def test_report_loading_is_local_size_limited_and_cli_redacts_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            from browser_fixtures import (
                PRECEDENCE_EXPECTED, WITHOUT_ADDON, PARAMETERS_WITHOUT_ADDON,
            )
            reports = list(evidence())
            reports[0]["runs_per_build"] = 1
            reports[0]["unfiltered_fixture_control"] = {
                "page": deepcopy(WITHOUT_ADDON),
                "parameters": dict(PARAMETERS_WITHOUT_ADDON),
            }
            for role in ("upstream", "meblock"):
                (folder / f"{role}-run-1.json").write_text(
                    json.dumps({"fixture_precedence": list(PRECEDENCE_EXPECTED)}),
                    encoding="utf-8",
                )
            stock, paths = package_evidence(folder, reports[1], reports[2])
            for name, report in zip(("correctness", "default", "memory"), reports):
                (folder / (name + ".json")).write_text(
                    json.dumps(report), encoding="utf-8"
                )
            args = [
                "--correctness-report", str(folder / "correctness.json"),
                "--default-report", str(folder / "default.json"),
                "--memory-report", str(folder / "memory.json"),
                "--correctness-dataset", str(folder / "correctness-dataset.json"),
                "--default-dataset", str(folder / "default-dataset.json"),
                "--memory-xpi", str(stock),
                "--upstream-xpi", str(paths["upstream"]),
                "--meblock-xpi", str(paths["meblock"]),
            ]
            with patch("baseline_readiness.verify_report_replay", return_value=True):
                self.assertEqual(main(args), 0)
                baseline_stdout = io.StringIO()
                with redirect_stdout(baseline_stdout):
                    self.assertEqual(main(args), 0)
                baseline_summary = json.loads(baseline_stdout.getvalue())
                self.assertTrue(baseline_summary["ready_for_optimization"])
                self.assertFalse(
                    baseline_summary["ready_for_specific_engine_change"]
                )
                target = [*args, "--target-family", "network-exceptions"]
                self.assertEqual(main(target), 0)
                family_stdout = io.StringIO()
                with redirect_stdout(family_stdout):
                    self.assertEqual(main(target), 0)
                family_summary = json.loads(family_stdout.getvalue())
                self.assertFalse(family_summary["ready_for_specific_engine_change"])
                self.assertEqual(
                    main([*args, "--target-family", "scriptlet-other-families"]), 1,
                )
                self.assertEqual(
                    main([*args, "--target-path", "src/js/static-net-filtering.js"]), 1,
                )
                self.assertEqual(
                    main([*target, "--target-path", "src/js/redirect-engine.js"]), 1,
                )
                self.assertEqual(
                    main([*args, "--target-path", "src/js/unmapped.js"]), 1,
                )
                changed = list(PRECEDENCE_EXPECTED)
                changed[2] = 0
                (folder / "upstream-run-1.json").write_text(
                    json.dumps({"fixture_precedence": changed}), encoding="utf-8",
                )
                self.assertEqual(main(target), 1)
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
