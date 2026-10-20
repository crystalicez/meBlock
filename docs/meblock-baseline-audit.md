# meBlock baseline audit: prerequisites before memory optimization

**Status: PARTIALLY VERIFIED, NOT YET AN OPTIMIZATION ACCEPTANCE BASELINE.**

This audit separates a working test instrument from a repeatable performance
baseline. The chosen baseline is now the **default enabled lists** in a clean,
uninstrumented upstream uBlock Origin or meBlock profile, with **no added
synthetic filter rules**. The user's older approximate 60 MB observation
under a different workload is not an exact or pinned measurement.
No performance change is justified solely by that estimate.

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
| Fast engine-owned buffer footprint | Verified for one pinned public fixture in real Firefox | A test-only read-only probe measures `filter_data` plus three trie backing buffers and WASM flags, with numeric schema and repeated-run stability checks. After disabling automatic filter updates **only in disposable test builds**, three independent Firefox sessions per identical build all reported 17.5 MiB of the four known buffers at 252,250 compiled network filters; the same-build delta was zero. These counters omit JS objects, other filter engines and native overhead; they are **not total extension RAM** |
| Memory process measurement | Verified, but **process-wide** | `analyze_memory.py` validates unit=bytes, reads raw Firefox JSON.gz, sums leaf reporters without parent+child duplication and rejects incomplete scopes. Extensions-process `explicit` is **not** the extension's private retained memory |
| Paired memory uncertainty and null controls | **Verified in three independent 300-second same-XPI pairs** | On default enabled lists and six isolated Firefox profiles, signed whole-process deltas were +0.074, -0.036 and +0.031 MiB; maximum observed absolute deviation was 0.074 MiB, paired bootstrap 95% interval [-0.036, +0.074] MiB, exact sign-test p=1.0. The runner marks this CONTROL, never optimization. A three-pair control does not bound all noise or attribute memory to an addon; a directional *candidate* process-wide signal needs at least six independent long pairs and additional checks |
| Stock/default-lists RAM baseline | **Three independent 300-second same-XPI negative-control pairs completed**; distinct-build candidate baseline outstanding | `memory_experiment.py --default-lists` pins the observed compiled network count (252,243), cosmetic count (44,192), ten selected default lists, their hashes and matching Firefox version across all six profiles, with no synthetic rule addition. The runner now additionally validates enabled-list counts, rejects instrumented performance XPIs, and reports observed absolute paired process noise. No claims of extension-private RAM reductions |
| Reproducible steady-state A/B memory | **Identical-XPI control complete; independent different-build comparison not yet** | Three alternating A/B pairs of the same XPI passed engine-readiness and post-capture state validation after 300 seconds each. An earlier uncontrolled online pair and synthetic-filter-count drift are not comparable. Actual upstream-versus-optimized-build confirmation still requires its own pinned 5-minute AB/BA experiment with enough repeats for the claimed effect; this control must not be presented as that comparison |
| Paired Firefox process-tree idle CPU | **Verified: three 300-second same-XPI A/B control pairs on Firefox 157** | Opt-in `--measure-idle-cpu` samples the disposable Firefox descendant processes during the same post-readiness window as RAM capture, reports CPU seconds and one-core-equivalent utilization, and flags process identity churn. Six isolated default-list profiles used 252,243 network and 44,192 cosmetic filters, zero injected rules, at least 300.044 seconds of sampled time, and no observed process churn. The observed median paired difference was **−0.068 one-core percentage points**, maximum absolute paired difference **0.183 points**. The paired RAM median was **−0.006 MiB**, maximum absolute deviation **0.079 MiB**. Both verdicts correctly remain **CONTROL**, not an optimization or addon-private CPU/RAM attribution. A different-build CPU screen still needs six long independent pairs and cannot establish addon-only CPU by itself |
| Default-list-derived network request coverage | **Screened public default-list rule candidates; other families incomplete** | Ten lists selected; eight mapped to bundled public text, two did not. Of 197,055 source lines, 99,120 simple host/party/type rule candidates were recognized before checking `$badfilter`; 13 matched disabling directives in the selected bundled sources (12 blocking rules, one exception). **99,107 remain** (98,943 blocking, 164 exceptions), still not necessarily all active or individually verified in the runtime. The earlier unscreened 165-candidate no-injection exception test is a historical differential observation only. Complex exceptions, two unmapped active lists, and most blocking shards remain untested |
| Selected default-list exception overrides under controlled blockers | **164/164 syntactically non-badfiltered exceptions showed context-level overrides in correctness-only tests** | The previous unresolved public exception was accompanied by a matching `$badfilter` directive in selected bundled assets; the generator now screens such cases across mapped files. A new 164-candidate/656-request challenge across two alternating A/B pairs (four Firefox profiles) confirmed **164/164 candidate overrides, 177/177 expected allow contexts, and 328/328 temporary blocking controls**, with identical decisions/modifiers across upstream and meBlock. The synthetic blockers are present **only in disposable correctness XPIs**, never in default-only RAM/performance builds. This is not independent attribution of each exception, complete coverage of all selected lists, or proof of optimization |
| Independently asserted static-network precedence | **Verified for 39 fixture v8 cases in real Firefox; not a per-default-rule oracle** | A correctness-only fixture asserts fixed decisions per index for block/exception precedence, type and party combinations, positive/negative domain scopes, first-party aliases, `important`, `badfilter`, `from`, negated type, and case-sensitive regex paths. One fresh upstream/meBlock Firefox pair passed all 39 decisions on 2026-10-20. Neither engine output defines the expected vector. Default-only performance XPIs receive zero fixture rules |
| Individual default-list rule behavior | **Not exhaustively verified** | The running browser reports compiled totals and selected-list fingerprints, but neither proves that each default-list rule has correct behavior in every URL, request-type, frame, priority, exception or modifier context. Full exhaustive context combinations are not feasible; use per-rule differential cases and targeted rule-interaction regressions for the code being optimized |
| Per-rule cosmetic/scriptlet browser behavior | Verified for fixed synthetic cases; broader scriptlet and procedural families remain | Fixture v8 checks per-index presence/hiding/near-miss vectors for 96 ordinary cosmetic selectors and eight `:has-text()` procedural selectors, 32 independent visible controls, and 12 `set-constant` scriptlets with 12 untouched near-miss properties. One fresh upstream/meBlock Firefox pair passed; previous fixture v5 passed two pairs on its smaller coverage. Missing nodes and wrong indices fail regardless of counts; no-addon inverse controls are checked |
| Extra 600k synthetic rules | No longer required for baseline | The prior hostname-heavy and mixed-network synthetic experiments remain opt-in stress-test tools only; they are **not** part of current default-list RAM or performance acceptance. No private subscriptions are copied |
| Local Firefox navigation and script-resource p95 | **Real Firefox v8 fixture and default-only control both verified; not representative real-site latency** | Browser `PerformanceNavigationTiming` and `PerformanceResourceTiming` record 20 navigations plus exactly one no-store allowed-script fetch per navigation after two warm-ups, checking behavior each visit. The unfiltered no-addon control now measures the same resource with inverse blocking expectations. On 2026-10-20, one default-list Firefox A/B pair with 1,460 requests per profile and zero added filters passed; local script-resource p95 was 21.66 ms versus 30.42 ms. These single-run values are noise-prone and **not** a speedup or real-world request-level p95 |
| Request latency confidence | Test-only instrumented matcher tail latency under calibration; not yet accepted | Test probes sample `matchRequest` individually over three passes, separately from page-navigation p95, and record clock quantization/overhead. Disposable Firefox profiles alone disable timer precision reduction. Earlier unmodified timer rounded most sub-ms requests to zero; no production website request p95 or reliable per-addon CPU result yet. A timer-quality gate withholds unresolved p95 rather than claiming accuracy |
| WASM vs JS/native retained allocations | **Not yet** | Process `explicit` aggregates unrelated runtime allocations. Without validated heap/WASM attribution it is unsafe to decide which representation to optimize |
| CI and public privacy | Verified for unit tests and local guard | The Windows regression workflow runs **only on manual dispatch**, not push or pull requests. It checks unit tests, Node syntax and privacy guard. Raw `about:memory` and regression JSON artifacts are never uploaded. Force-adding any file under `dist/build/` is rejected by the guard. The **optional** full Firefox integration job has not been proven on GitHub's hosted runner |

