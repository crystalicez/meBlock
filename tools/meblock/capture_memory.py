#!/usr/bin/env python3
"""Capture anonymized Firefox about:memory using an isolated test profile.

Only disposable test Firefox sessions. Raw reports must never leave ignored
dist/build/private-memory or be uploaded to a public GitHub Actions artifact.
"""

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import time
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service

from benchmark_firefox import collect_idle, geckodriver_path
from analyze_memory import MAX_DECOMPRESSED_BYTES, subtree_summary

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_DIR = ROOT / "dist" / "build" / "private-memory"
BUILT_IN_LIST = "assets/ublock/filters.min.txt"


def source_filter_assets_sha256(package):
    """Hash public bundled text assets, not host metadata or private user lists."""
    with ZipFile(package) as archive:
        names = sorted(
            name for name in archive.namelist()
            if name.startswith("assets/") and name.endswith(".txt")
        )
        if BUILT_IN_LIST not in names:
            raise ValueError("Test package has no bundled default filter list")
        digest = hashlib.sha256()
        for name in names:
            digest.update(name.encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(archive.read(name)).digest())
        return digest.hexdigest()


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


ENGINE_STATE_SCRIPT = """
const done = arguments[arguments.length - 1];
(async () => {
    for (let attempt = 0; attempt < 120; attempt++) {
        if (typeof vAPI === 'object' && vAPI.messaging?.send) {
            const response = await vAPI.messaging.send('dashboard', { what: 'getLists' });
            if (response && Number.isInteger(response.netFilterCount) &&
                response.netFilterCount > 0 &&
                Number.isInteger(response.cosmeticFilterCount) &&
                response.isUpdating === false) {
                const selected = Object.keys(response.current ?? {}).filter(
                    key => response.current[key].off !== true
                ).sort();
                if (selected.length > 0) {
                    done({
                        network_filters: response.netFilterCount,
                        cosmetic_filters: response.cosmeticFilterCount,
                        selected_list_keys: selected,
                        auto_update: response.autoUpdate,
                        updating: response.isUpdating,
                    });
                    return;
                }
            }
        }
        await new Promise(resolve => setTimeout(resolve, 1000));
    }
    done({ error: 'filter-engine-ready-timeout' });
})().catch(() => done({ error: 'filter-engine-state-read-failed' }));
"""


def validated_engine_state(state, minimum_network, expected_network=None):
    if not isinstance(state, dict) or "error" in state:
        raise RuntimeError("Cannot establish extension filtering readiness")
    network = state.get("network_filters")
    cosmetic = state.get("cosmetic_filters")
    selected = state.get("selected_list_keys")
    if any(type(value) is not int or value < 0 for value in (network, cosmetic)):
        raise RuntimeError("Firefox did not report a valid filter count")
    if (not isinstance(selected, list) or not selected
        or any(not isinstance(value, str) or not value for value in selected)
        or len(set(selected)) != len(selected)
        or state.get("updating") is not False
        or type(state.get("auto_update")) is not bool):
        raise RuntimeError("Firefox filter lists are not loaded and idle")
    if network < minimum_network:
        raise RuntimeError("The engine compiled fewer network filters than the workload minimum")
    if expected_network is not None and network != expected_network:
        raise RuntimeError("The engine filter count differs from the pinned expected count")
    return {
        "network_filters": network,
        "cosmetic_filters": cosmetic,
        "selected_lists": len(selected),
        "selected_lists_sha256": hashlib.sha256(
            "\n".join(sorted(selected)).encode("utf-8")
        ).hexdigest(),
        "updating": False,
        "auto_update": state["auto_update"],
    }


def read_engine_state(browser, addon_id, minimum_network, expected_network):
    browser.set_script_timeout(150)
    browser.set_context("chrome")
    page = browser.execute_script(
        'const policy = WebExtensionPolicy.getByID(arguments[0]);'
        'return policy && policy.getURL("3p-filters.html");',
        addon_id,
    )
    if not isinstance(page, str) or not page.startswith("moz-extension://"):
        raise RuntimeError("Firefox extension dashboard is unavailable")
    browser.set_context("content")
    browser.get(page)
    observed = browser.execute_async_script(ENGINE_STATE_SCRIPT)
    result = validated_engine_state(observed, minimum_network, expected_network)
    browser.get("about:blank")
    return result


def wait_for_stable_engine(browser, addon_id, minimum_network, expected_network,
                           attempts=15, interval_seconds=3):
    previous = None
    unchanged = 0
    for attempt in range(attempts):
        state = read_engine_state(browser, addon_id, 0, None)
        unchanged = unchanged + 1 if state == previous else 1
        if attempt >= 5 and unchanged >= 3:
            if state["network_filters"] < minimum_network:
                raise RuntimeError("The engine compiled fewer network filters than the workload minimum")
            if (expected_network is not None and
                state["network_filters"] != expected_network):
                raise RuntimeError("The stable engine filter count differs from the pinned expected count")
            return state
        previous = state
        time.sleep(interval_seconds)
    raise RuntimeError("Firefox filter counts did not stabilize after loading")


