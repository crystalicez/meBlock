#!/usr/bin/env python3
"""Paired, isolated offline Firefox memory experiment; evidence stays local."""

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics

from capture_memory import PRIVATE_DIR, ROOT, RULE_MIXES, SAFE_PROCESS_CATEGORIES, capture


def schedule(pairs):
    for index in range(pairs):
        order = ("stock", "synthetic") if index % 2 == 0 else ("synthetic", "stock")
        for condition in order:
            yield index, condition


def summarize_pairs(rows, mode="rule-count"):
    if mode not in ("rule-count", "build"):
        raise ValueError("Unknown baseline comparison mode")
    paired = {}
    for pair, condition, data in rows:
        slot = paired.setdefault(pair, {})
        if condition in slot:
            raise ValueError("Condition repeated within a pair")
        if data.get("filter_engine", {}).get("auto_update") is not False:
            raise ValueError("Paired memory baseline requires disabled automatic filter updates")
        snapshots = data.get("captures")
        if not isinstance(snapshots, list) or len(snapshots) != 1:
            raise ValueError("A paired run requires exactly one memory report")
        reading = snapshots[0].get("extension_process_explicit_mib")
        if (type(reading) not in (int, float)
            or not math.isfinite(reading) or reading <= 0):
            raise ValueError("Missing positive finite Extensions process memory measurement")
        slot[condition] = data

    deltas = []
    for pair, conditions in sorted(paired.items()):
        if set(conditions) != {"stock", "synthetic"}:
            raise ValueError("Incomplete A/B pair")
        original = conditions["stock"]
        amended = conditions["synthetic"]
        if original["firefox_version"] != amended["firefox_version"]:
            raise ValueError("Firefox or source XPI differs between conditions")
        if (mode == "rule-count" and
            original["source_package_sha256"] != amended["source_package_sha256"]):
            raise ValueError("Firefox or source XPI differs between conditions")
        if (mode == "build" and original["filter_engine"]["network_filters"] !=
            amended["filter_engine"]["network_filters"]):
            raise ValueError("Build comparison requires identical compiled network filter counts")
        if (original["filter_engine"]["selected_lists_sha256"] !=
            amended["filter_engine"]["selected_lists_sha256"]):
            raise ValueError("Selected filter-list fingerprint differs between conditions")
        if (original["source_filter_assets_sha256"] !=
            amended["source_filter_assets_sha256"]):
            raise ValueError("Bundled filter asset fingerprint differs between conditions")
        if (original["filter_engine"]["cosmetic_filters"] !=
            amended["filter_engine"]["cosmetic_filters"]):
            raise ValueError("Cosmetic filtering engine differs between conditions")
        base = original["captures"][0]["extension_process_explicit_mib"]
        test = amended["captures"][0]["extension_process_explicit_mib"]
        summary = {
            "pair": pair + 1,
            "stock_extensions_process_explicit_mib": base,
            "synthetic_extensions_process_explicit_mib": test,
            "delta_mib": round(test - base, 3),
            "delta_percent": round((test / base - 1) * 100, 3),
        }
        baseline_groups = original["captures"][0].get("safe_process_categories_mib")
        candidate_groups = amended["captures"][0].get("safe_process_categories_mib")
        if isinstance(baseline_groups, dict) and isinstance(candidate_groups, dict):
            allowed = (*SAFE_PROCESS_CATEGORIES, "other_process_allocations")
            for group in allowed:
                values = (baseline_groups.get(group, 0), candidate_groups.get(group, 0))
                if any(type(value) not in (int, float) or not math.isfinite(value)
                       for value in values):
                    raise ValueError("Invalid aggregate process memory category")
            summary["safe_process_category_deltas_mib"] = {
                group: round(candidate_groups.get(group, 0) -
                             baseline_groups.get(group, 0), 3)
                for group in allowed
                if group in baseline_groups or group in candidate_groups
            }
        deltas.append(summary)
    if not deltas:
        raise ValueError("No A/B pairs found")
    settling = []
    for _, _, data in rows:
        seconds = data.get("settle_seconds_after_filter_readiness")
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Missing or invalid post-readiness settling duration")
        settling.append(seconds)
    all_inputs = {
        (
            data["firefox_version"],
            data["source_filter_assets_sha256"],
            data["filter_engine"]["selected_lists_sha256"],
            data["filter_engine"]["cosmetic_filters"],
        )
        for _, _, data in rows
    }
    if len(all_inputs) != 1:
        raise ValueError("Input XPI, Firefox version or filter lists drifted across pairs")
    source_inputs = {}
    for _, condition, data in rows:
        source_inputs.setdefault(condition, set()).add(data["source_package_sha256"])
    if any(len(values) != 1 for values in source_inputs.values()):
        raise ValueError("Source XPI changed across experimental pairs")
    if mode == "rule-count" and source_inputs["stock"] != source_inputs["synthetic"]:
        raise ValueError("Input XPI changed between baseline and synthetic workload")
    return {
        "comparison_mode": mode,
        "condition_labels": {
            "stock": "baseline",
            "synthetic": "candidate" if mode == "build" else "synthetic-filter-volume",
        },
        "pairs": len(deltas),
        "minimum_settle_seconds": min(settling),
        "median_stock_mib": round(statistics.median(
            row["stock_extensions_process_explicit_mib"] for row in deltas), 3),
        "median_synthetic_mib": round(statistics.median(
            row["synthetic_extensions_process_explicit_mib"] for row in deltas), 3),
        "median_paired_delta_mib": round(statistics.median(
            row["delta_mib"] for row in deltas), 3),
        "per_pair": deltas,
        "verdict": (
            "EXPLORATORY: fewer than three independent pairs"
            if len(deltas) < 3 else
            "EXPLORATORY: post-readiness idle time shorter than 300 seconds"
            if min(settling) < 300 else
            "MEASURED: process-wide difference only; no per-extension optimization claim"
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--addon", type=Path, default=ROOT / "dist/build/uBlock0.firefox.xpi")
    parser.add_argument("--candidate-addon", type=Path,
                        help="Compare two independently built XPIs instead of changing rule count")
    parser.add_argument("--firefox", type=Path, default=Path(r"C:\Program Files\Mozilla Firefox\firefox.exe"))
    parser.add_argument("--pairs", type=int, default=3)
    parser.add_argument("--settle-seconds", type=float, default=300)
    parser.add_argument("--base-network-filters", type=int, required=True)
    parser.add_argument("--extra-synthetic-hostnames", "--extra-synthetic-rules",
                        dest="extra_synthetic_hostnames", type=int, default=0)
    parser.add_argument("--synthetic-rule-mix", choices=RULE_MIXES,
                        default="hostname")
    args = parser.parse_args(argv)
    if (args.pairs < 1 or args.settle_seconds < 0 or
        args.base_network_filters < 1 or args.extra_synthetic_hostnames < 0 or
        (args.candidate_addon is None and args.extra_synthetic_hostnames == 0)):
        parser.error("Invalid number of pairs, time, filter count or comparison mode")
    mode = "build" if args.candidate_addon is not None else "rule-count"
    rows = []
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        for pair, condition in schedule(args.pairs):
            if mode == "build":
                addon = args.candidate_addon if condition == "synthetic" else args.addon
                extra = args.extra_synthetic_hostnames
            else:
                addon = args.addon
                extra = args.extra_synthetic_hostnames if condition == "synthetic" else 0
            expected = args.base_network_filters + extra
            label = "candidate" if mode == "build" and condition == "synthetic" else condition
            print(f"Paired baseline {pair + 1}/{args.pairs}, {label} condition", flush=True)
            data = capture(
                addon, args.firefox, args.settle_seconds,
                extra_rules=extra, samples=1, expected_network_filters=expected,
                offline=True, rule_mix=args.synthetic_rule_mix,
            )
            rows.append((pair, condition, data))
        summary = summarize_pairs(rows, mode=mode)
        summary["base_network_filters"] = args.base_network_filters
        summary["extra_synthetic_rules"] = args.extra_synthetic_hostnames
        if args.synthetic_rule_mix == "hostname":
            summary["extra_synthetic_hostnames"] = args.extra_synthetic_hostnames
        summary["synthetic_rule_mix"] = args.synthetic_rule_mix
        summary["source_package_sha256"] = rows[0][2]["source_package_sha256"]
        summary["source_filter_assets_sha256"] = rows[0][2]["source_filter_assets_sha256"]
        if mode == "build":
            summary["candidate_source_package_sha256"] = next(
                data["source_package_sha256"]
                for _, condition, data in rows if condition == "synthetic"
            )
        summary["conditions"] = (
            "baseline XPI vs candidate XPI; both use equal pinned filter counts"
            if mode == "build" else
            "same XPI with and without additional synthetic "
            + ("hostname" if args.synthetic_rule_mix == "hostname"
               else "mixed network") + " rules"
        )
        summary["measurement"] = "whole Firefox Extensions process explicit, not addon-private RAM"
        summary["raw_reports"] = "retained only in ignored dist/build/private-memory"
        name = datetime.now(timezone.utc).strftime("paired-memory-%Y%m%dT%H%M%S%fZ.json")
        path = PRIVATE_DIR / name
        path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Baseline rejected: {type(error).__name__} ({str(error)[:190]})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
