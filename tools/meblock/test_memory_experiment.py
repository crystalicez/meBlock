"""Paired memory comparison must reject mismatched inputs and report exact deltas."""

import unittest

from memory_experiment import schedule, summarize_pairs


def sample(condition, mib, *, selection="stable", source="sha-stock", version="157.0"):
    return {
        "captures": [{"extension_process_explicit_mib": mib}],
        "filter_engine": {
            "selected_lists_sha256": selection,
            "cosmetic_filters": 44192,
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

    def test_missing_memory_measurement_refused(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            summarize_pairs([
                (0, "stock", sample("stock", 0)),
                (0, "synthetic", sample("synthetic", 65)),
            ])


if __name__ == "__main__":
    unittest.main()
