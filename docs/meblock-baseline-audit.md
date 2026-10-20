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
| Default-list-derived network request coverage | **Selected sources classified and bounded; not all active rules covered** | Ten lists selected: **8 map to packaged public text, 1 is an external public list without packaged text, and 1 is the `user-filters` slot** (not asserted to contain rules). There are **zero unrecognized source categories**. Of 197,055 packaged source lines, 99,120 simple host/party/type candidates were recognized; 13 matched `$badfilter` disabling directives. **99,107 remain** (98,943 blocking, 164 exceptions), still not necessarily active individually. The external list, any actual user-filter text, complex modifiers, scriptlets and most rule shards are not covered by this generated request corpus. Default-list parity still exercises the running engine's compiled lists |
| Selected default-list exception overrides under controlled blockers | **164/164 syntactically non-badfiltered exceptions showed context-level overrides in correctness-only tests** | The previous unresolved public exception was accompanied by a matching `$badfilter` directive in selected bundled assets; the generator now screens such cases across mapped files. A new 164-candidate/656-request challenge across two alternating A/B pairs (four Firefox profiles) confirmed **164/164 candidate overrides, 177/177 expected allow contexts, and 328/328 temporary blocking controls**, with identical decisions/modifiers across upstream and meBlock. The synthetic blockers are present **only in disposable correctness XPIs**, never in default-only RAM/performance builds. This is not independent attribution of each exception, complete coverage of all selected lists, or proof of optimization |
| Independently asserted static-network precedence | **Verified for 46 fixture v9 cases in real Firefox; not a per-default-rule oracle** | A correctness-only fixture asserts fixed decisions per index for block/exception precedence, type and party combinations, positive/negative domain scopes, first-party aliases, `important`, `badfilter`, `from`, negated type, case-sensitive regex paths, URL wildcards, end anchors and separator characters. One Firefox upstream/meBlock pair passed all 46 on 2026-10-20; an incorrectly formed anchor in the initial test was rejected and corrected before rerun. Neither engine output defines the expected vector. Default-only performance XPIs receive zero fixture rules |
| Individual default-list rule behavior | **Not exhaustively verified** | The running browser reports compiled totals and selected-list fingerprints, but neither proves that each default-list rule has correct behavior in every URL, request-type, frame, priority, exception or modifier context. Full exhaustive context combinations are not feasible; use per-rule differential cases and targeted rule-interaction regressions for the code being optimized |
| Per-rule cosmetic/scriptlet browser behavior | **Fixture v9 positive/near-miss checks verified in Firefox; other scriptlet families still open** | Fixture v9 asserts per-index presence/hiding/near-miss for 96 ordinary cosmetics, **8 `:has-text()`, 4 `:has()` and 4 `:matches-css()`** procedural selectors, 32 independent visible controls and 12 `set-constant` scriptlets with untouched near-miss properties. A real upstream/meBlock Firefox pair passed all page-side assertions and without-addon inverse controls. Missing nodes and individual wrong indices fail even if aggregate counts agree |
| Extra 600k synthetic rules | No longer required for baseline | The prior hostname-heavy and mixed-network synthetic experiments remain opt-in stress-test tools only; they are **not** part of current default-list RAM or performance acceptance. No private subscriptions are copied |
| Local Firefox navigation and script-resource p95 | **Real Firefox fixture v9 and three-pair default-only control verified; not representative real-site latency** | Browser `PerformanceNavigationTiming` and `PerformanceResourceTiming` record 20 navigations plus one no-store allowed-script fetch per navigation after two warm-ups, checking behavior each visit. The unfiltered no-addon control measures the same resource with inverse blocking expectations. On 2026-10-20, the updated 1,552-request default-only A/B control completed three pairs with zero injected filters. Resource duration p95 is retained as a **diagnostic** only; it is not a speedup or real-world request latency claim |
| Individual-call matcher p95 | **Not resolved by Firefox timer granularity** | The original `meblock-request-latency-v2` instrumented probe measures single calls, but Firefox's ~20 µs clock steps quantize many results. Its precision gate still withholds an individual-call p95. Never present zeros as zero execution time |
| Amortized matcher context p95 | **Verified for v9 in three real default-only Firefox pairs; noisy run explicitly rejected** | Each context is matched 512 times per batch; decisions, clock steps and empty-loop controls are verified. This is p95 of per-context **amortized means**, not individual-call tail latency. For the regenerated 1,552-request default-list corpus, the first six-profile run was rejected when upstream spread reached **52.36%** against the unchanged 50% cutoff. The complete independent rerun passed with median p95 **27.7734 µs in both roles**, relative spans **1.69% upstream, 0.56% meBlock**. The failed run remains preserved locally; neither run demonstrates an optimization |
| WASM vs JS/native retained allocations | **Not yet** | Process `explicit` aggregates unrelated runtime allocations. Without validated heap/WASM attribution it is unsafe to decide which representation to optimize |
| CI and public privacy | Verified for unit tests and local guard | The Windows regression workflow runs **only on manual dispatch**, not push or pull requests. It checks unit tests, Node syntax and privacy guard. Raw `about:memory` and regression JSON artifacts are never uploaded. Force-adding any file under `dist/build/` is rejected by the guard. The **optional** full Firefox integration job has not been proven on GitHub's hosted runner |

