"""Negative controls for small-effect claims from paired Firefox metrics."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import candidate_noise_preflight as preflight


def make_profiles(pairs, *, official_shift=0.0, category_shift=None):
    category_shift = category_shift or {}
    def run(official, mult):
        return {
            "us_per_request": official,
            "timing_sensitivity_injected_batches": 0,
            "batched_matcher": {
                "iterations_per_context": 512,
                "by_request_type": {
                    kind: {"p95_batch_us": 512 * 10.0 * mult.get(kind, 1.0)}
                    for kind in preflight.REQUEST_TYPES
                },
            },
        }
    return {
        "upstream": [run(10.0, {}) for _ in range(pairs)],
        "meblock": [
            run(10.0 * (1.0 + official_shift), category_shift)
            for _ in range(pairs)
        ],
    }


def null_report(pairs, *, same_code=True):
    return {
        "runs_per_build": pairs,
        "build_delta": {"files_different": 0 if same_code else 1},
        "comparison": {
            "builds_identical": same_code,
            "verdict": "CONTROL: byte-identical builds"
            if same_code else "INCONCLUSIVE: different builds",
            "batched_matcher": {
                role: {
                    "calibrated_for_amortized_screening": True,
                    "stable_three_profile_null_control": True,
                    "by_request_type": {
                        kind: {
                            "calibrated_for_amortized_screening": True,
                            "stable_three_profile_null_control": True,
                        }
                        for kind in preflight.REQUEST_TYPES
                    },
                } for role in ("upstream", "meblock")
            },
        },
    }


def public_dataset():
    return [
        {
            "frameUrl": "https://example.invalid/",
            "url": f"https://probe.example.invalid/{kind}",
            "cpt": kind,
        }
        for kind in preflight.REQUEST_TYPES
    ]


class NoisePreflightTests(unittest.TestCase):
    def test_three_pair_control_cannot_claim_three_percent_sensitivity(self):
        stats = preflight._cohort_stats(make_profiles(3))
        self.assertTrue(all(
            not row["null_equivalent_within_three_percent"]
            for row in stats.values()
        ))

    def test_ten_clean_balanced_pairs_pass_illustrative_null_equivalence(self):
        stats = preflight._cohort_stats(make_profiles(10))
        self.assertTrue(all(
            row["null_equivalent_within_three_percent"]
            for row in stats.values()
        ))
        self.assertEqual(stats["official"][
            "paired_bootstrap_95_percent_interval_percent"
        ], [0.0, 0.0])

    def test_ten_biased_identical_build_pairs_fail_even_with_repeatability(self):
        stats = preflight._cohort_stats(make_profiles(10, official_shift=0.07))
        self.assertFalse(stats["official"]["null_equivalent_within_three_percent"])
        self.assertTrue(stats["script"]["null_equivalent_within_three_percent"])
        stats = preflight._cohort_stats(make_profiles(
            10, category_shift={"image": 1.05},
        ))
        self.assertTrue(stats["official"]["null_equivalent_within_three_percent"])
        self.assertFalse(stats["image"]["null_equivalent_within_three_percent"])

    def test_invalid_and_missing_batch_metrics_fail_closed(self):
        for mutation in ("zero", "nan", "missing"):
            runs = make_profiles(10)
            row = runs["meblock"][0]
            if mutation == "zero":
                row["us_per_request"] = 0
            elif mutation == "nan":
                row["batched_matcher"]["by_request_type"]["image"][
                    "p95_batch_us"
                ] = float("nan")
            else:
                row["batched_matcher"]["by_request_type"].pop("script")
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValueError):
                    preflight._cohort_stats(runs)

    def test_cli_cannot_print_local_paths_and_rejects_failed_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            null = path / "private-null-report.json"
            dataset = path / "requests.json"
            dataset.write_text(json.dumps(public_dataset()), encoding="utf-8")
            scenarios = (
                (9, True, 1), (10, False, 1),
                (11, True, 1), (10, True, 0),
            )
            for pairs, same_code, expected in scenarios:
                with self.subTest(pairs=pairs, same_code=same_code):
                    with patch.object(
                        preflight, "_verified_report",
                        return_value=(null_report(pairs, same_code=same_code),
                                      make_profiles(pairs)),
                    ), patch.object(
                        preflight, "read_local_report", return_value={}
                    ), patch.object(
                        preflight, "verify_engine_package_identity",
                        return_value=True,
                    ):
                        output = io.StringIO()
                        with redirect_stdout(output):
                            code = preflight.main([
                                "--null-report", str(null),
                                "--dataset", str(dataset),
                                "--memory-report", str(null),
                                "--stock-xpi", str(null),
                            ])
                        self.assertEqual(code, expected)
                        self.assertNotIn(str(path), output.getvalue())
                        payload = json.loads(output.getvalue())
                        self.assertEqual(
                            payload["ready_for_candidate_effect_screening"],
                            expected == 0,
                        )
                        self.assertEqual(
                            payload["checks"]["at_least_ten_balanced_abba_pairs"],
                            pairs >= 10 and pairs % 2 == 0,
                        )

    def test_stock_engine_identity_failure_blocks_even_perfect_null_timings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            dataset = path / "requests.json"
            dataset.write_text(json.dumps(public_dataset()), encoding="utf-8")
            with patch.object(
                preflight, "_verified_report",
                return_value=(null_report(10), make_profiles(10)),
            ), patch.object(
                preflight, "read_local_report", return_value={}
            ), patch.object(
                preflight, "verify_engine_package_identity", return_value=False,
            ):
                output = io.StringIO()
                with redirect_stdout(output):
                    code = preflight.main([
                        "--null-report", str(path / "null.json"),
                        "--dataset", str(dataset),
                        "--memory-report", str(path / "memory.json"),
                        "--stock-xpi", str(path / "stock.xpi"),
                    ])
                self.assertEqual(code, 1)
                payload = json.loads(output.getvalue())
                self.assertFalse(
                    payload["checks"]["byte_identical_null_builds_and_stock_engine"]
                )
                self.assertFalse(payload["ready_for_candidate_effect_screening"])

    def test_batch_timer_instability_blocks_perfect_paired_null_ratios(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            dataset = path / "requests.json"
            dataset.write_text(json.dumps(public_dataset()), encoding="utf-8")
            record = null_report(10)
            record["comparison"]["batched_matcher"]["meblock"][
                "by_request_type"
            ]["image"]["stable_three_profile_null_control"] = False
            with patch.object(
                preflight, "_verified_report",
                return_value=(record, make_profiles(10)),
            ), patch.object(
                preflight, "read_local_report", return_value={}
            ), patch.object(
                preflight, "verify_engine_package_identity", return_value=True,
            ):
                output = io.StringIO()
                with redirect_stdout(output):
                    code = preflight.main([
                        "--null-report", str(path / "null.json"),
                        "--dataset", str(dataset),
                        "--memory-report", str(path / "memory.json"),
                        "--stock-xpi", str(path / "stock.xpi"),
                    ])
                self.assertEqual(code, 1)
                payload = json.loads(output.getvalue())
                self.assertTrue(
                    payload["checks"]["paired_null_uncertainty_within_three_percent"]
                )
                self.assertFalse(
                    payload["checks"]["calibrated_and_stable_batched_timings"]
                )

    def test_full_artifact_read_fails_closed_without_echoing_private_path(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "private-url-token.json"
            output = io.StringIO()
            with redirect_stdout(output):
                result = preflight.main([
                    "--null-report", str(private),
                    "--dataset", str(private),
                    "--memory-report", str(private),
                    "--stock-xpi", str(private),
                ])
            self.assertEqual(result, 2)
            self.assertNotIn(str(private), output.getvalue())
            self.assertNotIn("token", output.getvalue())


if __name__ == "__main__":
    unittest.main()