## Local baseline readiness gate

Before optimization, run `tools/meblock/baseline_readiness.py` against three
**ignored, local-only** evidence files: a real Firefox injected-rule
correctness report, a `--default-lists` Firefox parity report from the
revised harness, and a same-XPI CPU/RAM paired control report. For example:

```powershell
python tools/meblock/baseline_readiness.py --correctness-report dist/build/meblock-comparison/<correctness-run>/report.json --default-report dist/build/meblock-comparison/<default-run>/report.json --memory-report dist/build/private-memory/<paired-memory-report>.json
```

Replace the angle-bracket placeholders with your local report names. The
tool returns **0** only when every gate passes, **1** when evidence is
incomplete, and **2** for unreadable or invalid evidence. It validates
independently asserted correctness, no added rules in default-list mode,
matching upstream/fork and public asset revisions, filter-list fingerprints,
a 300-second three-pair same-XPI CPU/RAM null control, a real browser
loopback script-resource p95, and sufficient precision in the separately
instrumented matcher p95. Unlike a human checklist, the gate fails closed on
missing evidence and prints only check names and statuses, never raw memory
reporter data, browsing hosts, file paths, or filter text.

A pass means only that these **controlled baseline instruments** produced
usable evidence for an experiment. It does **not** prove coverage of every
network/cosmetic rule, representative real-site p95, addon-exclusive CPU or
RAM, or that an optimized candidate is safe to ship. A change to the
filtering engine requires its own affected-rule regression fixtures and
repeatable independent upstream/candidate A/B runs. An instrumented matcher
p95 that Firefox quantizes too coarsely is `NOT READY`, not a zero-microsecond
performance measurement.

