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

## Selected-default exception override challenge (correctness only)

The **default-list benchmark remains unchanged**: use `--default-lists`
without any injected rules for RAM/performance measurements. A separate,
explicit `--exception-challenge` mode allows the otherwise unobservable
selected default-list exceptions to be examined against **test-only blockers**
inside disposable Firefox builds. It is **not** a default-only performance
baseline.

A prior real Firefox run's *selected-filter-list keys* are pinned to the
public assets in a local XPI. Generate one locally ignored challenge corpus:

```powershell
python tools/meblock/default_rule_probes.py --addon dist/build/uBlock0.firefox.xpi --selected-run dist/build/meblock-comparison/SESSION/upstream-run-1.json --family exception --exception-challenge --shards 1 --out dist/build/meblock-exception-challenge.json
python tools/meblock/compare_original.py --exception-challenge --dataset dist/build/meblock-exception-challenge.json --runs 2
```

Replace `SESSION` with the local ignored prior default-list Firefox session.
The generator selects only eligible **public** hostname exception rules with
conservative request-type and first/third-party options. Each candidate has
four requests, with the matching public-list hostname and a distinct,
synthetic control hostname under reserved `.test`, each from an external
document on `reader.test` and a same-host document. A private
manifest contains boolean expected explicit-allow contexts derived from
the rule's option syntax, not inferred from Firefox results.

The comparison script validates the corpus SHA, source asset fingerprint,
selected-list identity and per-group shape. It then adds two synthetic
blocking control filters **per exception candidate** to each temporary
instrumented XPI: one blocking the matching host and one blocking the
synthetic control host. The original enabled default filters remain present.
The temporary blockers are never written to the source repository or used in
`--default-lists` mode. The reports must identify this as
`exception_challenge_correctness_only=true` with a **nonzero injected
filter-rule count**; an attempt to combine the switches is rejected.

The diagnostic explicitly counts:

- Matching hosts where an expected exception produced result `2` while
  the corresponding synthetic control host was blocked with result `1`.
- Candidates confirmed in at least one tested context, and candidates not
  confirmed.
- Synthetic-host blocking controls that **failed**, with only numeric offending
  indices, never hostnames.

This is evidence of a **context-level exception override**, not unique
attribution to a particular rule; overlapping default rules may affect the
result. A missing override may be shadowed, disabled, or require more
conditions. Failed synthetic-host controls are *not counted as confirmed*; if
any fail, the report identifies partial control coverage rather than claiming
an exhaustive pass. Both builds must still pass exact per-request
decision/modifier and other browser correctness checks. Performance timings
from the challenged XPIs have no optimization-acceptance meaning.

**Real Firefox validation, 2026-10-20:** The pinned public exception
corpus contained **165 eligible exceptions** and 660 request contexts.
Two alternating A/B pairs / four disposable Firefox profiles used
**330 synthetic blocking controls** *only in correctness XPIs*. Each
request's matching host and synthetic control host now use **equivalent
first-party or third-party document-origin relationships**, rather than
reusing the matching host's first-party origin for its control.
Every profile agreed on all 660 request decisions and modifier masks.
**164/165 candidates** showed context-specific overrides;
**177/179 syntax-expected allow contexts** were confirmed, and
**330/330 reserved-host blocking controls** were blocked. One
candidate (numeric index only in private evidence) remains
unconfirmed in its two expected contexts, identically on both builds;
the harness does not rewrite expected outcomes to make it pass.
Verdict **CORRECTNESS-ONLY**, not an optimization result.
A separate real `--default-lists` control verified 0 injected
filters and unchanged decisions/modifier masks.

**Matcher-log diagnostic (correctness mode only):** To investigate an
unconfirmed exception without exposing rule contents, the disposable
Firefox probe records a bit code for each engine match: `1` means the
logged rule has the exact-host-block **text shape**, `2` an important
block, `4` an exception log, and `8` another logged blocker shape.
Codes never contain URLs, hostnames or raw filter text. These are
**logging classifications, not provenance**: code `1` does **not**
establish that the selected match came from an injected test rule.
The comparator strictly validates the code vector and reports numeric
codes only for unconfirmed candidate indices. Non-challenge runs
must produce an empty code vector.

