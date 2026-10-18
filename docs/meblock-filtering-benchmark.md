# Filtering benchmark for meBlock

This workflow uses uBlock Origin's built-in `benchmarkStaticNetFiltering()` in `src/js/benchmarks.js`. It complements the earlier Firefox process RSS/CPU experiment. It does not alter filtering logic.

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