## Local baseline readiness gate

Before optimization, run `tools/meblock/baseline_readiness.py` against three
**ignored, local-only** evidence files: a real Firefox injected-rule
correctness report, a `--default-lists` Firefox parity report from the
revised harness, and a same-XPI CPU/RAM paired control report. For example:

```powershell
python tools/meblock/baseline_readiness.py `
  --correctness-report dist/build/meblock-comparison/<correctness-run>/report.json `
  --correctness-dataset dist/build/meblock-small.json `
  --default-report dist/build/meblock-comparison/<default-run>/report.json `
  --default-dataset dist/build/default-rule-probes-mixed.json `
  --memory-report dist/build/private-memory/<paired-memory-report>.json `
  --memory-xpi dist/build/uBlock0.firefox.xpi `
  --upstream-xpi dist/build/meblock-comparison/<default-run>/upstream/instrumented.xpi `
  --meblock-xpi dist/build/meblock-comparison/<default-run>/meblock/instrumented.xpi
```

Replace the angle-bracket placeholders with your local report names. The
tool returns **0** only when every gate passes, **1** when evidence is
incomplete, and **2** for unreadable or invalid evidence. It validates
independently asserted correctness, no added rules in default-list mode,
matching upstream/fork and public asset revisions, filter-list fingerprints,
and a separate **selected-list source-provenance gate**. That gate verifies
the exact request dataset digest/row count, the packaged-versus-external/user
source categories and zero unresolved selected sources; it does **not**
claim to reproduce external public text or any user-filter content.

The added **default-request-type breadth gate** rejects performance corpora
dominated by one request type. The original 1,552-request shard
contained 1,548 scripts and four images, insufficient for cross-type
screening. The new deterministic generator assigns unrestricted public
hostname rule candidates across script, image, XHR and stylesheet, while
respecting explicitly typed rules. Its ignored manifest records numeric
counts verified directly against the request dataset, and **each of the
four types must supply at least 10%**. Older datasets without a
validated type breakdown fail closed. This is a balanced *synthetic*
matcher diagnostic, not a representative distribution of real user
traffic and not additional installed filtering rules.

On 2026-10-20 the newly balanced **default-enabled-list** corpus contained
**1,552 requests** (384 scripts, 392 images, 388 XHR and 388 stylesheets).
A fresh **three-pair/six-profile** Firefox upstream-vs-meBlock run and a
separate synthetic fixture-v9 correctness pair passed, as did exact
per-profile evidence replay and the unchanged **three-pair/300-second
CPU/RAM same-XPI negative control**. The updated instrument-readiness
result was **10/10 PASS**, with **no injected filters** in the default-list
profiles. The amortized-context p95 medians were **8.2422 µs upstream**
and **7.9297 µs meBlock**, and relative cross-profile ranges
**8.53%** and **2.96%** respectively. The values are for **same-code
controls**, not an improvement. They cannot be compared to historical
script-dominated corpus p95 values: both request composition and the
measured path differ. The separate specific-engine-change gate remains
NOT READY for broad network engine edits without the missing family
fixtures.