For the historically unresolved candidate, upstream and meBlock both
returned block decision `1` and exact-host log-shape code `1` in
both contexts. A separate *forensic-only* run with
`--exception-challenge --challenge-withhold-target-blocker-index 126`
omitted that candidate's synthetic target blocker while retaining the
reserved-host control, yet observed the same decisions and log-shape
codes. This is **not** evidence of a particular rule's origin or an
optimization; it demonstrates why log text must not be treated as
source attribution. Inspecting the pinned selected **public** filter
text then revealed a matching `$badfilter` directive for the old
exception candidate. The initial 164/165 was a corpus eligibility
problem, **not** a proven filtering-engine regression.

The generator now scans all mapped selected public assets for
`$badfilter` directives matching supported simple-host candidates;
it normalizes the order of their safe request-type/party modifiers
before excluding them. Across the eight mapped public assets,
**99,120** supported candidate forms become **99,107** after
excluding **13** syntactically disabled rules (**12 blocking,
1 exception**). This is a conservative *public-source* screening,
not a runtime proof about the two selected lists without mapped
bundled text or about more complex modifier families.

**Updated real Firefox challenge:** All **164** remaining exception
candidates produced an observed contextual override, all **177/177**
syntax-expected allow contexts were confirmed, and **328/328** reserved
synthetic blocking controls worked, independently across two alternating
A/B pairs / four disposable Firefox profiles. All **656 requests per
profile** matched between upstream and meBlock. The prior 165-candidate
measurement remains historical evidence for the unscreened corpus,
not an unresolved product bug. No production filters or default-only
RAM/performance inputs were changed.

## Independent exception and precedence oracle (test-only fixture v9)

The non-default correctness comparison injects 26 **synthetic,
isolated** static-network rules in two disposable XPIs, and checks
**46 fixed per-index expected decisions** (0 no match, 1 block, 2
explicit allow). Beyond plain blocks, near misses and priority overrides,
the cases cover combined request-type/party options, positive and negative
`domain=` scopes, negated request types, first-party aliases, scoped
exceptions with matching blocker controls, `from=`, case-sensitive
regex URL matching, URL wildcards, trailing `|` anchors and `^` separators.
`important` and `badfilter` remain covered.

Unlike agreement-only upstream/fork parity, these expectations are
**independent of both engine outputs**. A missing vector, extra entry,
non-integer decision or incorrect result at any index causes the Firefox
run to fail. Fixture v9 checks eight `:has-text()`, four `:has()`,
and four `:matches-css()` procedural cosmetic rules, each with
independently asserted present/hidden targets and visible near-miss
controls; the fixture also retains 96 ordinary cosmetics, 32 visible
controls and 12 `set-constant` scriptlets with per-index observations.
The real Firefox upstream/meBlock A/B test passed the v9 network vector,
browser-page effects and no-addon inverse controls on 2026-10-20. This
is correctness-only; timings from these injected-rule XPIs are **not**
part of default-list performance acceptance.

The third-party test document is on a **different registrable site**
from the target. An initial test mistakenly placed both document and
target under the same registrable suffix, correctly failed three
third-party expectations, and was corrected without changing the
expected decisions.

Run the existing correctness harness **without** `--default-lists`:

```powershell
python tools/meblock/compare_original.py --dataset dist/build/meblock-requests-coverage.json --runs 2
```

These 26 network rules, 46 decision cases and sixteen procedural cosmetic
rules are **never injected or executed** in `--default-lists` mode; that
mode still checks only the real enabled subscription lists. Fixture precedence tests do not prove that all
default-list exceptions behave correctly or cover every rule priority
interaction.

## Request probes derived from bundled default-list rules

The original smoke corpus targets a few repeated, known network URLs.
For broader **differential correctness coverage** without changing any
enabled filter lists, generate request records from the public filter-list
text files **already packaged** in a pinned Firefox XPI:

```powershell
python tools/meblock/default_rule_probes.py --addon dist/build/uBlock0.firefox.xpi --shards 256 --shard-index 0
python tools/meblock/compare_original.py --default-lists --dataset dist/build/default-rule-probes.json --runs 2
```

The generator recognizes only conservative `||hostname^` block and
`@@||hostname^` exception shapes, optionally restricted by one request
kind (`script`, `image`, `xmlhttprequest` or `xhr`) and/or one party
qualifier (`third-party`, `3p`, `1p` or `~third-party`). Conflicting,
unknown, or action modifiers such as `redirect`, `csp`, `badfilter` and
`domain=` are **rejected** rather than guessed. Each eligible rule emits
**four request contexts**: matching/near-miss host from both a separate
reader origin and a same-host document origin. A near-miss is not
first-party to the same-host document; this is not an exhaustive party
modifier truth table. All URLs are fed to uBO's static
network matcher **without fetching those hostnames**. Neither tool injects
a filter into the measured default-list builds in this mode.

For stricter coverage tied to the **actual enabled list selection**, pass
a prior **private disposable Firefox run** to the generator:

```powershell
python tools/meblock/default_rule_probes.py --addon dist/build/uBlock0.firefox.xpi --selected-run dist/build/meblock-comparison/SESSION/upstream-run-1.json --shards 256 --shard-index 0
```

For an **exception-focused** smoke run, use the same pinned selected-run
evidence with a separate output path and the `--family exception` selector:

```powershell
python tools/meblock/default_rule_probes.py --addon dist/build/uBlock0.firefox.xpi --selected-run dist/build/meblock-comparison/SESSION/upstream-run-1.json --family exception --shards 1 --out dist/build/meblock-exception-probes.json
python tools/meblock/compare_original.py --default-lists --dataset dist/build/meblock-exception-probes.json --runs 2
```

`--family` can be `all`, `blocking` or `exception`. Each family shards
independently, so `--shards` and `--shard-index` apply only *within* the
chosen family; the manifest preserves allow/block candidate counts and
is rejected if those counters are inconsistent. Unlike ordinary host
blocks, many exception candidates may be shadowed or refer to contexts
not triggered by these four requests. An explicit allow result is counted,
but it does **not** independently prove that the specific exception rule
caused the outcome or correctly overrides a blocker.

Replace `SESSION` with the local ignored session directory. The generator
uses the XPI's public list registry to map the browser's selected list keys
to packaged public asset files. It records only a **SHA-256 of the selected
keys** and aggregate counts, not the raw list keys or any browser URLs.
The comparator checks that hash against every live Firefox run and also
compares the aggregate selected bundled-asset content fingerprint against
the actual disposable builds of both upstream and meBlock **before**
running Firefox. This prevents silently using a request corpus from a
different public asset snapshot or treating disabled lists as selected.
The source snapshot test confirmed the same digest across the local stock
XPI and both instrumented Firefox XPIs. Currently the
default Firefox profile selects 10 lists: **eight map to bundled
public assets, one public external list has no packaged text, and one is the
`user-filters` slot**. The generator now reports these as distinct aggregate
source categories, and records an additional count for unknown/unresolved
sources (which must be zero at baseline-readiness acceptance). The external
list and any actual user-filter text are **outside this generator's
coverage**; the default-only runtime comparison still uses the complete
engine-loaded default list selection, without synthetic rules. No private
filter text, list URLs or raw names are included in public reports.