def disable_test_list_updates(browser, addon_id):
    """Disable automatic updates only inside this disposable Firefox profile."""
    browser.set_context("chrome")
    page = browser.execute_script(
        'const policy = WebExtensionPolicy.getByID(arguments[0]);'
        'return policy && policy.getURL("3p-filters.html");',
        addon_id,
    )
    if not isinstance(page, str) or not page.startswith("moz-extension://"):
        raise RuntimeError("Test extension dashboard unavailable for update lock")
    browser.set_context("content")
    browser.get(page)
    browser.set_script_timeout(120)
    status = browser.execute_async_script("""
const done = arguments[arguments.length - 1];
(async () => {
    for (let attempt = 0; attempt < 90; attempt++) {
        if (typeof vAPI !== 'object' || !vAPI.messaging?.send) {
            await new Promise(resolve => setTimeout(resolve, 500));
            continue;
        }
        const active = await vAPI.messaging.send('dashboard', { what: 'getLists' });
        if (!Number.isInteger(active?.netFilterCount) || active.netFilterCount <= 0) {
            await new Promise(resolve => setTimeout(resolve, 500));
            continue;
        }
        await vAPI.messaging.send('dashboard', {
            what: 'userSettings', name: 'autoUpdate', value: false,
        });
        const saved = await browser.storage.local.get('autoUpdate');
        const now = await vAPI.messaging.send('dashboard', { what: 'getLists' });
        if (saved.autoUpdate === false && now?.autoUpdate === false) {
            done('persisted-and-active');
            return;
        }
        await new Promise(resolve => setTimeout(resolve, 500));
    }
    done('not-consistent');
})().catch(() => done('setting-error'));
""")
    if status != "persisted-and-active":
        raise RuntimeError("Could not disable automatic updates in disposable profile")
    browser.get("about:blank")


RULE_MIXES = ("hostname", "mixed-network")


def synthetic_rule(index, rule_mix):
    host = f"mb-memory-{index:07d}.example.invalid"
    if rule_mix == "hostname":
        return f"||{host}^\n"
    bucket = index % 100
    if bucket < 55:
        return f"||{host}^\n"
    if bucket < 75:
        return f"||{host}/asset.js\n"
    if bucket < 90:
        return f"||{host}^$script\n"
    if bucket < 99:
        return f"||{host}^$image,third-party\n"
    return f"/mb-memory-{index:07d}[.]example[.]invalid/\n"


def synthetic_xpi(source, target, count, rule_mix="hostname"):
    """Add public deterministic rules only to a disposable test XPI."""
    if count < 1:
        raise ValueError("Synthetic network-rule count must be positive")
    if rule_mix not in RULE_MIXES:
        raise ValueError("Unknown synthetic rule mix")
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
                    synthetic_rule(i, rule_mix) for i in range(count)
                ).encode("ascii")
                data = data + b"\n" + additions
            clone.writestr(original.getinfo(name), data)


SAFE_PROCESS_CATEGORIES = (
    "window-objects", "js-non-window", "dom", "images",
    "layout", "heap-overhead", "storage", "wasm",
)


def extension_process_explicit_summary(reports):
    """Public-safe numeric buckets for the whole Extensions process, never URLs."""
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
    if scope["unavailable_byte_reporters"] != 0:
        return None
    groups = scope["by_category_bytes"]
    values = {
        name: round(groups[name] / 1048576, 3)
        for name in SAFE_PROCESS_CATEGORIES if groups.get(name, 0) > 0
    }
    included = sum(groups.get(name, 0) for name in SAFE_PROCESS_CATEGORIES)
    values["other_process_allocations"] = round((scope["bytes"] - included) / 1048576, 3)
    return {
        "total_mib": scope["mib"],
        "safe_process_categories_mib": values,
    }


def extension_process_explicit_mib(reports):
    result = extension_process_explicit_summary(reports)
    return result["total_mib"] if result is not None else None


