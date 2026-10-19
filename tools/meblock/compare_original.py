#!/usr/bin/env python3
"""Compare pinned upstream and meBlock builds in real, isolated Firefox sessions.

Build artifacts, test instrumentation, captured results, and the temporary
filter corpus remain under dist/build/; the repository source is unchanged.
"""

import argparse
import hashlib
import io
import json
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
from browser_fixtures import (
    FIXTURE_VERSION, OBSERVATION_SCRIPT, ROUTES, RULES, WITH_ADDON, WITHOUT_ADDON,
    PARAMETERS_WITH_ADDON, PARAMETERS_WITHOUT_ADDON,
    DYNAMIC_FIREWALL_EXPECTED, STATE_PERSISTENCE_KEY,
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


def probe_package(build, destination, role, endpoint, token, dataset_bytes):
    network_engine = build / "js" / "static-net-filtering.js"
    original_engine = network_engine.read_text(encoding="utf-8")
    if "meblockEngineBufferFootprint" in original_engine:
        raise RuntimeError("Test-only engine buffer probe already injected")
    config = {"role": role, "endpoint": endpoint, "token": token}
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
    with core_filters.open("a", encoding="utf-8") as stream:
        stream.write("\n" + "\n".join(RULES) + "\n")
    text = PROBE_PATH.read_text(encoding="utf-8")
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


def observe_page(browser, endpoint, expected):
    observed = None
    for attempt in range(3):
        browser.get(endpoint + f"/fixture?attempt={attempt}")
        for _ in range(16):
            observed = browser.execute_script(OBSERVATION_SCRIPT)
            if observed == expected:
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
        if observed != WITHOUT_ADDON:
            raise RuntimeError(
                f"Unfiltered fixture control did not load all resources: {observed}"
            )
        parameters = observe_parameters(browser, endpoint)
        if parameters != PARAMETERS_WITHOUT_ADDON:
            raise RuntimeError(f"Unfiltered parameter control failed: {parameters}")
        return {"page": observed, "parameters": parameters}
    finally:
        browser.quit()


def firefox_run(xpi, role, state, driver_binary, firefox_binary,
                timeout, idle_sample, endpoint, expected_addon_id):
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
        result["addon_id_verified"] = True
        result["firefox_version"] = browser.capabilities.get("browserVersion")
        result.pop("token", None)
        observed = observe_page(browser, endpoint, WITH_ADDON)
        result["browser_behavior"] = observed
        if observed != WITH_ADDON:
            differing = {
                name: {"expected": expected, "actual": observed.get(name)}
                for name, expected in WITH_ADDON.items()
                if observed.get(name) != expected
            }
            raise RuntimeError(f"Firefox behavior regression: {differing}")
        result["browser_parameters"] = observe_parameters(browser, endpoint)
        if result["browser_parameters"] != PARAMETERS_WITH_ADDON:
            raise RuntimeError(
                f"Firefox URL-modification regression: {result['browser_parameters']}"
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
    if any(reading != readings[0] for reading in readings[1:]):
        raise ValueError("Engine buffer footprint drifted between matching test runs")
    return readings[0]


def compare(data, dataset, identical_build=False):
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
    expected_page = WITH_ADDON
    behavior["browser_page_probe_ran"] = all(isinstance(page, dict) for page in browser_pages)
    behavior["browser_page_match"] = all(page == expected_page for page in browser_pages)
    behavior["local_block_positive_control"] = behavior["browser_page_match"]
    url_parameters = [
        row.get("browser_parameters") for role in ROLES for row in data[role]
    ]
    behavior["url_modifier_match"] = all(
        parameters == PARAMETERS_WITH_ADDON for parameters in url_parameters
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
    left_buffers = summarize_engine_buffers(a)
    right_buffers = summarize_engine_buffers(b)
    if (left_buffers is None) != (right_buffers is None):
        raise ValueError("Engine buffer measurements missing for one build")
    engine_buffers = None
    if left_buffers is not None:
        if (left_buffers["compiled_network_filters"] !=
            right_buffers["compiled_network_filters"]):
            raise ValueError("Different compiled network filter counts between builds")
        engine_buffers = {
            "upstream": left_buffers,
            "meblock": right_buffers,
            "known_allocated_delta_bytes": (
                right_buffers["total_known_allocated_bytes"] -
                left_buffers["total_known_allocated_bytes"]
            ),
            "scope": "selected engine-owned typed/WASM buffers only; not total add-on RAM",
        }
    matching = (behavior["per_request_match"] and behavior["modifier_per_request_match"]
                and behavior["aggregate_modifier_counts_match"]
                and behavior["auxiliary_benchmarks_match"] and behavior["browser_page_match"]
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
        "paired_performance": paired,
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
            "Synthetic datasets are not evidence of real-user speedups.",
        ],
    }


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
    p.add_argument("--output-root", type=Path, default=BUILD_ROOT / "meblock-comparison")
    args = p.parse_args()
    if args.runs < 1 or args.timeout < 5 or args.idle_sample < 0:
        p.error("Invalid runs, timeout, or sample duration")
    if not args.firefox.is_file() and not args.build_only:
        p.error("Firefox binary not found")
    dirty_paths = git("status", "--porcelain")
    raw = args.dataset.read_bytes()
    dataset = valid_rows(json.loads(raw))
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
            snapshots[role] = source_files(built)
            xpi = session_dir / role / "instrumented.xpi"
            packages[role] = probe_package(built, xpi, role, endpoint, state.token, raw)
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
                )
                data[role].append(result)
                print(json.dumps({
                    "role": role, "us_per_request": result["us_per_request"],
                    "blocked": result["decisions"]["blocked"],
                }), flush=True)
                (session_dir / f"{role}-run-{iteration + 1}.json").write_text(
                    json.dumps(result, indent=2) + "\n", encoding="utf-8"
                )
        metadata["comparison"] = compare(
            data, dataset, identical_build=metadata["build_delta"]["files_different"] == 0
        )
        metadata["status"] = ("failed" if metadata["comparison"]["verdict"].startswith("FAIL")
                              else "complete")
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
