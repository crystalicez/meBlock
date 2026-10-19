#!/usr/bin/env python3
"""Capture anonymized Firefox about:memory using an isolated test profile.

Only disposable test Firefox sessions. Raw reports must never leave ignored
dist/build/private-memory or be uploaded to a public GitHub Actions artifact.
"""

import argparse
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import time
from zipfile import ZIP_DEFLATED, ZipFile

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service

from benchmark_firefox import geckodriver_path
from analyze_memory import MAX_DECOMPRESSED_BYTES, subtree_summary

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_DIR = ROOT / "dist" / "build" / "private-memory"
BUILT_IN_LIST = "assets/ublock/filters.min.txt"

DUMP_SCRIPT = """
const target = arguments[0];
const resolve = arguments[arguments.length - 1];
try {
    const dumper = Cc["@mozilla.org/memory-info-dumper;1"]
        .getService(Ci.nsIMemoryInfoDumper);
    dumper.dumpMemoryReportsToNamedFile(
        target, () => resolve("complete"), null,
        true, false
    );
} catch (error) {
    resolve("error: " + String(error));
}
"""


def synthetic_xpi(source, target, count):
    """Add public deterministic rules only to a disposable test XPI."""
    if count < 1:
        raise ValueError("Synthetic hostname-rule count must be positive")
    with ZipFile(source) as original, ZipFile(
        target, "w", compression=ZIP_DEFLATED, compresslevel=6
    ) as clone:
        names = set(original.namelist())
        if BUILT_IN_LIST not in names:
            raise ValueError("Source XPI lacks bundled uBO filters")
        for name in original.namelist():
            data = original.read(name)
            if name == BUILT_IN_LIST:
                additions = "".join(
                    f"||mb-memory-{i:07d}.example.invalid^\n"
                    for i in range(count)
                ).encode("ascii")
                data = data + b"\n" + additions
            clone.writestr(name, data)


def extension_process_explicit_mib(reports):
    processes = {
        row.get("process")
        for row in reports
        if isinstance(row, dict)
        and isinstance(row.get("process"), str)
        and "extension" in row["process"].casefold()
    }
    if len(processes) != 1:
        return None
    try:
        scope = subtree_summary(reports, "explicit", process_contains=processes.pop())
    except ValueError:
        return None
    return scope["mib"] if scope["unavailable_byte_reporters"] == 0 else None


def capture(addon, firefox, settle_seconds, extra_rules=0, samples=1):
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    if not addon.is_file():
        raise FileNotFoundError("Test-only addon XPI not found")
    if extra_rules:
        disposable = PRIVATE_DIR / "synthetic-meblock.xpi"
        synthetic_xpi(addon, disposable, extra_rules)
        addon = disposable

    options = Options()
    options.binary_location = str(firefox)
    options.add_argument("-headless")
    options.set_preference("browser.startup.homepage", "about:blank")
    options.set_preference("browser.startup.page", 0)
    options.set_preference("app.update.auto", False)
    options.set_preference("extensions.update.enabled", False)
    options.set_preference("toolkit.telemetry.enabled", False)
    options.set_preference("datareporting.healthreport.uploadEnabled", False)
    options.set_preference("browser.shell.checkDefaultBrowser", False)

    driver = geckodriver_path(None)
    browser = webdriver.Firefox(
        options=options,
        service=Service(
            executable_path=driver,
            service_args=["--allow-system-access"],
        ),
    )
    try:
        browser.get("about:blank")
        installed = browser.install_addon(str(addon.resolve()), temporary=True)
        with ZipFile(addon) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        expected = manifest["browser_specific_settings"]["gecko"]["id"]
        if installed != expected:
            raise RuntimeError("Installed addon ID differs from its manifest")

        print(f"Isolated Firefox started; waiting {settle_seconds:g}s before capture", flush=True)
        time.sleep(settle_seconds)
        browser.set_script_timeout(180)
        browser.set_context("chrome")
        captured = []
        for index in range(samples):
            suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            filename = f"firefox-memory-{suffix}-{index+1}.json.gz"
            target = PRIVATE_DIR / filename
            outcome = browser.execute_async_script(DUMP_SCRIPT, str(target.resolve()))
            if outcome != "complete" or not target.is_file():
                raise RuntimeError(
                    "Firefox about:memory reporter did not complete: "
                    + str(outcome)[:250]
                )
            with gzip.open(target, "rb") as stream:
                source = stream.read(MAX_DECOMPRESSED_BYTES + 1)
            if len(source) > MAX_DECOMPRESSED_BYTES:
                raise RuntimeError("Memory report exceeds size limit")
            result = json.loads(source)
            if not isinstance(result.get("reports"), list) or not result["reports"]:
                raise RuntimeError("Firefox returned empty memory report")
            captured.append({
                "name": filename,
                "reporter_count": len(result["reports"]),
                "extension_process_explicit_mib": extension_process_explicit_mib(
                    result["reports"]
                ),
            })
            print(f"Captured anonymized about:memory report #{index+1} ({len(result['reports'])} reporters)", flush=True)
        print(json.dumps({
            "outcome": "captured",
            "isolated_profile": True,
            "anonymized": True,
            "synthetic_extra_rules": extra_rules,
            "report_count": len(captured),
            "reports": captured,
            "raw_report_storage": "ignored dist/build/private-memory; never upload",
        }, indent=2))
        return captured
    finally:
        browser.quit()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--addon", type=Path, default=ROOT / "dist" / "build" / "uBlock0.firefox.xpi"
    )
    parser.add_argument(
        "--firefox", type=Path,
        default=Path(r"C:\Program Files\Mozilla Firefox\firefox.exe")
    )
    parser.add_argument("--settle-seconds", type=float, default=300)
    parser.add_argument("--extra-synthetic-hostnames", type=int, default=0)
    parser.add_argument("--samples", type=int, default=1)
    args = parser.parse_args(argv)
    if args.settle_seconds < 0 or args.samples < 1 or args.extra_synthetic_hostnames < 0:
        parser.error("Settle seconds and hostname count must be nonnegative, samples positive")
    try:
        capture(
            args.addon, args.firefox,
            args.settle_seconds, args.extra_synthetic_hostnames, args.samples
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"Firefox memory capture failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
