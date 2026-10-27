#!/usr/bin/env python3
"""One quick Firefox speed suite using ONLY uBO's built-in SFNE benchmark.

Instrumented packages and evidence stay in ignored dist/build/; no extra
filter rules, custom matcher timing loops, or browsing-profile access.
Correctness belongs to compare_original.py, not this quick speed screen.
"""

import argparse
import hashlib
import json
from pathlib import Path
import secrets
import statistics
import subprocess
import threading
import time

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service

import compare_original as shared
from comparison_stats import paired_performance
from filtering_benchmark import parse_report_text, valid_rows


HERE = Path(__file__).resolve().parent
PROBE = HERE / "sfne_quick_probe.js"
VARIANCE_PROBE = HERE / "sfne_variance_probe.js"
ROLE_ORDER = ("upstream", "meblock")


def validate_official_runs(reading, request_count):
    """Reject partial runs and unequal decisions/modifier counts."""
    if (
        not isinstance(reading, dict)
        or reading.get("status") != "ok"
        or reading.get("engine_ready") is not True
        or not isinstance(reading.get("selected_filter_lists"), list)
        or not reading["selected_filter_lists"]
        or len(reading["selected_filter_lists"]) != len(set(reading["selected_filter_lists"]))
        or any(not isinstance(item, str) or not item for item in reading["selected_filter_lists"])
    ):
        raise ValueError("Firefox engine or filter-list state is incomplete")
    output = reading.get("official_runs")
    if not isinstance(output, list) or len(output) != 5:
        raise ValueError("Expected exactly five measured uBO benchmark results")
    parsed = []
    for text in output:
        if not isinstance(text, str):
            raise ValueError("Missing original uBO benchmark report")
        reports = parse_report_text(text)
        if len(reports) != 1 or reports[0]["requests"] != request_count:
            raise ValueError("Original uBO benchmark skipped requests")
        parsed.append(reports[0])
    if any(row["counts"] != parsed[0]["counts"] for row in parsed):
        raise ValueError("uBO benchmark filtering counts changed between trials")
    return {
        "durations_ms_rounded": [row["elapsed_ms_rounded"] for row in parsed],
        "counts": parsed[0]["counts"],
        "selected_lists": reading["selected_filter_lists"],
    }


def assess_paired_noise(upstream, meblock, *, identical_builds, clock_calibrated):
    """Independent Firefox pairs are the samples; five intra-profile trials are not."""
    if not upstream or len(upstream) != len(meblock):
        raise ValueError("Null control requires paired independent profiles")
    paired = paired_performance(upstream, meblock)
    balanced = len(upstream) >= 10 and len(upstream) % 2 == 0

    def inside_tolerance(statistics):
        low, high = statistics["bootstrap_95_percent_interval_percent"]
        return low > -3.0 and high < 3.0

    by_order = {}
    for label, first in (("upstream_first", 0), ("meblock_first", 1)):
        left, right = upstream[first::2], meblock[first::2]
        if not left:
            by_order[label] = {
                "pairs": 0, "null_equivalent_within_three_percent": False,
            }
            continue
        cohort = paired_performance(left, right)
        by_order[label] = {
            **cohort,
            "null_equivalent_within_three_percent": (
                len(left) >= 5 and inside_tolerance(cohort)
            ),
        }
    pooled_pass = balanced and inside_tolerance(paired)
    order_pass = balanced and all(
        result["null_equivalent_within_three_percent"]
        for result in by_order.values()
    )
    return {
        "identical_source_builds": identical_builds,
        "at_least_ten_balanced_abba_pairs": balanced,
        "whole_ms_rounding_below_half_percent": clock_calibrated,
        "pooled": paired,
        "pooled_null_equivalent_within_three_percent": pooled_pass,
        "by_run_order": by_order,
        "both_run_orders_equivalent_within_three_percent": order_pass,
        "ready_for_candidate_effect_screening": (
            identical_builds and balanced and clock_calibrated
            and pooled_pass and order_pass
        ),
        "scope": (
            "Same-source Firefox negative control only; independent profiles "
            "are samples, not the five timed trials. An equivalence preflight "
            "does not prove statistical power to detect a real 3% change."
        ),
    }


