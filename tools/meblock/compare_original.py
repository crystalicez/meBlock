#!/usr/bin/env python3
"""Compare pinned upstream and meBlock builds in real, isolated Firefox sessions.

Build artifacts, test instrumentation, captured results, and the temporary
filter corpus remain under dist/build/; the repository source is unchanged.
"""

import argparse
from collections import Counter
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import statistics
import subprocess
import sys
import tarfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from urllib.parse import parse_qs, urlsplit
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from benchmark_firefox import collect_idle, geckodriver_path
from comparison_stats import paired_performance, timing_verdict
from default_rule_probes import bundled_asset_fingerprint
from browser_fixtures import (
    FIXTURE_VERSION, OBSERVATION_SCRIPT, ROUTES, RULES, WITH_ADDON, WITHOUT_ADDON,
    PARAMETERS_WITH_ADDON, PARAMETERS_WITHOUT_ADDON,
    DYNAMIC_FIREWALL_EXPECTED, STATE_PERSISTENCE_KEY,
    PRECEDENCE_CASES, PRECEDENCE_EXPECTED, precedence_case_request,
)
from filtering_benchmark import parse_report_text, valid_rows

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service


ROOT = Path(__file__).resolve().parents[2]
BUILD_ROOT = ROOT / "dist" / "build"
PROBE_PATH = Path(__file__).with_name("browser_probe.js")
BUFFER_PROBE_PATH = Path(__file__).with_name("engine_buffer_probe.js")
ROLES = ("upstream", "meblock")


def git(*arguments):
    return subprocess.check_output(
        ["git", "-C", str(ROOT), *arguments], text=True
    ).strip()


def hash_bytes(data):
    return hashlib.sha256(data).hexdigest()


def hash_file(path):
    return hash_bytes(path.read_bytes())


def source_files(root):
    return {
        p.relative_to(root).as_posix(): hash_file(p)
        for p in root.rglob("*") if p.is_file()
    }


def source_delta(a, b):
    different = sorted(
        k for k in a.keys() | b.keys() if a.get(k) != b.get(k)
    )
    return {"files_different": len(different), "paths_first_30": different[:30]}


def export_git(ref, target):
    target.mkdir(parents=True, exist_ok=False)
    content = subprocess.check_output(
        ["git", "-C", str(ROOT), "archive", "--format=tar", ref]
    )
    with tarfile.open(fileobj=io.BytesIO(content)) as archive:
        archive.extractall(target, filter="data")
    if not (target / "tools" / "make-firefox.sh").is_file():
        raise RuntimeError(f"Git snapshot lacks Firefox build tools: {ref}")


def run_build(source, assets):
    for branch in ("main", "prod"):
        destination = source / "dist" / "build" / "uAssets" / branch
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(assets / branch, destination)
    bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if not bash.is_file():
        raise RuntimeError("Git Bash not found")
    env = {**os.environ, "PYTHONUTF8": "1"}
    process = subprocess.run(
        [str(bash), "-lc", 'python3() { python "$@"; }; export -f python3; bash tools/make-firefox.sh'],
        cwd=source, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", timeout=150, check=False,
    )
    (source / "dist" / "build" / "build.log").write_text(
        process.stdout, encoding="utf-8"
    )
    if process.returncode != 0:
        raise RuntimeError(f"Firefox build failed ({process.returncode}): {process.stdout[-2500:]}")
    output = source / "dist" / "build" / "uBlock0.firefox"
    if not (output / "manifest.json").is_file():
        raise RuntimeError("Build finished without a Firefox manifest")
    return output


