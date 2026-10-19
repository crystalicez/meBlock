"""Paired memory comparison must reject mismatched inputs and report exact deltas."""

import unittest

from memory_experiment import schedule, summarize_pairs


def sample(condition, mib, *, selection="stable", source="sha-stock", version="157.0"):
    return {
        "captures": [{"extension_process_explicit_mib": mib}],
        "filter_engine": {
            "selected_lists_sha256": selection,
            "cosmetic_filters": 44192,
            "network_filters": 600000,
            "auto_update": False,
        },
        "source_package_sha256": source,
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
