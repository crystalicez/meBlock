# Filtering benchmark for meBlock

This workflow uses uBlock Origin's built-in `benchmarkStaticNetFiltering()` in `src/js/benchmarks.js`. It complements the earlier Firefox process RSS/CPU experiment. It does not alter filtering logic.

## Primary baseline suite: upstream first

**Active primary speed test:** uBlock Origin's own `benchmarkStaticNetFiltering()`, with a pinned, public request dataset and only the **default enabled filter lists**. Keep the existing `filtering_benchmark.py` parser/comparator for captured upstream text output. Use the **same benchmark** for both builds; do not replace its timing with meBlock's custom matcher loops. Warm up before recording runs, alternate A/B order, validate the input hashes and filtering counts, and report observed range alongside median. A 100,000-request corpus is an initial suggestion, not a measured precision guarantee; increase work if whole-millisecond reporting or environmental noise dominates. Synthetic request traffic is a smoke test, never representative user performance.

**Existing correctness gate, not an additional speed benchmark:** run `compare_original.py --default-lists` against the same pinned build/list selection when evaluating a candidate. It compares full per-request decision/modifier vectors, state and browser fixtures. Its `official` field contains the real uBO benchmark output, but its `us_per_request` field is a *separate* meBlock-only five-pass matcher+context loop, **not** uBO's benchmark duration. The old null preflight calls that custom field `official` internally; that historic label does not make it uBO's official result.

**Archived/diagnostic, excluded from the primary speed verdict:** `browser_probe.js`'s per-call p95 with timer overhead, 512-repetition context-batched p95, five-pass `us_per_request`, synthetic-delay sensitivity controls, and `candidate_noise_preflight.py`'s 3% null screen. Retain their code, tests and failed evidence for forensic/research use; do not delete them or present their results as speedup acceptance. The real same-code 10-pair null screen previously failed its ±3% target; none of these instruments establishes a 3% improvement.

**Separate on demand:** Firefox Profiler for CPU hot paths, existing Firefox memory experiment and engine-buffer counters for their separately scoped RAM questions. Do not mix profiler or memory capture timing into the network speed screen. Passing the quick screen alone never licenses an optimization: confirm correctness and independently validate a meaningful candidate A/B difference before claiming gains.

### Direct Firefox check of the existing upstream tool (2026-10-22)

Two fresh **independent upstream-first Firefox pairs** completed (four disposable profiles), each running the real uBO benchmark on 1,552 default-list requests. Built-in durations were **79/49 ms** (upstream/meBlock) in pair 1 and **55/38 ms** in pair 2, rounded to whole milliseconds. Every arm blocked **759** requests. The separate custom matcher-only timing produced **10.562/15.588** and **19.222/8.466 microseconds/request**, even reversing its apparent winner between pairs. The custom timer is therefore **unfit as the primary speed verdict**. The built-in result is directly attributable to uBO's own implementation, but is **not yet precision-qualified** on this very short corpus; both pairs ran in the same upstream-first order and are not an AB/BA candidate study. These are no speedup/regression claims. Keep the saved raw evidence local and ignored.


## What upstream measures

The function loads an array of objects containing `frameUrl`, `url`, and `cpt`. It processes the requests with the loaded static filtering engine, times that loop with `performance.now()`, and reports the total requests, elapsed milliseconds, average time per request, and aggregate blocking/modifier counts.

The function measures in-engine matching with actual filter lists. It does **not** measure full network latency, Firefox startup overhead, memory allocation, or end-to-end page loading.

The repo also includes hostname microbenchmarks at `docs/tests/hnset-benchmark.html` and `docs/tests/hnbigset-benchmark.html`. Those use Benchmark.js.

## Prepare a versioned dataset

For a basic repeatable smoke test, create a deterministic synthetic corpus:

```powershell
python tools/meblock/filtering_benchmark.py synthetic --count 100000 --seed 20261018 --out dist/build/meblock-requests.json
```

The companion manifest records the corpus SHA-256, count, and seed. **Synthetic hosts are not a real-world workload. Do not use this corpus alone to make user-performance claims.**