An existing gate verifies the **actual XPI files**, not merely
their reported filter counts: SHA-256 of the stock XPI must equal the
memory-control package hash, and each instrumented XPI must match its
reported package hash. The validator compares **every packaged file** against the stock
XPI and allows differences in exactly six known test-only paths:
the probe dataset, background HTML, temporary background settings,
two probe scripts, and `static-net-filtering.js`. The static
network engine must equal the stock engine with only the known buffer
probe appended (normalizing probe line endings). A missing, extra or
changed file outside this allowlist fails even if the report's XPI
hash is modified to match. This enforces identical underlying engine
and filter assets for RAM/CPU and Firefox screening, without
publishing file contents.

A new ninth **per-profile evidence replay** gate requires the exact private
correctness and default-list dataset files as explicit inputs. It verifies
each dataset SHA-256 and rule-derived sidecar, reads every expected Firefox
profile result from each report's own local directory, recomputes
upstream/meBlock per-request decisions, modifier vectors, browser behavior,
timing summaries, engine buffer observations and the default-rule effect
summary with the original comparison oracle, and requires an exact match
against each saved report. Changed summary fields, changed/missing profile
files or a stale dataset fail closed. Only pass/fail is printed; the reports
and request URLs remain local and are never staged.

The input-provenance check also requires the correctness and
default-only reports to match the **current local hashes of all eight
measurement harness modules**, not merely agree with each other.
Correctness evidence must use the exact current browser fixture version
(v9), so any changed synthetic network/cosmetic oracle invalidates stale
reports and requires new isolated Firefox checks.

### Targeted hot-path family gate

The ten checks above qualify the **measurement setup** only. Before
modifying an engine hot path, append one or more
`--target-family FAMILY-ID` arguments to the same command. The optional
eleventh gate uses the **private per-profile correctness results**, not just
a matching summary: it checks independently expected positive and negative
network outcomes per named case or per-index page-effect vectors, with the
without-addon inverse controls. It also requires all ten baseline gates
and their replay to pass. For example, append
`--target-family network-exceptions --target-family cosmetic-has` to
require both families, or `--target-family network-wildcard-anchors` for
the URL-pattern matching path.

The public-safe matrix in `tools/meblock/coverage_matrix.py` currently
distinguishes **17 covered fixture families** spanning hostname patterns,
exceptions, type/party/domain/priority interactions, regexp/wildcards,
cosmetic basic/relational/procedural selectors, `set-constant`, redirect,
removeparam, dynamic firewall and settings persistence. It explicitly
marks **seven unproven families**: other scriptlets, advanced network
modifiers, cosmetic exceptions, list-update/compilation behavior, external
list per-rule contexts, user-filter per-rule contexts and nested frames.
Requesting one of these missing families exits **1**, even when the
general baseline is 10/10 green. Mutation tests deliberately corrupt
individual decisions and vectors to prove a targeted pass becomes a
failure.

For **actual production edits**, also supply the repo-relative module with
`--target-path src/js/static-net-filtering.js` (repeat for every proposed
modified module). The conservative mapping in
`tools/meblock/coverage_matrix.py` automatically expands the affected
families, including **unproven** advanced modifiers, compilation, external
and user-list contexts and nested frames for the static network engine.
Unrecognized or private paths fail closed without being repeated in the
JSON output. Manually specified `--target-family` values are **additive**,
not a way to exclude implied source-file risks. The output exposes
`ready_for_specific_engine_change`, which is **false unless at least one
recognized production path is supplied and every mapped family passes**.
The historical `ready_for_optimization` field alone only indicates
that the requested instrumentation/fixture screen passed, and must
not be treated as permission to change any arbitrary engine module.

