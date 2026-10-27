"""User-visible contract tests for the quick, upstream-only SFNE run."""

import unittest

from sfne_quick import summarize_runs, validate_official_runs, null_control_exit_code


def official(*, duration=250, blocked=2, redirect=0):
    return (
        "Benchmarked static network filtering engine:\n"
        f"Evaluated 3 requests in {duration} ms\n"
        f"Average: {duration / 3:.3f} ms per request\n"
        f"Not blocked: {3 - blocked}\n"
        f"Blocked: {blocked}\n"
        "Unblocked: 0\n"
        f"redirect=: {redirect}\n"
        "removeparam=: 0\nurlskip=: 0\ncsp=: 0\n"
        "permissions=: 0\nreplace=: 0\n"
    )


def profile(durations=(210, 240, 250, 260, 290), *, blocked=2, selected=None):
    return {
        "status": "ok",
        "engine_ready": True,
        "selected_filter_lists": selected if selected is not None else ["easylist", "ublock-filters"],
        "official_runs": [official(duration=d, blocked=blocked) for d in durations],
    }


class SfneQuickTests(unittest.TestCase):
    def test_original_benchmark_is_the_only_speed_measure_and_rounding_is_reported(self):
        data = {
            "upstream": [profile(), profile((215, 245, 255, 265, 295))],
            "meblock": [profile((220, 250, 260, 270, 300)),
                        profile((225, 255, 265, 275, 305))],
        }
        summary = summarize_runs(data, 3)
        self.assertEqual(summary["requests_per_trial"], 3)
        self.assertEqual(summary["pairs"], 2)
        self.assertEqual(summary["counts"]["blocked"], 2)
        self.assertEqual(summary["roles"]["upstream"]["all_profile_medians_ms"], [250, 255])
        self.assertEqual(summary["roles"]["meblock"]["all_profile_medians_ms"], [260, 265])
        self.assertEqual(summary["timer_minimum_trial_ms_rounded"], 210)
        self.assertTrue(summary["whole_ms_rounding_below_half_percent"])
        self.assertFalse(summary["three_percent_effect_qualified"])

    def test_short_original_trial_is_not_certified_precise(self):
        data = {"upstream": [profile((5, 8, 9, 10, 11))],
                "meblock": [profile((6, 7, 8, 9, 12))]}
        self.assertFalse(summarize_runs(data, 3)["whole_ms_rounding_below_half_percent"])

    def test_rejects_partial_original_run(self):
        incomplete = profile()
        incomplete["official_runs"].pop()
        with self.assertRaisesRegex(ValueError, "exactly five"):
            validate_official_runs(incomplete, 3)

    def test_rejects_wrong_request_count(self):
        with self.assertRaisesRegex(ValueError, "skipped requests"):
            validate_official_runs(profile(), 4)

    def test_rejects_decision_drift_within_one_profile(self):
        inconsistent = profile()
        inconsistent["official_runs"][2] = official(blocked=1)
        with self.assertRaisesRegex(ValueError, "counts changed"):
            validate_official_runs(inconsistent, 3)

    def test_rejects_cross_build_count_changes(self):
        data = {"upstream": [profile()], "meblock": [profile(blocked=1)]}
        with self.assertRaisesRegex(ValueError, "decisions or enabled lists"):
            summarize_runs(data, 3)

    def test_rejects_selected_list_changes(self):
        data = {
            "upstream": [profile()],
            "meblock": [profile(selected=["easylist", "different"])],
        }
        with self.assertRaisesRegex(ValueError, "decisions or enabled lists"):
            summarize_runs(data, 3)

    def test_rejects_unready_firefox_and_duplicate_lists(self):
        unready = profile()
        unready["engine_ready"] = False
        with self.assertRaisesRegex(ValueError, "state is incomplete"):
            validate_official_runs(unready, 3)
        duplicate = profile(selected=["easylist", "easylist"])
        with self.assertRaisesRegex(ValueError, "state is incomplete"):
            validate_official_runs(duplicate, 3)

    def test_variance_diagnostic_keeps_all_original_trials_separate(self):
        from sfne_quick import summarize_variance
        profile = {
            "diagnostic_warmup_ms": [1400, 1350, 1300, 1250, 1200, 1180, 1160, 1140],
            "diagnostic_measured_ms": [
                1130, 1120, 1100, 1090, 1080, 1070,
                1060, 1050, 1040, 1030, 1020, 1010,
            ],
            "diagnostic_total_calls": 20,
            "diagnostic_host_pressure": {"host_cpu_percent_median": 20.5},
        }
        summary = summarize_variance({"upstream": [profile], "meblock": [profile]})
        record = summary["upstream"][0]
        self.assertEqual(record["early_median_ms"], 1100)
        self.assertEqual(record["late_median_ms"], 1030)
        self.assertEqual(record["measured_min_ms"], 1010)
        self.assertEqual(len(record["warmup_ms"]), 8)
        self.assertEqual(record["host_pressure"]["host_cpu_percent_median"], 20.5)
        profile["diagnostic_measured_ms"].pop()
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            summarize_variance({"upstream": [profile]})

    def test_null_control_exit_distinguishes_complete_unqualified_from_smoke(self):
        passed = {"paired_noise": {"ready_for_candidate_effect_screening": True}}
        failed = {"paired_noise": {"ready_for_candidate_effect_screening": False}}
        self.assertEqual(null_control_exit_code(True, passed), 0)
        self.assertEqual(null_control_exit_code(True, failed), 3)
        self.assertEqual(null_control_exit_code(False, failed), 0)

    def test_ten_independent_same_code_pairs_pass_strict_three_percent_null(self):
        from sfne_quick import assess_paired_noise
        same = [1250.0] * 10
        result = assess_paired_noise(
            same, same, identical_builds=True, clock_calibrated=True
        )
        self.assertTrue(result["ready_for_candidate_effect_screening"])
        self.assertEqual(result["pooled"]["median_paired_change_percent"], 0.0)
        self.assertEqual(result["by_run_order"]["upstream_first"]["pairs"], 5)
        self.assertEqual(result["by_run_order"]["meblock_first"]["pairs"], 5)

    def test_one_run_order_five_percent_drift_fails_even_if_other_order_stable(self):
        from sfne_quick import assess_paired_noise
        before = [1250.0] * 10
        after = [1250.0 if i % 2 == 0 else 1312.5 for i in range(10)]
        result = assess_paired_noise(
            before, after, identical_builds=True, clock_calibrated=True
        )
        self.assertFalse(result["ready_for_candidate_effect_screening"])
        self.assertTrue(
            result["by_run_order"]["upstream_first"]["null_equivalent_within_three_percent"]
        )
        self.assertFalse(
            result["by_run_order"]["meblock_first"]["null_equivalent_within_three_percent"]
        )

    def test_null_preflight_rejects_small_sample_timer_failure_and_different_builds(self):
        from sfne_quick import assess_paired_noise
        for counts, identical, calibrated in (
            (8, True, True), (10, False, True), (10, True, False)
        ):
            result = assess_paired_noise(
                [1100] * counts, [1100] * counts,
                identical_builds=identical, clock_calibrated=calibrated,
            )
            self.assertFalse(result["ready_for_candidate_effect_screening"])



if __name__ == "__main__":
    unittest.main()
