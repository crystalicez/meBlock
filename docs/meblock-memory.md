# Memory measurement for meBlock

Firefox `about:memory` is the primary memory attribution tool. The existing
Firefox process RSS benchmark is a secondary system diagnostic, not a measure
of one extension. The reported **about 60 MB** for roughly **600,000 filters**
is a user-observed starting point, **not yet a reproducible baseline** or a
verified attribution to a filtering engine component.

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

For strictly paired, alternating AB/BA experiments, run:

```powershell
python tools/meblock/memory_experiment.py --pairs 3 --settle-seconds 300 --base-network-filters 252243 --extra-synthetic-hostnames 347757
```

The paired runner compares the original test XPI and the same XPI with
the synthetic additions, using a **new Firefox profile for each condition**.

For a **real upstream-versus-fork memory comparison**, use two separate
uninstrumented XPIs built with the *same pinned filter assets*. This mode
checks an equal number of compiled network and cosmetic filters, identical
selected-list fingerprints, a stable source hash per build, and a matching
Firefox version. Both builds receive the **same** synthetic additions
when requested; a build-to-build memory test is never confused with a
stock-versus-more-rules test.

```powershell
python tools/meblock/memory_experiment.py --addon dist/build/upstream-test.xpi --candidate-addon dist/build/meblock-test.xpi --pairs 3 --settle-seconds 300 --base-network-filters 252243 --extra-synthetic-hostnames 347757
```

These named example XPI inputs must be built locally first, and the actual
stock network count must be verified for those packages. Do not use
`instrumented.xpi` as a production memory baseline. The build comparison
results are still **Extensions-process-wide**, not private extension heap
figures.
It rejects changes in the source XPI hash, Firefox version, enabled list
fingerprint or cosmetic filter count. It saves a sanitized summary locally
and never uploads raw reports. Even with three valid pairs, the verdict
is **process-wide measurement only**, not proof of extension-private
memory savings or of preserved lookup latency.

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
