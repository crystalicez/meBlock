#!/usr/bin/env python3
"""Fail-closed local audit of evidence needed before optimization work."""

import argparse
import json
import math
from pathlib import Path


REQUIRED_BEHAVIOR = (
    "per_request_match",
    "modifier_per_request_match",
    "aggregate_modifier_counts_match",
    "browser_page_match",
    "fixture_precedence_match",
    "url_modifier_match",
    "dynamic_firewall_match",
    "settings_reload_and_restore_match",
    "filter_list_selection_match",
)


def valid_navigation_sample(sample):
    if not isinstance(sample, dict) or set(sample) != {
        "load_event_end_ms", "dom_content_loaded_end_ms",
        "allowed_script_resource_duration_ms",
    }:
        return False
    load = sample["load_event_end_ms"]
    dom = sample["dom_content_loaded_end_ms"]
    resource = sample["allowed_script_resource_duration_ms"]
    return (
        all(type(value) in (int, float) and math.isfinite(value)
            for value in (load, dom, resource))
        and 0 < dom <= load and 0 < resource <= load
    )


def assess(correctness, default, memory):
    c = correctness.get("comparison", {})
    d = default.get("comparison", {})
    m = memory
    c_behavior = c.get("correctness", {})
    d_behavior = d.get("correctness", {})
    correctness_ready = (
        correctness.get("status") == "complete"
        and type(correctness.get("fixture_version")) is int
        and correctness["fixture_version"] >= 8
        and correctness.get("default_enabled_lists_only") is False
        and correctness.get("injected_filter_rules", 0) > 0
        and all(c_behavior.get(name) is True for name in REQUIRED_BEHAVIOR)
        and c.get("verdict", "").startswith("CONTROL:")
    )
    default_ready = (
        default.get("status") == "complete"
        and default.get("default_enabled_lists_only") is True
        and default.get("injected_filter_rules") == 0
        and d.get("builds_identical") is True
        and d.get("default_enabled_lists_only") is True
        and d.get("injected_filter_rules") == 0
        and all(d_behavior.get(name) is True for name in REQUIRED_BEHAVIOR)
        and d.get("verdict", "").startswith("CONTROL:")
    )
    cpu = m.get("idle_firefox_cpu") or {}
    cpu_deltas = cpu.get("paired_deltas_one_core_percent") or []
    cpu_ready = (
        m.get("comparison_mode") == "build"
        and m.get("identical_xpi_negative_control") is True
        and m.get("workload") == "default-enabled-filter-lists"
        and m.get("extra_synthetic_rules") == 0
        and type(m.get("pairs")) is int and m["pairs"] >= 3
        and m.get("minimum_settle_seconds", 0) >= 300
        and m.get("verdict", "").startswith("CONTROL:")
        and cpu.get("screening_gate_passed") is True
        and cpu.get("process_pid_churn_observed") is False
        and cpu.get("pairs") == m.get("pairs")
        and type(cpu.get("minimum_observed_seconds")) in (int, float)
        and cpu["minimum_observed_seconds"] >= 300
        and isinstance(cpu_deltas, list)
        and len(cpu_deltas) == m["pairs"]
        and all(type(value) in (int, float) and math.isfinite(value)
                for value in cpu_deltas)
        and cpu.get("verdict", "").startswith("CONTROL:")
    )
    identity = m.get("pinned_profile_identity", {})
    coverage = default.get("rule_derived_probe_coverage") or {}
    buffers = (d.get("engine_buffer_footprint") or {}).get("upstream") or {}
    source_pins = ("upstream", "meblock")
    asset_pins = ("main", "prod")
    c_refs = correctness.get("refs") or {}
    d_refs = default.get("refs") or {}
    c_assets = correctness.get("assets") or {}
    d_assets = default.get("assets") or {}
    same_sources = (
        all(isinstance(c_refs.get(name), str) and len(c_refs[name]) == 40
            and c_refs[name] == d_refs.get(name)
            for name in source_pins)
        and all(isinstance(c_assets.get(name), str) and len(c_assets[name]) == 40
                and c_assets[name] == d_assets.get(name)
                for name in asset_pins)
    )
    c_harness = correctness.get("harness_files_sha256") or {}
    d_harness = default.get("harness_files_sha256") or {}
    matching_probes = all(
        isinstance(c_harness.get(name), str)
        and len(c_harness[name]) == 64
        and c_harness[name] == d_harness.get(name)
        for name in ("browser_probe.js", "browser_fixtures.py",
                     "state_probe.js", "engine_buffer_probe.js")
    )
    inputs_ready = (
        default_ready and cpu_ready and correctness_ready and same_sources
        and matching_probes
        and identity.get("network_filters") == buffers.get("compiled_network_filters")
        and identity.get("selected_list_keys_sha256") ==
        coverage.get("selected_list_keys_sha256")
        and identity.get("selected_list_count") == coverage.get("selected_list_count")
        and type(identity.get("selected_list_count")) is int
        and identity["selected_list_count"] > 0
    )
    def resource_valid(record):
        duration = record.get("median_run_p95_allowed_script_resource_duration_ms")
        return type(duration) in (float, int) and 0 < duration < 60000

    nav = d.get("fixture_navigation") or {}
    control = default.get("unfiltered_fixture_control") or {}
    local_resource_ready = (
        default_ready
        and resource_valid(nav.get("upstream") or {})
        and resource_valid(nav.get("meblock") or {})
        and isinstance(control.get("navigation"), list)
        and len(control["navigation"]) == 20
        and all(valid_navigation_sample(sample) for sample in control["navigation"])
    )
    matcher = d.get("batched_matcher") or {}

    def stable_amortized_context(role):
        record = matcher.get(role) or {}
        observed = record.get("range_run_p95_amortized_context_us")
        median = record.get("median_run_p95_amortized_context_us")
        return (
            isinstance(observed, list) and len(observed) == 2
            and all(type(n) in (int, float) and math.isfinite(n) and n > 0
                    for n in observed)
            and observed[0] <= observed[1]
            and type(median) in (int, float) and math.isfinite(median)
            and observed[0] <= median <= observed[1]
            and median > 0
            and (observed[1] - observed[0]) / median <= 0.5
        )

    matcher_usable = default_ready and all(
        (matcher.get(role) or {}).get("schema") ==
            "meblock-batched-context-match-summary-v1"
        and (matcher[role].get("runs") or 0) >= 3
        and matcher[role].get("calibrated_for_amortized_screening") is True
        and type(matcher[role].get("median_run_p95_amortized_context_us"))
            in (int, float)
        and matcher[role]["median_run_p95_amortized_context_us"] > 0
        and stable_amortized_context(role)
        for role in ("upstream", "meblock")
    )
    checks = {
        "independent_correctness_fixture": correctness_ready,
        "unmodified_default_list_parity": default_ready,
        "paired_cpu_memory_null_control": cpu_ready,
        "pinned_runtime_inputs": inputs_ready,
        "browser_local_resource_p95": local_resource_ready,
        "amortized_matcher_context_p95_precision": matcher_usable,
    }
    return {
        "schema": "meblock-baseline-readiness-v1",
        "ready_for_optimization": all(checks.values()),
        "checks": checks,
        "unresolved": [key for key, value in checks.items() if not value],
        "scope": (
            "This gate validates synthetic Firefox correctness and local default-list "
            "baseline evidence; batched-context p95 is NOT individual-call p95. "
            "It does not prove every rule, real-site tail latency, addon-only "
            "CPU/RAM attribution, or a candidate speedup."
        ),
    }


def read_local_report(path):
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Missing or oversized private baseline report")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Invalid private baseline report schema")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--correctness-report", type=Path, required=True)
    parser.add_argument("--default-report", type=Path, required=True)
    parser.add_argument("--memory-report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = assess(
            read_local_report(args.correctness_report),
            read_local_report(args.default_report),
            read_local_report(args.memory_report),
        )
    except (OSError, ValueError, UnicodeError, TypeError, AttributeError):
        print(json.dumps({
            "schema": "meblock-baseline-readiness-v1",
            "ready_for_optimization": False,
            "error": "Invalid or inaccessible local evidence",
        }))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result["ready_for_optimization"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
