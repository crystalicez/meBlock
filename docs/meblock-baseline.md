# Firefox baseline for meBlock

Measured on 2026-10-18. This is a **pre-optimization baseline** for the unmodified uBlock Origin source imported into meBlock. These browser-wide numbers are exploratory and are not the primary performance gate. For repeatable filtering-engine measurements, see [Filtering benchmark for meBlock](meblock-filtering-benchmark.md).

## Exact source and environment

- meBlock commit: `023f1185d0cb5bf4e4b51eba31788ba1188dacc9`.
- Upstream code: `gorhill/uBlock` master `9e0dff715df9889389e6a6ddf62f36c24a5cb729`.
- uAssets main: `854631e40bc61446923487d20bfd426d97d7b2d5`.
- uAssets gh-pages: `863b58deafee12fb1023a91a1b441b8f386bc725`.
- Firefox: 157.0, Windows, headless.
- Selenium: 4.41.0. Five fresh temporary Firefox profiles per condition.
- Machine: 20 logical CPUs, about 31.43 GB installed physical memory.
- Addon manifest version: `1.75.1.13`.
- Addon ID: `uBlock0@raymondhill.net`. This is the **upstream identity for testing only**, not the final meBlock identity.
- Tested XPI: `dist/build/uBlock0.firefox.xpi`, SHA-256 `443307a0861d6b13aab3c898017f5456810649a1a5c42f6ee1659d6727dc090e`.
- Raw run evidence: `dist/build/meblock-baseline.json` (ignored build output).

## Build verification

The unmodified Firefox build completed with Git Bash, Python 3.13, and pre-fetched uAssets. The build script requires Linux-style tools and `python3`; on this Windows machine we used Git Bash, redirected the `python3` shell function to installed Python, and set `PYTHONUTF8=1` to avoid Windows CP874 decode failures.

Five warm build iterations, after downloading the assets, measured **2,221, 2,322, 2,250, 2,276, and 2,278 ms**. Median: **2,276 ms**. Range: **2,221 to 2,322 ms**. Each run invoked `bash tools/make-firefox.sh` and returned exit code 0.

The resulting unpacked Firefox build contained 665 files with a summed logical file size of 17,708,358 bytes. The benchmark XPI contained 665 files, was 5,031,760 bytes, passed its ZIP CRC check, and contained the correct Firefox extension ID.

The reproducible packaging utility is `tools/meblock/package_firefox.py`. It uses fixed ZIP entry timestamps, so its archive hash differs from the first ad-hoc baseline archive despite packaging the same source files.

## Browser measurements

Method: launch a clean Firefox profile under Selenium, wait for `about:blank` to finish, optionally install the XPI as a temporary extension, wait **6 seconds**, and observe the Firefox process descendants of the owned geckodriver for **5 seconds**. Run both conditions five times in alternating order. Selenium closes each temporary profile afterward.

| Measure | Firefox without addon, median (range) | Firefox with upstream addon, median (range) |
| --- | --- | --- |
| Selenium launch to blank page, ms | 4,558.68 (4,480.77–4,687.24) | 4,582.63 (4,460.09–4,598.05) |
| Temporary XPI install, ms | n/a | 424.72 (386.02–432.63) |
| Process-tree CPU over sampling window, % of one core | 0.917 (0.610–1.829) | 26.249 (2.133–31.392) |
| Median summed RSS for sampled Firefox processes, MB | 737.698 (727.077–753.320) | 1,045.885 (899.211–1,083.310) |
| Peak sampled summed RSS, MB | 759.149 (744.542–762.970) | 1,113.330 (990.577–1,157.280) |

Firefox reported the expected addon ID in all five addon runs. No observed run failed.

### What these numbers do and do not mean

- **No meBlock speedup was measured.** The benchmarked source is unchanged upstream code.
- The two startup columns cover launching Firefox **before** temporary addon installation. Their difference is not an estimate of extension startup overhead.
- The temporary installation time measures the WebDriver install operation. It is not startup time for an extension already installed in a persistent profile.
- The CPU window starts only six seconds after the extension was installed. The large variation in CPU readings suggests transient background work or initialization. Do not interpret the 26.249% median as stable long-term idle CPU consumption or as CPU attributable exclusively to uBlock Origin.
- RSS sums resident sets of multiple Firefox processes. This can count shared physical memory more than once. It is **not** private memory usage of the extension or a per-extension allocation.
- These measurements use `about:blank`; real browsing, filtering cost, list updates, tab counts, caching and long-term usage still need separate workloads and controls.
- Another Firefox instance was open on the machine. The test identifies the Selenium-owned Firefox descendant processes but system resource contention can still add noise.
- These results are exploratory system-level measurements, not a statistically supported improvement comparison.

## How to reproduce

Use the repository root as the working directory. Install Python packages `selenium` and `psutil`, and ensure Selenium can resolve `geckodriver`. On the measured machine, Selenium's cached geckodriver was used.

Only on first setup, fetch the upstream asset repositories with Git Bash:

```powershell
& 'C:\Program Files\Git\bin\bash.exe' -lc 'bash tools/pull-assets.sh'
```

Build the unpacked Firefox addon without modifying upstream files:

```powershell
& 'C:\Program Files\Git\bin\bash.exe' -lc 'python3() { python "$@"; }; export -f python3; export PYTHONUTF8=1; bash tools/make-firefox.sh'
```

Package the built directory as a development XPI, then run the benchmark:

```powershell
python tools/meblock/package_firefox.py
python tools/meblock/benchmark_firefox.py --addon dist/build/uBlock0.firefox.xpi --runs 5 --settle 6 --sample 5 --output dist/build/meblock-baseline.json
```

The benchmark saves its per-run metrics, environment, input hash, asset revisions, and summary in JSON. Pin equivalent Firefox versions, filter assets, test pages, profiles, and settings when comparing a future meBlock change to upstream.

## Current validation and follow-ups

- **Passed:** asset fetch, source build, five repeated builds, archive integrity, and ten real Firefox/Selenium runs.
- **Passed:** `npm ci --ignore-scripts --no-audit --no-fund`.
- **Not passing on imported upstream source:** `npm run lint` reports `no-undef` for WebExtension globals such as `browser` and `chrome` across upstream platform files. Do not attribute those diagnostics to the new Python benchmark utilities without new evidence.
- The upstream `npm test` command is a placeholder, not a test suite.

Before making a performance claim, add stable post-initialization idle measurements, realistic filtering workloads, browser-version controls, test assertions for filtering correctness, and profiling to identify actual bottlenecks. Run the same experiment against a pinned upstream build and a changed meBlock build.

Keep the meBlock release identity separate from upstream. In particular, change its Firefox addon ID and branding before packaging a version intended for user installation, while preserving upstream attribution and GPLv3 requirements.
