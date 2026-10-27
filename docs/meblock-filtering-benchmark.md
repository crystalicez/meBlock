# Filtering benchmark for meBlock

This workflow uses uBlock Origin's built-in `benchmarkStaticNetFiltering()` in `src/js/benchmarks.js`. It complements the earlier Firefox process RSS/CPU experiment. It does not alter filtering logic.

## Primary baseline suite: upstream first

**Active primary speed test:** `tools/meblock/sfne_quick.py` invoking uBlock Origin's own `benchmarkStaticNetFiltering()`, with a pinned, public request dataset and only the **default enabled filter lists**. Keep the existing `filtering_benchmark.py` parser/comparator for captured upstream text output. Use the **same benchmark** for both builds; do not replace its timing with meBlock's custom matcher loops. Warm up before recording runs, alternate A/B order, validate the input hashes and filtering counts, and report observed range alongside median. A 100,000-request corpus is an initial suggestion, not a measured precision guarantee; increase work if whole-millisecond reporting or environmental noise dominates. Synthetic request traffic is a smoke test, never representative user performance.

**Existing correctness gate, not an additional speed benchmark:** run `compare_original.py --default-lists` against the same pinned build/list selection when evaluating a candidate. It compares full per-request decision/modifier vectors, state and browser fixtures. Its `official` field contains the real uBO benchmark output, but its `us_per_request` field is a *separate* meBlock-only five-pass matcher+context loop, **not** uBO's benchmark duration. The old null preflight calls that custom field `official` internally; that historic label does not make it uBO's official result.

**Archived/diagnostic, excluded from the primary speed verdict:** `browser_probe.js`'s per-call p95 with timer overhead, 512-repetition context-batched p95, five-pass `us_per_request`, synthetic-delay sensitivity controls, and `candidate_noise_preflight.py`'s 3% null screen. Retain their code, tests and failed evidence for forensic/research use; do not delete them or present their results as speedup acceptance. The real same-code 10-pair null screen previously failed its ±3% target; none of these instruments establishes a 3% improvement.

**Separate on demand:** Firefox Profiler for CPU hot paths, existing Firefox memory experiment and engine-buffer counters for their separately scoped RAM questions. Do not mix profiler or memory capture timing into the network speed screen. Passing the quick screen alone never licenses an optimization: confirm correctness and independently validate a meaningful candidate A/B difference before claiming gains.

### Same-source Firefox variance investigation (2026-10-28)

**Observed, not inferred:** In the earlier ten-pair, 20-profile, 99,108-request
null-control report, **12 of 20 profiles** had a max/min spread above 25%
across five measured calls; **two exceeded 2x**. Each call measures only
uBO's original SFNE matching loop: Firefox startup and dataset loading occur
outside the timed interval. Byte-identical engine source, ten default lists,
block/allow/modifier counts and 99,108 requests were fixed.

The optional **diagnostic-only** `sfne_quick.py --diagnose-variance` mode
uses **eight warmups and twelve original SFNE calls per profile** (last five
remain in `official_runs`), and samples numerical host CPU activity over the
post-install wait. These runs are not eligible for null qualification or
optimization speed claims. Results of two AB/BA same-source pairs, **all Ion
settings at the Firefox default**, were:

| Role / profile | Median of last five (ms) | CV of twelve calls | Measured min–max (ms) | Median host CPU |
| --- | ---: | ---: | ---: | ---: |
| upstream / 1 | 1290 | 18.24% | 954–1642 | 18.8% |
| meblock / 1 | 1743 | 19.33% | 1025–1862 | 29.3% |
| meblock / 2 | 1040 | 5.58% | 1008–1202 | 32.5% |
| upstream / 2 | 1392 | 31.49% | 1040–2259 | 29.2% |

A separate alternating two-pair **setting intervention**, on the same
source and list selection, configured `javascript.options.ion=false` only
for the meblock-labelled arm. The last-five profile medians were **1472 and
1422 ms** at default settings, versus **3279 and 3106 ms** with Ion
disabled. Twelve-call coefficients of variation remained **11.47–19.44%**
across these profiles. This demonstrates a strong timing dependence on the
configured JIT mode, but does **not** establish that JIT tiering causes the
original same-configuration noise; jitter remained with Ion disabled.

During the diagnostic waits, system-wide CPU medians were **18.8–48%**
and Firefox's observed descendant-process CPU-time accrual was approximately
**0.82–0.94 CPU-second per wall-second**. An additional five-second
no-benchmark host sample had median **24.3% CPU** (range 5.1–32.6%)
on 20 logical CPUs. Other host work is present, but these coarse samples
include Firefox's own work and do not establish causation; faster and slower
profiles overlap in host load. No per-trial GC or scheduling trace was taken.

