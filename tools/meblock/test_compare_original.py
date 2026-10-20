"""Regression tests for the two-build Firefox comparison tool."""

import importlib.util
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import urllib.request
import unittest
from zipfile import ZipFile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("compare_original", HERE / "compare_original.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
from browser_fixtures import (
    ROUTES, RULES, WITH_ADDON, WITHOUT_ADDON,
    PARAMETERS_WITH_ADDON, PARAMETERS_WITHOUT_ADDON,
    DYNAMIC_FIREWALL_EXPECTED, STATE_PERSISTENCE_KEY,
    EXTRA_COSMETIC_COUNT, EXTRA_VISIBLE_COUNT, EXTRA_SCRIPTLET_COUNT,
    EXTRA_PROCEDURAL_COUNT,
    FIXTURE_VERSION, PAGE,
    PRECEDENCE_RULES, PRECEDENCE_CASES, PRECEDENCE_EXPECTED,
    precedence_case_request,
)


def official(blocked=1, redirect=0):
    return (
        "Benchmarked static network filtering engine:\n"
        "Evaluated 3 requests in 12 ms\n"
        "Average: 4.000 ms per request\n"
        f"Not blocked: {2 if blocked == 1 else 1}\n"
        f"Blocked: {blocked}\n"
        "Unblocked: 0\n"
        f"redirect=: {redirect}\nremoveparam=: 0\nurlskip=: 0\n"
        "csp=: 0\npermissions=: 0\nreplace=: 0\n"
    )


def make_run(outcomes=None, speed=7, modifier_masks=None):
    outcomes = [1, 0, 0] if outcomes is None else outcomes
    modifier_masks = [0, 0, 0] if modifier_masks is None else modifier_masks
    blocked = sum(v == 1 for v in outcomes)
    return {
        "outcomes": outcomes, "modifier_masks": modifier_masks, "us_per_request": speed,
        "browser_behavior": deepcopy(WITH_ADDON),
        "browser_parameters": dict(PARAMETERS_WITH_ADDON),
        "dynamic_firewall": {
            "cases": dict(DYNAMIC_FIREWALL_EXPECTED),
            "rollback": True,
            "source": "fixture-meblock.invalid",
            "destination": "media.fixture-meblock.invalid",
        },
        "state_persistence": {
            "setting": STATE_PERSISTENCE_KEY,
            "changed_and_reloaded": True,
            "restored": True,
            "originally": False,
        },
        "dataset_rows": 3, "official": official(
            blocked, redirect=sum(bool(mask & 1) for mask in modifier_masks)
        ),
        "decisions": {
            "blocked": blocked, "allowed": 0,
            "passed": 3 - blocked,
        },
    }


DATASET = [
    {"frameUrl": "https://test.invalid", "url": f"https://test.invalid/{i}", "cpt": "script"}
    for i in range(3)
]


def buffer_sample(size=1024):
    sizes = {
        "filter_data": size, "origin_trie": 256,
        "destination_trie": 384, "pattern_trie": 512,
    }
    return {
        "schema": "meblock-engine-buffers-v1",
        "allocated_bytes": sizes,
        "total_known_allocated_bytes": sum(sizes.values()),
        "filter_data_used_bytes": 256,
        "filter_references_count": 7,
        "compiled_network_filters": 252243,
        "wasm_enabled": {
            "origin_trie": False, "destination_trie": False,
            "pattern_trie": False,
        },
    }


class CompareTests(unittest.TestCase):
    def test_fixture_precedence_is_required_for_comparator_when_reported(self):
        expected = list(PRECEDENCE_EXPECTED)
        data = {
            "upstream": [make_run()],
            "meblock": [make_run()],
        }
        for role in data:
            data[role][0]["fixture_precedence"] = list(expected)
        accepted = runner.compare(data, DATASET, identical_build=True)
        self.assertTrue(accepted["correctness"]["fixture_precedence_match"])
        self.assertIn("CONTROL", accepted["verdict"])
        data["meblock"][0]["fixture_precedence"][5] = 1
        rejected = runner.compare(data, DATASET, identical_build=True)
        self.assertFalse(rejected["correctness"]["fixture_precedence_match"])
        self.assertEqual(rejected["verdict"], "FAIL: behavioral regression")
        data["meblock"][0]["fixture_precedence"] = []
        missing = runner.compare(data, DATASET)
        self.assertFalse(missing["correctness"]["fixture_precedence_match"])
        self.assertEqual(missing["verdict"], "FAIL: behavioral regression")

    def test_fixture_precedence_oracle_is_fixed_per_index(self):
        self.assertEqual(len(PRECEDENCE_CASES), 39)
        self.assertEqual(
            PRECEDENCE_EXPECTED,
            (1, 0, 2, 1, 0, 2, 1, 1, 2, 1, 0, 1, 0, 1, 0, 0, 1, 0,
             1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 1, 0, 2, 1, 1,
             1, 0, 0, 1, 0),
        )
        self.assertEqual(
            precedence_case_request(PRECEDENCE_CASES[2]),
            {
                "frameUrl": "https://reader.test/",
                "url": "https://mb-fixture-override.example.invalid/test.js",
                "cpt": "script",
            },
        )
        self.assertEqual(
            precedence_case_request(PRECEDENCE_CASES[34]),
            {
                "frameUrl": "https://reader.test/",
                "url": "https://mb-fixture-pathcase.example.invalid/Blocked.js",
                "cpt": "script",
            },
        )
        self.assertEqual(
            precedence_case_request(PRECEDENCE_CASES[35])["url"],
            "https://mb-fixture-pathcase.example.invalid/blocked.js",
        )
        self.assertEqual(runner.precedence_mismatches(
            list(PRECEDENCE_EXPECTED), default_lists=False,
        ), [])
        self.assertEqual(runner.precedence_mismatches(
            [], default_lists=True,
        ), [])
        self.assertEqual(len(runner.precedence_mismatches(
            [], default_lists=False,
        )), 1)
        self.assertEqual(len(runner.precedence_mismatches(
            list(PRECEDENCE_EXPECTED), default_lists=True,
        )), 1)
        for i in range(len(PRECEDENCE_EXPECTED)):
            mutated = list(PRECEDENCE_EXPECTED)
            mutated[i] = (mutated[i] + 1) % 3
            problems = runner.precedence_mismatches(mutated, default_lists=False)
            self.assertEqual(problems[0]["index"], i)
            self.assertEqual(problems[0]["name"], PRECEDENCE_CASES[i][0])
            self.assertEqual(len(problems), 1)
        corrupted = list(PRECEDENCE_EXPECTED)
        corrupted[0] = True
        self.assertEqual(len(runner.precedence_mismatches(
            corrupted, default_lists=False,
        )), 1)
        self.assertNotIn("https://", str(runner.precedence_mismatches(
            corrupted, default_lists=False,
        )))

    def test_page_side_fixture_is_deterministic_and_has_three_selector_shapes(self):
        self.assertEqual(FIXTURE_VERSION, 8)
        self.assertEqual(EXTRA_COSMETIC_COUNT, 96)
        self.assertEqual(EXTRA_VISIBLE_COUNT, 32)
        self.assertEqual(EXTRA_SCRIPTLET_COUNT, 12)
        self.assertEqual(EXTRA_PROCEDURAL_COUNT, 8)
        self.assertEqual(len(RULES), 8 + 96 + 12 + 23 + 8)
        self.assertIn("127.0.0.1##.mb-extra-class-000", RULES)
        self.assertIn("127.0.0.1###mb-extra-001", RULES)
        self.assertIn('127.0.0.1##[data-mb-extra="002"]', RULES)
        self.assertIn("127.0.0.1##+js(set-constant, mbFixtureVar011, true)", RULES)
        self.assertEqual(PAGE.count("cosmetic probe</div>"), 96)
        self.assertEqual(PAGE.count("cosmetic near miss</div>"), 96)
        self.assertEqual(PAGE.count("visible probe</div>"), 32)
        self.assertEqual(PAGE.count("mb-procedural-needle-"), 8)
        self.assertEqual(PAGE.count("near-miss-"), 8)
        self.assertEqual(WITH_ADDON["extra_cosmetic_hidden_count"], 96)
        self.assertEqual(WITH_ADDON["extra_visible_count"], 32)
        self.assertEqual(WITH_ADDON["extra_scriptlets_applied_count"], 12)
        self.assertEqual(WITH_ADDON["extra_procedural_hidden_by_index"], [True] * 8)
        self.assertEqual(WITHOUT_ADDON["extra_cosmetic_hidden_count"], 0)
        self.assertEqual(WITHOUT_ADDON["extra_visible_count"], 32)
        self.assertEqual(WITHOUT_ADDON["extra_scriptlets_applied_count"], 0)
        self.assertEqual(WITHOUT_ADDON["extra_procedural_hidden_by_index"], [False] * 8)
        for expected, hidden, applied in (
            (WITHOUT_ADDON, False, False),
            (WITH_ADDON, True, True),
        ):
            self.assertEqual(expected["extra_cosmetic_present_by_index"], [True] * 96)
            self.assertEqual(expected["extra_cosmetic_hidden_by_index"], [hidden] * 96)
            self.assertEqual(expected["extra_cosmetic_near_miss_visible_by_index"], [True] * 96)
            self.assertEqual(expected["extra_visible_by_index"], [True] * 32)
            self.assertEqual(expected["extra_scriptlets_applied_by_index"], [applied] * 12)
            self.assertEqual(expected["extra_scriptlet_near_miss_untouched_by_index"], [True] * 12)

    def test_every_page_side_rule_index_fails_when_its_result_is_wrong(self):
        for key in (
            "extra_cosmetic_present_by_index",
            "extra_cosmetic_hidden_by_index",
            "extra_cosmetic_near_miss_visible_by_index",
            "extra_visible_by_index",
            "extra_procedural_present_by_index",
            "extra_procedural_hidden_by_index",
            "extra_procedural_near_miss_visible_by_index",
            "extra_scriptlets_applied_by_index",
            "extra_scriptlet_near_miss_untouched_by_index",
        ):
            for index in range(len(WITH_ADDON[key])):
                with self.subTest(key=key, index=index):
                    changed = deepcopy(WITH_ADDON)
                    changed[key][index] = not changed[key][index]
                    self.assertEqual(
                        runner.fixture_page_mismatches(changed, WITH_ADDON),
                        [{"field": key, "indices_first_25": [index]}],
                    )
        self.assertEqual(runner.fixture_page_mismatches(deepcopy(WITH_ADDON), WITH_ADDON), [])
        self.assertEqual(runner.fixture_page_mismatches(deepcopy(WITHOUT_ADDON), WITHOUT_ADDON), [])

    def test_preserved_aggregate_counts_do_not_hide_wrong_rule_outcomes(self):
        original, candidate = make_run(), make_run()
        candidate["browser_behavior"]["extra_cosmetic_hidden_by_index"][41] = False
        candidate["browser_behavior"]["extra_cosmetic_near_miss_visible_by_index"][41] = False
        candidate["browser_behavior"]["extra_scriptlets_applied_by_index"][8] = False
        self.assertEqual(candidate["browser_behavior"]["extra_cosmetic_hidden_count"], 96)
        self.assertEqual(candidate["browser_behavior"]["extra_scriptlets_applied_count"], 12)
        result = runner.compare(
            {"upstream": [original], "meblock": [candidate]}, DATASET,
            identical_build=True,
        )
        self.assertEqual(result["verdict"], "FAIL: behavioral regression")
        self.assertFalse(result["correctness"]["browser_page_match"])
        failures = result["correctness"]["browser_page_mismatches_first_25"]
        self.assertEqual(
            [(failure["field"], failure["indices_first_25"]) for failure in failures],
            [
                ("extra_cosmetic_hidden_by_index", [41]),
                ("extra_cosmetic_near_miss_visible_by_index", [41]),
                ("extra_scriptlets_applied_by_index", [8]),
            ],
        )

    def test_missing_controls_and_malformed_vectors_are_rejected(self):
        for expected in (WITH_ADDON, WITHOUT_ADDON):
            changed = deepcopy(expected)
            changed["extra_visible_by_index"][3] = False
            self.assertEqual(
                runner.fixture_page_mismatches(changed, expected),
                [{"field": "extra_visible_by_index", "indices_first_25": [3]}],
            )
            changed = deepcopy(expected)
            changed["extra_cosmetic_present_by_index"][19] = False
            self.assertEqual(
                runner.fixture_page_mismatches(changed, expected),
                [{"field": "extra_cosmetic_present_by_index", "indices_first_25": [19]}],
            )
            changed = deepcopy(expected)
            changed["extra_scriptlet_near_miss_untouched_by_index"] = [True] * 11
            self.assertEqual(
                runner.fixture_page_mismatches(changed, expected),
                [{"field": "extra_scriptlet_near_miss_untouched_by_index",
                  "error": "missing or incomplete rule vector"}],
            )
            changed = deepcopy(expected)
            changed["extra_visible_by_index"][3] = 1
            self.assertEqual(
                runner.fixture_page_mismatches(changed, expected),
                [{"field": "extra_visible_by_index", "indices_first_25": [3]}],
            )
            changed = deepcopy(expected)
            changed["untrusted_private_field"] = "should not be copied to output"
            result = runner.fixture_page_mismatches(changed, expected)
            self.assertEqual(result, [
                {"field": "page", "error": "unexpected or missing fields"}
            ])

    def test_navigation_p95_samples_firefox_timing_and_checks_each_page(self):
        class FixtureBrowser:
            def __init__(self):
                self.visited = []

            def get(self, url):
                self.visited.append(url)

            def execute_script(self, script):
                if script == runner.NAVIGATION_SCRIPT:
                    index = len(self.visited) - 1
                    return {
                        "load_event_end_ms": 12.0 + index,
                        "dom_content_loaded_end_ms": 10.0 + index,
                        "navigation_type": "navigate",
                    }
                if script == runner.OBSERVATION_SCRIPT:
                    return dict(WITH_ADDON)
                raise AssertionError("Unexpected browser script")

        browser = FixtureBrowser()
        samples = runner.measure_fixture_navigation(
            browser, "http://127.0.0.1:8000", WITH_ADDON
        )
        self.assertEqual(len(browser.visited), 22)
        self.assertEqual(len(samples), 20)
        summary = runner.summarize_fixture_navigation(samples)
        self.assertEqual(summary["sample_count"], 20)
        self.assertEqual(summary["p50_load_event_end_ms"], 23.5)
        self.assertEqual(summary["p95_load_event_end_ms"], 32.0)
        self.assertEqual(summary["p95_dom_content_loaded_end_ms"], 30.0)
        self.assertEqual(summary["range_load_event_end_ms"], [14.0, 33.0])
        self.assertNotIn("127.0.0.1", str(samples))

    def test_navigation_errors_reject_bad_samples(self):
        good = [
            {"load_event_end_ms": 40.0, "dom_content_loaded_end_ms": 20.0}
            for _ in range(20)
        ]
        for corrupted in (
            good[:-1],
            [*good[:-1], {"load_event_end_ms": 0, "dom_content_loaded_end_ms": 0}],
            [*good[:-1], {"load_event_end_ms": 10, "dom_content_loaded_end_ms": 20}],
            [*good[:-1], {"load_event_end_ms": float("nan"), "dom_content_loaded_end_ms": 10}],
            [*good[:-1], {"load_event_end_ms": 50, "dom_content_loaded_end_ms": 20, "url": "private"}],
        ):
            with self.subTest(corrupted=corrupted[-1:]):
                with self.assertRaises(ValueError):
                    runner.summarize_fixture_navigation(corrupted)

    def test_comparison_reports_page_p95_and_rejects_missing_side(self):
        source, target = make_run(), make_run()
        timings = [
            {"load_event_end_ms": float(x), "dom_content_loaded_end_ms": float(x - 2)}
            for x in range(5, 25)
        ]
        source["fixture_navigation"] = timings
        target["fixture_navigation"] = [dict(v) for v in timings]
        report = runner.compare({"upstream": [source], "meblock": [target]}, DATASET)
        self.assertEqual(report["fixture_navigation"]["upstream"]["samples_per_run"], 20)
        self.assertEqual(report["fixture_navigation"]["upstream"]["median_run_p95_load_event_end_ms"], 23.0)
        self.assertIn("not production website", report["fixture_navigation"]["scope"])
        target.pop("fixture_navigation")
        with self.assertRaisesRegex(ValueError, "20 measured loads"):
            runner.compare({"upstream": [source], "meblock": [target]}, DATASET)

    def test_challenge_match_source_diagnostics_are_isolated_and_validated(self):
        valid = {"dataset_rows": 4, "challenge_match_source_codes": [1, 2, 4, 8]}
        runner.validate_challenge_match_codes(valid, True)
        runner.validate_challenge_match_codes({
            "dataset_rows": 4, "challenge_match_source_codes": [],
        }, False)
        for wrong in ([1, 2, 4], [True, 2, 4, 8],
                      [16, 2, 4, 8], ["||private.host^", 1, 4, 8]):
            with self.subTest(codes=wrong):
                with self.assertRaisesRegex(ValueError, "match-source diagnostics"):
                    runner.validate_challenge_match_codes({
                        "dataset_rows": 4, "challenge_match_source_codes": wrong,
                    }, True)
        with self.assertRaisesRegex(ValueError, "match-source diagnostics"):
            runner.validate_challenge_match_codes(valid, False)
        with self.assertRaisesRegex(ValueError, "match-source diagnostics"):
            runner.validate_challenge_match_codes({
                "dataset_rows": True, "challenge_match_source_codes": [],
            }, False)

    def test_exception_challenge_controls_and_override_attribution(self):
        # Three exceptions: first-party, third-party, and party-unrestricted.
        rows = []
        for index, label in enumerate(("a", "b", "c")):
            host = label + ".example"
            near = f"mb-exception-control-{index:05d}.test"
            for frame in ("https://reader.test/page", "https://" + host + "/page"):
                near_frame = (
                    "https://" + near + "/page"
                    if frame != "https://reader.test/page" else frame
                )
                rows.extend([
                    {"frameUrl": frame, "url": "https://" + host + "/probe.js",
                     "cpt": "script"},
                    {"frameUrl": near_frame, "url": "https://" + near + "/probe.js",
                     "cpt": "script"},
                ])
        coverage = {
            "exception_challenge": True,
            "simple_host_rules_in_shard": 3,
            "_expected_allow_by_origin": [False, True, True, False, True, True],
        }
        rules = runner.exception_blocking_controls(rows, coverage)
        self.assertEqual(rules, (
            "||a.example^", "||mb-exception-control-00000.test^",
            "||b.example^", "||mb-exception-control-00001.test^",
            "||c.example^", "||mb-exception-control-00002.test^",
        ))
        self.assertEqual(
            runner.withhold_target_challenge_blocker(rules, 1),
            (
                "||a.example^", "||mb-exception-control-00000.test^",
                "||mb-exception-control-00001.test^",
                "||c.example^", "||mb-exception-control-00002.test^",
            ),
        )
        for invalid in (-1, 3, True):
            with self.assertRaisesRegex(ValueError, "withheld"):
                runner.withhold_target_challenge_blocker(rules, invalid)
        outcomes = [
            1, 1, 2, 1,
            2, 1, 1, 1,
            2, 1, 2, 1,
        ]
        match_codes = [1, 1, 4, 1, 4, 1, 1, 1, 4, 1, 4, 1]
        observation = {
            "outcomes": outcomes,
            "challenge_match_source_codes": match_codes,
        }
        measured = runner.summarize_exception_challenge(observation, coverage)
        self.assertEqual(measured["candidate_rules"], 3)
        self.assertEqual(measured["expected_override_contexts"], 4)
        self.assertEqual(measured["confirmed_override_contexts"], 4)
        self.assertEqual(measured["candidates_with_confirmed_override"], 3)
        self.assertEqual(measured["blocking_negative_controls_passed"], 6)
        self.assertEqual(measured["first_25_unconfirmed_indices"], [])
        self.assertNotIn("example", json.dumps(measured))
        # When the negative near-miss is explicitly allowed, the positive
        # is NOT attributed to that candidate (even if the positive is allow).
        outcomes[11] = 2
        rejected = runner.summarize_exception_challenge(observation, coverage)
        self.assertEqual(rejected["confirmed_override_contexts"], 3)
        self.assertEqual(rejected["first_25_unconfirmed_match_codes"], [{
            "index": 2,
            "matching_host_codes": [4, 4],
            "control_host_codes": [1, 1],
        }])
        self.assertEqual(rejected["blocking_negative_controls_passed"], 5)
        self.assertEqual(rejected["first_25_control_failure_indices"], [2])
        broken = deepcopy(rows)
        broken[0]["frameUrl"] = "https://reader.example.invalid/page"
        with self.assertRaisesRegex(ValueError, "Malformed exception challenge"):
            runner.exception_blocking_controls(broken, coverage)
        # A control request must not inherit the positive host's first-party
        # document; this previously made one reserved-host control untestable.
        broken = deepcopy(rows)
        broken[3]["frameUrl"] = "https://a.example/page"
        with self.assertRaisesRegex(ValueError, "Malformed exception challenge"):
            runner.exception_blocking_controls(broken, coverage)
        broken = deepcopy(rows)
        broken[1]["url"] = "https://unrelated.example/probe.js"
        with self.assertRaisesRegex(ValueError, "Malformed exception challenge"):
            runner.exception_blocking_controls(broken, coverage)
        with self.assertRaisesRegex(ValueError, "Incomplete exception challenge"):
            runner.summarize_exception_challenge({
                **observation, "outcomes": outcomes[:-1],
            }, coverage)
        for malformed in (match_codes[:-1], [True] + match_codes[1:],
                          [16] + match_codes[1:]):
            with self.assertRaisesRegex(ValueError, "match-source codes"):
                runner.summarize_exception_challenge({
                    **observation, "challenge_match_source_codes": malformed,
                }, coverage)

    def test_default_rule_candidate_effects_detect_real_outcome_and_modifier_differences(self):
        observation = {
            "outcomes": [1, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0],
            "modifier_masks": [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0],
        }
        result = runner.summarize_candidate_effects(observation, 3)
        self.assertEqual(result["candidate_rules"], 3)
        self.assertEqual(result["same_host_frame_distinguishable_candidates"], 1)
        self.assertEqual(result["external_reader_frame_distinguishable_candidates"], 2)
        self.assertEqual(result["both_contexts_distinguishable_candidates"], 1)
        self.assertEqual(result["at_least_one_context_distinguishable_candidates"], 2)
        self.assertEqual(result["no_distinguishable_result_in_tested_contexts"], 1)
        self.assertEqual(result["positive_requests_blocked"], 2)
        self.assertEqual(result["near_miss_requests_blocked"], 0)
        self.assertEqual(result["positive_requests_explicitly_allowed"], 0)
        self.assertEqual(result["near_miss_requests_explicitly_allowed"], 0)
        self.assertNotIn("https://", json.dumps(result))
        allowed = {
            "outcomes": [2, 0, 0, 0],
            "modifier_masks": [0, 0, 0, 0],
        }
        observed = runner.summarize_candidate_effects(allowed, 1)
        self.assertEqual(observed["positive_requests_explicitly_allowed"], 1)
        self.assertEqual(observed["near_miss_requests_explicitly_allowed"], 0)
        self.assertEqual(observed["at_least_one_context_distinguishable_candidates"], 1)
        with self.assertRaisesRegex(ValueError, "Malformed per-rule"):
            runner.summarize_candidate_effects({
                **observation, "modifier_masks": [0] * 11
            }, 3)
        with self.assertRaisesRegex(ValueError, "Malformed per-rule"):
            runner.summarize_candidate_effects({
                **observation, "outcomes": [True] + observation["outcomes"][1:]
            }, 3)
        with self.assertRaisesRegex(ValueError, "Invalid count"):
            runner.summarize_candidate_effects(observation, 0)

    def test_request_latency_nearest_rank_validator_rejects_bad_samples(self):
        baseline = {
            "schema": "meblock-request-latency-v2", "clock_step_us": 0.5,
            "sample_count": 12, "passes": 3, "zero_sample_count": 0,
            "min_us": 1.0, "median_us": 2.0, "p95_us": 4.0,
            "p99_us": 5.0, "max_us": 6.0,
            "empty_timer_pair_median_us": 0.2,
            "empty_timer_pair_p95_us": 0.5,
        }
        rows = [{"request_latency": deepcopy(baseline)} for _ in range(2)]
        summary = runner.summarize_request_latency(rows, 4)
        self.assertEqual(summary["samples_per_run"], 12)
        self.assertEqual(summary["median_run_p95_us"], 4.0)
        self.assertTrue(summary["precision_usable_for_instrumented_screening"])
        self.assertEqual(summary["timer_pair_p95_range_us"], [0.5, 0.5])
        quantized = deepcopy(baseline)
        quantized.update({
            "zero_sample_count": 11, "min_us": 0, "median_us": 0,
            "p95_us": 0, "p99_us": 1000, "max_us": 1000,
            "empty_timer_pair_median_us": 0, "empty_timer_pair_p95_us": 0,
        })
        coarse = runner.summarize_request_latency([
            {"request_latency": quantized}
        ], 4)
        self.assertFalse(coarse["precision_usable_for_instrumented_screening"])
        self.assertIsNone(coarse["median_run_p95_us"])
        for field, wrong in (
            ("sample_count", 11), ("passes", 0),
            ("zero_sample_count", 13), ("p95_us", 1.5),
            ("p99_us", float("nan")), ("min_us", True),
            ("empty_timer_pair_p95_us", 0.1),
        ):
            with self.subTest(field=field):
                broken = deepcopy(baseline)
                broken[field] = wrong
                with self.assertRaisesRegex(ValueError, "request-level latency"):
                    runner.summarize_request_latency([
                        {"request_latency": baseline}, {"request_latency": broken}
                    ], 4)
        with self.assertRaisesRegex(ValueError, "request-level latency"):
            runner.summarize_request_latency([
                {"request_latency": baseline}, {}
            ], 4)

    def test_request_latency_cross_build_missing_data_rejected(self):
        upstream, fork = make_run(), make_run()
        upstream["request_latency"] = {
            "schema": "meblock-request-latency-v2", "clock_step_us": 0.5,
            "sample_count": 9, "passes": 3, "zero_sample_count": 0,
            "min_us": 1, "median_us": 1, "p95_us": 3,
            "p99_us": 4, "max_us": 4,
            "empty_timer_pair_median_us": 0.1,
            "empty_timer_pair_p95_us": 0.2,
        }
        with self.assertRaisesRegex(ValueError, "missing from one build"):
            runner.compare({"upstream": [upstream], "meblock": [fork]}, DATASET)
        fork["request_latency"] = deepcopy(upstream["request_latency"])
        result = runner.compare(
            {"upstream": [upstream], "meblock": [fork]}, DATASET,
            identical_build=True,
        )
        self.assertEqual(result["request_latency"]["upstream"]["median_run_p95_us"], 3)

    def test_pinned_live_selection_rejects_changes(self):
        good = {"selected_filter_lists": ["easylist", "ublock-filters"]}
        selection = {
            "selected_list_keys_sha256": runner.hash_bytes(
                b"easylist\nublock-filters"
            )
        }
        data = {role: [deepcopy(good)] for role in runner.ROLES}
        runner.verify_pinned_selection(data, selection)
        data["meblock"][0]["selected_filter_lists"] = ["different"]
        with self.assertRaisesRegex(ValueError, "selection differs"):
            runner.verify_pinned_selection(data, selection)
        data["meblock"][0]["selected_filter_lists"] = [1, "easylist"]
        with self.assertRaisesRegex(ValueError, "selection differs"):
            runner.verify_pinned_selection(data, selection)

    def test_rule_derived_manifest_checks_integrity_and_only_returns_safe_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "rule-requests.json"
            rows = [
                {"frameUrl": "https://reader.example.invalid/page",
                 "url": "https://alpha.example/probe.js", "cpt": "script"}
                for _ in range(4)
            ]
            payload = (json.dumps(rows) + "\n").encode("utf-8")
            dataset.write_bytes(payload)
            manifest = {
                "schema": "meblock-default-rule-probes-v1",
                "shard_index": 0, "shard_count": 16,
                "simple_host_rules_in_shard": 1, "simple_host_rule_unique": 16,
                "positive_requests": 2, "near_miss_requests": 2,
                "request_count": 4, "bundled_asset_count": 2,
                "dataset_sha256": runner.hash_bytes(payload),
                "bundled_asset_contents_sha256": "a" * 64,
                "private_raw_filter": "must never be copied to results",
            }
            sidecar = dataset.with_suffix(".json.manifest.json")
            sidecar.write_text(json.dumps(manifest), encoding="utf-8")
            safe = runner.read_rule_derived_manifest(dataset, payload, rows, True)
            self.assertEqual(safe["request_count"], 4)
            self.assertEqual(safe["bundled_asset_contents_sha256"], "a" * 64)
            self.assertNotIn("private_raw_filter", safe)
            counted = {
                **manifest,
                "simple_host_rule_before_badfilter_unique": 19,
                "excluded_by_bundled_badfilter_rule_count": 3,
            }
            sidecar.write_text(json.dumps(counted), encoding="utf-8")
            badfilter_meta = runner.read_rule_derived_manifest(dataset, payload, rows, True)
            self.assertEqual(badfilter_meta["excluded_by_bundled_badfilter_rule_count"], 3)
            counted["excluded_by_bundled_badfilter_rule_count"] = 2
            sidecar.write_text(json.dumps(counted), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "badfilter-excluded coverage"):
                runner.read_rule_derived_manifest(dataset, payload, rows, True)
            sidecar.write_text(json.dumps(manifest), encoding="utf-8")
            family_manifest = {
                **manifest,
                "rule_family": "exception", "family_rule_unique": 1,
                "eligible_exception_rule_unique": 1,
                "eligible_blocking_rule_unique": 15,
            }
            sidecar.write_text(json.dumps(family_manifest), encoding="utf-8")
            with_family = runner.read_rule_derived_manifest(
                dataset, payload, rows, True
            )
            self.assertEqual(with_family["rule_family"], "exception")
            self.assertEqual(with_family["family_rule_unique"], 1)
            challenge = {
                **family_manifest,
                "selected_list_keys_sha256": "b" * 64,
                "selected_list_count": 1,
                "selected_without_bundled_asset_count": 0,
                "exception_challenge": True,
                "expected_explicit_allow_by_origin": [False, True],
            }
            sidecar.write_text(json.dumps(challenge), encoding="utf-8")
            checked = runner.read_rule_derived_manifest(dataset, payload, rows, True)
            self.assertEqual(checked["_expected_allow_by_origin"], [False, True])
            self.assertTrue(checked["exception_challenge"])
            challenge["expected_explicit_allow_by_origin"] = [False, 1]
            sidecar.write_text(json.dumps(challenge), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exception-challenge oracle"):
                runner.read_rule_derived_manifest(dataset, payload, rows, True)
            family_manifest["eligible_blocking_rule_unique"] = 14
            sidecar.write_text(json.dumps(family_manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "rule-family provenance"):
                runner.read_rule_derived_manifest(dataset, payload, rows, True)
            sidecar.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "require --default-lists"):
                runner.read_rule_derived_manifest(dataset, payload, rows, False)
            with self.assertRaisesRegex(ValueError, "differs"):
                runner.read_rule_derived_manifest(dataset, payload + b"!", rows, True)
            manifest["positive_requests"] = 3
            sidecar.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "differs"):
                runner.read_rule_derived_manifest(dataset, payload, rows, True)

    def test_default_lists_comparison_expects_unmodified_loopback_page(self):
        upstream, meblock = make_run(), make_run()
        for row in (upstream, meblock):
            row["browser_behavior"] = deepcopy(WITHOUT_ADDON)
            row["browser_parameters"] = dict(PARAMETERS_WITHOUT_ADDON)
        result = runner.compare(
            {"upstream": [upstream], "meblock": [meblock]},
            DATASET, identical_build=True, default_lists=True,
        )
        self.assertEqual(result["verdict"],
                         "CONTROL: byte-identical builds; timing differences are environmental")
        self.assertTrue(result["default_enabled_lists_only"])
        self.assertEqual(result["injected_filter_rules"], 0)
        self.assertTrue(result["correctness"]["browser_page_match"])
        self.assertTrue(result["correctness"]["url_modifier_match"])
        self.assertIsNone(result["correctness"]["local_block_positive_control"])
        meblock["browser_behavior"]["extra_cosmetic_hidden_by_index"][0] = True
        broken = runner.compare(
            {"upstream": [upstream], "meblock": [meblock]},
            DATASET, default_lists=True,
        )
        self.assertEqual(broken["verdict"], "FAIL: behavioral regression")
        self.assertEqual(
            broken["correctness"]["browser_page_mismatches_first_25"][0]["indices_first_25"],
            [0],
        )

    def test_engine_buffers_report_exact_known_byte_difference(self):
        upstream, meblock = make_run(), make_run()
        upstream["engine_buffer_footprint"] = buffer_sample(1024)
        meblock["engine_buffer_footprint"] = buffer_sample(2048)
        result = runner.compare({"upstream": [upstream], "meblock": [meblock]}, DATASET)
        self.assertEqual(result["engine_buffer_footprint"]["known_allocated_delta_bytes"], 1024)
        self.assertIn("not total add-on RAM", result["engine_buffer_footprint"]["scope"])

    def test_mismatched_compiled_network_filter_count_rejected(self):
        upstream, meblock = make_run(), make_run()
        upstream["engine_buffer_footprint"] = buffer_sample()
        meblock["engine_buffer_footprint"] = buffer_sample()
        meblock["engine_buffer_footprint"]["compiled_network_filters"] -= 1
        with self.assertRaisesRegex(ValueError, "compiled network filter counts"):
            runner.compare({"upstream": [upstream], "meblock": [meblock]}, DATASET)

    def test_buffer_counter_drift_is_reported_without_claiming_byte_delta(self):
        one, two = make_run(), make_run()
        one["engine_buffer_footprint"] = buffer_sample()
        two["engine_buffer_footprint"] = buffer_sample(2048)
        result = runner.compare({
            "upstream": [one, two],
            "meblock": [one, two],
        }, DATASET, identical_build=True)
        observed = result["engine_buffer_footprint"]
        self.assertFalse(observed["stable_enough_for_byte_delta"])
        self.assertIsNone(observed["known_allocated_delta_bytes"])
        self.assertEqual(
            observed["upstream"]["observed_known_allocated_range_bytes"],
            [2176, 3200],
        )
        self.assertIn("CONTROL:", result["verdict"])
        malicious = buffer_sample()
        malicious["user_profile_path"] = "private-diagnostic-string"
        with self.assertRaisesRegex(ValueError, "invalid test-only"):
            runner.summarize_engine_buffers([
                {"engine_buffer_footprint": malicious}
            ])
        invalid = buffer_sample()
        invalid["total_known_allocated_bytes"] += 1
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            runner.summarize_engine_buffers([{"engine_buffer_footprint": invalid}])


    def test_equal_behavior_with_identical_runs_is_inconclusive(self):
        data = {"upstream": [make_run() for _ in range(5)],
                "meblock": [make_run() for _ in range(5)]}
        report = runner.compare(data, DATASET)
        self.assertTrue(report["correctness"]["per_request_match"])
        self.assertIn("INCONCLUSIVE", report["verdict"])

    def test_identical_builds_are_control_even_when_time_differs(self):
        data = {
            "upstream": [make_run(speed=2) for _ in range(5)],
            "meblock": [make_run(speed=20) for _ in range(5)],
        }
        result = runner.compare(data, DATASET, identical_build=True)
        self.assertTrue(result["builds_identical"])
        self.assertIn("CONTROL", result["verdict"])

    def test_regression_detected_even_if_aggregate_counts_match(self):
        data = {
            "upstream": [make_run() for _ in range(5)],
            "meblock": [make_run([0, 1, 0]) for _ in range(5)],
        }
        outcome = runner.compare(data, DATASET)
        self.assertEqual(outcome["verdict"], "FAIL: behavioral regression")
        self.assertEqual(outcome["correctness"]["mismatches_first_25"][0]["request_index"], 0)

    def test_modifier_regression_detected_despite_equal_block_counts(self):
        data = {
            "upstream": [make_run() for _ in range(5)],
            "meblock": [make_run() for _ in range(5)],
        }
        for row in data["meblock"]:
            row["official"] = row["official"].replace("redirect=: 0", "redirect=: 1")
            row["modifier_masks"] = [1, 0, 0]
        report = runner.compare(data, DATASET)
        self.assertEqual(report["verdict"], "FAIL: behavioral regression")
        self.assertFalse(report["correctness"]["aggregate_modifier_counts_match"])

    def test_per_request_modifier_swap_fails_even_with_identical_aggregate(self):
        data = {
            "upstream": [make_run(modifier_masks=[1, 0, 0]) for _ in range(5)],
            "meblock": [make_run(modifier_masks=[0, 1, 0]) for _ in range(5)],
        }
        result = runner.compare(data, DATASET)
        self.assertEqual(result["verdict"], "FAIL: behavioral regression")
        self.assertTrue(result["correctness"]["aggregate_modifier_counts_match"])
        self.assertFalse(result["correctness"]["modifier_per_request_match"])
        self.assertEqual(
            result["correctness"]["modifier_mismatches_first_25"][0]["request_index"], 0
        )

    def test_modifier_count_mismatch_is_rejected(self):
        data = {
            "upstream": [make_run() for _ in range(5)],
            "meblock": [make_run() for _ in range(5)],
        }
        data["meblock"][0]["modifier_masks"] = [1, 0, 0]
        with self.assertRaisesRegex(ValueError, "modifier counts disagree"):
            runner.compare(data, DATASET)

    def test_missing_modifier_vector_is_rejected(self):
        row = make_run()
        row.pop("modifier_masks")
        with self.assertRaisesRegex(ValueError, "modifier vector"):
            runner.compare({"upstream": [row], "meblock": [make_run()]}, DATASET)

    def test_document_benchmark_must_process_every_document(self):
        dataset = [dict(row) for row in DATASET]
        dataset[0]["cpt"] = "main_frame"
        data = {
            "upstream": [make_run() for _ in range(5)],
            "meblock": [make_run() for _ in range(5)],
        }
        for role in ("upstream", "meblock"):
            for row in data[role]:
                row["extra"] = {
                    "cosmetic": "Evaluated 1 retrieval in 2 ms",
                    "scriptlet": "Evaluated 1 retrieval in 2 ms",
                }
        self.assertTrue(runner.compare(data, dataset)["correctness"]["auxiliary_benchmarks_match"])
        data["meblock"][0]["extra"]["scriptlet"] = "Evaluated 0 retrieval in 2 ms"
        with self.assertRaisesRegex(ValueError, "scriptlet benchmark"):
            runner.compare(data, dataset)

    def test_browser_fixture_fails_if_control_is_not_blocked(self):
        data = {
            "upstream": [make_run() for _ in range(5)],
            "meblock": [make_run() for _ in range(5)],
        }
        data["meblock"][0]["browser_behavior"]["blocked_script"] = True
        report = runner.compare(data, DATASET)
        self.assertEqual(report["verdict"], "FAIL: behavioral regression")
        self.assertFalse(report["correctness"]["browser_page_match"])

    def test_filter_list_selection_change_is_regression(self):
        data = {
            "upstream": [make_run() for _ in range(5)],
            "meblock": [make_run() for _ in range(5)],
        }
        for role in ("upstream", "meblock"):
            for row in data[role]:
                row["selected_filter_lists"] = ["ublock-filters"]
        data["meblock"][0]["selected_filter_lists"] = ["different-list"]
        report = runner.compare(data, DATASET)
        self.assertEqual(report["verdict"], "FAIL: behavioral regression")

    def test_incorrect_upstream_report_rejected(self):
        data = {
            "upstream": [make_run() for _ in range(5)],
            "meblock": [make_run() for _ in range(5)],
        }
        data["meblock"][1]["official"] = official(2)
        with self.assertRaisesRegex(ValueError, "counts disagree"):
            runner.compare(data, DATASET)

    def test_missing_outcome_not_accepted(self):
        with self.assertRaisesRegex(ValueError, "complete decision vector"):
            runner.check_behavior([make_run()], [make_run([1])], DATASET)

    def test_fixture_contract_has_positive_and_negative_controls(self):
        self.assertTrue(all(WITHOUT_ADDON[key] is True for key in (
            "allowed_script", "blocked_script", "allowed_image", "blocked_image",
            "allowed_frame", "blocked_frame", "control_visible",
        )))
        self.assertFalse(WITHOUT_ADDON["cosmetic_hidden"])
        self.assertEqual(WITH_ADDON["blocked_script"], False)
        self.assertEqual(WITH_ADDON["blocked_image"], False)
        self.assertEqual(WITH_ADDON["blocked_frame"], False)
        self.assertTrue(WITH_ADDON["cosmetic_hidden"])
        self.assertEqual(
            len(RULES),
            31 + EXTRA_COSMETIC_COUNT + EXTRA_SCRIPTLET_COUNT + EXTRA_PROCEDURAL_COUNT,
        )
        self.assertTrue(PARAMETERS_WITHOUT_ADDON["tracking_parameter_present"])
        self.assertFalse(PARAMETERS_WITH_ADDON["tracking_parameter_present"])
        self.assertFalse(WITH_ADDON["xhr_blocked"])
        self.assertTrue(WITH_ADDON["scriptlet_applied"])
        self.assertFalse(WITH_ADDON["redirected_script_executed"])
        self.assertTrue(WITH_ADDON["redirected_script_loaded"])

    def test_image_and_cosmetic_regressions_fail(self):
        for field, wrong in (
            ("blocked_image", True),
            ("blocked_frame", True),
            ("cosmetic_hidden", False),
            ("xhr_blocked", True),
            ("scriptlet_applied", False),
            ("extra_cosmetic_hidden_count", 95),
            ("extra_visible_count", 31),
            ("extra_scriptlets_applied_count", 11),
        ):
            with self.subTest(field=field):
                data = {
                    "upstream": [make_run() for _ in range(5)],
                    "meblock": [make_run() for _ in range(5)],
                }
                data["meblock"][0]["browser_behavior"][field] = wrong
                result = runner.compare(data, DATASET)
                self.assertEqual(result["verdict"], "FAIL: behavioral regression")

    def test_url_parameter_regression_detected(self):
        data = {
            "upstream": [make_run() for _ in range(5)],
            "meblock": [make_run() for _ in range(5)],
        }
        data["meblock"][0]["browser_parameters"]["tracking_parameter_present"] = True
        report = runner.compare(data, DATASET)
        self.assertEqual(report["verdict"], "FAIL: behavioral regression")
        self.assertFalse(report["correctness"]["url_modifier_match"])

    def test_dynamic_firewall_regression_fails(self):
        data = {
            "upstream": [make_run() for _ in range(5)],
            "meblock": [make_run() for _ in range(5)],
        }
        data["meblock"][0]["dynamic_firewall"]["cases"]["allow"] = 1
        report = runner.compare(data, DATASET)
        self.assertEqual(report["verdict"], "FAIL: behavioral regression")
        self.assertFalse(report["correctness"]["dynamic_firewall_match"])

    def test_settings_reload_regression_fails(self):
        data = {
            "upstream": [make_run() for _ in range(5)],
            "meblock": [make_run() for _ in range(5)],
        }
        data["meblock"][0]["state_persistence"]["restored"] = False
        report = runner.compare(data, DATASET)
        self.assertEqual(report["verdict"], "FAIL: behavioral regression")
        self.assertFalse(report["correctness"]["settings_reload_and_restore_match"])

    def test_source_delta(self):
        self.assertEqual(runner.source_delta({"a": "1"}, {"a": "2", "b": "3"})["files_different"], 2)


class PackageTests(unittest.TestCase):
    def test_default_lists_package_never_appends_fixture_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory) / "build"
            (build / "assets" / "ublock").mkdir(parents=True)
            (build / "js").mkdir()
            (build / "manifest.json").write_text(
                json.dumps({"browser_specific_settings": {
                    "gecko": {"id": "default-fixture@example.invalid"}
                }}), encoding="utf-8",
            )
            (build / "background.html").write_text(
                "<html><body></body></html>", encoding="utf-8",
            )
            (build / "js" / "static-net-filtering.js").write_text(
                "export default {};", encoding="utf-8",
            )
            (build / "js" / "background.js").write_text(
                "const defaults = {\n    autoUpdate: true,\n};", encoding="utf-8",
            )
            asset = build / "assets" / "ublock" / "filters.min.txt"
            original = b"! bundled stock filters\n||public.example.invalid^\n"
            asset.write_bytes(original)
            output = Path(directory) / "default-probed.xpi"
            runner.probe_package(
                build, output, "meblock", "http://127.0.0.1:8888",
                "test-token", b"[]", include_fixture_rules=False,
            )
            self.assertEqual(asset.read_bytes(), original)
            with ZipFile(output) as archive:
                self.assertEqual(archive.read("assets/ublock/filters.min.txt"), original)
                self.assertIn(
                    b"meblockEngineBufferFootprint",
                    archive.read("js/static-net-filtering.js"),
                )
                self.assertIn(b"autoUpdate: false", archive.read("js/background.js"))

    def test_challenge_rules_only_enter_disposable_correctness_xpi(self):
        with tempfile.TemporaryDirectory() as folder:
            build = Path(folder) / "test-build"
            (build / "assets" / "ublock").mkdir(parents=True)
            (build / "js").mkdir()
            (build / "manifest.json").write_text(json.dumps({
                "browser_specific_settings": {"gecko": {"id": "fake@example.invalid"}}
            }), encoding="utf-8")
            (build / "background.html").write_text(
                "<html><body></body></html>", encoding="utf-8"
            )
            (build / "js" / "static-net-filtering.js").write_text(
                "export default {};", encoding="utf-8"
            )
            (build / "js" / "background.js").write_text(
                "const defaults = {\n    autoUpdate: true,\n};", encoding="utf-8"
            )
            (build / "assets" / "ublock" / "filters.min.txt").write_text(
                "! stock public fake list\n", encoding="utf-8"
            )
            archive = Path(folder) / "challenge.xpi"
            fake_blockers = (
                "||target.example.invalid^",
                "||mb-exception-control-00000.test^",
            )
            runner.probe_package(
                build, archive, "upstream", "http://127.0.0.1:1234",
                "fake-secret", b"[]", include_fixture_rules=False,
                challenge_rules=fake_blockers,
            )
            with ZipFile(archive) as result:
                actual = result.read("assets/ublock/filters.min.txt").decode("utf-8")
                self.assertIn("||target.example.invalid^", actual)
                self.assertIn("||mb-exception-control-00000.test^", actual)
                self.assertNotIn("mb-fixture-override", actual)
                self.assertIn(b'"precedence_cases": []',
                              result.read("js/meblock-probe.js"))
                self.assertIn(b'"challenge_diagnostics": true',
                              result.read("js/meblock-probe.js"))

    def test_instrumented_package_tracks_fork_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "build"
            (root / "assets" / "ublock").mkdir(parents=True)
            (root / "js").mkdir()
            (root / "manifest.json").write_text(
                json.dumps({"browser_specific_settings": {
                    "gecko": {"id": "meblock@example.invalid"}
                }}), encoding="utf-8"
            )
            (root / "background.html").write_text(
                "<html><body></body></html>", encoding="utf-8"
            )
            (root / "js" / "static-net-filtering.js").write_text(
                "export default {};", encoding="utf-8"
            )
            (root / "js" / "background.js").write_text(
                "const defaults = {\n    autoUpdate: true,\n};", encoding="utf-8"
            )
            (root / "assets" / "ublock" / "filters.min.txt").write_text(
                "! test baseline\n", encoding="utf-8"
            )
            xpi = Path(folder) / "instrumented.xpi"
            result = runner.probe_package(
                root, xpi, "meblock", "http://127.0.0.1:8888", "test-token", b"[]"
            )
            self.assertEqual(result["addon_id"], "meblock@example.invalid")
            with ZipFile(xpi) as archive:
                self.assertEqual(archive.read("assets/meblock-requests.json"), b"[]")
                self.assertIn(b"meblock-probe.js", archive.read("background.html"))
                self.assertIn(
                    b"checkPersistedSettings",
                    archive.read("js/meblock-state-probe.js"),
                )
                self.assertIn(
                    b"test-blocked.js", archive.read("assets/ublock/filters.min.txt")
                )
                engine = archive.read("js/static-net-filtering.js")
                self.assertIn(b"export default {};", engine)
                self.assertIn(b"export function meblockEngineBufferFootprint()", engine)
                self.assertIn(b"engine_buffer_footprint", archive.read("js/meblock-probe.js"))
                self.assertIn(b"autoUpdate: false", archive.read("js/background.js"))
            with self.assertRaisesRegex(RuntimeError, "already injected"):
                runner.probe_package(
                    root, xpi, "meblock", "http://127.0.0.1:8888", "test-token", b"[]"
                )


class CollectorTests(unittest.TestCase):
    def test_local_dataset_and_authenticated_report(self):
        state = runner.State(b'[]', "correct-secret")
        server = runner.Collector(state)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        endpoint = f"http://127.0.0.1:{server.server_port}"
        try:
            for path in ("/dataset", "/dataset?_=8"):
                with urllib.request.urlopen(endpoint + path, timeout=5) as result:
                    self.assertEqual(result.read(), b'[]')
            for path, (expected_type, body) in ROUTES.items():
                with self.subTest(path=path):
                    with urllib.request.urlopen(endpoint + path, timeout=5) as response:
                        self.assertEqual(response.read(), body)
                        self.assertEqual(response.headers.get_content_type(), expected_type.split(";")[0])
            body = json.dumps({"token": "correct-secret", "role": "upstream", "status": "ok"}).encode()
            request = urllib.request.Request(
                endpoint + "/result", data=body,
                headers={"Content-Type": "application/json"}, method="POST"
            )
            with urllib.request.urlopen(request, timeout=5) as result:
                self.assertEqual(result.status, 200)
            self.assertEqual(state.wait("upstream", 1)["status"], "ok")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
