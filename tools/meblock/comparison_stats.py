"""Deterministic paired performance statistics for upstream/fork A/B runs."""

import math
import random
import statistics


def paired_performance(upstream, fork, *, samples=4000, seed=20261019):
    if not upstream or len(upstream) != len(fork):
        raise ValueError("Paired comparison requires the same positive number of runs")
    if samples < 1:
        raise ValueError("Bootstrap sample count must be positive")
    for value in upstream + fork:
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("Filtering costs must be finite and strictly positive")
    logs = [math.log(b / a) for a, b in zip(upstream, fork)]
    observed = statistics.median(logs)
    rng = random.Random(seed)
    estimates = sorted(
        statistics.median(rng.choices(logs, k=len(logs))) for _ in range(samples)
    )
    lower = estimates[int((samples - 1) * 0.025)]
    upper = estimates[int((samples - 1) * 0.975)]
    return {
        "pairs": len(logs),
        "per_pair_change_percent": [
            round((math.exp(v) - 1) * 100, 4) for v in logs
        ],
        "median_paired_change_percent": round((math.exp(observed) - 1) * 100, 4),
        "bootstrap_95_percent_interval_percent": [
            round((math.exp(lower) - 1) * 100, 4),
            round((math.exp(upper) - 1) * 100, 4),
        ],
        "bootstrap_method": "median log ratio, deterministic paired resampling",
        "bootstrap_seed": seed,
        "bootstrap_resamples": samples,
    }


def timing_verdict(stats, minimum_pairs=10, minimum_effect_percent=3):
    if stats["pairs"] < minimum_pairs:
        return "INCONCLUSIVE: fewer than ten paired A/B runs"
    lower, upper = stats["bootstrap_95_percent_interval_percent"]
    if upper < -minimum_effect_percent:
        return "CANDIDATE: filtering engine faster; verify on realistic workloads"
    if lower > minimum_effect_percent:
        return "CANDIDATE: filtering engine slower; investigate regression"
    return "INCONCLUSIVE: paired uncertainty includes the practical-change threshold"