With the earlier fixture v9 and archived Firefox 9/9 general evidence,
`--target-family network-exceptions` passes the family diagnostic,
whereas `--target-path src/js/static-net-filtering.js` correctly
returns **NOT READY (9/10)** due to missing advanced-family/context
oracles. A completely unmapped production file also fails. This
does **not** claim exhaustive context coverage for supported families.
Before changing such a hot path, add focused independent Firefox
positive/near-miss and interaction cases for all affected unverified
families; rerun correctness and default-only screening without weakening
the thresholds or injecting fixture rules into performance measurements.

The remaining gates require a 300-second three-pair same-XPI CPU/RAM null
control, real loopback script-resource timing, and a **calibrated amortized
matcher-context p95** on three independent profiles per condition. The p95
range must be at most 50% of its median across those profiles. The gate
fails closed on missing evidence, and outputs only pass/fail categories,
never raw reports, browsing hosts, private paths or filter text.

A pass means only that these **controlled baseline instruments** produced
usable evidence for an experiment. It does **not** prove coverage of every
network/cosmetic rule, representative real-site p95, addon-exclusive CPU or
RAM, or that an optimized candidate is safe to ship. A change to the
filtering engine requires its own affected-rule regression fixtures and
repeatable independent upstream/candidate A/B runs. An individual-call
matcher p95 that Firefox quantizes too coarsely remains unavailable; the
calibrated batched-context p95 is a **different screening metric**, not a
replacement with the same interpretation.

On 2026-10-20, the baseline evolved from the historical six-gate,
1,460-request screen to an **eight-gate** proof using the regenerated
**1,552-request** default-list shard (388 public candidate rules).
The source audit confirms eight packaged public lists, one external
public list and the standard `user-filters` slot, with **zero unresolved
list identities**. Upstream and meBlock agreed on all 1,552 decisions,
modifier masks and browser controls. The first new three-pair run correctly
**failed** the amortized matcher p95 stability check: one upstream profile
had an outlier above the predeclared 50% spread threshold. It remains
recorded as a rejected control, not silently discarded.

A complete, independently repeated three-pair run **passed** with
per-context amortized p95 medians **27.1484 µs upstream and
27.1094 µs meBlock**, with cross-profile spreads **2.45% and 2.31%**.
The same-source fixture-v8 correctness control passed, and the stock
CPU/RAM XPI, actual instrumented XPIs and unchanged filtering modules
passed full package fingerprint and probe-difference validation.
The local audit subsequently returned **9/9 READY for controlled
instrumentation screening only**, after rechecking the saved Firefox
per-profile data and corpus hashes through the new replay gate. This is
not a measured optimization. The earlier failed run documents the
possibility of substantial environmental timing interference. Repeat candidate/control experiments
rather than choosing an unusually favorable run.

The current **fixture v9** then expanded the independently asserted
network oracle to **46 cases** and added four `:has()` plus four
`:matches-css()` cosmetic positive/near-miss targets (16 total
procedural/relational checks). Both Firefox builds and the no-addon
control passed. Changing the fixture now forces exact version and
on-disk SHA-256 agreement for all eight harness modules, so an old v8
correctness report was independently tested and correctly rejected.
The refreshed v9 default-only comparison again had one rejected
three-pair run (52.36% upstream p95 span against a 50% ceiling).
Repeating the *complete* three-pair experiment without changing any
threshold passed with 1.69% upstream and 0.56% meBlock p95 spans,
zero injected default-only rules and **9/9 baseline gates**. Both
results remain part of the local evidence; no favorable individual
profile was cherry-picked and no performance gain is claimed.

Firefox's individual-call matcher timer remains too coarse (~20 µs); the
legacy individual-call p95 is still withheld. Batched-context p95 is
only a *different* controlled screening metric. Local script-resource
p95 has also shown environmental outliers and is **not** stable production
website latency. Real-site request tails, addon-private CPU/RAM
attribution, the public external list's unavailable packaged text,
user-filter text (if any), and advanced modifier/scriptlet context
coverage remain documented limits. A future targeted optimization still
requires affected-rule correctness cases and independent candidate-vs-control
A/B runs; do not optimize while an essential readiness gate is failing.

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