def summarize_runs(profiles, request_count, *, identical_builds=False):
    if len(profiles["upstream"]) != len(profiles["meblock"]) or not profiles["upstream"]:
        raise ValueError("Incomplete A/B benchmark pairs")
    records = {
        role: [validate_official_runs(row, request_count) for row in profiles[role]]
        for role in ROLE_ORDER
    }
    reference = records["upstream"][0]
    signatures = [row for side in records.values() for row in side]
    if any(
        row["counts"] != reference["counts"] or
        row["selected_lists"] != reference["selected_lists"]
        for row in signatures
    ):
        raise ValueError("Firefox A/B filtering decisions or enabled lists differ")
    samples = {}
    for role in ROLE_ORDER:
        samples[role] = [
            statistics.median(row["durations_ms_rounded"])
            for row in records[role]
        ]
    minimum = min(
        duration for row in signatures for duration in row["durations_ms_rounded"]
    )
    paired_noise = assess_paired_noise(
        samples["upstream"], samples["meblock"],
        identical_builds=identical_builds, clock_calibrated=minimum >= 200,
    )
    return {
        "schema": "meblock-upstream-sfne-only-v1",
        "paired_noise": paired_noise,
        "requests_per_trial": request_count,
        "pairs": len(records["upstream"]),
        "measured_trials_per_profile": 5,
        "warmup_trials_per_profile": 2,
        "timer_minimum_trial_ms_rounded": minimum,
        "whole_ms_rounding_below_half_percent": minimum >= 200,
        "counts": reference["counts"],
        "selected_list_count": len(reference["selected_lists"]),
        "roles": {
            role: {
                "median_profile_duration_ms": round(statistics.median(samples[role]), 3),
                "range_profile_duration_ms": [min(samples[role]), max(samples[role])],
                "all_profile_medians_ms": samples[role],
                "all_measured_trial_ms": [
                    item["durations_ms_rounded"] for item in records[role]
                ],
            }
            for role in ROLE_ORDER
        },
        "interpretation": (
            "uBO built-in SFNE full request + modifier benchmark, rounded to whole ms; "
            "default enabled lists only. Quick diagnostic. NOT addon CPU, real-site "
            "latency, a validated 3%-effect experiment, or independent per-request proof."
        ),
        "three_percent_effect_qualified": False,
    }


def null_control_exit_code(requested, summary):
    """Exit 3 marks a completed null test whose variance gate did not pass."""
    if requested and not summary["paired_noise"]["ready_for_candidate_effect_screening"]:
        return 3
    return 0


def summarize_variance(profiles):
    """Diagnostics only; never used to decide SFNE performance or readiness."""
    output = {}
    for role, readings in profiles.items():
        output[role] = []
        for reading in readings:
            warmup = reading.get("diagnostic_warmup_ms")
            trials = reading.get("diagnostic_measured_ms")
            if (
                not isinstance(warmup, list) or len(warmup) != 8
                or not isinstance(trials, list) or len(trials) != 12
                or reading.get("diagnostic_total_calls") != 20
                or any(type(value) is not int or value <= 0
                       for value in warmup + trials)
            ):
                raise ValueError("Incomplete SFNE variance diagnostic runs")
            mean = statistics.mean(trials)
            output[role].append({
                "warmup_ms": warmup,
                "measured_ms": trials,
                "early_median_ms": statistics.median(trials[:5]),
                "late_median_ms": statistics.median(trials[-5:]),
                "measured_cv": round(statistics.pstdev(trials) / mean, 4),
                "measured_min_ms": min(trials),
                "measured_max_ms": max(trials),
                "host_pressure": reading.get("diagnostic_host_pressure"),
            })
    return output


def wait_with_host_samples(state, role, timeout, service):
    """Sample coarse host load while Firefox finishes; not attribution or SFNE timing."""
    import psutil

    done = threading.Event()
    host_cpu = []
    clock_mhz = []
    observed_cpu = {}
    begin = time.monotonic()

    def sample():
        psutil.cpu_percent(interval=None)
        while not done.wait(0.25):
            host_cpu.append(psutil.cpu_percent(interval=None))
            freq = psutil.cpu_freq()
            if freq is not None and freq.current > 0:
                clock_mhz.append(freq.current)
            try:
                root = psutil.Process(service.process.pid)
                processes = [root, *root.children(recursive=True)]
                for process in processes:
                    identity = (process.pid, process.create_time())
                    current = process.cpu_times()
                    elapsed_cpu = current.user + current.system
                    prior = observed_cpu.get(identity)
                    if prior is None:
                        observed_cpu[identity] = (elapsed_cpu, elapsed_cpu)
                    else:
                        observed_cpu[identity] = (prior[0], max(prior[1], elapsed_cpu))
            except (psutil.Error, OSError):
                continue

    worker = threading.Thread(target=sample, daemon=True)
    worker.start()
    try:
        received = state.wait(role, timeout)
    finally:
        done.set()
        worker.join(timeout=2)
    wait_seconds = time.monotonic() - begin
    process_cpu = sum(last - first for first, last in observed_cpu.values())
    received["diagnostic_host_pressure"] = {
        "observation": "Whole post-install wait, includes startup settle; not timed SFNE attribution",
        "wait_seconds": round(wait_seconds, 2),
        "host_cpu_percent_median": round(statistics.median(host_cpu), 1) if host_cpu else None,
        "host_cpu_percent_max": round(max(host_cpu), 1) if host_cpu else None,
        "cpu_frequency_mhz_median": round(statistics.median(clock_mhz)) if clock_mhz else None,
        "firefox_tree_observed_cpu_seconds": round(process_cpu, 2),
        "firefox_tree_cpu_per_wall_second": round(process_cpu / wait_seconds, 3),
        "host_sample_count": len(host_cpu),
    }
    return received


