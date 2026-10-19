"""Paired memory comparison must reject mismatched inputs and report exact deltas."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from memory_experiment import assert_uninstrumented_xpi, main, schedule, summarize_pairs


def sample(condition, mib, *, selection="stable", source="sha-stock", version="157.0"):
    return {
        "captures": [{"extension_process_explicit_mib": mib}],
        "filter_engine": {
            "selected_lists_sha256": selection,
            "selected_lists": 10,
            "cosmetic_filters": 44192,
            "network_filters": 600000,
            "auto_update": False,
        },
        "source_package_sha256": source,
        "source_filter_assets_sha256": "same-pinned-public-filter-asset-hash",
        "settle_seconds_after_filter_readiness": 300,
        "firefox_version": version,
    }


class PairedMemoryTests(unittest.TestCase):
    def test_abba_schedule_is_balanced(self):
        self.assertEqual(
            list(schedule(3)),
            [(0, "stock"), (0, "synthetic"),
             (1, "synthetic"), (1, "stock"),
             (2, "stock"), (2, "synthetic")],
        )

    def test_exact_memory_delta_and_inconclusive_single_pair(self):
        result = summarize_pairs([
            (0, "stock", sample("stock", 50.0)),
            (0, "synthetic", sample("synthetic", 65.0)),
        ])
        self.assertEqual(result["median_paired_delta_mib"], 15.0)
        self.assertEqual(result["per_pair"][0]["delta_percent"], 30.0)
        self.assertEqual(result["pairs"], 1)
        self.assertIn("EXPLORATORY", result["verdict"])

    def test_three_independent_pairs_have_stable_medians(self):
        data = []
        for i, pair in enumerate(((51, 60), (52, 63), (53, 65))):
            data.extend([
                (i, "stock", sample("stock", pair[0])),
                (i, "synthetic", sample("synthetic", pair[1])),
            ])
        result = summarize_pairs(data)
        self.assertEqual(result["median_stock_mib"], 52)
        self.assertEqual(result["median_synthetic_mib"], 63)
        self.assertEqual(result["median_paired_delta_mib"], 11)
        self.assertIn("process-wide", result["verdict"])

    def test_three_matching_deltas_are_not_statistically_decisive(self):
        rows = [
            (i, name, sample(name, amount))
            for i in range(3)
            for name, amount in (("stock", 50.0), ("synthetic", 60.0))
        ]
        result = summarize_pairs(rows)
        stats = result["paired_uncertainty"]
        self.assertEqual(stats["paired_delta_range_mib"], [10.0, 10.0])
        self.assertEqual(stats["paired_delta_bootstrap_95_interval_mib"], [10.0, 10.0])
        self.assertEqual(stats["exact_two_sided_sign_test_p"], 0.25)
        self.assertFalse(stats["directional_process_signal"])
        self.assertIn("directional difference unproven", result["verdict"])

    def test_six_consistent_independent_deltas_are_process_signal_only(self):
        rows = [
            (i, name, sample(name, 50.0 if name == "stock" else 62.0 + i))
            for i in range(6)
            for name in ("stock", "synthetic")
        ]
        result = summarize_pairs(rows)
        stats = result["paired_uncertainty"]
        self.assertEqual(stats["paired_delta_range_mib"], [12.0, 17.0])
        self.assertEqual(stats["exact_two_sided_sign_test_p"], 0.03125)
        self.assertTrue(stats["directional_process_signal"])
        self.assertIn("process-wide signal only", result["verdict"])
        self.assertIn("no per-extension optimization claim", result["verdict"])

    def test_identical_xpi_cannot_be_called_optimization_even_with_six_large_deltas(self):
        rows = [
            (i, name, sample(name, 60.0 if name == "stock" else 45.0))
            for i in range(6)
            for name in ("stock", "synthetic")
        ]
        result = summarize_pairs(rows, mode="build")
        self.assertTrue(result["identical_xpi_negative_control"])
        self.assertTrue(result["paired_uncertainty"]["directional_process_signal"])
        self.assertEqual(result["same_xpi_process_noise_floor"][
            "median_absolute_process_delta_mib"], 15.0)
        self.assertEqual(result["same_xpi_process_noise_floor"][
            "max_absolute_process_delta_mib"], 15.0)
        self.assertEqual(result["same_xpi_process_noise_floor"][
            "median_absolute_process_delta_percent"], 25.0)
        self.assertEqual(result["same_xpi_process_noise_floor"][
            "max_absolute_process_delta_percent"], 25.0)
        self.assertIn("CONTROL: identical XPI", result["verdict"])
        self.assertIn("not an optimization", result["verdict"])

    def test_enabled_list_count_drift_rejected_even_if_hash_is_unchanged(self):
        stable = sample("stock", 54)
        changed = sample("synthetic", 55)
        changed["filter_engine"]["selected_lists"] = 9
        with self.assertRaisesRegex(ValueError, "across pairs"):
            summarize_pairs([
                (0, "stock", stable),
                (0, "synthetic", sample("synthetic", 55)),
                (1, "stock", sample("stock", 54)),
                (1, "synthetic", changed),
            ], mode="build")

    def test_unreported_enabled_list_count_rejected(self):
        missing = sample("stock", 54)
        missing["filter_engine"].pop("selected_lists")
        with self.assertRaisesRegex(ValueError, "enabled-list count"):
            summarize_pairs([
                (0, "stock", missing),
                (0, "synthetic", sample("synthetic", 55)),
            ], mode="build")

    def test_short_identical_xpi_control_is_flagged_in_verdict_and_cli(self):
        rows = []
        for pair in range(3):
            for name, mib in (("stock", 60), ("synthetic", 70)):
                reading = sample(name, mib)
                reading["settle_seconds_after_filter_readiness"] = 2
                rows.append((pair, name, reading))
        report = summarize_pairs(rows, mode="build")
        self.assertEqual(report["condition_labels"], {
            "stock": "control-A", "synthetic": "control-B"
        })
        self.assertTrue(report["identical_xpi_negative_control"])
        self.assertIn("EXPLORATORY", report["verdict"])
        self.assertIn("IDENTICAL-XPI NEGATIVE CONTROL", report["verdict"])
        with tempfile.TemporaryDirectory() as directory:
            with (patch("memory_experiment.PRIVATE_DIR", Path(directory)),
                  patch("memory_experiment.capture", return_value=sample("stock", 60)),
                  redirect_stdout(StringIO())):
                outcome = main([
                    "--addon", "same.xpi", "--candidate-addon", "same.xpi",
                    "--pairs", "1", "--settle-seconds", "2",
                    "--base-network-filters", "600000",
                ])
            self.assertEqual(outcome, 0)
            saved = json.loads(next(Path(directory).glob("paired-memory-*.json")).read_text())
            self.assertEqual(saved["conditions"],
                             "identical XPI in both positions; same-build negative control")

    def test_default_ram_rejects_instrumented_comparison_xpi_before_firefox(self):
        with tempfile.TemporaryDirectory() as directory:
            good = Path(directory) / "stock.xpi"
            bad = Path(directory) / "instrumented.xpi"
            with ZipFile(good, "w") as source:
                source.writestr("manifest.json", '{"manifest_version":2}')
            with ZipFile(bad, "w") as source:
                source.writestr("manifest.json", '{"manifest_version":2}')
                source.writestr("js/meblock-probe.js", "/* test-only */")
            assert_uninstrumented_xpi(good)
            with self.assertRaisesRegex(ValueError, "Instrumented performance XPI"):
                assert_uninstrumented_xpi(bad)
            with redirect_stderr(StringIO()) as text:
                with self.assertRaises(SystemExit) as error:
                    main([
                        "--addon", str(bad), "--candidate-addon", str(good),
                        "--default-lists", "--pairs", "1",
                    ])
            self.assertEqual(error.exception.code, 2)
            self.assertIn("Instrumented performance XPI", text.getvalue())
            with self.assertRaisesRegex(ValueError, "does not exist"):
                assert_uninstrumented_xpi(Path(directory) / "missing.xpi")

    def test_default_lists_auto_pin_first_real_network_count_and_never_inject(self):
        calls = []
        def fake_capture(*_args, **kwargs):
            calls.append(dict(kwargs))
            result = sample("stock", 56 if len(calls) % 2 else 58,
                            source="sha-fork" if _args[0] == Path("fork.xpi")
                            else "sha-stock")
            result["filter_engine"]["network_filters"] = 252243
            return result

        with tempfile.TemporaryDirectory() as directory:
            with (patch("memory_experiment.PRIVATE_DIR", Path(directory)),
                  patch("memory_experiment.capture", side_effect=fake_capture),
                  patch("memory_experiment.assert_uninstrumented_xpi"),
                  redirect_stdout(StringIO())):
                outcome = main([
                    "--addon", "stock.xpi", "--candidate-addon", "fork.xpi",
                    "--default-lists", "--pairs", "2", "--settle-seconds", "2",
                ])
            self.assertEqual(outcome, 0)
            report = json.loads(next(Path(directory).glob("paired-memory-*.json")).read_text())
        self.assertEqual([call["extra_rules"] for call in calls], [0, 0, 0, 0])
        self.assertEqual(
            [call["expected_network_filters"] for call in calls],
            [None, 252243, 252243, 252243],
        )
        self.assertTrue(all(call["offline"] is True for call in calls))
        self.assertEqual(report["workload"], "default-enabled-filter-lists")
        self.assertEqual(report["base_network_filters"], 252243)
        self.assertEqual(report["extra_synthetic_rules"], 0)
        self.assertEqual(report["synthetic_rule_mix"], "none")
        self.assertEqual(report["pinned_profile_identity"], {
            "firefox_version": "157.0",
            "network_filters": 252243,
            "cosmetic_filters": 44192,
            "selected_list_count": 10,
            "selected_list_keys_sha256": "stable",
            "bundled_filter_assets_sha256": "same-pinned-public-filter-asset-hash",
        })
        self.assertIsNone(report["same_xpi_process_noise_floor"])
        self.assertEqual(
            report["conditions"],
            "original XPI vs candidate XPI using default enabled filter lists",
        )

    def test_default_lists_forbid_injected_rules_manual_count_or_missing_candidate(self):
        for extra in (
            [],
            ["--candidate-addon", "fork.xpi", "--extra-synthetic-rules", "4"],
            ["--candidate-addon", "fork.xpi", "--base-network-filters", "252243"],
        ):
            with self.subTest(extra=extra):
                with redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit) as caught:
                        main(["--default-lists", "--pairs", "1", *extra])
                self.assertEqual(caught.exception.code, 2)

    def test_default_lists_reject_filter_count_drift_before_accepting_pair(self):
        values = [252243, 252244]
        def fake_capture(*_args, **kwargs):
            result = sample("stock", 60)
            result["filter_engine"]["network_filters"] = values.pop(0)
            return result
        with tempfile.TemporaryDirectory() as directory:
            with (patch("memory_experiment.PRIVATE_DIR", Path(directory)),
                  patch("memory_experiment.capture", side_effect=fake_capture),
                  patch("memory_experiment.assert_uninstrumented_xpi"),
                  redirect_stdout(StringIO()) as output):
                outcome = main([
                    "--default-lists", "--candidate-addon", "fork.xpi",
                    "--pairs", "1", "--settle-seconds", "2",
                ])
            self.assertEqual(outcome, 1)
            self.assertIn("filter count drifted", output.getvalue())
            self.assertEqual(list(Path(directory).glob("paired-memory-*.json")), [])

    def test_network_count_drift_across_build_pairs_is_rejected(self):
        rows = []
        for pair, count in ((0, 252243), (1, 252244)):
            for role in ("stock", "synthetic"):
                result = sample(role, 62 if role == "stock" else 65)
                result["filter_engine"]["network_filters"] = count
                rows.append((pair, role, result))
        with self.assertRaisesRegex(ValueError, "across pairs"):
            summarize_pairs(rows, mode="build")

    def test_noisy_pairs_report_spread_and_no_directional_signal(self):
        readings = ((50.0, 60.0), (60.0, 50.0), (55.0, 55.0),
                    (52.0, 53.0), (54.0, 52.0), (56.0, 57.0))
        rows = [
            (i, name, sample(name, pair[0 if name == "stock" else 1]))
            for i, pair in enumerate(readings)
            for name in ("stock", "synthetic")
        ]
        stats = summarize_pairs(rows)["paired_uncertainty"]
        self.assertEqual(stats["paired_delta_range_mib"], [-10.0, 10.0])
        self.assertEqual(stats["exact_two_sided_sign_test_p"], 1.0)
        self.assertFalse(stats["directional_process_signal"])

    def test_three_pairs_without_five_minutes_remain_exploratory(self):
        rows = []
        for pair in range(3):
            for name, mib in (("stock", 55), ("synthetic", 64)):
                data = sample(name, mib)
                data["settle_seconds_after_filter_readiness"] = 30
                rows.append((pair, name, data))
        result = summarize_pairs(rows)
        self.assertEqual(result["minimum_settle_seconds"], 30)
        self.assertIn("shorter than 300 seconds", result["verdict"])

    def test_missing_settling_time_refused(self):
        stock = sample("stock", 55)
        del stock["settle_seconds_after_filter_readiness"]
        with self.assertRaisesRegex(ValueError, "settling duration"):
            summarize_pairs([
                (0, "stock", stock),
                (0, "synthetic", sample("synthetic", 65)),
            ])

    def test_missing_condition_refused(self):
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            summarize_pairs([(0, "stock", sample("stock", 50))])

    def test_auto_update_enabled_refused(self):
        unsafe = sample("synthetic", 65)
        unsafe["filter_engine"]["auto_update"] = True
        with self.assertRaisesRegex(ValueError, "automatic filter updates"):
            summarize_pairs([
                (0, "stock", sample("stock", 50)),
                (0, "synthetic", unsafe),
            ])

    def test_list_selection_drift_refused(self):
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            summarize_pairs([
                (0, "stock", sample("stock", 50)),
                (0, "synthetic", sample("synthetic", 65, selection="changed")),
            ])

    def test_different_xpi_and_browser_refused(self):
        for change in ({"source": "different"}, {"version": "158.0"}):
            with self.subTest(change=change):
                with self.assertRaisesRegex(ValueError, "Firefox or source XPI"):
                    summarize_pairs([
                        (0, "stock", sample("stock", 50)),
                        (0, "synthetic", sample("synthetic", 65, **change)),
                    ])

    def test_cross_pair_list_drift_refused_even_if_each_pair_matches(self):
        with self.assertRaisesRegex(ValueError, "across pairs"):
            summarize_pairs([
                (0, "stock", sample("stock", 50)),
                (0, "synthetic", sample("synthetic", 65)),
                (1, "synthetic", sample("synthetic", 66, selection="changed")),
                (1, "stock", sample("stock", 51, selection="changed")),
            ])

    def test_distinct_builds_match_on_equal_filter_counts(self):
        comparison = summarize_pairs([
            (0, "stock", sample("stock", 60, source="upstream-hash")),
            (0, "synthetic", sample("synthetic", 57, source="candidate-hash")),
        ], mode="build")
        self.assertEqual(comparison["comparison_mode"], "build")
        self.assertEqual(comparison["median_paired_delta_mib"], -3)
        self.assertIn("EXPLORATORY", comparison["verdict"])

    def test_distinct_builds_must_use_same_bundled_filter_assets(self):
        newer = sample("synthetic", 57, source="candidate-hash")
        newer["source_filter_assets_sha256"] = "changed-content"
        with self.assertRaisesRegex(ValueError, "Bundled filter asset"):
            summarize_pairs([
                (0, "stock", sample("stock", 60, source="upstream-hash")),
                (0, "synthetic", newer),
            ], mode="build")

    def test_distinct_builds_must_load_same_network_count(self):
        newer = sample("synthetic", 57, source="candidate-hash")
        newer["filter_engine"]["network_filters"] = 599999
        with self.assertRaisesRegex(ValueError, "compiled network filter counts"):
            summarize_pairs([
                (0, "stock", sample("stock", 60, source="upstream-hash")),
                (0, "synthetic", newer),
            ], mode="build")

    def test_distinct_builds_must_pin_each_source_across_pairs(self):
        with self.assertRaisesRegex(ValueError, "Source XPI changed"):
            summarize_pairs([
                (0, "stock", sample("stock", 60, source="upstream-hash")),
                (0, "synthetic", sample("synthetic", 57, source="candidate-one")),
                (1, "stock", sample("stock", 60, source="upstream-hash")),
                (1, "synthetic", sample("synthetic", 57, source="candidate-two")),
            ], mode="build")

    def test_mixed_cli_summary_labels_network_rule_shape_truthfully(self):
        def fake_capture(*_args, **kwargs):
            reading = 60 if kwargs["extra_rules"] == 0 else 65
            return sample("synthetic", reading)

        with tempfile.TemporaryDirectory() as directory:
            with (patch("memory_experiment.PRIVATE_DIR", Path(directory)),
                  patch("memory_experiment.capture", side_effect=fake_capture),
                  redirect_stdout(StringIO())):
                outcome = main([
                    "--pairs", "1", "--settle-seconds", "2",
                    "--base-network-filters", "5",
                    "--extra-synthetic-rules", "2",
                    "--synthetic-rule-mix", "mixed-network",
                ])
            self.assertEqual(outcome, 0)
            report = json.loads(next(Path(directory).glob("paired-memory-*.json")).read_text())
        self.assertEqual(report["synthetic_rule_mix"], "mixed-network")
        self.assertEqual(report["extra_synthetic_rules"], 2)
        self.assertNotIn("extra_synthetic_hostnames", report)
        self.assertEqual(report["conditions"],
                         "same XPI with and without additional synthetic mixed network rules")
        self.assertIn("EXPLORATORY", report["verdict"])

    def test_unknown_comparison_mode_refused(self):
        with self.assertRaisesRegex(ValueError, "Unknown"):
            summarize_pairs([], mode="unsupported")

    def test_nonfinite_memory_measurements_are_refused(self):
        for invalid in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "positive finite"):
                    summarize_pairs([
                        (0, "stock", sample("stock", invalid)),
                        (0, "synthetic", sample("synthetic", 60)),
                    ])

    def test_safe_category_deltas_do_not_leak_arbitrary_reporter_names(self):
        upstream = sample("stock", 60)
        fork = sample("synthetic", 62)
        upstream["captures"][0]["safe_process_categories_mib"] = {
            "window-objects": 50, "other_process_allocations": 10,
            "private-untrusted-host.example.invalid": 1,
        }
        fork["captures"][0]["safe_process_categories_mib"] = {
            "window-objects": 53, "other_process_allocations": 9,
            "private-untrusted-host.example.invalid": 77,
        }
        result = summarize_pairs([(0, "stock", upstream), (0, "synthetic", fork)])
        aggregate = result["per_pair"][0]["safe_process_category_deltas_mib"]
        self.assertEqual(aggregate, {
            "window-objects": 3, "other_process_allocations": -1
        })
        self.assertNotIn("private-untrusted-host", str(result))

    def test_missing_memory_measurement_refused(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            summarize_pairs([
                (0, "stock", sample("stock", 0)),
                (0, "synthetic", sample("synthetic", 65)),
            ])


if __name__ == "__main__":
    unittest.main()