Deterministic, disjoint shards are indexed from zero. All rule candidates
from the chosen bundled files are assigned exactly once across the chosen shard
count; each run checks every generated request decision and modifier mask
against the pinned upstream engine. The generator fails when a selected
shard would exceed its safe request cap. Run other `--shard-index`
values separately to cover more candidates. The comparator also reports
**per-candidate matched-versus-near-miss observed effects** (including
modifier decisions) separately for external-reader and same-host document
origins. A near-miss URL is not necessarily first-party even when its
matching-host counterpart is, so these are origin-context comparisons,
not isolated third-party modifier tests. This counts
how many candidates actually produced a distinguishable outcome in the
tested context; it **does not prove which rule caused the difference**
or that a non-distinguishable rule is inactive. In a prior
one-shard live Firefox control, all **365/365** candidate rules had a
distinguishable decision/modifier outcome in at least one of two contexts.
Of 730 matching-host requests, 728 were blocked; of 730 near-miss
requests, three were also blocked by the complete default-list engine.
These results are **not** a per-rule independent correctness oracle or
representative browsing traffic. The resulting dataset and
numeric-only coverage manifest remain in **Git-ignored `dist/build/`**,
and the comparison validates that manifest's request count and SHA-256.

**What this does not prove:** A bundled file may include lists or lines
not enabled at runtime, and a filter may be shadowed by an exception or
another rule. The count of eligible candidates is therefore **not the
count of enabled or individually proven rules**. Comparing two engines
does not prove that a rule fired, or that their shared behavior follows
filter syntax semantics. Unsupported network patterns, modifiers,
procedural cosmetic filters, scriptlets, dynamic rules and interactions
need separate targeted tests. The manifest exposes the exact eligible
and excluded/duplicate counts; never report this as exhaustive coverage
of all default-list rules or all possible contexts.

## Default-filter benchmark without injected test rules

The current optimization baseline uses default enabled filter lists from
the pinned upstream and meBlock builds, with no added test filters:

```powershell
python tools/meblock/default_rule_probes.py --addon dist/build/uBlock0.firefox.xpi --selected-run dist/build/meblock-comparison/SESSION/upstream-run-1.json --out dist/build/default-rule-probes-mixed.json --shards 256 --shard-index 0
python tools/meblock/compare_original.py --default-lists --dataset dist/build/default-rule-probes-mixed.json --runs 3
```

Replace `SESSION` with a previous ignored Firefox default-list run in
the local build directory. Both corpus JSON and its manifest remain ignored
and must be pinned to the selected list keys observed in that disposable
profile. For performance decisions use **at least three independent
same-build control pairs** to validate the amortized diagnostic; actual
different-build change claims need a separate candidate/control design
with substantially more independent pairs.

`--default-lists` leaves the built-in filter file untouched by the
regression fixture. Both test builds still receive **identical disposable
measurement probes** and have automatic filter updates disabled to keep
the comparison pinned; they are not release XPIs suitable for total-RAM
measurement. Only these disposable, instrumented comparison profiles also set
`privacy.reduceTimerPrecision=false` to allow microsecond-scale
matcher diagnostics; this is not applied to production Firefox profiles.
The request dataset is synthetic traffic, but the matching
engine uses the actual default enabled lists. For **untyped public
hostname rule candidates**, the current generator rotates script, image,
XHR and stylesheet request contexts deterministically. An explicit rule's
request type still wins; exception-challenge fixtures retain their
separate, fixed semantics. The public-safe sidecar records only counts per
request type, never the selected hostnames. A new baseline-readiness gate
requires **all four types to contribute at least 10% of the default-only
corpus**; it cross-checks the numeric counts against the actual dataset
and fails closed for older script-skewed evidence. This is an intentional
synthetic **balanced diagnostic**, not a real-world traffic distribution.
It checks each dataset request's decision and modifier mask against
upstream, **not the behavior of every enabled filter rule under every
possible context**.
The loopback page should behave like the without-addon control because
the additional fixture-specific rules were not installed. Therefore this
mode does not claim to check the independent positive blocking fixture.

For exhaustive checks of the small controlled cosmetic/scriptlet fixture,
run the comparison **without** `--default-lists`. That mode injects test
filters and is for correctness testing, not the stock-filter baseline.
For retained RAM use **uninstrumented** XPIs with
`memory_experiment.py --default-lists` instead.

## What is checked

