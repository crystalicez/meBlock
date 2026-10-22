"""User-visible contract tests for the quick, upstream-only SFNE run."""

import unittest

from sfne_quick import summarize_runs, validate_official_runs


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


if __name__ == "__main__":
    unittest.main()
