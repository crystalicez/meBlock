"""Regression tests for deterministic paired filtering performance analysis."""

import unittest

from comparison_stats import paired_performance, timing_verdict


class PairedStatsTests(unittest.TestCase):
    def test_same_runs_have_zero_change_and_no_candidate(self):
        report = paired_performance([10.0] * 10, [10.0] * 10)
        self.assertEqual(report["median_paired_change_percent"], 0.0)
        self.assertEqual(report["bootstrap_95_percent_interval_percent"], [0.0, 0.0])
        self.assertIn("INCONCLUSIVE", timing_verdict(report))

    def test_real_differences_need_minimum_pairs(self):
        short = paired_performance([10.0] * 5, [8.0] * 5)
        self.assertIn("fewer than ten", timing_verdict(short))
        long = paired_performance([10.0] * 10, [8.0] * 10)
        self.assertIn("faster", timing_verdict(long))
        slower = paired_performance([10.0] * 10, [12.0] * 10)
        self.assertIn("slower", timing_verdict(slower))

    def test_bootstrap_is_reproducible(self):
        upstream = [10.0, 9.0, 11.0, 10.5, 11.3, 9.6, 10.2, 9.4, 10.7, 8.7]
        meblock = [9.5, 10.1, 10.5, 8.7, 10.8, 9.1, 9.8, 9.2, 10.0, 8.2]
        self.assertEqual(
            paired_performance(upstream, meblock),
            paired_performance(upstream, meblock),
        )

    def test_invalid_sample_times_fail(self):
        for upstream, meblock in (
            ([], []),
            ([1.0, 2.0], [1.0]),
            ([0.0], [1.0]),
            ([1.0], [float("nan")]),
            ([float("inf")], [1.0]),
        ):
            with self.subTest(values=(upstream, meblock)):
                with self.assertRaises(ValueError):
                    paired_performance(upstream, meblock)


if __name__ == "__main__":
    unittest.main()