| Gate | Check | Interpretation |
| --- | --- | --- |
| Git and assets | Exact commit hashes, asset revisions, dataset SHA-256, harness file hashes, fixture version and selected filter-list keys | Detect mismatched inputs across both builds and preserve the exact test implementation |
| Build regression | Both Firefox builds must succeed; resulting file hashes are compared | All modified bundled files are visible |
| Runtime | Separate Firefox profiles, installed addon ID and filter engine readiness | Code actually ran in Firefox |
| Original benchmark | Calls `benchmarkStaticNetFiltering` in both builds | Verifies upstream's engine benchmark completes |
| Behavior regression | Full per-request decision vector and six modifier bitmasks | Compares block/allow outcomes and each redirect, removeparam, URL skip, CSP, permissions and replace decision by request index; also checks aggregate counts against upstream |
| Browser behavior fixture | Independent Firefox-without-addon negative control, then both test builds | Asserts actual script, image, iframe, XHR, noop redirect, CSS hiding, scriptlet injection and query-parameter removal behavior |
| Dynamic firewall state | Set block, allow, noop and destination override on an isolated source hostname, then roll back | Checks actual uBO session firewall API and asserts no leftover rule |
| Settings reload and restore | Toggle real `colorBlindFriendly` user setting, persist, reload disposable extension, then restore | Fails if persisted value does not survive the reload or cannot be restored |
| Built-in extra benchmarks | Cosmetic and scriptlet benchmarking where dataset contains document requests | Separate diagnostic timings |
| Performance | Two in-session warm-ups and five measured engine passes, repeated across alternating A/B sessions | Median and range, per-pair percentage change, paired bootstrap interval, byte-identical control verdict; at least 10 A/B pairs required for a timing candidate |
| Request matcher p95 | Three additional instrumented passes sample every `matchRequest` call with two `performance.now()` reads; the page context is prepared before timing | Per-run nearest-rank p50/p95/p99, zero-sample count, inferred clock step and empty timer-pair overhead. The diagnostic p95 is withheld if it is fewer than three clock steps, or mostly zero samples. **Instrumented wall time**, not addon CPU, actual request lifetime, or production website p95 |
| Local page navigation p95 | Two warm-up and 20 measured loopback fixture navigations per Firefox session, checked against the complete expected browser behavior | Firefox `loadEventEnd` and DOMContentLoaded p95, with strict sample validation. Includes browser overhead and is not real-site or request-level p95 |
| Startup diagnostics | Extension-reported engine-ready time, when available | Recorded separately from filtering speed; not browser startup overhead |
| Firefox processes | Optional idle CPU time and summed RSS after benchmark | Diagnostic only; not per-extension attribution |
| Offline tests | `python -m unittest discover -s tools/meblock -p test_*.py -v` | Catches regressions in the harness and comparator |

**Coverage limits:** These fixtures exercise selected network and page-side effects and the internal session firewall state. Browser-wide dynamic-firewall blocking, CNAME uncloaking, HTML filtering, filter subscriptions, persistence across a full Firefox process restart, large-page behavior, all redirect resources, advanced scriptlets and private memory attribution require separate tests. The settings check exercises persistence across **extension reload**, not process restart. The upstream cosmetic/scriptlet microbenchmarks still expose only aggregate timings. Synthetic datasets do not establish real-user speedups.

## Deterministic page-side workload

The disposable Firefox fixture version 9 has **96 ordinary cosmetic
rules** (32 each using class, ID and attribute selectors). Each has a matching
DOM target which must be hidden, and a separately indexed **near-miss
control** which must stay visible. Another 32 unrelated DOM controls must
also stay visible. It additionally covers **eight procedural `:has-text()`,
four `:has()` and four `:matches-css()` rules** with independent matching
and near-miss nodes, plus **12 set-constant scriptlets** with targeted and
untouched near-miss properties.

Firefox checks exact fixed-order boolean vectors rather than aggregate
counts: presence, hiding and near-miss visibility for the 96 ordinary
selectors and all 16 procedural/relational selectors, 32 general visibility
checks, 12 scriptlet results and 12 scriptlet near-miss properties. Without an
addon, all 112 cosmetic targets must be present and visible; each indexed
near-miss/general control stays visible, and no scriptlet target is set.
With the disposable correctness addon, all 112 cosmetic targets must be
hidden and all near-miss controls remain visible; all 12 scriptlet targets
must be set. Scriptlet near-miss properties remain untouched in both cases.

