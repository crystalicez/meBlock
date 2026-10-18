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
import secrets
import shutil
import statistics
import subprocess
import sys
import tarfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from benchmark_firefox import collect_idle, geckodriver_path
from filtering_benchmark import parse_report_text, valid_rows

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service


ROOT = Path(__file__).resolve().parents[2]
BUILD_ROOT = ROOT / "dist" / "build"
PROBE_PATH = Path(__file__).with_name("browser_probe.js")
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
    config = {"role": role, "endpoint": endpoint, "token": token}
    (build / "assets" / "meblock-requests.json").write_bytes(dataset_bytes)
    text = PROBE_PATH.read_text(encoding="utf-8")
    if "__MEBLOCK_CONFIG__" not in text:
        raise RuntimeError("Probe configuration placeholder not found")
    (build / "js" / "meblock-probe.js").write_text(
        text.replace("__MEBLOCK_CONFIG__", json.dumps(config)), encoding="utf-8"
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
    return {"bytes": destination.stat().st_size, "sha256": hash_file(destination)}


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

    def respond(self, status, body=b""):
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.respond(204)

    def do_GET(self):
        if urlsplit(self.path).path != "/dataset":
            return self.respond(404)
        self.respond(200, self.server.state.dataset)

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


def firefox_run(xpi, role, state, driver_binary, firefox_binary,
                timeout, idle_sample):
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
        if reported_id != "uBlock0@raymondhill.net":
            raise RuntimeError(f"Unexpected upstream test addon ID {reported_id!r}")
        if result.get("status") != "ok":
            raise RuntimeError(result.get("message", "Firefox benchmark failed") + "\n" + result.get("stack", ""))
        if result.get("engine_ready") is not True:
            raise RuntimeError("Benchmark ran without filtering readiness")
        result["addon_id_verified"] = True
        result.pop("token", None)
        if idle_sample:
            result["idle_process_sample"] = collect_idle(
                browser.service.process.pid, idle_sample
            )
        return result
    finally:
        browser.quit()


def check_behavior(upstream, meblock, dataset):
    baseline = upstream[0]["outcomes"]
    problems = []
    for label, series in (("upstream", upstream), ("meblock", meblock)):
        for index, record in enumerate(series):
            vector = record.get("outcomes")
            if not isinstance(vector, list) or len(vector) != len(dataset):
                raise ValueError(f"{label} run {index} lacks complete decision vector")
            for offset, (a, b) in enumerate(zip(baseline, vector)):
                if a != b:
                    if len(problems) < 25:
                        problems.append({
                            "role": label, "run": index + 1, "request_index": offset,
                            "reference": a, "observed": b, "request": dataset[offset],
                        })
    return {
        "per_request_match": not problems,
        "mismatches_first_25": problems,
        "checked_requests_per_run": len(baseline),
    }


def summarize_role(series):
    values = [row["us_per_request"] for row in series]
    return {
        "runs": len(values),
        "median_us_per_request": round(statistics.median(values), 5),
        "range_us_per_request": [round(min(values), 5), round(max(values), 5)],
        "individual_us_per_request": [round(v, 5) for v in values],
    }


def compare(data, dataset):
    a, b = data["upstream"], data["meblock"]
    behavior = check_behavior(a, b, dataset)
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
    left, right = summarize_role(a), summarize_role(b)
    matching = behavior["per_request_match"]
    noise = max(left["range_us_per_request"][1] - left["range_us_per_request"][0],
                right["range_us_per_request"][1] - right["range_us_per_request"][0])
    delta = right["median_us_per_request"] - left["median_us_per_request"]
    if not matching:
        verdict = "FAIL: behavioral regression"
    elif len(a) < 5 or len(b) < 5:
        verdict = "INCONCLUSIVE: fewer than five runs per build"
    elif abs(delta) <= noise:
        verdict = "INCONCLUSIVE: difference within run-to-run spread"
    else:
        verdict = "CANDIDATE: repeat with realistic requests and profiler"
    return {
        "verdict": verdict,
        "correctness": behavior,
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
        for iteration in range(args.runs):
            for role in (ROLES if iteration % 2 == 0 else tuple(reversed(ROLES))):
                print(f"[{iteration + 1}/{args.runs}] {role} Firefox run", flush=True)
                result = firefox_run(
                    builds[role], role, state, driver_path,
                    args.firefox, args.timeout, args.idle_sample,
                )
                data[role].append(result)
                print(json.dumps({
                    "role": role, "us_per_request": result["us_per_request"],
                    "blocked": result["decisions"]["blocked"],
                }), flush=True)
                (session_dir / f"{role}-run-{iteration + 1}.json").write_text(
                    json.dumps(result, indent=2) + "\n", encoding="utf-8"
                )
        metadata["comparison"] = compare(data, dataset)
        metadata["status"] = "failed" if not metadata["comparison"]["correctness"]["per_request_match"] else "complete"
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
