# Memory measurement for meBlock

Firefox `about:memory` is the primary memory attribution tool. The existing
Firefox process RSS benchmark is a secondary system diagnostic, not a measure
of one extension. The reported **about 60 MB** for roughly **600,000 filters**
is a user-observed starting point, **not yet a reproducible baseline** or a
verified attribution to a filtering engine component.

## Choose the least noisy measurement that answers the question

For small changes to static network filtering structures, screen with the
read-only **known-buffer byte counters** from
`tools/meblock/compare_original.py`. The comparison tool adds a small
export to the static network filtering module in **disposable test builds
only** and reads actual loaded typed/WASM backing-buffer capacities.
These counters are exact for the four selected allocations and can be
checked in a single Firefox run. Repeat across fresh profiles to verify
identical buffer counts. In a six-profile same-build control with the test
fixture and automatic filter updates disabled, all three sessions per build
reported 17.5 MiB of these four backing buffers and a zero byte delta.
This is not an estimate of the complete extension heap or a 600k-rule
memory baseline. The counters do **not** cover all JS objects,
other engine caches, the cosmetic/scriptlet engines, or Firefox's native
heap. See [fast buffer screening](meblock-comparison.md#fast-network-engine-buffer-screening).

For an early whole-process RAM signal, run a short offline A/B screen
with `memory_experiment.py --pairs 3 --settle-seconds 30` plus the required
`--base-network-filters` and either `--candidate-addon` or
`--extra-synthetic-rules` arguments. Its verdict
must remain **EXPLORATORY**, regardless of the observed difference.
Use a same-XPI-versus-itself negative control to estimate startup/process
noise before treating differences of a few MiB as real. Do not interpret
repeated snapshots from one profile as independent pairs.

For a final RAM comparison, capture with **uninstrumented** XPIs, at
least three alternating independent A/B pairs and at least 300 seconds
after verified engine readiness for **each** condition. Keep the
workload and filter state pinned. Do not replace this check with
engine buffer counts or shorter Firefox sessions. Even long captures
currently measure the whole Extensions process, not extension-exclusive
retained RAM. Candidate confirmation must also check request latency
and page behavior.

## Capture an actual Firefox memory report

1. Install the **uninstrumented** upstream uBlock Origin or meBlock Firefox
   package in an isolated Firefox profile. Use the same Firefox version,
   selected filter lists, user settings and dataset for both builds. Do not
   use the benchmark's `instrumented.xpi` files: their extra probes and reloads
   can affect memory.
2. Wait until all lists have finished loading. Keep the browser in the same
   test state. For example, keep a blank tab open and capture snapshots after
   5, 10 and 15 minutes, measured from filter readiness.
3. Open `about:memory`. Click **Measure and save…** to save a `.json.gz`
   report for each point. Do not rely on a screenshot or on Firefox's overall
   resident memory reading.
4. Save a second run after restarting Firefox using the same profile. Record
   the Firefox version, selected filter-list revisions, addon revision, number
   of enabled rules, elapsed minutes and whether **Minimize memory usage**
   or GC/CC was triggered. Do not mix pre-GC and post-GC reports in a pair.
5. Repeat across several alternating upstream/meBlock runs when comparing
   changes. Prefer the same user workload and no browser updates during runs.

**Privacy:** Firefox memory reports may contain tab URLs, extension IDs and
other browsing metadata. Consider Firefox's **anonymize** option before
sharing them; it may also make reporter names harder to identify.
The analyzer below processes files locally and does not upload them.

## Capture a report without using your personal Firefox profile

The local capture tool launches a **fresh disposable Firefox profile**,
installs only the specified test XPI, waits for uBO's actual live
`netFilterCount`, `cosmeticFilterCount`, at least one enabled list, and
`isUpdating=false`. It requires several stable observations before starting
the timer, waits **300 seconds after readiness**, and checks the live
filter counts again after the memory dump. If the engine changes, the
baseline fails rather than silently accepting a mismatched run.

The tool uses Mozilla's memory dumper with **anonymization on** and
**GC/minimization off**. Raw memory reports are saved only under ignored
`dist/build/private-memory/`; never upload, commit or attach them to a
public GitHub issue.

**Pinned-list mode** runs this *test profile only* with external networking
disabled **before installing the XPI**, then persists `autoUpdate=false`
through uBO's own dashboard API. This setting change is confined to the
temporary Firefox profile; it is checked again before and after capture,
without modifying the XPI or the user's Firefox profile. Use this mode
for public bundled-list experiments, not to represent all normal online
browsing. The `--expected-network-filters` value is
specific to the **tested package**, not a permanent universal constant.

```powershell
python tools/meblock/capture_memory.py --offline --settle-seconds 300 --runs 3 --expected-network-filters 252243
```

To exercise **600,000 network filters in total**, take the verified
stock engine count (252,243 for this pinned package) and inject
**347,757 additional deterministic hostname rules** into a disposable
test XPI. The engine must report exactly 600,000 compiled network
filters before and after measurement. This is still a synthetic,
hostname-heavy dataset, **not the user's real 600k filter mix**.

```powershell
python tools/meblock/capture_memory.py --offline --settle-seconds 300 --extra-synthetic-hostnames 347757 --expected-network-filters 600000
```

For a second, deterministic **mixed network-rule** workload, use
`--synthetic-rule-mix mixed-network`. Each 100 injected rules contain 55
hostname blocks, 20 path-specific blocks, 15 script-specific blocks, nine
image/third-party blocks and one regex. All fixture hosts use the reserved
`.invalid` test domain. The default remains `hostname` to preserve older
baseline comparisons. This mix measures more network matcher shapes, but
still does not reproduce the user's subscriptions, cosmetic rule composition,
scriptlets, or live webpage activity.

```powershell
python tools/meblock/capture_memory.py --offline --settle-seconds 2 --extra-synthetic-rules 347757 --synthetic-rule-mix mixed-network --expected-network-filters 600000
```

For strictly paired, alternating AB/BA experiments, run:

```powershell
python tools/meblock/memory_experiment.py --pairs 3 --settle-seconds 300 --base-network-filters 252243 --extra-synthetic-hostnames 347757
```

Add `--synthetic-rule-mix mixed-network` to the paired runner when the
mixed fixture is the intended workload. Do not compare its totals directly
with hostname-only results as though they used identical filter inputs.

The paired runner compares the original test XPI and the same XPI with
the synthetic additions, using a **new Firefox profile for each condition**.

For a **real upstream-versus-fork memory comparison**, use two separate
uninstrumented XPIs built with the *same pinned filter assets*. This mode
checks an equal number of compiled network and cosmetic filters, identical
selected-list fingerprints, **matching SHA-256 of all bundled public text filter
assets**, a stable source hash per build, and a matching Firefox version.
The bundled-asset hash protects against changed list content even if the
compiled rule *count* stays the same; it does not fingerprint separately
downloaded user subscriptions. Both builds receive the **same** synthetic additions
when requested; a build-to-build memory test is never confused with a
stock-versus-more-rules test.

```powershell
python tools/meblock/memory_experiment.py --addon dist/build/upstream-test.xpi --candidate-addon dist/build/meblock-test.xpi --pairs 3 --settle-seconds 300 --base-network-filters 252243 --extra-synthetic-hostnames 347757
```

These named example XPI inputs must be built locally first, and the actual
stock network count must be verified for those packages. Do not use
`instrumented.xpi` as a production memory baseline. The build comparison
results are still **Extensions-process-wide**, not private extension heap
figures. The runner rejects inconsistent build inputs, list fingerprints,
bundled filter asset hashes, rule counts, Firefox versions, and enabled
automatic list updates. It saves aggregate-only results locally and never
uploads raw reports. Even with three valid pairs, the verdict is
**process-wide measurement only**, not proof of extension-private
memory savings or preserved lookup latency. A result remains
**EXPLORATORY** until it has at least three independent A/B pairs and
each condition was held for at least 300 seconds after filter readiness.
Short Firefox smoke runs cannot be labeled steady-state baseline measurements.

The summary reports each paired RAM difference, the range, median absolute
deviation, an independently resampled 95% interval for the paired median,
and a two-sided exact sign-test result. Three pairs cannot establish a
directional RAM difference from this test: even three perfectly consistent
nonzero differences have a two-sided sign-test p-value of 0.25. A directional
**process-wide** signal needs at least six independent pairs, p < 0.05,
and a resampled interval entirely to one side of zero. None of these
statistics provide addon-only attribution or remove Firefox runtime noise.

For a zero-change negative control, use the **same uninstrumented XPI** as
both `--addon` and `--candidate-addon`. The summary marks it as
`identical_xpi_negative_control=true` and, after qualifying steady-state
runs, labels it `CONTROL` regardless of how large or consistent the RAM
difference appears. Short controls stay `EXPLORATORY` and explicitly display
`IDENTICAL-XPI NEGATIVE CONTROL` in the verdict and `control-A`/`control-B`
labels. The report includes the identification field regardless of duration.
A real candidate must beat the control noise in comparable sessions and pass
filtering correctness and latency checks
before any optimization claim.

Firefox system-level scripting is enabled **only** in this isolated test
session via geckodriver's `--allow-system-access` flag so the tool can ask
`nsIMemoryInfoDumper` to write a full anonymized JSON.gz report. The driver
remains loopback-only and is shut down after the capture. No change is made
to a production extension build. Do not run this tool against a personal
Firefox profile.

This report is not guaranteed to correspond to the about:memory 60 MB seen
in a regular Firefox profile. Real filter-list composition, actual enabled
filter count, background pages, tab activity and extension preference state
affect the result.

## Analyze and compare reports

First find reporter paths relevant to the extension, using the exact file
saved by Firefox:

```powershell
python tools/meblock/analyze_memory.py inspect dist/build/memory/upstream-5m.json.gz --find "add-ons"
python tools/meblock/analyze_memory.py inspect dist/build/memory/upstream-5m.json.gz --find "moz-extension"
```

`inspect` is **not a total**: parent and child reporters may describe the
same bytes. Find the smallest stable subtree that corresponds to the
extension, for example an `explicit/add-ons/<addon-id>` path in reports
where Firefox provides that section. Current Firefox versions can use
different paths and separate extension processes. Do **not** automatically
assume every process's `explicit` tree is attributable to the extension.

Summarize one exact reporter tree:

```powershell
python tools/meblock/analyze_memory.py summarize dist/build/memory/upstream-5m.json.gz --prefix "explicit/add-ons/uBlock0@raymondhill.net"
```

For an A/B comparison, use the equivalent subtree from each report.
If the identities or reporter roots differ, specify both prefixes explicitly:

```powershell
python tools/meblock/analyze_memory.py compare dist/build/memory/upstream-5m.json.gz dist/build/memory/meblock-5m.json.gz --prefix "explicit/add-ons/uBlock0@raymondhill.net" --after-prefix "explicit/add-ons/meBlock@example.invalid"
```

The automated capture also includes a **privacy-safe, allowlisted numeric
category breakdown** of the Extensions process (for example
`window-objects` and `js-non-window`). It never copies raw reporter paths,
hostnames or URLs into that aggregate. The remainder is grouped as
`other_process_allocations`. These buckets are **not** per-extension
allocations and must not be interpreted as a unique JS/WASM/native breakdown
without additional profiling.

The tool reads Mozilla's compressed or plain JSON memory reports. It counts
only byte-valued measurements and sums **leaf reporters** rather than adding
an aggregate parent to its children. Unavailable byte reporters are counted
separately and cause a comparison to fail rather than silently claiming
a complete total. It reports bytes, MiB, process attribution, category
breakdown, and signed differences. The output is JSON for archiving.

To investigate individual allocations locally, `summarize` also accepts
`--show-paths`, but the output may disclose browsing information.

## How to interpret the numbers

- The `about:memory` reporter subtree measures the allocations covered by
  **that scope**, not necessarily all of meBlock's JS heap, WASM linear
  memory, process RSS or other browser allocations.
- Some memory is unclassified or shared and cannot be attributed precisely
  to a single extension. Separate process totals must not be added to a
  reporter subtree as if they were independent.
- If both builds use the same compiled filtering engine, time variation and
  unrelated browser allocations are not evidence of a fork improvement.
- A single 60 MB observation cannot distinguish network rules, cosmetic
  selectors, scriptlet resources, JS overhead or WASM buffers.
- To claim a reduction, compare identical scopes across reproducible runs
  **and** pass the per-request behavior and modifier checks in
  `docs/meblock-comparison.md`. Also inspect filtering latency, p95 and
  startup readiness. This tool does **not** claim a RAM reduction itself.

Mozilla explains the export format, tree semantics and privacy options in
[about:memory documentation](https://firefox-source-docs.mozilla.org/performance/memory/about_colon_memory.html).

## Acceptance criteria for an optimization experiment

- Same pinned 600k-list configuration, extension settings and Firefox build.
- At least three independently collected memory reports per condition after
  filter readiness, with matching process/report roots.
- Report 5/10/15-minute memory and restart behavior, not one screenshot.
- State the baseline, absolute delta and percentage delta, with raw reports
  retained privately and the sanitized summaries in the comparison evidence.
- Do not accept a change unless filtering behavior is unchanged, and median
  and p95 request-matching latency have no meaningful regression.