**Conclusion:** Wrong source/list data, Firefox *launch duration inside*
the timed SFNE interval, and a simple warmup-count fix are ruled out.
Changing Ion configuration changes throughput, but does not eliminate
variance. **Precise GC/JIT-versus-OS scheduler attribution remains
unresolved**; a focused Firefox Profiler trace or phase-aligned CPU sampling
is the next justified experiment. The ±3% baseline remains NOT READY.

To reproduce locally (the report stays ignored):

```powershell
python tools/meblock/sfne_quick.py --dataset dist/build/default-rule-probes-sfne-null-100k.json --runs 2 --upstream-ref upstream/master --meblock-ref upstream/master --diagnose-variance
python tools/meblock/sfne_quick.py --dataset dist/build/default-rule-probes-sfne-null-100k.json --runs 2 --upstream-ref upstream/master --meblock-ref upstream/master --diagnose-variance --diagnose-no-ion
```

### Direct Firefox check of the existing upstream tool (2026-10-22)

Two fresh **independent upstream-first Firefox pairs** completed (four disposable profiles), each running the real uBO benchmark on 1,552 default-list requests. Built-in durations were **79/49 ms** (upstream/meBlock) in pair 1 and **55/38 ms** in pair 2, rounded to whole milliseconds. Every arm blocked **759** requests. The separate custom matcher-only timing produced **10.562/15.588** and **19.222/8.466 microseconds/request**, even reversing its apparent winner between pairs. The custom timer is therefore **unfit as the primary speed verdict**. The built-in result is directly attributable to uBO's own implementation, but is **not yet precision-qualified** on this very short corpus; both pairs ran in the same upstream-first order and are not an AB/BA candidate study. These are no speedup/regression claims. Keep the saved raw evidence local and ignored.


## Run the one primary speed suite (automated Firefox)

Use `tools/meblock/sfne_quick.py`. It packages disposable, default-list-only
upstream and meBlock builds, launches separate isolated Firefox profiles in
alternating A/B order, calls **uBO's original benchmark directly** (two
warmups and five measured calls per profile), validates identical enabled-list
keys and all uBO block/allow/modifier counts, and saves the complete evidence
**only** in ignored `dist/build/meblock-sfne/`. This path deliberately skips
custom matcher loops, per-call/batched p95, browser fixture navigation and
RAM sampling. It is a speed **screen**, not a correctness or 3% acceptance gate.

Generate the default-list-derived, public-only balanced diagnostic workload
from a previously verified disposable upstream Firefox run, not from a
personal browser profile:

```powershell
python tools/meblock/default_rule_probes.py --addon dist/build/uBlock0.firefox.xpi --selected-run dist/build/meblock-comparison/SESSION/upstream-run-1.json --shards 20 --shard-index 0 --out dist/build/default-rule-probes-sfne-primary.json
python tools/meblock/sfne_quick.py --dataset dist/build/default-rule-probes-sfne-primary.json --runs 3
```

Replace `SESSION` with a **local ignored** default-lists verification run.
The 20-shard workload has approximately 20,000 requests; the exact count,
digest and selected public lists are pinned by the generated sidecar. The
number of requests and work distribution are diagnostic, not real browser
traffic frequency. The program reports medians, all five measurements per
profile, observed range, exact counters and a **whole-millisecond rounding**
check. A completed screen is never a credible 3% speedup claim without a
separate independent noise-qualified A/B study plus the full correctness
gate. Do not select a winner from overlapping/noisy trials. The bundled
JS code does not implement another timer; it calls uBO's existing function.

The manual capture workflow below remains available for diagnosis, **not a
second primary suite**. `compare_original.py` and its older p95 scripts
remain in the secondary correctness/forensic suite.

### Automated single-suite Firefox verification (2026-10-22)

The new `sfne_quick.py` **actually ran uBO's original SFNE benchmark**
inside isolated Firefox 157 builds with zero injected filter rules. On the
balanced, public-default-list-derived 19,824-request corpus, **three
independent alternating A/B pairs (six Firefox profiles)** completed,
including 2 warmups and 5 measured uBO calls/profile. All profiles used
the same 10 enabled lists and identical filtering/modifier counts:
9,689 blocked, 2 explicitly allowed, 10,133 passed. Uninstrumented
source builds had zero differing packaged files (same-build noise control).

| Paired profile | Upstream median of 5 trials | meBlock median of 5 trials |
| --- | ---: | ---: |
| 1 (upstream first) | 201 ms | 199 ms |
| 2 (meBlock first) | 206 ms | 204 ms |
| 3 (upstream first) | 197 ms | 205 ms |