On 2026-10-20, the real Firefox fixture-v8, default-only and
three-pair/300-second CPU/RAM null-control reports passed **five of six**
readiness checks. The remaining blocker is `instrumented_matcher_p95_precision`:
the Firefox timer's approximately 20-microsecond step is too coarse to
support a credible individual static-network matcher p95 on the default
request corpus. Do not substitute the local browser resource duration,
whole-Firefox CPU percentage or an aggregate throughput mean for that
missing engine p95.

## Behavioral gate before changing a filtering hot path

A compiled rule count is not a behavioral proof. The default lists include
network rules, exceptions, cosmetic selectors and scriptlets with
context-dependent effects. The browser fixture's strict per-index tests
only cover **its own intentionally injected rules**, not every default
list entry.

Before changing an engine path, create deterministic positive and
non-matching contexts for affected default-list rules **where a distinct
effect can be observed**. Compare network decisions and modifier results
against the pinned upstream engine per context, exercise precedence and
exception interactions, and assert page-side results for cosmetic and
scriptlet rule types. The coverage report must count the rules tested,
rules without a constructible decisive context and rule types not yet
covered, rather than announcing universal coverage from matching totals.
It is not feasible to exhaust all URLs, document origins, request types,
page structures and combinations for every rule. The per-default-rule
coverage report and the remaining interaction regressions are **not yet
implemented**; until then, the default-list test is a screening baseline,
not exhaustive proof of correctness for a proposed optimization.

## Actual Firefox validation, 2026-10-19

