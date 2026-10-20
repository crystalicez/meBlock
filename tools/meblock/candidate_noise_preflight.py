"""Fail-closed paired Firefox noise preflight for future candidate claims.

A passing 11-gate three-pair baseline validates measurement plumbing, NOT
sensitivity to 3% changes. This tool separately requires at least ten
balanced AB/BA same-build null-control pairs, strict evidence replay, and a
95% paired-bootstrap CI fully within +/-3% for every measured cohort.
Even a qualifying null preflight is not a prospective power guarantee.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path

from baseline_readiness import (
    read_local_report, verify_engine_package_identity, verify_report_replay,
)
from comparison_stats import paired_performance
from filtering_benchmark import valid_rows


MIN_PAIRS = 10
NULL_TOLERANCE_PERCENT = 3.0
REQUEST_TYPES = ("script", "image", "xmlhttprequest", "stylesheet")
HARNESS_FILES = (
    "compare_original.py", "browser_probe.js", "engine_buffer_probe.js",
    "state_probe.js", "browser_fixtures.py", "comparison_stats.py",
    "benchmark_firefox.py", "filtering_benchmark.py",
)


def _verified_report(path, dataset_path, dataset, digest):
    report = read_local_report(path)
    runs = report.get("runs_per_build")
    if (report.get("status") != "complete"
        or report.get("default_enabled_lists_only") is not True
        or report.get("timing_sensitivity_control_only") is not False
        or report.get("injected_filter_rules") != 0
        or not isinstance(runs, int) or isinstance(runs, bool)
        or runs < 1 or runs > 100
        or report.get("dataset_count") != len(dataset)
        or report.get("dataset_sha256") != digest
        or not isinstance(report.get("comparison"), dict)
        or report["comparison"].get("default_enabled_lists_only") is not True
        or report["comparison"].get("injected_filter_rules") != 0
        or report["comparison"].get("verdict", "").startswith("FAIL")
        or not isinstance(report.get("harness_files_sha256"), dict)):
        raise ValueError("Report is not a valid ordinary default-list measurement")
    for name in HARNESS_FILES:
        source = Path(__file__).with_name(name)
        if (not source.is_file()
            or report["harness_files_sha256"].get(name)
                != hashlib.sha256(source.read_bytes()).hexdigest()):
            raise ValueError("Report came from a different measurement harness")
    for role in ("upstream", "meblock"):
        package = report.get("packages", {}).get(role, {})
        xpi = path.parent / role / "instrumented.xpi"
        if (not xpi.is_file() or not isinstance(package, dict)
            or xpi.stat().st_size > 64 * 1024 * 1024
            or package.get("bytes") != xpi.stat().st_size
            or hashlib.sha256(xpi.read_bytes()).hexdigest() != package.get("sha256")):
            raise ValueError("Disposable Firefox XPI does not match saved evidence")
    if not verify_report_replay(path, dataset_path, report, default_lists=True):
        raise ValueError("Full per-profile Firefox evidence replay failed")
    matcher = report["comparison"].get("batched_matcher") or {}
    for role in ("upstream", "meblock"):
        source = matcher.get(role) or {}
        cohorts = source.get("by_request_type") or {}
        if (
            source.get("schema") != "meblock-batched-context-match-summary-v2"
            or source.get("runs") != runs
            or set(cohorts) != set(REQUEST_TYPES)
            or any(not isinstance(cohorts[kind], dict)
                   for kind in REQUEST_TYPES)
        ):
            raise ValueError("Null-control batch timer precision or stability failed")
    profiles = {
        role: [
            read_local_report(path.parent / f"{role}-run-{i}.json")
            for i in range(1, runs + 1)
        ] for role in ("upstream", "meblock")
    }
    for role in profiles:
        for sample in profiles[role]:
            if (type(sample.get("timing_sensitivity_injected_batches")) is not int
                or sample["timing_sensitivity_injected_batches"] != 0):
                raise ValueError("Injected test-only timing cannot qualify")
    return report, profiles


def _sample_pairs(profiles, cohort):
    def value(run):
        if cohort == "official":
            result = run.get("us_per_request")
        else:
            timing = run.get("batched_matcher") or {}
            bucket = timing.get("by_request_type") or {}
            selected = bucket.get(cohort) or {}
            count = timing.get("iterations_per_context")
            if (type(count) is not int or count < 128
                or type(selected.get("p95_batch_us")) not in (int, float)):
                raise ValueError("Missing independently measured category")
            result = selected["p95_batch_us"] / count
        if type(result) not in (int, float) or not math.isfinite(result) or result <= 0:
            raise ValueError("Invalid paired Firefox timing value")
        return result
    return (
        [value(item) for item in profiles["upstream"]],
        [value(item) for item in profiles["meblock"]],
    )


def _cohort_stats(profiles):
    results = {}
    for cohort in ("official", *REQUEST_TYPES):
        a, b = _sample_pairs(profiles, cohort)
        result = paired_performance(a, b)
        lower, upper = result["bootstrap_95_percent_interval_percent"]
        result["null_equivalent_within_three_percent"] = (
            len(a) >= MIN_PAIRS and lower > -NULL_TOLERANCE_PERCENT
            and upper < NULL_TOLERANCE_PERCENT
        )
        results[cohort] = {
            "pairs": result["pairs"],
            "null_median_paired_change_percent":
                result["median_paired_change_percent"],
            "paired_bootstrap_95_percent_interval_percent":
                result["bootstrap_95_percent_interval_percent"],
            "null_equivalent_within_three_percent":
                result["null_equivalent_within_three_percent"],
        }
    return results


def assess_null(null_report, dataset_path, memory_path, stock_xpi):
    if not 0 < dataset_path.stat().st_size <= 16 * 1024 * 1024:
        raise ValueError("Missing, empty, or oversized request dataset")
    raw = dataset_path.read_bytes()
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("Oversized request dataset")
    dataset = valid_rows(json.loads(raw))
    digest = hashlib.sha256(raw).hexdigest()
    report, profiles = _verified_report(
        null_report, dataset_path, dataset, digest
    )
    memory = read_local_report(memory_path)
    engine_pinned = verify_engine_package_identity(
        report, memory, stock_xpi, {
            role: null_report.parent / role / "instrumented.xpi"
            for role in ("upstream", "meblock")
        },
    )
    same_code = (
        engine_pinned and
        (report.get("build_delta") or {}).get("files_different") == 0
        and report["comparison"].get("builds_identical") is True
        and report["comparison"].get("verdict", "").startswith("CONTROL:")
    )
    count = report["runs_per_build"]
    pairs_balanced = count >= MIN_PAIRS and count % 2 == 0
    matcher = report["comparison"]["batched_matcher"]
    clock_calibrated_and_stable = all(
        matcher[role]["calibrated_for_amortized_screening"] is True
        and matcher[role]["stable_three_profile_null_control"] is True
        and all(
            matcher[role]["by_request_type"][kind][
                "calibrated_for_amortized_screening"
            ] is True
            and matcher[role]["by_request_type"][kind][
                "stable_three_profile_null_control"
            ] is True
            for kind in REQUEST_TYPES
        )
        for role in ("upstream", "meblock")
    ) if isinstance(matcher, dict) and set(matcher) >= {
        "upstream", "meblock"
    } else False
    stats = _cohort_stats(profiles)
    equivalent = all(
        x["null_equivalent_within_three_percent"] for x in stats.values()
    )
    ready = (
        same_code and pairs_balanced and clock_calibrated_and_stable
        and equivalent
    )
    return {
        "schema": "meblock-candidate-noise-preflight-v1",
        "ready_for_candidate_effect_screening": ready,
        "checks": {
            "byte_identical_null_builds_and_stock_engine": same_code,
            "at_least_ten_balanced_abba_pairs": pairs_balanced,
            "calibrated_and_stable_batched_timings": clock_calibrated_and_stable,
            "paired_null_uncertainty_within_three_percent": equivalent,
        },
        "paired_noise_by_cohort": stats,
        "scope": (
            "Null preflight for 3% practical effects; requires >=10 balanced "
            "same-code Firefox pairs and 95% paired-bootstrap median log-ratio "
            "interval entirely inside +/-3% in official and four per-type "
            "batched metrics. It is an empirical screen, NOT a power proof, "
            "real-site request p95 or evidence of candidate speedup. "
            "Actual candidate acceptance also requires different-source "
            "paired trials, correctness and affected-family gates."
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--null-report", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--memory-report", required=True, type=Path)
    parser.add_argument("--stock-xpi", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = assess_null(
            args.null_report, args.dataset,
            args.memory_report, args.stock_xpi,
        )
    except (OSError, ValueError, KeyError, TypeError, IndexError, OverflowError,
            UnicodeError, AttributeError):
        print(json.dumps({
            "schema": "meblock-candidate-noise-preflight-v1",
            "ready_for_candidate_effect_screening": False,
            "error": "Invalid or inaccessible local null-control evidence",
        }))
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["ready_for_candidate_effect_screening"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