def firefox_quick_run(xpi, role, state, driver_path, firefox_binary, timeout, addon_id,
                      *, diagnose=False, disable_ion=False):
    options = Options()
    options.binary_location = str(firefox_binary)
    options.add_argument("-headless")
    for name, value in {
        "browser.startup.homepage": "about:blank",
        "browser.shell.checkDefaultBrowser": False,
        "app.update.auto": False,
        "extensions.update.enabled": False,
        "toolkit.telemetry.enabled": False,
    }.items():
        options.set_preference(name, value)
    if disable_ion:
        options.set_preference("javascript.options.ion", False)
    service = Service(executable_path=driver_path) if driver_path else Service()
    browser = webdriver.Firefox(options=options, service=service)
    try:
        installed_id = browser.install_addon(str(xpi.resolve()), temporary=True)
        if diagnose:
            received = wait_with_host_samples(state, role, timeout, service)
        else:
            received = state.wait(role, timeout)
        if installed_id != addon_id:
            raise RuntimeError("Disposable Firefox addon identity changed")
        if received.get("status") != "ok":
            raise RuntimeError("uBO benchmark failed inside Firefox")
        received.pop("token", None)
        received["firefox_version"] = browser.capabilities.get("browserVersion")
        return received
    finally:
        browser.quit()