Every boolean position is compared to the expected result after the initial
load and after **each** navigation used to measure local page-load p95.
A missing DOM node, malformed vector, false positive or false negative
rejects the run. Failures identify the field and failing rule indices.
Counts remain diagnostics only: passing a count cannot compensate for
incorrect per-rule behavior. Unit tests deliberately change individual
positions without changing reported counts and require the comparison to
fail.

This is a controlled behavior fixture, **not** a demonstration that every
possible option, filter interaction or every default-list rule has been
exercised. A synthetic 600k corpus exists only for opt-in stress tests,
not baseline performance acceptance. The fixture covers three selected procedural/relational selector families,
not arbitrary procedural cosmetics, advanced scriptlets or normal user
browsing. Only compare navigation timings with
the **same fixture version** and equivalent pinned inputs.

## Calibrated amortized matcher context p95 (test-only)

Single `matchRequest` wall-time samples in this Firefox environment are
often shorter than the `performance.now()` clock step (about 20 µs).
The existing `meblock-request-latency-v2` result is **retained as a
diagnostic** and continues to report individual-call p95 as unusable when
quantized. Do not interpret zero samples as zero-cost requests.

The separate `meblock-batched-context-match-v2` probe repeats each fixed
request context **512 times in a timed batch** after setup. Every repeated
decision is checked against its own control decision. It records three passes,
clock-step calibration, empty-loop controls and fixed-order elapsed batches,
but publishes only aggregate numeric percentiles and no URLs or rule text.
The Python validator requires exact field counts, finite ordered quantiles,
consistent decisions, and batches at least eight clock steps long; the p95
batch must exceed empty-loop p95 by fourfold and clock-step by sixteenfold.
The updated report additionally summarizes per-type batch samples, with
the exact count for each request type derived from the **pinned request
dataset**, so a missing category or forged sample count fails validation.
No URLs or raw filtering rules are included in the per-type metrics.

A failed aggregate **or category-specific** calibration withholds that
screening metric. In the balanced default-list corpus the readiness
validator requires script, image, XHR and stylesheet to **each** have
at least three independently measured Firefox profiles, calibrated
per-type p95, and cross-profile p95 spread at most 50%. A stable
aggregate can no longer hide an unstable request-type cohort.
This does **not** provide individual-call p95 or represent production
website traffic.

`comparison.batched_matcher.*.median_run_p95_amortized_context_us` is the
nearest-rank p95 **across per-context averages** (`batch elapsed / 512`),
not p95 of individual calls, nor a claim about browser resource p95.
It suppresses within-context jitter and includes loop/timer overhead.
The readiness check requires at least three *independent Firefox profiles
per identical-build condition* plus the separate 300-second default-list
CPU/RAM null control; one pair is diagnostic only. Cross-version A/B
comparisons must preserve the same workload and method.

## Controlled Firefox page navigation p95

The Firefox integration runner now measures a synthetic loopback page in
addition to static network matcher speed. After engine readiness and a
successful browser-behavior check, each fresh Firefox profile opens the same
fixture for **two warm-ups and 20 measured navigations**. It reads
`PerformanceNavigationTiming.loadEventEnd` and
`domContentLoadedEventEnd` inside Firefox. It also reads the duration
of exactly one `/test-allowed.js` `PerformanceResourceTiming` entry per
navigation. The fixture server sends `Cache-Control: no-store` and the
page-side assertion requires the script to execute. These values exclude
Selenium command transit time. The runner checks blocked script, image,
frame, CSS and scriptlet effects after **every** navigation. Missing or
duplicate target resource entries, zero/non-finite timing, wrong page
behavior, incomplete samples or reversed milestones reject the run.

