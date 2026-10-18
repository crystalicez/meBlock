# Comparing original uBlock Origin and meBlock

This test suite compares **two real Firefox builds**, using pinned Git trees:

- Original: `upstream/master` (or an explicit upstream commit)
- Fork: `HEAD` (or an explicit meBlock commit)

It does not call the old Firefox-without-extension condition a fork comparison. It snapshots both revisions, builds both packages, adds the **same temporary test probe** to both build outputs, and runs each in a new headless Firefox profile. Original upstream source and the meBlock source tree remain untouched.

## Quick start (Windows)

Install Firefox, Python 3.13 or later, Selenium and psutil, and Git for Windows with Bash. Selenium must find its geckodriver. The official upstream filter assets must exist at `dist/build/uAssets/main` and `dist/build/uAssets/prod` (run `tools/pull-assets.sh` once with Git Bash).

Generate a fixed dataset:

```powershell
python tools/meblock/filtering_benchmark.py synthetic --count 20000 --seed 20261018 --out dist/build/meblock-requests.json
```

Build and compare in Firefox, alternating original and fork over five runs per build:

```powershell
python tools/meblock/compare_original.py --dataset dist/build/meblock-requests.json --runs 5
```

To verify only the two builds, without launching Firefox:

```powershell
python tools/meblock/compare_original.py --build-only --dataset dist/build/meblock-requests.json
```

To compare specific immutable versions:

```powershell
python tools/meblock/compare_original.py --upstream-ref 9e0dff715d --meblock-ref HEAD --runs 5
```

The suite exports **committed revisions only**. Uncommitted source edits are not included even when the working tree is dirty. Commit the implementation you intend to measure, or select its Git commit with `--meblock-ref`. The report records the resolved hashes and whether the working tree was dirty.

For a quick diagnostic run with system-wide idle memory and CPU readings:

```powershell
python tools/meblock/compare_original.py --runs 5 --idle-sample 5
```

Those process-wide readings are secondary diagnostics, not reliable per-extension allocation or CPU metrics.

## What is checked

| Gate | Check | Interpretation |
| --- | --- | --- |
| Git and assets | Exact commit hashes, asset revisions, dataset SHA-256 and selected filter-list keys | Detect mismatched inputs across both builds |
| Build regression | Both Firefox builds must succeed; resulting file hashes are compared | All modified bundled files are visible |
| Runtime | Separate Firefox profiles, installed addon ID and filter engine readiness | Code actually ran in Firefox |
| Original benchmark | Calls `benchmarkStaticNetFiltering` in both builds | Verifies upstream's engine benchmark completes |
| Behavior regression | Full decision vector plus upstream modifier counts | **Every request's block/allow/passthrough result must match**, including unchanged aggregate redirect, removeparam, CSP and other modifier counts |
| Browser behavior fixture | Load a local page with one allowed and one deliberately filtered script | Both builds must allow the expected script and block the other in Firefox's real request pipeline |
| Built-in extra benchmarks | Cosmetic and scriptlet benchmarking where dataset contains document requests | Separate diagnostic timings |
| Performance | Two in-session warm-ups and five measured engine passes, repeated across alternating A/B sessions | Median, range, noise floor, byte-identical control verdict and conservative comparison |
| Startup diagnostics | Extension-reported engine-ready time, when available | Recorded separately from filtering speed; not browser startup overhead |
| Firefox processes | Optional idle CPU time and summed RSS after benchmark | Diagnostic only; not per-extension attribution |
| Offline tests | `python -m unittest discover -s tools/meblock -p test_*.py -v` | Catches regressions in the harness and comparator |

**Coverage limits:** The local-page fixture tests basic real-world script blocking but does not exercise every browser API. A matching direct network-filter decision vector does not prove complete browser-level behavior. Redirect, URL modification, CNAME, dynamic firewall, cosmetic application, and scriptlet effects still need dedicated behavioral fixtures and end-to-end tests. The upstream cosmetic/scriptlet helpers expose only aggregate benchmark timings. Synthetic datasets do not establish real-world performance.

The GitHub workflow `.github/workflows/meblock-regression.yml` runs Python unit regression tests, JavaScript syntax validation and a deterministic dataset generator on Windows. It does **not** claim to run Firefox engine integration tests in CI; those need an available Firefox installation and pinned filter assets.

## Safety and isolation

- Source snapshots are created via `git archive` under ignored `dist/build/meblock-comparison`.
- The probe, embedded dataset and a local-only `test-blocked.js` filter rule are injected into both generated test packages, never into source files or release packages. This controlled rule is excluded from the clean-build file comparison.
- The HTTP collector listens on `127.0.0.1`, uses a random per-session token for uploads, bounds response size, and closes after execution.
- Every Firefox session uses Selenium's temporary profile, preserving your personal Firefox profile.
- The probe does not require privileged Marionette or `-remote-allow-system-access`.
- Output includes potentially sensitive input URLs when it needs to explain a mismatch; keep reports private unless the dataset is sanitized.
- The original Firefox extension ID is intentionally left unchanged **in these isolated test builds only**. MeBlock releases require their own name and ID.

## Results

The script writes a separate dated session directory under `dist/build/meblock-comparison/`.

- `report.json`: verdict, hashes, file diff, environment inputs and both timing summaries.
- `upstream-run-N.json` and `meblock-run-N.json`: raw measured engine results and per-request decisions.
- `upstream/checkout/dist/build/build.log` and equivalent fork path: build logs.
- `upstream/instrumented.xpi` and `meblock/instrumented.xpi`: diagnostic test packages, **not distributable releases**.

Interpret results conservatively: five or more runs per build, same request corpus and list snapshots, alternating A/B order, and no behavior differences. If an observed timing difference is within the spread of individual measurements, the result is *inconclusive*. When both built trees are byte-identical, any apparent speedup is environmental noise, not a change attributable to meBlock.

Do not claim improvement without evaluating performance profiles and realistic browsing workloads. A direct engine microbenchmark is only one component of total page-load cost.

## Failure handling

The test exits nonzero if either source cannot build, the addon or engine cannot initialize, required output is missing, or per-request behavior differs. It saves `report.json` with `status: failed` and an error message. A run that cannot reach the engine is **not** a performance pass. The generated XPI instrumentation is disposable and can be regenerated.
