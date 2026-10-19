# meBlock baseline audit: prerequisites before memory optimization

**Status: PARTIALLY VERIFIED, NOT YET AN OPTIMIZATION ACCEPTANCE BASELINE.**

This audit separates a working test instrument from a repeatable performance
baseline. The user's approximate 60 MB observation is **not an exact or pinned
measurement**. No performance change is justified solely by that estimate.

## Coverage and evidence

| Capability | State | What has been verified and what remains |
| --- | --- | --- |
| Firefox build input and compatibility | Verified for the existing regression harness | `compare_original.py` exports pinned upstream and fork commits with identical asset snapshots, compares clean build bytes before instrumentation, checks addon IDs and saves hashes |
| Static network correctness | Verified on synthetic workloads | Exact per-request block/allow vector, six modifier-bit decisions and counts compared across original/fork Firefox sessions; deliberately injected decision regressions fail tests |
| Real Firefox behavior | Verified on controlled loopback fixtures | Script, image, frame, fetch/XHR, redirect, cosmetic CSS, scriptlet and URL-parameter behavior; without-addon negative control |
| Firefox dynamic firewall and settings | Partially verified | Engine rule precedence, rollback and persistence across extension reload pass; end-to-end browser dynamic firewall and persistence across a *full Firefox restart* are not covered |
| Firefox anonymous memory capture | Verified | `capture_memory.py` opens temporary headless Firefox; Mozilla `nsIMemoryInfoDumper` writes anonymized JSON.gz only into ignored `dist/build/private-memory/`; test profile is never the user's profile |
| Memory capture waits for loaded lists | Verified | The capture tool calls uBO's own dashboard `getLists`, polls until network/cosmetic counts and the selected-list fingerprint stabilize, requires updating=false, and starts `--settle-seconds` **after readiness**. It checks the state again after capture and fails on changes |
| The extra 600,000 test rules really compile | Verified for the current synthetic fixture | Stock XPI: 252,243 network rules; same XPI plus 600,000 unique hostname rules: **852,243**; 44,192 cosmetic rules and ten selected filter lists in both. Counts were read directly from the running extension, not inferred from ZIP contents |
| Memory process measurement | Verified, but **process-wide** | `analyze_memory.py` validates unit=bytes, reads raw Firefox JSON.gz, sums leaf reporters without parent+child duplication and rejects incomplete scopes. Extensions-process `explicit` is **not** the extension's private retained memory |
| Reproducible steady-state A/B memory | **Not yet** | An exploratory online 5-minute pair showed 57.621 vs 72.851 MiB for the whole Extensions process, but filter readiness and stable counts were not validated then. Later strict tests detected filter-list changes during capture, including a 600,000-to-600,122 network-count drift after five minutes **despite offline mode**. The capture tool now additionally persists `autoUpdate=false` in each disposable profile and fails closed when rules change; it requires independently matched 5-minute AB/BA pairs before any RAM claim |
| 600k production-like filter composition | **Not yet** | The 600,000 injected rules are *hostname-only* plus preloaded public lists. User subscriptions may include many regex, cosmetic, scriptlet and modifier rules and must not be copied from a personal profile |
| Request latency confidence | **Not yet** | Existing benchmarking reports per-session means and paired uncertainty; no representative **request-level p95** or rigorous end-to-end page-load performance. Claiming "no slowdown" requires this |
| WASM vs JS/native retained allocations | **Not yet** | Process `explicit` aggregates unrelated runtime allocations. Without validated heap/WASM attribution it is unsafe to decide which representation to optimize |
| CI and public privacy | Verified for unit tests and local guard | Windows CI runs regression unit tests, Node syntax and privacy guard. Raw `about:memory` and regression JSON artifacts are never uploaded. Force-adding any file under `dist/build/` is rejected by the guard. The **optional** full Firefox integration workflow has not been proven on GitHub's hosted runner |

## Actual Firefox validation, 2026-10-19

- After adding the readiness and fingerprint gates, an offline five-minute
  **stock** capture completed with 252,243 network filters, 44,192 cosmetic
  filters and **54.953 MiB** Extensions-process `explicit` memory.
  That experiment had not yet disabled automatic asset updates in uBO.
