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
| Git and assets | Exact commit hashes, asset revisions, dataset SHA-256, harness file hashes, fixture version and selected filter-list keys | Detect mismatched inputs across both builds and preserve the exact test implementation |
| Build regression | Both Firefox builds must succeed; resulting file hashes are compared | All modified bundled files are visible |
| Runtime | Separate Firefox profiles, installed addon ID and filter engine readiness | Code actually ran in Firefox |
| Original benchmark | Calls `benchmarkStaticNetFiltering` in both builds | Verifies upstream's engine benchmark completes |
| Behavior regression | Full per-request decision vector and six modifier bitmasks | Compares block/allow outcomes and each redirect, removeparam, URL skip, CSP, permissions and replace decision by request index; also checks aggregate counts against upstream |
| Browser behavior fixture | Independent Firefox-without-addon negative control, then both test builds | Asserts actual script, image, iframe, XHR, noop redirect, CSS hiding, scriptlet injection and query-parameter removal behavior |
| Built-in extra benchmarks | Cosmetic and scriptlet benchmarking where dataset contains document requests | Separate diagnostic timings |
| Performance | Two in-session warm-ups and five measured engine passes, repeated across alternating A/B sessions | Median, range, noise floor, byte-identical control verdict and conservative comparison |
| Startup diagnostics | Extension-reported engine-ready time, when available | Recorded separately from filtering speed; not browser startup overhead |
| Firefox processes | Optional idle CPU time and summed RSS after benchmark | Diagnostic only; not per-extension attribution |
| Offline tests | `python -m unittest discover -s tools/meblock -p test_*.py -v` | Catches regressions in the harness and comparator |

**Coverage limits:** These fixtures exercise selected network and page-side effects but not the full extension. Dynamic firewall rules, CNAME uncloaking, HTML filtering, filter subscriptions, settings persistence, large-page behavior, all redirect resources, advanced scriptlets and private memory attribution require separate tests. The upstream cosmetic/scriptlet microbenchmarks still expose only aggregate timings. Synthetic datasets do not establish real-user speedups.

The GitHub workflow `.github/workflows/meblock-regression.yml` automatically runs Python unit regression tests, JavaScript syntax validation and deterministic corpus generation on Windows. A separate **opt-in Firefox integration job** is available from GitHub Actions → **Run workflow** → enable `firefox_integration`. That job fetches the official upstream commit and filter assets, runs one real Firefox comparison per build with a fresh profile, and uploads JSON evidence. It is not the five-run performance gate; run the full command locally for statistical comparisons. This CI integration job must be verified on a hosted runner before its results can be claimed.

## Safety and isolation

- Source snapshots are created via `git archive` under ignored `dist/build/meblock-comparison`.
- The probe, embedded dataset and the controlled filter rules in `tools/meblock/browser_fixtures.py` are injected identically into both disposable test packages. Nothing is added to source files or release packages. Build equivalence is checked **before** instrumentation.
- The HTTP collector listens on `127.0.0.1`, uses a random per-session token for uploads, bounds response size, and closes after execution.
- Every Firefox session uses Selenium's temporary profile, preserving your personal Firefox profile. A Firefox-without-addon negative control first verifies that blocked targets really load in the absence of filters; each instrumented profile must then satisfy every expected page observation.
- The probe does not require privileged Marionette or `-remote-allow-system-access`.
- Output includes potentially sensitive input URLs when it needs to explain a mismatch; keep reports private unless the dataset is sanitized.
- Each test package keeps the Firefox extension ID from its source manifest. The runner verifies that Firefox installed that exact ID, so the fork may use a different ID after rebranding. Neither package is installed into your regular Firefox profile.

## Results

The script writes a separate dated session directory under `dist/build/meblock-comparison/`.

- `report.json`: verdict, source and harness SHA-256 hashes, fixture version, file diff, environment inputs, negative control and timing summaries.
- `upstream-run-N.json` and `meblock-run-N.json`: raw engine results, decision arrays, modifier mask arrays, browser observations and timing samples.
- `upstream/checkout/dist/build/build.log` and equivalent fork path: build logs.
- `upstream/instrumented.xpi` and `meblock/instrumented.xpi`: diagnostic test packages, **not distributable releases**.

Interpret results conservatively: five or more runs per build, same request corpus and list snapshots, alternating A/B order, and no behavior differences. If an observed timing difference is within the spread of individual measurements, the result is *inconclusive*. When both built trees are byte-identical, any apparent speedup is environmental noise, not a change attributable to meBlock.

Do not claim improvement without evaluating performance profiles and realistic browsing workloads. A direct engine microbenchmark is only one component of total page-load cost.

### Verified control run, 2026-10-19

On Firefox 157 for Windows, the test completed five upstream and five meBlock
browser runs with **10,000 pinned synthetic requests per run**. The source trees
exported for that run were `9e0dff715df9889389e6a6ddf62f36c24a5cb729`
(upstream) and `7b6e467457c423bafbef57564ca71f451471231a` (fork).
The Firefox build outputs were byte-identical before instrumentation.

- Dataset SHA-256: `74f26dc4ead3a01056d31681af991c372d830b947cbc16c3fa614264d5844447`.
- Each run: 2,974 blocked, 7,026 not blocked and 2,018 redirect modifiers.
- All block/allow decisions and all six per-request modifier-mask categories matched.
- Firefox-without-addon negative control loaded every intentionally blockable resource.
- Both extensions passed scripted positive/negative checks for script, image,
  iframe, XHR, noop redirect, cosmetic CSS, scriptlet injection and URL parameter removal.
- Median filtering match cost: 17.20 µs/request upstream versus 23.12 µs/request
  fork. **This is a control, not a regression.** The code was identical and
  run-to-run timings varied substantially.
- Local evidence: `dist/build/meblock-comparison/20261019-005149-896a7f/report.json`.

The synthetic corpus exists to exercise the pipeline. Realistic website replay,
CNAME, dynamic filtering, settings persistence, and HTML response filtering are
separate work not covered by this control run.

## Failure handling

The test exits nonzero if either source cannot build, the addon or engine cannot initialize, required output is missing, or per-request behavior differs. It saves `report.json` with `status: failed` and an error message. A run that cannot reach the engine is **not** a performance pass. The generated XPI instrumentation is disposable and can be regenerated.