The report retains only numeric timings, never resource URLs. It computes
nearest-rank p95 from the 20 browser-observed local resource durations and
navigation milestones, then reports per-run p95 and observed spread under
`comparison.fixture_navigation`. Each profile contributes one run; use
independent, alternating Firefox profiles for comparisons. A same-build
comparison is a negative control, not a speedup claim. The without-addon
Firefox control also executes 20 measured navigations with the inverse
blocking expectations, so the resource instrumentation itself must work
without an installed extension. A real upstream/meBlock fixture-v9 pair
and its unfiltered control passed this measurement on 2026-10-20.

These are **controlled loopback document loads and script fetches**,
including browser, extension and test HTTP server work. A local resource
fetch duration is a browser-observed request measurement, not static-filter
matcher CPU time, public-website request p95, a representative browsing
distribution, or addon-exclusive CPU. Representative real-page and
request-level latency remain separate optimization acceptance gates.

## Fast network-engine buffer screening

For quick memory triage, the Firefox comparison runner appends
`tools/meblock/engine_buffer_probe.js` **only to disposable instrumented
test builds**. Production `src/js/static-net-filtering.js` and release XPIs
are unchanged. Once Firefox reports engine readiness, it reads four
engine-owned backing-buffer capacities directly:

- `filter_data`, `origin_trie`, `destination_trie`, and `pattern_trie`.
- The sum of these known allocated buffers, the used portion of filter data,
  the count of JS filter references, and whether each trie uses WASM.

The runner rejects invalid, non-numeric, or changing buffer readings across
sessions of the same build. Its JSON `comparison.engine_buffer_footprint`
contains only fixed numeric counters and WASM booleans, alongside the
byte delta between original and fork. Use pinned public filter assets and
matching compiled filter counts. A useful negative control compares
byte-identical builds: its known-buffer delta should be **exactly zero**.

```powershell
python tools/meblock/compare_original.py --dataset dist/build/meblock-requests-coverage.json --runs 3
```

This is a **fast structural memory measurement**, not a measurement of the
full extension heap. It counts known network-engine typed-array and WASM
backing buffers, not JS object graphs, filter-reference storage, other
extension components, Firefox allocators, or native allocations. Reading
the counters is cheap, but packaging both builds and starting fresh Firefox
sessions still takes time. Buffer equality does not prove that total
retained RAM is equal. A changed buffer size identifies a candidate for
profiling, not a proven memory optimization.

For changes outside these buffers or final acceptance, use the uninstrumented
`memory_experiment.py` and isolated `about:memory` captures. Start with a
short, explicitly **EXPLORATORY** paired screen. Reserve the slower three or
more independent 300-second A/B pairs, same-XPI noise control, and realistic
page behavior/latency checks for promising candidates. A shorter settle time
must not become a claimed steady-state memory result.

Run regression checks locally by default. `.github/workflows/meblock-regression.yml` uses **manual `workflow_dispatch` only**, so pushes and pull requests do not start its GitHub Actions jobs. When a hosted Windows check is needed, open GitHub Actions, select **meBlock regression tooling**, then choose **Run workflow**. The default manual run covers Python unit tests, JavaScript syntax and deterministic corpus generation. Enable `firefox_integration` only for the optional real Firefox comparison. That job fetches official upstream source and public filter assets into a fresh test environment. No raw JSON evidence is uploaded to this public repository. Neither manual job replaces the full local multi-run performance gate, and the hosted Firefox job's results must be verified before being cited.

## Extension memory attribution

Before accepting any optimization, review the [baseline coverage audit](meblock-baseline-audit.md). It records verified test gates, known gaps and privacy restrictions. **A working instrument is not automatically a reproducible performance baseline.**

Use the `about:memory` export workflow in [meBlock memory measurements](meblock-memory.md) to measure the extension's actual reporter subtrees after filter readiness. `tools/meblock/analyze_memory.py` parses Firefox `.json.gz` files and compares equivalent reporter scopes without double-counting parent and child allocations. The optional process-wide RSS numbers here are **not** equivalent to those measurements.

## Safety and isolation