- Extended default-list rule-derived probe parser to recognize simple
  hostname exceptions/blocks with one optional request type and one
  first/third-party qualifier, rejecting unknown, contradictory or action
  modifiers. Selection remains pinned to **10 active default lists**, of
  which eight have bundled public text. The eligible public candidates
  rose from 93,326 basic shapes to **99,120** (98,955 blocks and 165
  exceptions). A **complete exception-family probe** across two actual
  A/B pairs (four disposable Firefox profiles), 660 requests per
  profile, matched every upstream-vs-fork decision and modifier mask,
  page control and selected-list fingerprint with **zero extra rules**.
  Of the 165 exception candidates, **11** had a distinguishable
  matched-versus-near-miss decision/modifier result in at least one tested
  context, while **154** did not. Only **9 positive requests** were
  explicitly allowed, and 10 positive requests were blocked by the full
  engine. This is **not proof of correct exception override precedence**:
  an exception without a matching block may have no observable effect,
  and all compared builds were byte-identical same-code controls.
  Verdict CONTROL; no speedup or completed per-rule behavioral claim.
- **Default-lists-only steady-state RAM noise control completed:** three
  independent alternating A/B pairs (six disposable Firefox 157 profiles),
  **300 seconds after verified filter readiness in every profile**, with
  **252,243 network and 44,192 cosmetic filters** and ten selected default
  lists, zero added rules and a byte-identical XPI on both sides. Process-wide
  paired differences (B - A) were **+0.074, -0.036, +0.031 MiB**. Median
  paired delta **+0.031 MiB**, median absolute difference **0.036 MiB**,
  maximum observed absolute difference **0.074 MiB (0.135%)**, paired
  bootstrap 95% interval **[-0.036, +0.074] MiB** and exact sign-test p=1.
  The verdict is correctly **CONTROL: identical XPI, not optimization**.
  This is a *sampled* same-build process noise estimate, not a universal
  upper bound and **not retained RAM attributable to the addon**.
  Unanonymized browsing/profile data were not used, and raw anonymized
  reports remain under ignored private build artifacts.
- Updated selected-list-pinned network baseline, using a real disposable
  Firefox selected-list snapshot, completed a **strict one-pair / two
  Firefox-profile** control with 1,460 requests per profile and **zero
  injected filter rules**. Decisions, modifiers, synthetic page checks
  and selected-list identity all matched. The selected four engine buffers
  remained stable at **17.5 MiB** in each profile; they are not complete
  addon RAM. The new independently sampled request-matcher p95 diagnostics
  were **withheld**: observed Firefox timer granularity was about **20 µs**
  and ~47% of individual timings rounded to zero, making a useful
  microsecond p95 unavailable. The same-build verdict remained CONTROL
  (byte-identical code, no speedup).
- Previous four-profile run exposed **real buffer capacity drift** across
  profiles with equal network-filter counts. The buffer comparator now
  reports observed ranges and suppresses a single byte-delta conclusion
  rather than quietly inferring allocation improvements. A longer
  test-only engine readiness delay restored stable known-buffer readings
  in a subsequent four-profile control. Neither result attributes the
  full Extensions-process footprint to a single addon.
- Syntax-only classification of selected bundled public lists found
  **197,055 non-comment lines**: 144,667 network patterns/modifiers,
  4,840 network exceptions/modifiers, 33,028 cosmetic/HTML,
  10,390 scriptlets and 4,130 other syntaxes. These are *source line
  categories*, not compiled rule counts, coverage or proof of
  individual rule behavior. Two active lists had no mapped bundled
  public text and remain outside this classification.
- An actual default-XPI bundled-rule probe on Firefox generated **1,460
  requests** from 365 distinct simple host-anchored public filter
  candidates in shard 0/256. Across **two A/B pairs in four Firefox
  profiles**, each candidate received a matching and near-miss request
  in first- and third-party document contexts. Per-request decisions and
  modifier bitmasks were identical, all page checks passed, and the
  four known engine buffers had a zero-byte delta. Test builds had
  **zero injected filter rules** and byte-identical source. The result
  was labeled CONTROL. This does not demonstrate that each rule fired,
  cover all enabled lists, or prove any speedup. The generator reports
  93,326 distinct eligible candidates across eight bundled public
  assets, **not 93,326 individually verified active rules**; other
  shards and all non-simple types remain untested.
