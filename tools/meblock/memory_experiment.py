#!/usr/bin/env python3
"""Paired, isolated offline Firefox memory experiment; evidence stays local."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics

from capture_memory import PRIVATE_DIR, ROOT, capture


def schedule(pairs):
    for index in range(pairs):
        order = ("stock", "synthetic") if index % 2 == 0 else ("synthetic", "stock")
        for condition in order:
            yield index, condition


def summarize_pairs(rows):
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
        if type(reading) not in (int, float) or reading <= 0:
            raise ValueError("Missing positive Extensions process memory measurement")
        slot[condition] = data

    deltas = []
    for pair, conditions in sorted(paired.items()):
        if set(conditions) != {"stock", "synthetic"}:
            raise ValueError("Incomplete A/B pair")
        original = conditions["stock"]
        amended = conditions["synthetic"]
        if (original["source_package_sha256"] != amended["source_package_sha256"]
            or original["firefox_version"] != amended["firefox_version"]):
            raise ValueError("Firefox or source XPI differs between conditions")
        if (original["filter_engine"]["selected_lists_sha256"] !=
            amended["filter_engine"]["selected_lists_sha256"]):
            raise ValueError("Selected filter-list fingerprint differs between conditions")
        if (original["filter_engine"]["cosmetic_filters"] !=
            amended["filter_engine"]["cosmetic_filters"]):
            raise ValueError("Cosmetic filtering engine differs between conditions")
        base = original["captures"][0]["extension_process_explicit_mib"]
        test = amended["captures"][0]["extension_process_explicit_mib"]
        deltas.append({
            "pair": pair + 1,
            "stock_extensions_process_explicit_mib": base,
            "synthetic_extensions_process_explicit_mib": test,
            "delta_mib": round(test - base, 3),
            "delta_percent": round((test / base - 1) * 100, 3),
        })
    if not deltas:
        raise ValueError("No A/B pairs found")
    all_inputs = {
        (
            data["source_package_sha256"],
            data["firefox_version"],
            data["filter_engine"]["selected_lists_sha256"],
            data["filter_engine"]["cosmetic_filters"],
        )
        for _, _, data in rows
    }
    if len(all_inputs) != 1:
        raise ValueError("Input XPI, Firefox version or filter lists drifted across pairs")
    return {
        "pairs": len(deltas),
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
            "MEASURED: process-wide difference only; no per-extension optimization claim"
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--addon", type=Path, default=ROOT / "dist/build/uBlock0.firefox.xpi")
    parser.add_argument("--firefox", type=Path, default=Path(r"C:\Program Files\Mozilla Firefox\firefox.exe"))
    parser.add_argument("--pairs", type=int, default=3)
    parser.add_argument("--settle-seconds", type=float, default=300)
    parser.add_argument("--base-network-filters", type=int, required=True)
    parser.add_argument("--extra-synthetic-hostnames", type=int, required=True)
    args = parser.parse_args(argv)
    if (args.pairs < 1 or args.settle_seconds < 0 or
        args.base_network_filters < 1 or args.extra_synthetic_hostnames < 1):
        parser.error("Invalid number of pairs, time or filters")
    rows = []
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        for pair, condition in schedule(args.pairs):
            extra = args.extra_synthetic_hostnames if condition == "synthetic" else 0
            expected = args.base_network_filters + extra
            print(f"Paired baseline {pair + 1}/{args.pairs}, {condition} condition", flush=True)
            data = capture(
                args.addon, args.firefox, args.settle_seconds,
                extra_rules=extra, samples=1, expected_network_filters=expected,
                offline=True,
            )
            rows.append((pair, condition, data))
        summary = summarize_pairs(rows)
        summary["base_network_filters"] = args.base_network_filters
        summary["extra_synthetic_hostnames"] = args.extra_synthetic_hostnames
        summary["source_package_sha256"] = rows[0][2]["source_package_sha256"]
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