def capture(addon, firefox, settle_seconds, extra_rules=0, samples=1,
            expected_network_filters=None, sample_spacing=60, offline=False,
            rule_mix="hostname", measure_idle_cpu=False):
    if (not math.isfinite(settle_seconds) or settle_seconds < 0
        or not math.isfinite(sample_spacing) or sample_spacing < 0):
        raise ValueError("Firefox memory sampling times must be finite and nonnegative")
    if measure_idle_cpu and settle_seconds < 1:
        raise ValueError("Idle CPU measurement requires at least one second after readiness")
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    if not addon.is_file():
        raise FileNotFoundError("Test-only addon XPI not found")
    source_sha256 = hashlib.sha256(addon.read_bytes()).hexdigest()
    public_assets_sha256 = source_filter_assets_sha256(addon)
    if extra_rules:
        disposable = PRIVATE_DIR / "synthetic-meblock.xpi"
        synthetic_xpi(addon, disposable, extra_rules, rule_mix=rule_mix)
        addon = disposable

    test_sha256 = hashlib.sha256(addon.read_bytes()).hexdigest()
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
        if offline:
            browser.set_context("chrome")
            state = browser.execute_script(
                'Services.io.manageOfflineStatus = false;'
                'Services.io.offline = true;'
                'return Services.io.offline;'
            )
            if state is not True:
                raise RuntimeError("Firefox offline control was not applied")
            browser.set_context("content")
        installed = browser.install_addon(str(addon.resolve()), temporary=True)
        with ZipFile(addon) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        expected = manifest["browser_specific_settings"]["gecko"]["id"]
        if installed != expected:
            raise RuntimeError("Installed addon ID differs from its manifest")
        if offline:
            disable_test_list_updates(browser, expected)

        state = wait_for_stable_engine(
            browser, expected, extra_rules, expected_network_filters
        )
        if offline and state["auto_update"] is not False:
            raise RuntimeError("Offline test profile still has automatic updates enabled")
        print(
            f"Filtering ready: {state['network_filters']} network filters, "
            f"{state['cosmetic_filters']} cosmetic filters; "
            f"waiting {settle_seconds:g}s from readiness", flush=True
        )
        idle_cpu = (
            collect_idle(browser.service.process.pid, settle_seconds)
            if measure_idle_cpu else None
        )
        if idle_cpu is None:
            time.sleep(settle_seconds)
        browser.set_script_timeout(180)
        browser.set_context("chrome")
        captured = []
        for index in range(samples):
            if index:
                time.sleep(sample_spacing)
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
            profile = extension_process_explicit_summary(result["reports"])
            if profile is None:
                raise RuntimeError("Extensions process explicit memory is ambiguous or incomplete")
            captured.append({
                "name": filename,
                "reporter_count": len(result["reports"]),
                "extension_process_explicit_mib": profile["total_mib"],
                "safe_process_categories_mib": profile["safe_process_categories_mib"],
            })
            print(f"Captured anonymized about:memory report #{index+1} ({len(result['reports'])} reporters)", flush=True)
        final_state = read_engine_state(browser, expected, 0, None)
        if final_state != state:
            raise RuntimeError(
                "Filter engine changed during memory sampling: "
                f"network {state['network_filters']} -> {final_state['network_filters']}, "
                f"cosmetic {state['cosmetic_filters']} -> {final_state['cosmetic_filters']}, "
                f"selected lists {state['selected_lists']} -> {final_state['selected_lists']}"
            )
        print(json.dumps({
            "outcome": "captured",
            "isolated_profile": True,
            "anonymized": True,
            "synthetic_extra_rules": extra_rules,
            "synthetic_rule_mix": rule_mix if extra_rules else "none",
            "browser_offline_for_pinned_baseline": offline,
            "source_package_sha256": source_sha256,
            "source_filter_assets_sha256": public_assets_sha256,
            "test_package_sha256": test_sha256,
            "firefox_version": browser.capabilities.get("browserVersion"),
            "settle_seconds_after_filter_readiness": settle_seconds,
            "idle_firefox_cpu": idle_cpu,
            "filter_engine": state,
            "report_count": len(captured),
            "reports": captured,
            "raw_report_storage": "ignored dist/build/private-memory; never upload",
        }, indent=2))
        return {
            "captures": captured,
            "filter_engine": state,
            "firefox_version": browser.capabilities.get("browserVersion"),
            "source_package_sha256": source_sha256,
            "source_filter_assets_sha256": public_assets_sha256,
            "test_package_sha256": test_sha256,
            "settle_seconds_after_filter_readiness": settle_seconds,
            "idle_firefox_cpu": idle_cpu,
            "synthetic_rule_mix": rule_mix if extra_rules else "none",
        }
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
    parser.add_argument("--extra-synthetic-hostnames", "--extra-synthetic-rules",
                        dest="extra_synthetic_hostnames", type=int, default=0)
    parser.add_argument("--synthetic-rule-mix", choices=RULE_MIXES, default="hostname")
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--runs", type=int, default=1, help="Independent fresh Firefox profiles")
    parser.add_argument("--sample-spacing", type=float, default=60)
    parser.add_argument("--offline", action="store_true", help="Disable external network in disposable Firefox before loading the addon")
    parser.add_argument("--expected-network-filters", type=int)
    args = parser.parse_args(argv)
    if (not math.isfinite(args.settle_seconds) or args.settle_seconds < 0
        or not math.isfinite(args.sample_spacing) or args.sample_spacing < 0
        or args.runs < 1 or args.samples < 1 or args.extra_synthetic_hostnames < 0
        or args.expected_network_filters is not None and args.expected_network_filters < 1):
        parser.error("Invalid runs, samples, filter counts or sample timing")
    try:
        for number in range(args.runs):
            print(f"Independent Firefox baseline run {number + 1}/{args.runs}", flush=True)
            capture(
                args.addon, args.firefox,
                args.settle_seconds, args.extra_synthetic_hostnames, args.samples,
                args.expected_network_filters, args.sample_spacing,
                args.offline,
                args.synthetic_rule_mix,
            )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"Firefox memory capture failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