- A real **default-lists-only 300-second negative-control pair** ran
  to completion in two independently started Firefox 157 profiles with
  the same unchanged XPI, exactly 252,243 compiled network filters,
  44,192 cosmetic filters, ten enabled default lists, and **zero**
  injected rules. Both profiles settled for 300 seconds after filter
  readiness, with identical list and bundled-asset fingerprints. The
  whole Extensions-process `explicit` values were **54.923 MiB** and
  **54.860 MiB**, an apparent difference of **-0.063 MiB
  (-0.115%)**. This is a **single same-XPI noise control**, not a fork
  comparison or proof of RAM improvement. Verdict remains EXPLORATORY
  because at least three independent steady-state pairs and a longer
  noise assessment are still missing.
- In the updated **stock/default-enabled-lists-only** mode, a real
  two-pair A/B same-XPI Firefox control loaded **252,243 network and
  44,192 cosmetic filters**, adding **zero synthetic rules**. The
  pairs had only a two-second post-readiness settling period. The
  whole Extensions-process RAM paired differences were **-0.391 MiB
  and -8.511 MiB**; verdict EXPLORATORY, IDENTICAL-XPI NEGATIVE CONTROL.
  These values reveal environmental noise, **not RAM improvement**.
- A separate real Firefox **default-list** network-matcher comparison
  ran two alternating A/B pairs with byte-identical builds and no
  appended fixture filter rules. All 3,000 synthetic request decisions
  per run and modifier masks matched exactly, with **zero bytes**
  difference in the selected four known engine buffers. Observed
  average matcher costs were 15.07–22.33 µs/request (upstream runs)
  and 14.80–17.07 µs/request (fork runs). The verdict remained CONTROL
  and is not a speedup claim. This mode keeps disposable probes but
  uses default filter lists; its request traffic remains synthetic.
- Fixture v5 completed **two alternating A/B pairs across four disposable
  Firefox profiles** with byte-identical upstream/fork code. Every actual
  profile returned complete all-true vectors for 96 cosmetic target
  presence checks, 96 hidden-rule effects, 96 visible near-miss controls,
  32 additional visible controls, 12 applied scriptlets and 12 untouched
  scriptlet near-miss variables. The independent without-addon control
  correctly observed the inverse cosmetic/scriptlet behaviors. All
  network decisions, modifiers and page behavior checks passed. Unit
  tests also inject wrong rule indices while preserving aggregate counts
  and verify that the comparison fails with the offending index. These
  checks cover **every rule in the small controlled fixture**, not
  every rule in a 600,000-filter distribution.
- Fixture v4 was exercised in **three alternating upstream/fork Firefox A/B
  pairs** (six isolated profiles; identical compiled source). Each browser
  reported exactly **96 hidden cosmetic targets, 32 visible controls and
  12 applied set-constant scriptlets**. The no-addon negative control
  matched the opposite behavior. All network decision/modifier vectors
  and the loopback page behavior checks passed. Each profile also performed
  20 measured navigations after two warm-ups; per-profile loopback
  `loadEventEnd` p95 values ranged **66 to 108 ms** across the six
  sessions. This range demonstrates noise, **not a difference between builds**
  or a real-website speedup. The fixture does not represent 600k public
  filter-list proportions.
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
- A **strictly paired five-minute** stock-versus-600k run also completed
  successfully on Firefox 157.0 in two fresh profiles, with automatic
  updates disabled and identical enabled-list fingerprint and package
  source hash. It recorded **54.510 MiB** at 252,243 network rules and
  **63.593 MiB** at exactly 600,000 network rules: one observed paired
  difference of **+9.083 MiB (+16.663%)** in Extensions-process
  `explicit` memory. The runner correctly marked this as
  **EXPLORATORY** because one pair is insufficient for a reliable
  steady-state difference; it is not per-extension retained memory or
  a candidate optimization result.