The tool's total six-profile wall time was approximately **89 seconds**.
All thirty measured trials are retained locally. The smallest measured
uBO duration was **193 ms**, below the prespecified 200 ms threshold for
less than 0.5% whole-millisecond rounding uncertainty, so that calibration
check deliberately returned **false**. Three pairs do not establish 3%
detectability, an actual candidate speedup, or reduced real-world CPU.

A preliminary 6,196-request one-pair quick run also finished in about
32 seconds (upstream/meBlock median 62/64 ms), but failed timer-rounding
qualification. Neither the tiny nor the medium corpus is adequate evidence
to optimize. The single-suite execution path itself has now been proven
in Firefox; any precision claim must be established separately.

### Whole-millisecond timer calibration, not a speedup claim

A second quick-screen control used the existing **100,000-request synthetic**
dataset (ten default enabled filter lists, zero injected filters) across
**two alternating A/B Firefox pairs**. The same-build source snapshots had
zero differing files; request decision/modifier aggregate counts were
identical between every profile. The original uBO benchmark reported
profile medians **1,706/1,645 ms upstream** and **1,687/1,816 ms meBlock**.
The shortest individual trial was **1,636 ms**, so the prespecified
whole-millisecond rounding check **passed**.

This confirms that more requests can make timer quantization negligible.
It does **not** remove between-profile environmental noise: paired
differences still change direction on byte-identical source builds.
The synthetic corpus is not realistic site traffic, and two pairs cannot
qualify any claimed 3% change. Keep the balanced public-list-derived
19,824-request corpus as the **fast smoke workload**. The new public-list-derived
99,108-request corpus is the larger SFNE null-calibration input. The
100,000-request **synthetic** corpus is retained for clock calibration only.
Never select a speed winner from these same-source controls.

### Extended SFNE same-code noise qualification

The canonical runner now reuses `comparison_stats.paired_performance()` to
compare independent Firefox profile medians (not intra-profile trials).
A same-source null control requires ten balanced A/B pairs, a minimum 200-ms
SFNE duration per trial, and pooled plus both run-order-specific 95% paired
bootstrap confidence intervals strictly inside ±3%. The quick runner writes
these statistics to `paired_noise`. This is an equivalence preflight,
**not** proof that a 3% candidate optimization will be detectable; the
separate controlled-slowdown sensitivity check remains outstanding.

The 99,108-request public-derived corpus is created from a pinned default-list
Firefox run using `default_rule_probes.py --shards 4 --max-requests 120000`.
Its data and manifest remain ignored locally. The old 20,000-request
safety default is unchanged. Candidate correctness continues to use
`compare_original.py`. The custom-timer `candidate_noise_preflight.py`
is **not** the SFNE null gate.

### Full 10-pair upstream same-build SFNE null (2026-10-28)

The real Firefox 157 run completed **ten AB/BA pairs, twenty independent
profiles** with 99,108 default-list-derived public requests and ten selected
default lists. The upstream and meBlock roles exported the **same exact
source commit**; source builds had zero differing files. No extra rules were
injected. Each profile completed two warmups and five original uBO SFNE
measurements, with identical aggregate block/allow/modifier counts across arms.

The smallest timed SFNE trial was **965 ms**, so whole-millisecond rounding
**passed**. The paired median **same-code** difference was **+22.8104%**,
95% paired-bootstrap interval **[-16.4239%, +80.4825%]**. The upstream-first
stratum had median **+13.1919%**, interval **[-25.4952%, +67.536%]**; the
meBlock-first stratum had median **+49.8483%**, interval
**[-30.8553%, +238.9233%]**. All three ±3% null-equivalence gates **failed**.
These deltas are **measurement noise from identical code**, not a speedup or
regression of either project. All trial evidence is ignored and local.

**Result:** Steps 1–3 are implemented and the ten-pair null test ran fully,
but the baseline is **NOT precision-ready** for a 3% change. The next task
is to investigate profile/OS runtime noise and separately test known
sensitivity; adding requests alone did not solve variance. Do not loosen
the statistical thresholds or cherry-pick trials.

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

The reproducible dataset and validation tests run automatically. The **primary** `sfne_quick.py` command also launches real isolated Firefox and captures upstream's built-in benchmark automatically; the manual DevTools method remains optional. Neither unit tests nor merely capturing successful Firefox durations is proof of a small speed gain.

## Before claiming a speedup

- Freeze the upstream commit, meBlock commit, browser version, filter-list revisions, dataset SHA-256, and all relevant settings.
- Warm up both engines consistently, run enough work that whole-millisecond rounding is small relative to total elapsed time, and alternate A/B runs.
- Check blocked, allowed, and modifier counts, then run a separate *per-request* correctness suite. No blocking regression is acceptable.
- For borderline results, measure system contention and profile bottlenecks in a separate run. Report the number of runs and the median with the full range.
- Validate improvements in a realistic replayed browsing workload before concluding that users will benefit.