For a captured public dataset in JSON array or JSON Lines format, normalize its records:

```powershell
python tools/meblock/filtering_benchmark.py normalize --input path/to/public-requests.json --out dist/build/meblock-requests.json
```

Only share data for which you have distribution rights. Real browsing URLs may contain private tokens or user details. Inspect or scrub them before storing or sharing. Keep input data and a cryptographic hash fixed across comparisons.

The upstream benchmark accepts the `document` and `xhr` request types and maps them to `main_frame` and `xmlhttprequest`.

## Run upstream's own benchmark

1. Create an isolated Firefox profile for each build. Install either an untouched upstream build or a meBlock development build into that profile. Never install the upstream-ID test XPI over your usual extension.
2. Start a loopback HTTP server in the repo root. Keep it running only during the test:

   ```powershell
   python -m http.server 8765 --bind 127.0.0.1 --directory dist/build
   ```

3. Confirm `http://127.0.0.1:8765/meblock-requests.json` serves the exact pinned dataset.
4. In the extension dashboard, open advanced settings and set `benchmarkDatasetURL` to that HTTP URL. Apply the setting. The dataset is served locally and is not uploaded.
5. Open the extension's Dev tools page. Its **SNFE: Benchmark** button is enabled when the hidden setting is configured. Run one warm-up trial, then capture at least five measured outputs. Do **not** count the warm-up. Record each complete output block in a text file for the corresponding build.
6. Test upstream and meBlock in alternating order with equal browser versions, settings, profiles, filter-list snapshots, and dataset. Avoid concurrent Firefox tests, background downloads, and DevTools live expression evaluation during timing.

The benchmark logs output with this header:

```text
Benchmarked static network filtering engine:
Evaluated 100000 requests in 120 ms
Average: 0.001 ms per request
Not blocked: 70000
Blocked: 30000
Unblocked: 0
redirect=: 0
removeparam=: 0
urlskip=: 0
csp=: 0
permissions=: 0
replace=: 0
```

The block above is **an invented format illustration only**, not an observed result. Do not include it as empirical data. Capture actual outputs from the extension.

Firefox 157 does not permit ordinary Selenium WebDriver navigation to `moz-extension://` pages. This workflow deliberately does not weaken browser system-access restrictions. Opening the Dev tools page remains a manual step until a safe supported browser automation path is established.

## Compare captured runs

After capturing two text files with consecutive complete SNFE output blocks, run:

```powershell
python tools/meblock/filtering_benchmark.py compare --upstream upstream-runs.txt --meblock meblock-runs.txt --dataset dist/build/meblock-requests.json
```

Output: `dist/build/meblock-filtering-comparison.json`. It includes both input-log hashes, dataset SHA-256, run counts, per-run millisecond-derived microseconds/request, median, range, and a conservative verdict.

The comparator refuses mismatched request counts. It marks aggregate decision-count changes as invalid and treats overlapping run ranges as inconclusive. Even equal aggregate counts do **not** prove identical decisions for every request. A true correctness gate still needs per-request comparison or a dedicated regression suite.

The comparator uses `elapsed milliseconds / request count` because the upstream tool rounds duration to whole milliseconds and prints the per-request value to three decimal places. This imposes a resolution limit, especially for small datasets or extremely fast matches.

## Validate the tooling

```powershell
python -m unittest discover -s tools/meblock -p test_filtering_benchmark.py -v
```

The reproducible dataset and the validation tests can run automatically. The actual engine benchmark needs Firefox and explicit manual capture; it has not been claimed successful merely because the generator and parser pass.

## Before claiming a speedup

- Freeze the upstream commit, meBlock commit, browser version, filter-list revisions, dataset SHA-256, and all relevant settings.
- Warm up both engines consistently, run enough work that whole-millisecond rounding is small relative to total elapsed time, and alternate A/B runs.
- Check blocked, allowed, and modifier counts, then run a separate *per-request* correctness suite. No blocking regression is acceptable.
- For borderline results, measure system contention and profile bottlenecks in a separate run. Report the number of runs and the median with the full range.
- Validate improvements in a realistic replayed browsing workload before concluding that users will benefit.