def run(args):
    if args.runs < 1 or args.timeout < 5:
        raise ValueError("Invalid run count or timeout")
    if args.diagnose_variance and args.null_control:
        raise ValueError("Variance diagnostic cannot qualify a canonical null control")
    if args.diagnose_no_ion and not args.diagnose_variance:
        raise ValueError("Ion toggle is diagnostic-only")
    if args.diagnose_variance and args.upstream_ref != args.meblock_ref:
        raise ValueError("Variance diagnostic requires the same source ref for both roles")
    if not args.dataset.is_file() or args.dataset.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Dataset missing or exceeds 32 MiB")
    raw = args.dataset.read_bytes()
    dataset = valid_rows(json.loads(raw))
    if len(dataset) > 500000:
        raise ValueError("Dataset exceeds 500,000 requests")
    coverage = shared.read_rule_derived_manifest(args.dataset, raw, dataset, True)
    assets = shared.BUILD_ROOT / "uAssets"
    for folder in ("main", "prod"):
        if not (assets / folder / ".git").is_dir():
            raise ValueError("Required pinned default-filter assets are missing")
    if not args.firefox.is_file():
        raise ValueError("Firefox executable unavailable")

    session = args.output_root / (time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3))
    session.mkdir(parents=True, exist_ok=False)
    state = shared.State(raw, secrets.token_hex(24))
    server = shared.Collector(state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    report_path = session / "report.json"
    metadata = {
        "schema": "meblock-quick-sfne-v1",
        "status": "incomplete",
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "dataset_count": len(dataset),
        "dataset_rule_derived": coverage is not None,
        "default_enabled_lists_only": True,
        "injected_filter_rules": 0,
        "uses_only_upstream_speed_benchmark": True,
        "source_refs": {
            role: shared.git("rev-parse", ref)
            for role, ref in (("upstream", args.upstream_ref), ("meblock", args.meblock_ref))
        },
        "probe_sha256": shared.hash_file(VARIANCE_PROBE if args.diagnose_variance else PROBE),
        "timing_diagnostic_only": args.diagnose_variance,
        "ion_disabled_for_meblock_role": args.diagnose_no_ion,
        "runner_sha256": shared.hash_file(Path(__file__)),
        "asset_refs": {
            name: subprocess.check_output(
                ["git", "-C", str(assets / name), "rev-parse", "HEAD"],
                text=True,
            ).strip()
            for name in ("main", "prod")
        },
        "runs_per_build": args.runs,
    }
    try:
        endpoint = "http://127.0.0.1:" + str(server.server_port)
        build_images = {}
        packages = {}
        fingerprints = {}
        for role in ROLE_ORDER:
            checkout = session / role / "checkout"
            shared.export_git(metadata["source_refs"][role], checkout)
            build = shared.run_build(checkout, assets)
            if coverage is not None and shared.bundled_asset_fingerprint(build) != coverage["bundled_asset_contents_sha256"]:
                raise ValueError("Pinned public filter assets changed")
            fingerprints[role] = shared.source_files(build)
            destination = session / role / "instrumented.xpi"
            packages[role] = shared.probe_package(
                build, destination, role, endpoint, state.token, raw,
                include_fixture_rules=False,
                probe_source=VARIANCE_PROBE if args.diagnose_variance else PROBE,
            )
            build_images[role] = destination
        metadata["packages"] = packages
        metadata["build_delta"] = shared.source_delta(fingerprints["upstream"], fingerprints["meblock"])
        identical_builds = metadata["build_delta"]["files_different"] == 0
        if args.null_control and not identical_builds:
            raise ValueError("Null-control flag requires byte-identical source builds")
        metadata["same_build_null_control"] = args.null_control
        profiles = {role: [] for role in ROLE_ORDER}
        driver_path = shared.geckodriver_path(None)
        for index in range(args.runs):
            order = ROLE_ORDER if index % 2 == 0 else tuple(reversed(ROLE_ORDER))
            for role in order:
                print(json.dumps({"pair": index + 1, "role": role, "state": "starting"}), flush=True)
                reading = firefox_quick_run(
                    build_images[role], role, state, driver_path,
                    args.firefox, args.timeout, packages[role]["addon_id"],
                    diagnose=args.diagnose_variance,
                    disable_ion=args.diagnose_no_ion and role == "meblock",
                )
                profiles[role].append(reading)
                validated = validate_official_runs(reading, len(dataset))
                print(json.dumps({
                    "pair": index + 1, "role": role,
                    "median_ms": statistics.median(validated["durations_ms_rounded"]),
                    "requests": len(dataset),
                }), flush=True)
                (session / (role + "-run-" + str(index + 1) + ".json")).write_text(
                    json.dumps(reading, indent=2) + "\n", encoding="utf-8"
                )
        summary = summarize_runs(profiles, len(dataset), identical_builds=identical_builds)
        versions = {
            entry.get("firefox_version")
            for readings in profiles.values() for entry in readings
        }
        if len(versions) != 1 or None in versions:
            raise ValueError("Firefox browser versions differ across A/B profiles")
        metadata["firefox_version"] = versions.pop()
        if coverage is not None:
            expected_digest = coverage["selected_list_keys_sha256"]
            observed = "\n".join(summary_row for summary_row in sorted(profiles["upstream"][0]["selected_filter_lists"]))
            if hashlib.sha256(observed.encode("utf-8")).hexdigest() != expected_digest:
                raise ValueError("Selected public default lists differ from the generated dataset")
        metadata["benchmark"] = summary
        if args.diagnose_variance:
            summary["variance_diagnostics"] = summarize_variance(profiles)
        metadata["status"] = "complete"
        qualified = summary["paired_noise"]["ready_for_candidate_effect_screening"]
        metadata["null_qualification"] = (
            "ready" if qualified else "not_ready"
        ) if args.null_control else "not_requested"
        print(json.dumps({
            "status": "complete",
            "pairs": args.runs,
            "request_count": len(dataset),
            "timer_rounding_sufficient": summary["whole_ms_rounding_below_half_percent"],
            "same_build_null_ready": qualified if args.null_control else False,
            "null_qualification": metadata["null_qualification"],
            "speed_effect_qualified": False,
        }), flush=True)
        return null_control_exit_code(args.null_control, summary)
    except Exception as error:
        metadata["status"] = "failed"
        metadata["error_type"] = type(error).__name__
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}), flush=True)
        return 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        report_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--runs", type=int, default=3, help="Independent Firefox A/B pairs (alternating order)")
    p.add_argument("--diagnose-variance", action="store_true",
                   help="Diagnostic-only: 8 warmups + 12 original SFNE calls and host pressure samples")
    p.add_argument("--diagnose-no-ion", action="store_true",
                   help="Diagnostic-only: set javascript.options.ion=false on the meblock-labeled arm")
    p.add_argument("--null-control", action="store_true",
                   help="Require byte-identical source builds for same-code noise screening")
    p.add_argument("--timeout", type=float, default=240)
    p.add_argument("--upstream-ref", default="upstream/master")
    p.add_argument("--meblock-ref", default="HEAD")
    p.add_argument("--firefox", type=Path, default=Path(r"C:\Program Files\Mozilla Firefox\firefox.exe"))
    p.add_argument("--output-root", type=Path, default=shared.BUILD_ROOT / "meblock-sfne")
    return run(p.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