def probe_package(build, destination, role, endpoint, token, dataset_bytes,
                  *, include_fixture_rules=True, challenge_rules=(),
                  timing_sensitivity_control=False, probe_source=None):
    network_engine = build / "js" / "static-net-filtering.js"
    original_engine = network_engine.read_text(encoding="utf-8")
    if "meblockEngineBufferFootprint" in original_engine:
        raise RuntimeError("Test-only engine buffer probe already injected")
    if timing_sensitivity_control and (include_fixture_rules or challenge_rules):
        raise ValueError("Timing sensitivity control requires default-list-only probes")
    config = {
        "role": role, "endpoint": endpoint, "token": token,
        "sensitivity_control": timing_sensitivity_control and role == "meblock",
        "precedence_cases": (
            [precedence_case_request(case) for case in PRECEDENCE_CASES]
            if include_fixture_rules else []
        ),
        "challenge_diagnostics": bool(challenge_rules),
    }
    (build / "assets" / "meblock-requests.json").write_bytes(dataset_bytes)
    background = build / "js" / "background.js"
    settings_source = background.read_text(encoding="utf-8")
    update_default = "    autoUpdate: true,"
    if settings_source.count(update_default) != 1:
        raise RuntimeError("Cannot freeze automatic updates in temporary test build")
    background.write_text(
        settings_source.replace(update_default, "    autoUpdate: false,", 1),
        encoding="utf-8",
    )
    core_filters = build / "assets" / "ublock" / "filters.min.txt"
    if not core_filters.is_file():
        raise RuntimeError("Built-in filter list missing for browser behavior fixture")
    if include_fixture_rules and challenge_rules:
        raise ValueError("Exception challenge and generic fixture cannot be combined")
    added = RULES if include_fixture_rules else challenge_rules
    if added:
        with core_filters.open("a", encoding="utf-8") as stream:
            stream.write("\n" + "\n".join(added) + "\n")
    text = (probe_source or PROBE_PATH).read_text(encoding="utf-8")
    if "__MEBLOCK_CONFIG__" not in text:
        raise RuntimeError("Probe configuration placeholder not found")
    (build / "js" / "meblock-probe.js").write_text(
        text.replace("__MEBLOCK_CONFIG__", json.dumps(config)), encoding="utf-8"
    )
    state_probe = Path(__file__).with_name("state_probe.js")
    (build / "js" / "meblock-state-probe.js").write_bytes(state_probe.read_bytes())
    network_engine.write_text(
        original_engine + "\n" + BUFFER_PROBE_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    bg = build / "background.html"
    html = bg.read_text(encoding="utf-8")
    anchor = "</body>"
    if html.count(anchor) != 1:
        raise RuntimeError("Unexpected upstream background HTML")
    bg.write_text(
        html.replace(anchor, '<script src="js/meblock-probe.js" type="module"></script>\n' + anchor),
        encoding="utf-8",
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(p for p in build.rglob("*") if p.is_file()):
            info = ZipInfo(path.relative_to(build).as_posix(), (1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)
    with ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Instrumented XPI is corrupt")
        manifest = json.loads(archive.read("manifest.json"))
        if not manifest["browser_specific_settings"]["gecko"]["id"]:
            raise RuntimeError("Missing addon identity")
    return {
        "bytes": destination.stat().st_size,
        "sha256": hash_file(destination),
        "addon_id": manifest["browser_specific_settings"]["gecko"]["id"],
    }


class State:
    def __init__(self, dataset, token):
        self.dataset = dataset
        self.token = token
        self.lock = threading.Condition()
        self.pending = {}

    def receive(self, role, body):
        with self.lock:
            if role in self.pending:
                raise ValueError(f"Duplicate result for {role}")
            self.pending[role] = body
            self.lock.notify_all()

    def wait(self, role, seconds):
        end = time.monotonic() + seconds
        with self.lock:
            while role not in self.pending:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"No Firefox engine result for {role}")
                self.lock.wait(remaining)
            return self.pending.pop(role)


class Collector(ThreadingHTTPServer):
    def __init__(self, state):
        super().__init__(("127.0.0.1", 0), Handler)
        self.state = state


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        return

    def respond(self, status, body=b"", content_type="application/json"):
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.respond(204)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/dataset":
            return self.respond(200, self.server.state.dataset)
        if path in ROUTES:
            content_type, body = ROUTES[path]
            return self.respond(200, body, content_type)
        self.respond(404)

    def do_POST(self):
        if self.path != "/result":
            return self.respond(404)
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n < 1 or n > 4_000_000:
                return self.respond(413)
            msg = json.loads(self.rfile.read(n))
            if msg.get("token") != self.server.state.token:
                return self.respond(403)
            role = msg.get("role")
            if role not in ROLES:
                return self.respond(400)
            self.server.state.receive(role, msg)
            self.respond(200, b'{"ok":true}')
        except (ValueError, TypeError, json.JSONDecodeError):
            self.respond(400)


NAVIGATION_WARMUP = 2
NAVIGATION_SAMPLES = 20
NAVIGATION_SCRIPT = """
const entry = performance.getEntriesByType('navigation')[0];
if (!entry) { return null; }
const allowed = performance.getEntriesByType('resource').filter(
    resource => resource.initiatorType === 'script' &&
        new URL(resource.name).pathname === '/test-allowed.js'
);
return {
    load_event_end_ms: entry.loadEventEnd,
    dom_content_loaded_end_ms: entry.domContentLoadedEventEnd,
    allowed_script_resource_duration_ms: allowed.length === 1 ? allowed[0].duration : null,
    navigation_type: entry.type,
};
"""


def nearest_rank(values, percentile):
    ordered = sorted(values)
    return ordered[math.ceil(percentile * len(ordered)) - 1]


def summarize_fixture_navigation(samples):
    if not isinstance(samples, list) or len(samples) != NAVIGATION_SAMPLES:
        raise ValueError("Fixture navigation requires exactly 20 measured loads")
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != {
            "load_event_end_ms", "dom_content_loaded_end_ms",
            "allowed_script_resource_duration_ms",
        }:
            raise ValueError("Incomplete fixture navigation timing sample")
        load = sample["load_event_end_ms"]
        dom = sample["dom_content_loaded_end_ms"]
        resource = sample["allowed_script_resource_duration_ms"]
        if (
            type(load) not in (int, float) or type(dom) not in (int, float)
            or type(resource) not in (int, float)
            or not all(math.isfinite(v) for v in (load, dom, resource))
            or not 0 < dom <= load or not 0 < resource <= load
        ):
            raise ValueError("Invalid browser navigation or resource timing")
    load_times = [sample["load_event_end_ms"] for sample in samples]
    dom_times = [sample["dom_content_loaded_end_ms"] for sample in samples]
    resource_times = [
        sample["allowed_script_resource_duration_ms"] for sample in samples
    ]
    return {
        "sample_count": len(samples),
        "warmup_navigations": NAVIGATION_WARMUP,
        "p50_load_event_end_ms": round(statistics.median(load_times), 3),
        "p95_load_event_end_ms": round(nearest_rank(load_times, 0.95), 3),
        "range_load_event_end_ms": [round(min(load_times), 3), round(max(load_times), 3)],
        "p95_dom_content_loaded_end_ms": round(nearest_rank(dom_times, 0.95), 3),
        "p95_allowed_script_resource_duration_ms": round(
            nearest_rank(resource_times, 0.95), 3
        ),
        "range_allowed_script_resource_duration_ms": [
            round(min(resource_times), 3), round(max(resource_times), 3)
        ],
        "scope": (
            "Loopback fixture navigation and one no-store allowed script resource "
            "per navigation. Resource Timing duration is an actual browser-observed "
            "local request, not a production website or representative request p95."
        ),
    }


def measure_fixture_navigation(browser, endpoint, expected):
    records = []
    for index in range(NAVIGATION_WARMUP + NAVIGATION_SAMPLES):
        browser.get(endpoint + f"/fixture?navigation_trial={index}")
        timing = browser.execute_script(NAVIGATION_SCRIPT)
        if not isinstance(timing, dict) or timing.get("navigation_type") != "navigate":
            raise RuntimeError("Firefox navigation performance entry missing or not a navigation")
        observed = browser.execute_script(OBSERVATION_SCRIPT)
        for _ in range(12):
            if observed == expected:
                break
            time.sleep(0.05)
            observed = browser.execute_script(OBSERVATION_SCRIPT)
        failures = fixture_page_mismatches(observed, expected)
        if failures:
            raise RuntimeError(f"Fixture behavior changed during navigation timing: {failures}")
        if index >= NAVIGATION_WARMUP:
            records.append({
                "load_event_end_ms": timing.get("load_event_end_ms"),
                "dom_content_loaded_end_ms": timing.get("dom_content_loaded_end_ms"),
                "allowed_script_resource_duration_ms": timing.get(
                    "allowed_script_resource_duration_ms"
                ),
            })
    summarize_fixture_navigation(records)
    return records


def fixture_page_mismatches(observed, expected):
    if not isinstance(observed, dict):
        return [{"field": "page", "error": "missing observation"}]
    failures = []
    for field, wanted in expected.items():
        actual = observed.get(field)
        if isinstance(wanted, list):
            if not isinstance(actual, list) or len(actual) != len(wanted):
                failures.append({"field": field, "error": "missing or incomplete rule vector"})
                continue
            wrong = [
                index for index, (reference, value) in enumerate(zip(wanted, actual))
                if type(value) is not bool or value is not reference
            ]
            if wrong:
                failures.append({"field": field, "indices_first_25": wrong[:25]})
        elif type(actual) is not type(wanted) or actual != wanted:
            failures.append({"field": field, "error": "incorrect value"})
    if set(observed) != set(expected):
        failures.append({"field": "page", "error": "unexpected or missing fields"})
    return failures


def observe_page(browser, endpoint, expected):
    observed = None
    for attempt in range(3):
        browser.get(endpoint + f"/fixture?attempt={attempt}")
        for _ in range(16):
            observed = browser.execute_script(OBSERVATION_SCRIPT)
            if not fixture_page_mismatches(observed, expected):
                return observed
            time.sleep(0.25)
    return observed


def observe_parameters(browser, endpoint):
    browser.get(endpoint + "/fixture-params?utm_source=remove&keep=one")
    current = urlsplit(browser.current_url)
    query = parse_qs(current.query)
    return {
        "tracking_parameter_present": "utm_source" in query,
        "keep_parameter_preserved": query.get("keep") == ["one"],
        "fixture_path_correct": current.path == "/fixture-params",
    }


def test_without_addon(endpoint, driver_binary, firefox_binary):
    options = Options()
    options.binary_location = str(firefox_binary)
    options.add_argument("-headless")
    service = Service(executable_path=driver_binary) if driver_binary else Service()
    browser = webdriver.Firefox(options=options, service=service)
    try:
        observed = observe_page(browser, endpoint, WITHOUT_ADDON)
        control_failures = fixture_page_mismatches(observed, WITHOUT_ADDON)
        if control_failures:
            raise RuntimeError(
                f"Unfiltered fixture control failed: {control_failures}"
            )
        parameters = observe_parameters(browser, endpoint)
        if parameters != PARAMETERS_WITHOUT_ADDON:
            raise RuntimeError(f"Unfiltered parameter control failed: {parameters}")
        navigation = measure_fixture_navigation(browser, endpoint, WITHOUT_ADDON)
        return {"page": observed, "parameters": parameters, "navigation": navigation}
    finally:
        browser.quit()


def precedence_mismatches(observed, *, default_lists):
    expected = () if default_lists else PRECEDENCE_EXPECTED
    if not isinstance(observed, list) or len(observed) != len(expected):
        return [{"error": "missing or incomplete precedence vector"}]
    return [
        {"index": index, "name": PRECEDENCE_CASES[index][0],
         "expected": wanted, "actual": value}
        for index, (wanted, value) in enumerate(zip(expected, observed))
        if type(value) is not int or value != wanted
    ]


def validate_challenge_match_codes(result, enabled):
    observed = result.get("challenge_match_source_codes")
    rows = result.get("dataset_rows")
    expected_count = rows if enabled else 0
    if (
        type(rows) is not int or rows < 1
        or type(expected_count) is not int or expected_count < 0
        or not isinstance(observed, list) or len(observed) != expected_count
        or any(type(code) is not int or not 0 <= code <= 15
               for code in observed)
    ):
        raise ValueError("Incorrect challenge match-source diagnostics")


def firefox_run(xpi, role, state, driver_binary, firefox_binary,
                timeout, idle_sample, endpoint, expected_addon_id,
                *, default_lists=False, expect_challenge_diagnostics=False):
    options = Options()
    options.binary_location = str(firefox_binary)
    options.add_argument("-headless")
    for name, value in {
        "browser.startup.homepage": "about:blank",
        "browser.shell.checkDefaultBrowser": False,
        "app.update.auto": False,
        "extensions.update.enabled": False,
        "toolkit.telemetry.enabled": False,
        # Disposable benchmark profiles only. Default Firefox rounds
        # performance.now() to ~1ms, so microsecond samples otherwise
        # degenerate to 0 or 1000us and cannot estimate matcher p95.
        "privacy.reduceTimerPrecision": False,
    }.items():
        options.set_preference(name, value)
    service = Service(executable_path=driver_binary) if driver_binary else Service()
    browser = webdriver.Firefox(options=options, service=service)
    try:
        reported_id = browser.install_addon(str(xpi.resolve()), temporary=True)
        result = state.wait(role, timeout)
        if reported_id != expected_addon_id:
            raise RuntimeError(f"Unexpected test addon ID {reported_id!r}, expected {expected_addon_id!r}")
        if result.get("status") != "ok":
            raise RuntimeError(result.get("message", "Firefox benchmark failed") + "\n" + result.get("stack", ""))
        if result.get("engine_ready") is not True:
            raise RuntimeError("Benchmark ran without filtering readiness")
        failures = precedence_mismatches(
            result.get("fixture_precedence"), default_lists=default_lists
        )
        if failures:
            raise RuntimeError(f"Firefox static precedence regression: {failures[:18]}")
        validate_challenge_match_codes(result, expect_challenge_diagnostics)
        result["fixture_precedence_verified"] = True
        result["addon_id_verified"] = True
        result["firefox_version"] = browser.capabilities.get("browserVersion")
        result.pop("token", None)
        expected_page = WITHOUT_ADDON if default_lists else WITH_ADDON
        expected_parameters = (
            PARAMETERS_WITHOUT_ADDON if default_lists else PARAMETERS_WITH_ADDON
        )
        observed = observe_page(browser, endpoint, expected_page)
        result["browser_behavior"] = observed
        differing = fixture_page_mismatches(observed, expected_page)
        if differing:
            raise RuntimeError(f"Firefox behavior regression: {differing}")
        result["browser_parameters"] = observe_parameters(browser, endpoint)
        if result["browser_parameters"] != expected_parameters:
            raise RuntimeError(
                f"Firefox URL-modification regression: {result['browser_parameters']}"
            )
        result["fixture_navigation"] = measure_fixture_navigation(
            browser, endpoint, expected_page
        )
        if idle_sample:
            result["idle_process_sample"] = collect_idle(
                browser.service.process.pid, idle_sample
            )
        return result
    finally:
        browser.quit()


def check_behavior(upstream, meblock, dataset):
    baseline = upstream[0]["outcomes"]
    baseline_modifiers = upstream[0].get("modifier_masks")
    if not isinstance(baseline_modifiers, list) or len(baseline_modifiers) != len(dataset):
        raise ValueError("Reference run lacks complete modifier vector")
    problems = []
    modifier_problems = []
    for label, series in (("upstream", upstream), ("meblock", meblock)):
        for index, record in enumerate(series):
            vector = record.get("outcomes")
            modifiers = record.get("modifier_masks")
            if not isinstance(vector, list) or len(vector) != len(dataset):
                raise ValueError(f"{label} run {index} lacks complete decision vector")
            if not isinstance(modifiers, list) or len(modifiers) != len(dataset):
                raise ValueError(f"{label} run {index} lacks complete modifier vector")
            if any(type(mask) is not int or not 0 <= mask <= 63 for mask in modifiers):
                raise ValueError(f"{label} run {index} contains invalid modifier mask")
            for offset, (a, b) in enumerate(zip(baseline, vector)):
                if a != b and len(problems) < 25:
                    problems.append({
                        "role": label, "run": index + 1, "request_index": offset,
                        "reference": a, "observed": b, "request": dataset[offset],
                    })
            for offset, (a, b) in enumerate(zip(baseline_modifiers, modifiers)):
                if a != b and len(modifier_problems) < 25:
                    modifier_problems.append({
                        "role": label, "run": index + 1, "request_index": offset,
                        "reference_mask": a, "observed_mask": b,
                        "request": dataset[offset],
                    })
    return {
        "per_request_match": not problems,
        "mismatches_first_25": problems,
        "modifier_per_request_match": not modifier_problems,
        "modifier_mismatches_first_25": modifier_problems,
        "checked_requests_per_run": len(baseline),
        "checked_modifier_masks_per_run": len(baseline_modifiers),
    }


def summarize_role(series):
    values = [row["us_per_request"] for row in series]
    summary = {
        "runs": len(values),
        "median_us_per_request": round(statistics.median(values), 5),
        "range_us_per_request": [round(min(values), 5), round(max(values), 5)],
        "individual_us_per_request": [round(v, 5) for v in values],
    }
    ready = []
    for row in series:
        match = re.match(r"^(\d+) ms", str(row.get("engine_ready_after", "")))
        if match:
            ready.append(int(match.group(1)))
    if len(ready) == len(series):
        summary["extension_engine_ready_median_ms"] = statistics.median(ready)
        summary["extension_engine_ready_range_ms"] = [min(ready), max(ready)]
    return summary


REQUEST_LATENCY_KEYS = (
    "schema", "clock_step_us", "sample_count", "passes", "zero_sample_count",
    "min_us", "median_us", "p95_us", "p99_us", "max_us",
    "empty_timer_pair_median_us", "empty_timer_pair_p95_us",
)


def summarize_request_latency(series, request_count):
    readings = [row.get("request_latency") for row in series]
    if all(reading is None for reading in readings):
        return None
    for reading in readings:
        if (
            not isinstance(reading, dict)
            or set(reading) != set(REQUEST_LATENCY_KEYS)
            or reading["schema"] != "meblock-request-latency-v2"
            or type(reading["passes"]) is not int or reading["passes"] < 1
            or type(reading["sample_count"]) is not int
            or reading["sample_count"] != reading["passes"] * request_count
            or type(reading["zero_sample_count"]) is not int
            or not 0 <= reading["zero_sample_count"] <= reading["sample_count"]
        ):
            raise ValueError("Invalid or incomplete request-level latency samples")
        numeric = (
            "min_us", "median_us", "p95_us", "p99_us", "max_us",
            "clock_step_us", "empty_timer_pair_median_us", "empty_timer_pair_p95_us",
        )
        if any(
            type(reading[key]) not in (int, float)
            or not math.isfinite(reading[key]) or reading[key] < 0
            for key in numeric
        ) or not (
            reading["min_us"] <= reading["median_us"] <= reading["p95_us"]
            <= reading["p99_us"] <= reading["max_us"]
            and reading["empty_timer_pair_median_us"] <=
            reading["empty_timer_pair_p95_us"]
        ):
            raise ValueError("Invalid request-level latency percentiles")
    p95_values = [row["p95_us"] for row in readings]
    precision_usable = all(
        reading["clock_step_us"] > 0
        and reading["p95_us"] >= reading["clock_step_us"] * 3
        and reading["p95_us"] > reading["empty_timer_pair_p95_us"]
        and reading["zero_sample_count"] / reading["sample_count"] < 0.85
        for reading in readings
    )
    return {
        "runs": len(readings),
        "samples_per_run": readings[0]["sample_count"],
        "passes_per_run": readings[0]["passes"],
        "precision_usable_for_instrumented_screening": precision_usable,
        "median_run_p95_us": (
            round(statistics.median(p95_values), 4) if precision_usable else None
        ),
        "range_run_p95_us": (
            [round(min(p95_values), 4), round(max(p95_values), 4)]
            if precision_usable else None
        ),
        "max_zero_sample_fraction": round(max(
            row["zero_sample_count"] / row["sample_count"] for row in readings
        ), 5),
        "timer_pair_p95_range_us": [
            round(min(row["empty_timer_pair_p95_us"] for row in readings), 4),
            round(max(row["empty_timer_pair_p95_us"] for row in readings), 4),
        ],
        "timer_step_range_us": [
            round(min(row["clock_step_us"] for row in readings), 4),
            round(max(row["clock_step_us"] for row in readings), 4),
        ],
        "scope": (
            "Instrumented individual static-network matchRequest wall time, "
            "excluding context setup, including two performance.now calls. "
            "Timer overhead, quantization, scheduling and probe effects remain; "
            "not browser request lifetime, actual CPU time, or production website p95."
        ),
    }


BATCHED_MATCHER_KEYS = (
    "schema", "clock_step_us", "contexts_per_pass", "passes",
    "iterations_per_context", "sample_count", "decision_mismatch_count",
    "minimum_batch_us", "median_batch_us", "p95_batch_us", "p99_batch_us",
    "maximum_batch_us", "empty_loop_median_us", "empty_loop_p95_us",
)


def summarize_batched_matcher(series, request_count, request_types=None):
    readings = [row.get("batched_matcher") for row in series]
    if all(reading is None for reading in readings):
        return None
    for reading in readings:
        if (
            not isinstance(reading, dict)
            or reading.get("schema") not in (
                "meblock-batched-context-match-v1",
                "meblock-batched-context-match-v2",
            )
            or set(reading) != set(BATCHED_MATCHER_KEYS) | (
                {"by_request_type"} if reading["schema"].endswith("-v2") else set()
            )
            or type(reading["contexts_per_pass"]) is not int
            or reading["contexts_per_pass"] != request_count
            or type(reading["passes"]) is not int or reading["passes"] != 3
            or type(reading["iterations_per_context"]) is not int
            or reading["iterations_per_context"] < 128
            or reading["iterations_per_context"] > 4096
            or type(reading["sample_count"]) is not int
            or reading["sample_count"] != reading["passes"] * request_count
            or type(reading["decision_mismatch_count"]) is not int
            or reading["decision_mismatch_count"] != 0
        ):
            raise ValueError("Invalid batched matcher sample structure")
        numeric = (
            "clock_step_us", "minimum_batch_us", "median_batch_us",
            "p95_batch_us", "p99_batch_us", "maximum_batch_us",
            "empty_loop_median_us", "empty_loop_p95_us",
        )
        if (
            any(type(reading[key]) not in (int, float)
                or not math.isfinite(reading[key]) or reading[key] < 0
                for key in numeric)
            or not (reading["minimum_batch_us"] <= reading["median_batch_us"]
                    <= reading["p95_batch_us"] <= reading["p99_batch_us"]
                    <= reading["maximum_batch_us"])
            or reading["empty_loop_median_us"] > reading["empty_loop_p95_us"]
        ):
            raise ValueError("Invalid batched matcher calibration or percentile")
    if len({reading["schema"] for reading in readings}) != 1:
        raise ValueError("Mixed batched matcher schemas across profiles")
    expected_counts = (
        Counter(request_types) if request_types is not None else None
    )
    by_type = {}
    if readings[0]["schema"].endswith("-v2"):
        if (expected_counts is None or sum(expected_counts.values()) != request_count
            or any(not isinstance(name, str) or not name
                   for name in expected_counts)):
            raise ValueError("Missing batched matcher type provenance")
        for reading in readings:
            types = reading["by_request_type"]
            if not isinstance(types, dict) or set(types) != set(expected_counts):
                raise ValueError("Missing or unexpected batched matcher request type")
            for name, count in expected_counts.items():
                bucket = types[name]
                if (
                    not isinstance(bucket, dict)
                    or set(bucket) != {
                        "sample_count", "minimum_batch_us", "p95_batch_us",
                        "maximum_batch_us",
                    }
                    or type(bucket["sample_count"]) is not int
                    or bucket["sample_count"] != count * reading["passes"]
                    or any(
                        type(bucket[field]) not in (int, float)
                        or not math.isfinite(bucket[field])
                        or bucket[field] < 0
                        for field in (
                            "minimum_batch_us", "p95_batch_us",
                            "maximum_batch_us",
                        )
                    )
                    or not (reading["minimum_batch_us"] <=
                            bucket["minimum_batch_us"] <= bucket["p95_batch_us"]
                            <= bucket["maximum_batch_us"] <= reading["maximum_batch_us"])
                ):
                    raise ValueError("Invalid batched matcher per-type samples")
        for name, count in sorted(expected_counts.items()):
            estimates = [
                reading["by_request_type"][name]["p95_batch_us"] /
                reading["iterations_per_context"] for reading in readings
            ]
            median = statistics.median(estimates)
            spread = (max(estimates) - min(estimates)) / median if median else math.inf
            calibrated = all(
                reading["clock_step_us"] > 0
                and reading["by_request_type"][name]["minimum_batch_us"] >=
                    reading["clock_step_us"] * 8
                and reading["by_request_type"][name]["p95_batch_us"] >=
                    reading["clock_step_us"] * 16
                and reading["by_request_type"][name]["p95_batch_us"] >
                    reading["empty_loop_p95_us"] * 4
                for reading in readings
            )
            by_type[name] = {
                "contexts_per_pass": count,
                "samples_per_run": count * readings[0]["passes"],
                "calibrated_for_amortized_screening": calibrated,
                "stable_three_profile_null_control": (
                    len(readings) >= 3 and calibrated and spread <= 0.5
                ),
                "relative_run_p95_span": (
                    round(spread, 4) if math.isfinite(spread) else None
                ),
                "median_run_p95_amortized_context_us": (
                    round(median, 4) if calibrated else None
                ),
                "range_run_p95_amortized_context_us": (
                    [round(min(estimates), 4), round(max(estimates), 4)]
                    if calibrated else None
                ),
            }
    suitable = all(
        sample["clock_step_us"] > 0
        and sample["minimum_batch_us"] >= sample["clock_step_us"] * 8
        and sample["p95_batch_us"] >= sample["clock_step_us"] * 16
        and sample["p95_batch_us"] > sample["empty_loop_p95_us"] * 4
        for sample in readings
    )
    means = [
        sample["p95_batch_us"] / sample["iterations_per_context"]
        for sample in readings
    ]
    run_median = statistics.median(means)
    relative_span = (max(means) - min(means)) / run_median if run_median else math.inf
    stable = len(readings) >= 3 and suitable and relative_span <= 0.5
    return {
        "schema": (
            "meblock-batched-context-match-summary-v2"
            if by_type else "meblock-batched-context-match-summary-v1"
        ),
        "by_request_type": by_type if by_type else None,
        "runs": len(readings),
        "contexts_per_pass": request_count,
        "passes_per_run": 3,
        "samples_per_run": readings[0]["sample_count"],
        "iterations_per_context_range": [
            min(s["iterations_per_context"] for s in readings),
            max(s["iterations_per_context"] for s in readings),
        ],
        "calibrated_for_amortized_screening": suitable,
        "stable_three_profile_null_control": stable,
        "relative_run_p95_span": (
            round(relative_span, 4) if math.isfinite(relative_span) else None
        ),
        "median_run_p95_amortized_context_us": (
            round(statistics.median(means), 4) if suitable else None
        ),
        "range_run_p95_amortized_context_us": (
            [round(min(means), 4), round(max(means), 4)] if suitable else None
        ),
        "minimum_batch_to_clock_step_ratio": round(min(
            s["minimum_batch_us"] / s["clock_step_us"]
            if s["clock_step_us"] else 0 for s in readings
        ), 3),
        "scope": (
            "Nearest-rank p95 over per-context AMORTIZED means from repeated "
            "matchRequest calls. Setup excluded; includes loop and timer overhead. "
            "This is not p95 of individual calls, browser requests, CPU time, "
            "or real-site tail latency; jitter inside each batch is hidden."
        ),
    }


ENGINE_BUFFER_KEYS = ("filter_data", "origin_trie", "destination_trie", "pattern_trie")
WASM_BUFFER_KEYS = ENGINE_BUFFER_KEYS[1:]


def summarize_engine_buffers(series):
    readings = [row.get("engine_buffer_footprint") for row in series]
    if all(reading is None for reading in readings):
        return None
    for reading in readings:
        if not isinstance(reading, dict) or set(reading) != {
            "schema", "allocated_bytes", "total_known_allocated_bytes",
            "filter_data_used_bytes", "filter_references_count",
            "compiled_network_filters", "wasm_enabled",
        } or reading["schema"] != "meblock-engine-buffers-v1":
            raise ValueError("Missing or invalid test-only engine buffer measurement")
        buffers = reading["allocated_bytes"]
        wasm = reading["wasm_enabled"]
        if (not isinstance(buffers, dict) or set(buffers) != set(ENGINE_BUFFER_KEYS)
            or not isinstance(wasm, dict) or set(wasm) != set(WASM_BUFFER_KEYS)
            or any(type(v) is not bool for v in wasm.values())
            or any(type(v) is not int or v < 0 for v in buffers.values())
            or type(reading["filter_data_used_bytes"]) is not int
            or reading["filter_data_used_bytes"] < 0
            or reading["filter_data_used_bytes"] > buffers["filter_data"]
            or type(reading["filter_references_count"]) is not int
            or reading["filter_references_count"] < 1
            or type(reading["compiled_network_filters"]) is not int
            or reading["compiled_network_filters"] < 1
            or type(reading["total_known_allocated_bytes"]) is not int
            or reading["total_known_allocated_bytes"] != sum(buffers.values())):
            raise ValueError("Invalid or inconsistent engine buffer counters")
    if len({reading["compiled_network_filters"] for reading in readings}) != 1:
        raise ValueError("Compiled filter count drifted across buffer measurements")
    unstable = any(reading != readings[0] for reading in readings[1:])
    totals = [reading["total_known_allocated_bytes"] for reading in readings]
    return {
        **readings[0],
        "unstable_across_profiles": unstable,
        "observed_known_allocated_range_bytes": [min(totals), max(totals)],
        "measurement_count": len(readings),
    }


def compare(data, dataset, identical_build=False, *, default_lists=False):
    a, b = data["upstream"], data["meblock"]
    behavior = check_behavior(a, b, dataset)
    aggregate_signatures = []
    auxiliary_signatures = []
    documents = sum(row['cpt'] in ('main_frame', 'document') for row in dataset)
    for label in ROLES:
        for row in data[label]:
            if row["dataset_rows"] != len(dataset):
                raise ValueError(f"{label} processed incorrect request count")
            official = parse_report_text(row["official"])
            if len(official) != 1 or official[0]["requests"] != len(dataset):
                raise ValueError(f"{label} official benchmark did not execute all requests")
            if any(official[0]["counts"][k] != row["decisions"][v] for k, v in (
                ("blocked", "blocked"), ("unblocked", "allowed"), ("not_blocked", "passed")
            )):
                raise ValueError(f"{label} official benchmark and behavior counts disagree")
            aggregate_signatures.append(official[0]["counts"])
            modifier_bits = {
                "redirect": 1, "removeparam": 2, "urlskip": 4,
                "csp": 8, "permissions": 16, "replace": 32,
            }
            for name, bit in modifier_bits.items():
                actual = sum(bool(mask & bit) for mask in row["modifier_masks"])
                if actual != official[0]["counts"][name]:
                    raise ValueError(
                        f"{label} {name} per-request modifier counts disagree with upstream benchmark "
                        f"(observed={actual}, expected={official[0]['counts'][name]})"
                    )
            auxiliary = {}
            if documents:
                for kind in ('cosmetic', 'scriptlet'):
                    text = row.get('extra', {}).get(kind, '')
                    match = re.search(r'Evaluated\s+(\d+)\s+retrieval', text)
                    if match is None or int(match.group(1)) != documents:
                        raise ValueError(f'{label} {kind} benchmark did not process all document requests')
                    auxiliary[kind] = int(match.group(1))
            auxiliary_signatures.append(auxiliary)
    behavior["auxiliary_benchmarks_match"] = all(
        signature == auxiliary_signatures[0] for signature in auxiliary_signatures
    )
    behavior["aggregate_modifier_counts_match"] = all(
        signature == aggregate_signatures[0] for signature in aggregate_signatures
    )
    behavior["reference_aggregate_counts"] = aggregate_signatures[0]
    browser_pages = [row.get("browser_behavior") for role in ROLES for row in data[role]]
    expected_page = WITHOUT_ADDON if default_lists else WITH_ADDON
    expected_parameters = (
        PARAMETERS_WITHOUT_ADDON if default_lists else PARAMETERS_WITH_ADDON
    )
    behavior["browser_page_probe_ran"] = all(isinstance(page, dict) for page in browser_pages)
    page_failures = [
        {"run": index + 1, **failure}
        for index, page in enumerate(browser_pages)
        for failure in fixture_page_mismatches(page, expected_page)
    ]
    behavior["browser_page_match"] = not page_failures
    behavior["browser_page_mismatches_first_25"] = page_failures[:25]
    behavior["local_block_positive_control"] = (
        None if default_lists else behavior["browser_page_match"]
    )
    vectors = [
        row.get("fixture_precedence")
        for role in ROLES for row in data[role]
    ]
    behavior["fixture_precedence_match"] = (
        None if all(value is None for value in vectors)
        else not any(
            precedence_mismatches(value, default_lists=default_lists)
            for value in vectors
        )
    )
    url_parameters = [
        row.get("browser_parameters") for role in ROLES for row in data[role]
    ]
    behavior["url_modifier_match"] = all(
        parameters == expected_parameters for parameters in url_parameters
    )
    dynamic_checks = [
        row.get("dynamic_firewall") for role in ROLES for row in data[role]
    ]
    behavior["dynamic_firewall_match"] = all(
        isinstance(check, dict)
        and check.get("cases") == DYNAMIC_FIREWALL_EXPECTED
        and check.get("rollback") is True for check in dynamic_checks
    )
    settings_checks = [
        row.get("state_persistence") for role in ROLES for row in data[role]
    ]
    behavior["settings_reload_and_restore_match"] = all(
        isinstance(check, dict)
        and check.get("setting") == STATE_PERSISTENCE_KEY
        and check.get("changed_and_reloaded") is True
        and check.get("restored") is True
        and type(check.get("originally")) is bool for check in settings_checks
    )
    filters = [row.get("selected_filter_lists") for role in ROLES for row in data[role]]
    behavior["filter_list_selection_match"] = (
        None if all(value is None for value in filters)
        else all(isinstance(value, list) and value == filters[0] for value in filters)
    )
    left, right = summarize_role(a), summarize_role(b)
    request_latency_series = {
        role: summarize_request_latency(data[role], len(dataset)) for role in ROLES
    }
    if ((request_latency_series["upstream"] is None) !=
        (request_latency_series["meblock"] is None)):
        raise ValueError("Request-level latency data is missing from one build")
    request_latency = None
    if request_latency_series["upstream"] is not None:
        request_latency = {
            "upstream": request_latency_series["upstream"],
            "meblock": request_latency_series["meblock"],
            "scope": request_latency_series["upstream"]["scope"],
        }
    batched_series = {
        role: summarize_batched_matcher(
            data[role], len(dataset), [row["cpt"] for row in dataset]
        ) for role in ROLES
    }
    if (batched_series["upstream"] is None) != (batched_series["meblock"] is None):
        raise ValueError("Batched matcher measurements missing from one build")
    batched_matcher = None
    if batched_series["upstream"] is not None:
        batched_matcher = {
            "upstream": batched_series["upstream"],
            "meblock": batched_series["meblock"],
            "scope": batched_series["upstream"]["scope"],
        }
    navigation_series = {
        label: [row.get("fixture_navigation") for row in data[label]]
        for label in ROLES
    }
    if any(samples is not None for values in navigation_series.values() for samples in values):
        navigation_summaries = {
            label: [summarize_fixture_navigation(samples) for samples in values]
            for label, values in navigation_series.items()
        }
        fixture_navigation = {
            label: {
                "runs": len(summaries),
                "samples_per_run": NAVIGATION_SAMPLES,
                "median_run_p95_load_event_end_ms": round(statistics.median(
                    item["p95_load_event_end_ms"] for item in summaries
                ), 3),
                "median_run_p95_allowed_script_resource_duration_ms": round(
                    statistics.median(
                        item["p95_allowed_script_resource_duration_ms"]
                        for item in summaries
                    ), 3
                ),
                "range_run_p95_allowed_script_resource_duration_ms": [
                    min(item["p95_allowed_script_resource_duration_ms"]
                        for item in summaries),
                    max(item["p95_allowed_script_resource_duration_ms"]
                        for item in summaries),
                ],
                "range_run_p95_load_event_end_ms": [
                    min(item["p95_load_event_end_ms"] for item in summaries),
                    max(item["p95_load_event_end_ms"] for item in summaries),
                ],
            }
            for label, summaries in navigation_summaries.items()
        }
        fixture_navigation["scope"] = (
            "synthetic loopback page navigation and one actual no-store script "
            "resource per navigation; browser-wide local request duration, "
            "not production website request p95 or addon-only filtering latency"
        )
    else:
        fixture_navigation = None
    left_buffers = summarize_engine_buffers(a)
    right_buffers = summarize_engine_buffers(b)
    if (left_buffers is None) != (right_buffers is None):
        raise ValueError("Engine buffer measurements missing for one build")
    engine_buffers = None
    if left_buffers is not None:
        if (left_buffers["compiled_network_filters"] !=
            right_buffers["compiled_network_filters"]):
            raise ValueError("Different compiled network filter counts between builds")
        stable = (
            not left_buffers["unstable_across_profiles"]
            and not right_buffers["unstable_across_profiles"]
        )
        engine_buffers = {
            "upstream": left_buffers,
            "meblock": right_buffers,
            "stable_enough_for_byte_delta": stable,
            "known_allocated_delta_bytes": (
                right_buffers["total_known_allocated_bytes"] -
                left_buffers["total_known_allocated_bytes"]
                if stable else None
            ),
            "scope": (
                "selected engine-owned typed/WASM buffers only; not total add-on RAM. "
                "If per-profile buffer capacity drifts, report ranges and withhold a "
                "single byte delta; never infer an optimization from runtime noise."
            ),
        }
    matching = (behavior["per_request_match"] and behavior["modifier_per_request_match"]
                and behavior["aggregate_modifier_counts_match"]
                and behavior["auxiliary_benchmarks_match"] and behavior["browser_page_match"]
                and behavior["fixture_precedence_match"] is not False
                and behavior["url_modifier_match"]
                and behavior["dynamic_firewall_match"]
                and behavior["settings_reload_and_restore_match"]
                and behavior["filter_list_selection_match"] is not False)
    noise = max(left["range_us_per_request"][1] - left["range_us_per_request"][0],
                right["range_us_per_request"][1] - right["range_us_per_request"][0])
    delta = right["median_us_per_request"] - left["median_us_per_request"]
    paired = paired_performance(
        [row["us_per_request"] for row in a],
        [row["us_per_request"] for row in b],
    )
    if not matching:
        verdict = "FAIL: behavioral regression"
    elif identical_build:
        verdict = "CONTROL: byte-identical builds; timing differences are environmental"
    else:
        verdict = timing_verdict(paired)
    return {
        "verdict": verdict,
        "correctness": behavior,
        "builds_identical": identical_build,
        "default_enabled_lists_only": default_lists,
        "injected_filter_rules": 0 if default_lists else len(RULES),
        "paired_performance": paired,
        "request_latency": request_latency,
        "batched_matcher": batched_matcher,
        "fixture_navigation": fixture_navigation,
        "engine_buffer_footprint": engine_buffers,
        "upstream": left,
        "meblock": right,
        "delta_median_us_per_request": round(delta, 5),
        "delta_percent": round(100 * delta / left["median_us_per_request"], 3)
        if left["median_us_per_request"] else None,
        "noise_us": round(noise, 5),
        "limitations": [
            "Direct-match behavior is compared per request, not the complete network pipeline.",
            "Cosmetic/scriptlet tests use upstream's existing aggregate benchmarks.",
            "CPU and RSS process samples are browser-wide diagnostic figures.",
            "Engine buffer footprint counts only selected typed/WASM buffers, not JS objects or total retained add-on RAM.",
            "The buffer probe modifies only disposable instrumented test builds.",
            "Fixture navigation p95 uses 20 synthetic local page loads per session and includes browser overhead; it is not request-level or real-world p95.",
            "Synthetic datasets are not evidence of real-user speedups.",
        ],
    }


def read_rule_derived_manifest(dataset, payload, rows, default_lists):
    sidecar = dataset.with_suffix(dataset.suffix + ".manifest.json")
    if not sidecar.is_file():
        return None
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Request probe manifest is not an object")
    if manifest.get("schema") != "meblock-default-rule-probes-v1":
        return None
    if not default_lists:
        raise ValueError("Rule-derived request probes require --default-lists")
    numbers = (
        "shard_index", "shard_count", "simple_host_rules_in_shard",
        "positive_requests", "near_miss_requests", "request_count",
        "simple_host_rule_unique", "bundled_asset_count",
    )
    if any(type(manifest.get(field)) is not int for field in numbers):
        raise ValueError("Rule-derived coverage contains invalid counters")
    if (
        manifest["shard_count"] < 1
        or not 0 <= manifest["shard_index"] < manifest["shard_count"]
        or manifest["simple_host_rules_in_shard"] < 1
        or manifest["simple_host_rule_unique"] < manifest["simple_host_rules_in_shard"]
        or manifest["bundled_asset_count"] < 1
        or manifest["positive_requests"] != 2 * manifest["simple_host_rules_in_shard"]
        or manifest["near_miss_requests"] != 2 * manifest["simple_host_rules_in_shard"]
        or manifest["request_count"] != len(rows)
        or manifest["request_count"] != 4 * manifest["simple_host_rules_in_shard"]
        or manifest.get("dataset_sha256") != hash_bytes(payload)
    ):
        raise ValueError("Rule-derived request manifest differs from dataset")
    type_counts = manifest.get("request_type_request_counts")
    if type_counts is not None:
        actual = dict(sorted(Counter(row["cpt"] for row in rows).items()))
        if (
            not isinstance(type_counts, dict)
            or type_counts != actual
            or any(type(value) is not int or value < 4 or value % 4
                   for value in type_counts.values())
            or not set(type_counts) <= {
                "script", "image", "xmlhttprequest", "stylesheet"
            }
        ):
            raise ValueError("Invalid default-list request type mix")
    assets = manifest.get("bundled_asset_contents_sha256")
    if not isinstance(assets, str) or re.fullmatch(r"[0-9a-f]{64}", assets) is None:
        raise ValueError("Rule-derived asset digest is invalid")
    family = manifest.get("rule_family")
    family_fields = (
        "family_rule_unique", "eligible_exception_rule_unique",
        "eligible_blocking_rule_unique",
    )
    if family is not None:
        if (
            family not in ("all", "exception", "blocking")
            or any(type(manifest.get(key)) is not int or manifest[key] < 0
                   for key in family_fields)
            or (manifest["eligible_exception_rule_unique"] +
                manifest["eligible_blocking_rule_unique"] !=
                manifest["simple_host_rule_unique"])
            or manifest["family_rule_unique"] != {
                "all": manifest["simple_host_rule_unique"],
                "exception": manifest["eligible_exception_rule_unique"],
                "blocking": manifest["eligible_blocking_rule_unique"],
            }[family]
            or manifest["simple_host_rules_in_shard"] >
                manifest["family_rule_unique"]
        ):
            raise ValueError("Invalid default filter rule-family provenance")
    selected_digest = manifest.get("selected_list_keys_sha256")
    provenance_fields = (
        "selected_locally_mapped_list_count",
        "selected_remote_only_list_count",
        "selected_user_defined_list_count",
        "selected_unresolved_list_count",
    )
    if selected_digest is not None:
        if (not isinstance(selected_digest, str) or
            re.fullmatch(r"[0-9a-f]{64}", selected_digest) is None or
            type(manifest.get("selected_list_count")) is not int or
            manifest["selected_list_count"] < 1 or
            type(manifest.get("selected_without_bundled_asset_count")) is not int or
            not 0 <= manifest["selected_without_bundled_asset_count"] <=
                manifest["selected_list_count"] or
            any(type(manifest.get(name)) is not int or manifest[name] < 0
                for name in provenance_fields) or
            manifest["selected_locally_mapped_list_count"] +
                manifest["selected_without_bundled_asset_count"] !=
                manifest["selected_list_count"] or
            sum(manifest[name] for name in provenance_fields[1:]) !=
                manifest["selected_without_bundled_asset_count"]):
            raise ValueError("Invalid selected default-list provenance")
    allowed = ("schema", *numbers, "dataset_sha256", "bundled_asset_contents_sha256")
    if type_counts is not None:
        allowed += ("request_type_request_counts",)
    badfilter_fields = (
        "simple_host_rule_before_badfilter_unique",
        "excluded_by_bundled_badfilter_rule_count",
    )
    if any(key in manifest for key in badfilter_fields):
        if (
            any(type(manifest.get(key)) is not int or manifest[key] < 0
                for key in badfilter_fields)
            or manifest["simple_host_rule_before_badfilter_unique"] != (
                manifest["simple_host_rule_unique"] +
                manifest["excluded_by_bundled_badfilter_rule_count"]
            )
        ):
            raise ValueError("Invalid badfilter-excluded coverage counters")
        allowed += badfilter_fields
    if family is not None:
        allowed += ("rule_family", *family_fields)
    allowed += (("selected_list_keys_sha256", "selected_list_count",
                 "selected_without_bundled_asset_count", *provenance_fields)
                if selected_digest is not None else ())
    output = {key: manifest[key] for key in allowed}
    if "exception_challenge" in manifest:
        expected = manifest.get("expected_explicit_allow_by_origin")
        if (manifest["exception_challenge"] is not True or
            family != "exception" or selected_digest is None or
            not isinstance(expected, list) or
            len(expected) != 2 * manifest["simple_host_rules_in_shard"] or
            any(type(value) is not bool for value in expected)):
            raise ValueError("Invalid exception-challenge oracle")
        output["exception_challenge"] = True
        output["_expected_allow_by_origin"] = expected
    return output


def summarize_candidate_effects(reference, candidates):
    """Count distinguishable default-rule probes, not proof of rule ownership."""
    if type(candidates) is not int or candidates < 1:
        raise ValueError("Invalid count of rule-derived candidates")
    values = reference.get("outcomes")
    modifiers = reference.get("modifier_masks")
    if (
        not isinstance(values, list) or not isinstance(modifiers, list)
        or len(values) != 4 * candidates or len(modifiers) != len(values)
        or any(type(value) is not int or value not in (0, 1, 2) for value in values)
        or any(type(value) is not int or value < 0 or value > 63
               for value in modifiers)
    ):
        raise ValueError("Malformed per-rule result vectors")
    same_host_frame = external_reader_frame = both = 0
    positive_blocked = near_miss_blocked = 0
    positive_allowed = near_miss_allowed = 0
    for offset in range(0, len(values), 4):
        first_diff = (
            (values[offset], modifiers[offset]) !=
            (values[offset + 1], modifiers[offset + 1])
        )
        third_diff = (
            (values[offset + 2], modifiers[offset + 2]) !=
            (values[offset + 3], modifiers[offset + 3])
        )
        # Generator emits the external reader (third-party) before
        # the same-host document (first-party).
        external_reader_frame += int(first_diff)
        same_host_frame += int(third_diff)
        both += int(first_diff and third_diff)
        positive_blocked += int(values[offset] == 1) + int(values[offset + 2] == 1)
        near_miss_blocked += int(values[offset + 1] == 1) + int(values[offset + 3] == 1)
        positive_allowed += int(values[offset] == 2) + int(values[offset + 2] == 2)
        near_miss_allowed += int(values[offset + 1] == 2) + int(values[offset + 3] == 2)
    return {
        "candidate_rules": candidates,
        "same_host_frame_distinguishable_candidates": same_host_frame,
        "external_reader_frame_distinguishable_candidates": external_reader_frame,
        "both_contexts_distinguishable_candidates": both,
        "at_least_one_context_distinguishable_candidates": (
            same_host_frame + external_reader_frame - both
        ),
        "no_distinguishable_result_in_tested_contexts": (
            candidates - same_host_frame - external_reader_frame + both
        ),
        "positive_requests_blocked": positive_blocked,
        "near_miss_requests_blocked": near_miss_blocked,
        "positive_requests_explicitly_allowed": positive_allowed,
        "near_miss_requests_explicitly_allowed": near_miss_allowed,
        "scope": (
            "Decision+modifier outcome of matched and near-miss URLs. "
            "A distinguishable outcome does not isolate which filter caused it; "
            "non-distinguishable does not prove the rule is inactive."
        ),
    }


def exception_blocking_controls(dataset, coverage):
    """Build only disposable blockers; never publish selected hostnames."""
    if coverage is None or coverage.get("exception_challenge") is not True:
        raise ValueError("Exception challenge requires a pinned exception corpus")
    if len(dataset) != 4 * coverage["simple_host_rules_in_shard"]:
        raise ValueError("Exception challenge corpus length changed")
    rules = []
    for index in range(0, len(dataset), 4):
        external, external_near, same, same_near = dataset[index:index + 4]
        positive = urlsplit(external["url"])
        negative = urlsplit(external_near["url"])
        host, near = positive.hostname, negative.hostname
        if (
            positive.scheme != "https" or negative.scheme != "https"
            or not host or not near or
            re.fullmatch(r"[a-z0-9.-]+", host) is None or
            near != f"mb-exception-control-{index // 4:05d}.test"
            or external["frameUrl"] != "https://reader.test/page"
            or same["frameUrl"] != f"https://{host}/page"
            or same_near["frameUrl"] != f"https://{near}/page"
            or external_near["frameUrl"] != external["frameUrl"]
            or same["url"] != external["url"] or
            same_near["url"] != external_near["url"] or
            len({row["cpt"] for row in (external, external_near, same, same_near)}) != 1
        ):
            raise ValueError(f"Malformed exception challenge row group {index // 4}")
        rules.extend((f"||{host}^", f"||{near}^"))
    if len(rules) != len(set(rules)):
        raise ValueError("Exception challenge contains repeated blocking controls")
    return tuple(rules)


def withhold_target_challenge_blocker(challenge_rules, index):
    """Forensic-only leave-one-target-blocker-out; retain every control."""
    if (type(index) is not int or not 0 <= index < len(challenge_rules) // 2
        or len(challenge_rules) % 2 or
        any(type(rule) is not str or not rule.startswith("||")
            for rule in challenge_rules)):
        raise ValueError("Invalid withheld challenge candidate index")
    return tuple(
        rule for rule_index, rule in enumerate(challenge_rules)
        if rule_index != index * 2
    )


def summarize_exception_challenge(sample, coverage):
    if coverage is None or coverage.get("exception_challenge") is not True:
        raise ValueError("No exception challenge oracle")
    expectations = coverage["_expected_allow_by_origin"]
    outcomes = sample.get("outcomes")
    match_codes = sample.get("challenge_match_source_codes")
    count = coverage["simple_host_rules_in_shard"]
    if (not isinstance(outcomes, list) or len(outcomes) != count * 4 or
        any(type(v) is not int or v not in (0, 1, 2) for v in outcomes)):
        raise ValueError("Incomplete exception challenge decisions")
    if (not isinstance(match_codes, list) or len(match_codes) != count * 4
        or any(type(code) is not int or not 0 <= code <= 15
               for code in match_codes)):
        raise ValueError("Incomplete exception challenge match-source codes")
    expected_total = confirmed = controls_blocked = 0
    candidates_confirmed = 0
    missed = []
    controls_failed = []
    for n in range(count):
        base = n * 4
        observed_for_rule = 0
        for context in range(2):
            yes, no = outcomes[base + context * 2:base + context * 2 + 2]
            if no == 1:
                controls_blocked += 1
            else:
                controls_failed.append(n)
            should_allow = expectations[n * 2 + context]
            if should_allow:
                expected_total += 1
                if yes == 2 and no == 1:
                    confirmed += 1
                    observed_for_rule += 1
                else:
                    missed.append(n)
        candidates_confirmed += int(observed_for_rule > 0)
    return {
        "candidate_rules": count,
        "expected_override_contexts": expected_total,
        "confirmed_override_contexts": confirmed,
        "candidates_with_confirmed_override": candidates_confirmed,
        "unconfirmed_candidate_count": count - candidates_confirmed,
        "blocking_negative_controls_passed": controls_blocked,
        "blocking_negative_controls_total": count * 2,
        "first_25_unconfirmed_indices": sorted(set(missed))[:25],
        "first_25_control_failure_indices": sorted(set(controls_failed))[:25],
        "first_25_unconfirmed_match_codes": [
            {
                "index": index,
                "matching_host_codes": [
                    match_codes[index * 4], match_codes[index * 4 + 2],
                ],
                "control_host_codes": [
                    match_codes[index * 4 + 1], match_codes[index * 4 + 3],
                ],
            }
            for index in sorted(set(missed + controls_failed))[:25]
        ],
        "match_source_code_bits": {
            "exact_host_block_log_shape": 1,
            "important_block": 2,
            "logged_exception": 4,
            "other_logged_blocker": 8,
        },
        "scope": (
            "Pinned public default exception candidates plus test-only per-host blockers. "
            "Match-source bit codes describe normalized engine log shapes, NOT "
            "which source list or injected rule supplied the match. "
            "A positive explicit-allow decision against a blocked near-miss is an "
            "observed override in this context, not unique per-rule attribution. "
            "Unconfirmed outcomes can reflect absent lists, disabled rules, "
            "other precedence, or probe-context mismatch. Never performance data."
        ),
    }


def verify_timing_sensitivity(data, summary, dataset):
    """Validate a deliberate, disposable 5ms/script-batch timing signal.

    This is not an engine performance experiment or a candidate speedup.
    """
    counts = Counter(row["cpt"] for row in dataset)
    if set(counts) != {"script", "image", "stylesheet", "xmlhttprequest"}:
        raise ValueError("Sensitivity control requires a mixed default-list corpus")
    expected = counts["script"] * 3
    for role in ROLES:
        for run in data[role]:
            required = expected if role == "meblock" else 0
            if (type(run.get("timing_sensitivity_injected_batches")) is not int
                    or run["timing_sensitivity_injected_batches"] != required):
                raise ValueError("Incorrect disposable timing sensitivity injection count")
    figures = summary.get("batched_matcher") or {}
    try:
        reference = figures["upstream"]["by_request_type"]["script"][
            "median_run_p95_amortized_context_us"
        ]
        changed = figures["meblock"]["by_request_type"]["script"][
            "median_run_p95_amortized_context_us"
        ]
        both = (
            figures["upstream"]["by_request_type"]["script"][
                "stable_three_profile_null_control"
            ] is True
            and figures["meblock"]["by_request_type"]["script"][
                "stable_three_profile_null_control"
            ] is True
        )
    except (KeyError, TypeError):
        raise ValueError("Missing per-type timing sensitivity evidence") from None
    if (type(reference) not in (float, int) or
        type(changed) not in (float, int) or
        not math.isfinite(reference) or not math.isfinite(changed) or
        reference <= 0 or changed <= 0):
        raise ValueError("Invalid timing sensitivity estimates")
    ratio = changed / reference
    return {
        "schema": "meblock-timing-sensitivity-control-v1",
        "injected_delay_per_script_batch_us": 5000,
        "injected_script_batches_per_experimental_run": expected,
        "observed_script_amortized_ratio": round(ratio, 4),
        "visible_strong_signal": both and ratio >= 1.20,
        "scope": (
            "Disposable test-probe positive control only: deliberate 5ms "
            "timing delay for each script batch in the experimental role, "
            "outside matchRequest but inside the batch timer. Does not "
            "demonstrate an actual engine slowdown, detectability of "
            "small changes, or any candidate speedup."
        ),
    }


def verify_pinned_selection(data, derived_coverage):
    if derived_coverage is None or "selected_list_keys_sha256" not in derived_coverage:
        return
    for role in ROLES:
        for sample in data[role]:
            selected = sample.get("selected_filter_lists")
            if (not isinstance(selected, list) or not selected
                or any(not isinstance(key, str) or not key for key in selected)
                or len(set(selected)) != len(selected) or
                hashlib.sha256("\n".join(sorted(selected)).encode("utf-8")
                               ).hexdigest() !=
                derived_coverage["selected_list_keys_sha256"]):
                raise ValueError("Live enabled list selection differs from pinned rule probes")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", type=Path, default=BUILD_ROOT / "meblock-requests.json")
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--timeout", type=float, default=120)
    p.add_argument("--idle-sample", type=float, default=0)
    p.add_argument("--upstream-ref", default="upstream/master")
    p.add_argument("--meblock-ref", default="HEAD")
    p.add_argument("--firefox", type=Path, default=Path(r"C:\Program Files\Mozilla Firefox\firefox.exe"))
    p.add_argument("--build-only", action="store_true")
    p.add_argument(
        "--timing-sensitivity-control", action="store_true",
        help="Disposable +5ms/script-batch positive control in one role; "
             "NEVER a default baseline or optimization measurement",
    )
    p.add_argument(
        "--challenge-withhold-target-blocker-index", type=int, default=None,
        help="Forensic correctness-only challenge: omit one synthetic target blocker "
             "by numeric candidate index; never valid for default-list benchmarking",
    )
    p.add_argument(
        "--exception-challenge", action="store_true",
        help="Correctness-only selected default exceptions versus temporary blockers; not a performance baseline",
    )
    p.add_argument(
        "--default-lists", action="store_true",
        help="Benchmark installed default lists only; do not inject test filter rules",
    )
    p.add_argument("--output-root", type=Path, default=BUILD_ROOT / "meblock-comparison")
    args = p.parse_args()
    if args.runs < 1 or args.timeout < 5 or args.idle_sample < 0:
        p.error("Invalid runs, timeout, or sample duration")
    if args.default_lists and args.exception_challenge:
        p.error("Exception challenge must be isolated from --default-lists baseline")
    if args.timing_sensitivity_control and (
        not args.default_lists or args.runs < 3 or args.build_only
        or args.exception_challenge or
        args.challenge_withhold_target_blocker_index is not None
    ):
        p.error("Timing sensitivity control requires >=3 real default-list-only pairs")
    if (args.challenge_withhold_target_blocker_index is not None and
        not args.exception_challenge):
        p.error("Withheld target blocker requires correctness-only --exception-challenge")
    if not args.firefox.is_file() and not args.build_only:
        p.error("Firefox binary not found")
    dirty_paths = git("status", "--porcelain")
    raw = args.dataset.read_bytes()
    dataset = valid_rows(json.loads(raw))
    derived_coverage = read_rule_derived_manifest(
        args.dataset, raw, dataset, args.default_lists or args.exception_challenge
    )
    challenge_rules = (
        exception_blocking_controls(dataset, derived_coverage)
        if args.exception_challenge else ()
    )
    if args.challenge_withhold_target_blocker_index is not None:
        try:
            challenge_rules = withhold_target_challenge_blocker(
                challenge_rules, args.challenge_withhold_target_blocker_index
            )
        except ValueError as error:
            p.error(str(error))
    if (not args.exception_challenge and derived_coverage is not None and
        derived_coverage.get("exception_challenge")):
        p.error("Exception challenge corpus requires --exception-challenge")
    hashes = {role: git("rev-parse", ref) for role, ref in (
        ("upstream", args.upstream_ref), ("meblock", args.meblock_ref)
    )}
    assets = BUILD_ROOT / "uAssets"
    for part in ("main", "prod"):
        if not (assets / part / ".git").exists():
            p.error(f"Missing uAssets checkout at {assets / part}")
    assets_hashes = {
        name: subprocess.check_output(
            ["git", "-C", str(assets / name), "rev-parse", "HEAD"], text=True
        ).strip()
        for name in ("main", "prod")
    }
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    session_dir = args.output_root / f"{timestamp}-{secrets.token_hex(3)}"
    session_dir.mkdir(parents=True, exist_ok=False)
    metadata = {
        "status": "incomplete", "session": str(session_dir),
        "refs": hashes, "assets": assets_hashes,
        "dataset_sha256": hash_bytes(raw), "dataset_count": len(dataset),
        "rule_derived_probe_coverage": (
            {k: v for k, v in derived_coverage.items() if not k.startswith("_")}
            if derived_coverage is not None else None
        ),
        "fixture_version": FIXTURE_VERSION,
        "harness_files_sha256": {
            name: hash_file(Path(__file__).with_name(name))
            for name in (
                "compare_original.py", "browser_probe.js", "engine_buffer_probe.js",
                "state_probe.js", "browser_fixtures.py", "comparison_stats.py",
                "benchmark_firefox.py", "filtering_benchmark.py",
            )
        },
        "runs_per_build": args.runs, "steps": [], "test_only_probe": True,
        "default_enabled_lists_only": args.default_lists,
        "exception_challenge_correctness_only": args.exception_challenge,
        "timing_sensitivity_control_only": args.timing_sensitivity_control,
        "challenge_withheld_target_blocker_index": (
            args.challenge_withhold_target_blocker_index
        ),
        "injected_filter_rules": (
            len(challenge_rules) if args.exception_challenge else
            0 if args.default_lists else len(RULES)
        ),
        "working_tree_dirty": bool(dirty_paths),
        "note": "Both source snapshots are exported from resolved commits, not the working tree.",
    }
    report_path = session_dir / "report.json"
    state = State(raw, secrets.token_hex(24))
    server = Collector(state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}"
        builds = {}
        snapshots = {}
        packages = {}
        for role in ROLES:
            checkout = session_dir / role / "checkout"
            export_git(hashes[role], checkout)
            built = run_build(checkout, assets)
            if (derived_coverage is not None and bundled_asset_fingerprint(built) !=
                derived_coverage["bundled_asset_contents_sha256"]):
                raise ValueError("Built default filter assets differ from rule-probe source")
            snapshots[role] = source_files(built)
            xpi = session_dir / role / "instrumented.xpi"
            packages[role] = probe_package(
                built, xpi, role, endpoint, state.token, raw,
                include_fixture_rules=not (args.default_lists or args.exception_challenge),
                challenge_rules=challenge_rules,
                timing_sensitivity_control=args.timing_sensitivity_control,
            )
            builds[role] = xpi
            metadata["steps"].append(f"{role} built and test probe injected")
        metadata["build_delta"] = source_delta(snapshots["upstream"], snapshots["meblock"])
        metadata["packages"] = packages
        if args.build_only:
            metadata["status"] = "build_only"
            return 0
        data = {role: [] for role in ROLES}
        driver_path = geckodriver_path(None)
        metadata["unfiltered_fixture_control"] = test_without_addon(
            endpoint, driver_path, args.firefox
        )
        metadata["steps"].append("Firefox without-addon negative control passed")
        for iteration in range(args.runs):
            for role in (ROLES if iteration % 2 == 0 else tuple(reversed(ROLES))):
                print(f"[{iteration + 1}/{args.runs}] {role} Firefox run", flush=True)
                result = firefox_run(
                    builds[role], role, state, driver_path,
                    args.firefox, args.timeout, args.idle_sample, endpoint,
                    packages[role]["addon_id"],
                    default_lists=args.default_lists or args.exception_challenge,
                    expect_challenge_diagnostics=args.exception_challenge,
                )
                data[role].append(result)
                print(json.dumps({
                    "role": role, "us_per_request": result["us_per_request"],
                    "blocked": result["decisions"]["blocked"],
                }), flush=True)
                (session_dir / f"{role}-run-{iteration + 1}.json").write_text(
                    json.dumps(result, indent=2) + "\n", encoding="utf-8"
                )
        verify_pinned_selection(data, derived_coverage)
        metadata["comparison"] = compare(
            data, dataset, identical_build=metadata["build_delta"]["files_different"] == 0,
            default_lists=args.default_lists or args.exception_challenge,
        )
        if args.timing_sensitivity_control:
            metadata["comparison"]["timing_sensitivity"] = (
                verify_timing_sensitivity(
                    data, metadata["comparison"], dataset,
                )
            )
            if metadata["comparison"]["verdict"].startswith("FAIL"):
                pass
            elif metadata["comparison"]["timing_sensitivity"]["visible_strong_signal"]:
                metadata["comparison"]["verdict"] = (
                    "SENSITIVITY-ONLY: strong artificial script-batch delay "
                    "detected, never an engine speedup or baseline"
                )
            else:
                metadata["comparison"]["verdict"] = (
                    "FAIL: artificial script-batch delay not reliably detected"
                )
        if args.exception_challenge:
            metadata["comparison"]["default_enabled_lists_only"] = False
            metadata["comparison"]["injected_filter_rules"] = len(challenge_rules)
            checks = {
                role: [
                    summarize_exception_challenge(result, derived_coverage)
                    for result in data[role]
                ] for role in ROLES
            }
            metadata["comparison"]["exception_challenge"] = checks
            metadata["comparison"]["exception_challenge_all_controls_passed"] = all(
                item["blocking_negative_controls_passed"] ==
                item["blocking_negative_controls_total"]
                for side in checks.values() for item in side
            )
            metadata["comparison"]["exception_challenge_scope"] = (
                "Correctness diagnostic only: challenge blockers were injected into "
                "temporary builds; this is NOT the unmodified default-list baseline. "
                "Withholding a target blocker is a forensic differential, not "
                "additional proof of exception correctness. "
                "A non-blocked negative control invalidates only that candidate's "
                "override attribution, never silently counts it as confirmed."
            )
            if not metadata["comparison"]["verdict"].startswith("FAIL"):
                metadata["comparison"]["verdict"] = (
                    "CORRECTNESS-ONLY: selected default exceptions challenged with "
                    "disposable blocker controls; timing is not an optimization signal"
                )
        if derived_coverage is not None:
            metadata["comparison"]["rule_derived_observed_effects"] = (
                summarize_candidate_effects(
                    data["upstream"][0],
                    derived_coverage["simple_host_rules_in_shard"],
                )
            )
        metadata["status"] = (
            "failed" if metadata["comparison"]["verdict"].startswith("FAIL")
            else "sensitivity_only" if args.timing_sensitivity_control
            else "complete"
        )
        return 1 if metadata["status"] == "failed" else 0
    except Exception as error:
        metadata["status"] = "failed"
        metadata["error"] = f"{type(error).__name__}: {error}"
        print(metadata["error"], file=sys.stderr)
        return 1
    finally:
        server.shutdown()
        server.server_close()
        report_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        print(f"Evidence: {report_path}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