- The paired runner now supports **two separate uninstrumented XPIs**
  (original versus fork) with identical filter counts, selected-list
  fingerprints and browser versions. A **negative-control real Firefox
  smoke** compared the **exact same XPI against itself** in two fresh
  profiles after two seconds. It passed input correctness gates but
  reported 59.944 versus 56.940 MiB (**−3.004 MiB, −5.011%**) of
  Extensions-process memory. This is **environmental/runtime variation,
  not an improvement**. The verdict remains EXPLORATORY.
- A three-pair same-XPI **two-second** negative control in six real isolated
  Firefox profiles passed with exactly 252,243 network filters, 44,192
  cosmetic filters and stable selected-list fingerprints. The whole
  Extensions-process paired differences ranged from **-2.448 to +7.155 MiB**,
  with a paired median absolute deviation of **3.786 MiB** and two-sided
  sign-test p-value **1.0**. The summary correctly identified identical
  XPI inputs, reported the interval and marked the run **EXPLORATORY**.
  This is startup/runtime noise, not an optimization measurement.
- The analyzer can additionally report numeric allowlisted process-level
  categories such as `window-objects` and `js-non-window` without
  exposing private reporter paths. These categories cannot yet isolate
  WASM or JS allocations belonging uniquely to this extension.
- A three-pair alternating AB/BA **smoke**, six real isolated Firefox
  profiles with only **10 seconds of post-readiness idle**, passed for
  252,243 versus exactly 600,000 compiled network filters. Median whole
  Extensions-process `explicit`: **55.320 MiB** baseline versus
  **69.297 MiB** with synthetic rules. Median **paired** increase was
  **+13.441 MiB** (individual pair increases +14.126, +12.818,
  +13.441 MiB). This is **NOT a steady-state memory baseline**:
  the older script labelled three pairs as `MEASURED` without enforcing
  sufficient idle duration. The corrected verdict requires both
  **three independent pairs and >=300 seconds** of post-readiness idle
  for every condition; ten-second data must remain `EXPLORATORY`.
- A separate **10-second same-XPI-versus-itself Firefox negative control**
  passed all build-matching requirements (identical source XPI hash,
  public bundled filter assets, enabled-list fingerprint, compiled rule
  counts and browser version), but the two isolated profiles still differed
  by **+7.031 MiB (+12.464%)** of whole Extensions-process
  `explicit` RAM. The result was correctly `EXPLORATORY`:
  this is runtime noise, **not an optimization improvement or regression**.
- The build-comparison runner now verifies SHA-256 of all bundled public
  text filter assets, as well as the active filter-list fingerprint,
  compiled rule counts and Firefox version. A mismatch fails closed.
  This content fingerprint cannot verify separately downloaded or user
  subscription lists; keep the test browser offline and updates disabled.
- A deterministic `mixed-network` fixture now exercises 55% hostname,
  20% path-specific, 15% script-specific, 9% image/third-party and 1%
  regex network rules in addition to the pinned stock lists. A fresh,
  offline Firefox 157.0 smoke with 347,757 mixed additions verified
  **600,000 compiled network filters**, **44,192 cosmetic filters**,
  ten selected lists, disabled auto-update, and identical engine state
  before and after capture. A separate two-profile stock-versus-mixed
  paired runner also passed its count and source-input checks and
  correctly returned `EXPLORATORY` for one two-second pair.
  These observations **are not** steady-state RAM evidence. The test
  still lacks a representative mix of cosmetic filters, scriptlets and
  real page traffic.
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

# Mixed network filter shapes, still synthetic and not a production mix
python tools/meblock/capture_memory.py --offline --settle-seconds 2 --extra-synthetic-rules 347757 --synthetic-rule-mix mixed-network --expected-network-filters 600000

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