- The initial five-minute **600,000-network-rule** capture failed:
  the filter count went from 600,000 to 600,122 after collection despite
  Firefox's offline state. The gate correctly rejected the measurement.
- After disabling `autoUpdate` via uBO's dashboard API in the **temporary
  test profile**, another full five-minute capture completed with exactly
  600,000 network rules before and after, 44,192 cosmetic rules, identical
  enabled-list fingerprint and **63.660 MiB** whole Extensions-process
  `explicit` memory. No personal Firefox profile was used.
- **These are not an accepted A/B pair**, because the stock and 600k test
  had different update-control settings. Do **not** divide their MiB
  values to assert a percentage difference, or present these totals as
  memory exclusively attributable to uBO/meBlock.
- The automated `memory_experiment.py` paired runner **also passed on
  actual Firefox**, comparing separate stock and exactly-600k synthetic
  profiles, with matching Firefox version, source package hash,
  cosmetic count, selected-list fingerprint and `autoUpdate=false`.
  Its deliberately short two-second smoke measured 61.373 vs 66.214
  MiB for the whole Extensions process (one pair, +4.841 MiB).
  Its verdict was correctly **EXPLORATORY: fewer than three independent
  pairs**. These two-second observations are not steady-state RAM figures
  and do not prove performance or memory optimization.
- All raw reports and disposable synthetic XPI packages remain beneath
  Git-ignored `dist/build/private-memory/`. No raw reporting paths or
  browser URLs are included in this public audit.

## Reproducible capture and evidence gates

Run from the repository root against the **uninstrumented** test XPI, with
only public datasets and a fresh Firefox profile; never touch any existing
personal Firefox profile. The example exact counts below apply only to the
currently tested XPI. If a filter-list update changes the counts, pin the
new package and update the expected values based on a verified clean run.

```powershell
# Basic readiness and actual-engine-count smoke (fresh temporary profile)
python tools/meblock/capture_memory.py --offline --settle-seconds 2 --expected-network-filters 252243

# Synthetic test with exactly 600k compiled network rules in total:
# 252243 pinned stock rules + 347757 synthetic hostname rules
python tools/meblock/capture_memory.py --offline --settle-seconds 2 --extra-synthetic-hostnames 347757 --expected-network-filters 600000

# Repeatable, independent 5-minute experiments (start timer after readiness)
python tools/meblock/capture_memory.py --offline --settle-seconds 300 --runs 3 --expected-network-filters 252243
python tools/meblock/memory_experiment.py --pairs 3 --settle-seconds 300 --base-network-filters 252243 --extra-synthetic-hostnames 347757
```

Each `--runs` iteration creates a **fresh Firefox profile**. Multiple `--samples` in one
profile are not independent runs; the tool spaces those samples out using
`--sample-spacing`. For performance conclusions, interleave the two
conditions across repeated experiment batches or extend the harness to automate
AB/BA ordering. Report medians and variation as well as paired deltas.

The raw reports, their filename metadata and individual reporter paths are
**private**. Never publish them in a public GitHub repository, issue or Actions
artifact. The scripts may print aggregate counts and MiB, but even anonymized
report payloads stay ignored locally.

## Pre-optimization decision

No optimization patch should be called complete until:

1. Both original and candidate builds pass request-by-request and browser
   fixture comparison using equivalent filter lists and Firefox versions.
2. The engine is ready and the expected number of rules is loaded at both
   baseline and candidate capture time; list snapshots, addon input SHA,
   Firefox release and selected-list counts are comparable.
3. At least three independent steady-state A/B memory runs pass, with
   comparable `about:memory` process reporter paths and no missing
   measurements; results include absolute MiB, percentages and variability.
4. An observed memory reduction is attributable to extension-retained
   structures rather than an unrelated browser process or transient GC.
5. A representative latency and page-load workload establishes that filtering
   speed and correctness are not meaningfully worse.

Until all these gates pass, verdict **BASELINE INCOMPLETE / NO OPTIMIZATION CLAIM**.