- Source snapshots are created via `git archive` under ignored `dist/build/meblock-comparison`.
- The probe, its dedicated state module, embedded dataset and the controlled filter rules in `tools/meblock/browser_fixtures.py` are injected identically into both disposable test packages. Nothing is added to source files or release packages. Build equivalence is checked **before** instrumentation.
- The HTTP collector listens on `127.0.0.1`, uses a random per-session token for uploads, bounds response size, and closes after execution.
- Every Firefox session uses Selenium's temporary profile, preserving your personal Firefox profile. A Firefox-without-addon negative control first verifies that blocked targets really load in the absence of filters; each instrumented profile must then satisfy every expected page observation. The state module changes only the disposable profile's `colorBlindFriendly` preference and a fixture-only firewall rule, reloads the extension, and verifies restoration.
- The probe does not require privileged Marionette or `-remote-allow-system-access`.
- Output includes potentially sensitive input URLs when it needs to explain a mismatch; keep reports private unless the dataset is sanitized.
- Each test package keeps the Firefox extension ID from its source manifest. The runner verifies that Firefox installed that exact ID, so the fork may use a different ID after rebranding. Neither package is installed into your regular Firefox profile.

## Results

The script writes a separate dated session directory under `dist/build/meblock-comparison/`.

- `report.json`: verdict, source and harness SHA-256 hashes, fixture version, file diff, environment inputs, negative control and timing summaries.
- `upstream-run-N.json` and `meblock-run-N.json`: raw engine results, decision arrays, modifier mask arrays, browser observations and timing samples.
- `upstream/checkout/dist/build/build.log` and equivalent fork path: build logs.
- `upstream/instrumented.xpi` and `meblock/instrumented.xpi`: diagnostic test packages, **not distributable releases**.

Interpret results conservatively: five or more runs per build for behavior smoke tests, and **at least 10 paired A/B runs** before classifying a timing difference as a candidate. The timing comparator reports each B/A ratio and a deterministic bootstrap 95% interval of the median log ratio, with a 3% practical-change threshold. The interval is exploratory, not a guarantee, and assumes comparable runs; repeat on realistic workloads and hardware. When both built trees are byte-identical, any apparent speedup is environmental noise, not a change attributable to meBlock.

To evaluate a genuine modified build, run `python tools/meblock/compare_original.py --runs 10 --dataset dist/build/meblock-requests.json` with unchanged environment and real-world data. Do not claim improvement without evaluating performance profiles and realistic browsing workloads. A direct engine microbenchmark is only one component of total page-load cost.

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
CNAME, HTML response filtering, browser-level dynamic firewall behavior, settings
across a full Firefox process restart, and extension-private memory accounting
remain separate work.

### Dynamic-firewall and extension-reload control, 2026-10-19

Fixture version 3 was verified against upstream and meBlock in five paired
sessions, using the same 10,000 synthetic requests per run. The test-only
background probe changes the real `colorBlindFriendly` user setting, waits for
persistent storage, calls `browser.runtime.reload()`, verifies the saved
value after the extension is initialized again, and restores the original
setting. Separately, it temporarily sets source-scoped firewall block, allow,
noop and destination-override rules and verifies exact outputs and rollback.

- All network decisions, six modifier masks, original browser behavior fixtures,
  firewall state and settings reload/restore checks passed for both builds.
- Both rebuilt extension bundles were byte-identical before instrumentation.
- Median filtering cost: 15.80 µs/request upstream, 15.44 µs/request meBlock.
- Paired median time change: −4.48%; exploratory paired bootstrap 95% interval:
  **−19.58% to +41.46%**. Five pairs are insufficient to classify a timing
  candidate, and these two extension bundles are identical, so this is
  explicitly a **control run**, not a demonstrated optimization.
- Local evidence: `dist/build/meblock-comparison/20261019-011526-fe15d2/report.json`.

The engine-ready diagnostic is collected after the test-only extension reload,
so it must **not** be interpreted as cold Firefox or extension startup time.

## Failure handling

The test exits nonzero if either source cannot build, the addon or engine cannot initialize, required output is missing, or per-request behavior differs. It saves `report.json` with `status: failed` and an error message. A run that cannot reach the engine is **not** a performance pass. The generated XPI instrumentation is disposable and can be regenerated.
