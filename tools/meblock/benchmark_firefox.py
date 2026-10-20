#!/usr/bin/env python3
"""Measure Firefox startup and idle process use with and without a temporary addon.

The script never opens an existing Firefox profile. Selenium creates and removes
a separate temporary profile for every run.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import time
from zipfile import ZipFile

import psutil
import selenium
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service


def git_revision(path: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def geckodriver_path(explicit: str | None) -> str | None:
    if explicit:
        return str(Path(explicit).resolve())
    found = shutil.which("geckodriver")
    if found:
        return found
    cache = Path.home() / ".cache" / "selenium" / "geckodriver"
    candidates = sorted(cache.rglob("geckodriver.exe")) if cache.exists() else []
    return str(candidates[-1]) if candidates else None


def firefox_process_snapshot(gecko_pid: int) -> dict:
    """Collect individual Firefox process readings without touching other profiles."""
    parent = psutil.Process(gecko_pid)
    descendants = parent.children(recursive=True)
    readings = {}
    for process in descendants:
        try:
            if not process.name().lower().startswith("firefox"):
                continue
            cpu = process.cpu_times()
            readings[process.pid] = {
                "rss_bytes": process.memory_info().rss,
                "cpu_seconds": cpu.user + cpu.system,
                "create_time": process.create_time(),
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return readings


def collect_idle(gecko_pid: int, duration: float) -> dict:
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("CPU sample duration must be positive and finite")
    readings = []
    started = time.perf_counter()
    while True:
        snap = firefox_process_snapshot(gecko_pid)
        readings.append((time.perf_counter(), snap))
        if readings[-1][0] - started >= duration:
            break
        time.sleep(min(0.5, max(0.02, duration / 10)))

    if not readings or any(not snapshot for _, snapshot in readings):
        raise RuntimeError("Could not observe Firefox descendants of the owned geckodriver")

    cpu_seconds = 0.0
    churn = False
    for (_, previous), (_, current) in zip(readings, readings[1:]):
        if set(previous) != set(current):
            churn = True
        for pid in set(previous) & set(current):
            if previous[pid]["create_time"] != current[pid]["create_time"]:
                churn = True
                continue
            cpu_seconds += max(
                0.0, current[pid]["cpu_seconds"] - previous[pid]["cpu_seconds"]
            )
    elapsed = readings[-1][0] - readings[0][0]
    rss = [sum(p["rss_bytes"] for p in snap.values()) for _, snap in readings]
    process_counts = [len(snap) for _, snap in readings]
    return {
        "measurement_seconds": round(elapsed, 3),
        "cpu_percent_one_core_equivalent": round(100 * cpu_seconds / elapsed, 3),
        "rss_mb_median_sum": round(statistics.median(rss) / 1_000_000, 3),
        "rss_mb_peak_sum": round(max(rss) / 1_000_000, 3),
        "firefox_process_count_peak": max(process_counts),
        "cpu_seconds_total": round(cpu_seconds, 5),
        "sample_count": len(readings),
        "process_pid_churn_observed": churn,
    }


def run_once(binary: str, driver_path: str | None, addon: Path | None,
             expected_id: str, settle: float, sample: float) -> dict:
    options = Options()
    options.binary_location = binary
    options.add_argument("-headless")
    options.set_preference("browser.startup.homepage", "about:blank")
    options.set_preference("browser.startup.page", 0)
    options.set_preference("browser.shell.checkDefaultBrowser", False)
    options.set_preference("app.update.auto", False)
    options.set_preference("extensions.update.enabled", False)
    options.set_preference("toolkit.telemetry.enabled", False)
    options.set_preference("datareporting.healthreport.uploadEnabled", False)

    service = Service(executable_path=driver_path) if driver_path else Service()
    start = time.perf_counter()
    browser = webdriver.Firefox(options=options, service=service)
    try:
        browser.get("about:blank")
        if browser.execute_script("return document.readyState") != "complete":
            raise RuntimeError("about:blank did not load")
        startup_ms = round((time.perf_counter() - start) * 1000, 2)
        result = {
            "condition": "with_addon" if addon else "without_addon",
            "browser_startup_to_blank_ms": startup_ms,
            "firefox_version": browser.capabilities.get("browserVersion"),
            "extension_install_ms": None,
            "installed_addon_id": None,
        }
        if addon:
            began = time.perf_counter()
            addon_id = browser.install_addon(str(addon), temporary=True)
            result["extension_install_ms"] = round(
                (time.perf_counter() - began) * 1000, 2
            )
            result["installed_addon_id"] = addon_id
            if addon_id != expected_id:
                raise RuntimeError(
                    f"Firefox installed unexpected extension ID: {addon_id!r}"
                )
        time.sleep(settle)
        result.update(collect_idle(browser.service.process.pid, sample))
        return result
    finally:
        browser.quit()


def summarize(results: list[dict]) -> dict:
    fields = (
        "browser_startup_to_blank_ms",
        "extension_install_ms",
        "cpu_percent_one_core_equivalent",
        "rss_mb_median_sum",
        "rss_mb_peak_sum",
    )
    summary = {}
    for condition in ("without_addon", "with_addon"):
        rows = [row for row in results if row["condition"] == condition]
        measures = {}
        for field in fields:
            values = [row[field] for row in rows if row[field] is not None]
            if values:
                measures[field] = {
                    "median": round(statistics.median(values), 3),
                    "min": min(values),
                    "max": max(values),
                    "runs": len(values),
                }
        summary[condition] = measures
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--addon", type=Path, required=True)
    parser.add_argument("--firefox", default=r"C:\Program Files\Mozilla Firefox\firefox.exe")
    parser.add_argument("--geckodriver")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--settle", type=float, default=3)
    parser.add_argument("--sample", type=float, default=5)
    parser.add_argument("--output", type=Path, default=Path("dist/build/meblock-baseline.json"))
    args = parser.parse_args()
    if args.runs < 1 or args.sample < 1 or args.settle < 0:
        parser.error("runs >= 1, sample >= 1 and settle >= 0 are required")
    if not args.addon.is_file():
        parser.error(f"Addon archive not found: {args.addon}")
    if not Path(args.firefox).is_file():
        parser.error(f"Firefox binary not found: {args.firefox}")
    with ZipFile(args.addon) as z:
        if z.testzip() is not None:
            parser.error("XPI archive CRC validation failed")
        manifest = json.loads(z.read("manifest.json"))
    expected_id = manifest["browser_specific_settings"]["gecko"]["id"]
    addon_sha = hashlib.sha256(args.addon.read_bytes()).hexdigest()
    repository = Path(__file__).resolve().parents[2]
    result = {
        "schema_version": 1,
        "status": "running",
        "baseline_type": "cold_headless_firefox_isolated_profiles",
        "baseline_revision": git_revision(repository),
        "assets_main_revision": git_revision(repository / "dist/build/uAssets/main"),
        "assets_prod_revision": git_revision(repository / "dist/build/uAssets/prod"),
        "firefox_binary": args.firefox,
        "geckodriver": geckodriver_path(args.geckodriver),
        "python_version": platform.python_version(),
        "selenium_version": selenium.__version__,
        "os": platform.platform(),
        "logical_cpu_count": os.cpu_count(),
        "addon_sha256": addon_sha,
        "addon_version": manifest.get("version"),
        "expected_addon_id": expected_id,
        "runs_per_condition": args.runs,
        "settle_seconds": args.settle,
        "sample_seconds": args.sample,
        "results": [],
        "measurement_notes": [
            "Fresh headless Selenium profile per run; does not use existing Firefox profiles.",
            "Startup time covers WebDriver Firefox launch and about:blank ready state, NOT startup with a persisted addon.",
            "Addon install time covers temporary addon install API; it is NOT Firefox startup overhead.",
            "RSS is the sum of Firefox process resident-set sizes, which can double-count shared physical pages.",
            "CPU percent is process-tree CPU time relative to wall time, where 100 percent is one fully busy logical core.",
            "Idle page is about:blank and automatic background services may vary between runs.",
            "Results compare the same upstream code loaded versus not loaded, NOT a meBlock speedup."
        ],
    }
    try:
        driver_path = result["geckodriver"]
        for i in range(args.runs):
            for label, addon in (("without_addon", None), ("with_addon", args.addon)):
                print(f"Run {i + 1}/{args.runs} {label}", flush=True)
                row = run_once(
                    args.firefox, driver_path, addon.resolve() if addon else None,
                    expected_id, args.settle, args.sample,
                )
                row["iteration"] = i + 1
                result["results"].append(row)
                print(json.dumps(row, sort_keys=True), flush=True)
        result["status"] = "completed"
        result["summary"] = summarize(result["results"])
    except Exception as error:
        result["status"] = "failed"
        result["error"] = f"{type(error).__name__}: {error}"
        print(result["error"], file=sys.stderr, flush=True)
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Benchmark evidence saved to {args.output}", flush=True)
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
