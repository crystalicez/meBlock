# meBlock baseline test selection

**Primary speed test:** `sfne_quick.py` calls uBlock Origin's original SFNE benchmark in isolated Firefox profiles (2 warmups + 5 measured trials/profile, alternating A/B order).

```powershell
python tools/meblock/default_rule_probes.py --addon dist/build/uBlock0.firefox.xpi --selected-run dist/build/meblock-comparison/SESSION/upstream-run-1.json --shards 4 --shard-index 0 --max-requests 120000 --out dist/build/default-rule-probes-sfne-null-100k.json
python tools/meblock/sfne_quick.py --dataset dist/build/default-rule-probes-sfne-null-100k.json --runs 3
```

`SESSION` is a previously verified **local, ignored** Firefox default-list run. The generator still defaults to a 20,000-request cap unless explicitly overridden.

**Same-code null-noise test (10 independent AB/BA pairs):**

```powershell
python tools/meblock/sfne_quick.py --dataset dist/build/default-rule-probes-sfne-null-100k.json --null-control --upstream-ref upstream/master --meblock-ref upstream/master --runs 10
```

- **Workload:** Default enabled filter lists only; zero injected filters. The pinned public-list-derived four-shard corpus contains 99,108 requests with balanced script, image, XHR and stylesheet contexts. The old 19,824-request corpus remains a quicker smoke test. The performance workload does not prove real-world request distribution.
- **Correctness (separate):** Use `tools/meblock/compare_original.py --default-lists` to verify per-request block/allow decisions, modifiers and Firefox browser behavior before accepting an engine change.
- **Legacy diagnostics:** Custom five-pass matcher (`us_per_request`), per-call/batched p95, artificial timing delays and `candidate_noise_preflight.py` remain for historical investigation, **not** the primary speed verdict. Its former `official` cohort is now explicitly `legacy_custom_matcher`.
- **Null statistics:** Reuses `comparison_stats.paired_performance()` on each independent profile's five-trial median. Reports paired bootstrap 95% intervals for all pairs and separately for upstream-first/meBlock-first slots; the null screen requires at least 10 balanced pairs, byte-identical source builds, >=200 ms on every measured trial, and all three 95% intervals strictly inside ±3%. `--null-control` exits **0** for a qualified null, **3** for complete but unqualified noise (retains evidence and `status=complete`), and **1** for execution errors. Regular smoke runs keep exit 0 when complete.
- **Limits:** The original uBO benchmark has been exercised in Firefox, but sensitivity to a 3% change remains **unproven** until both null-equivalence and a separate positive sensitivity check succeed. A null pass alone never proves detection power. Profile CPU separately; measure RAM separately.
- **Readiness:** `baseline_readiness.py` now additionally requires `--sfne-report` and `--sfne-dataset`. It replays all ten-pair Firefox SFNE profiles against the pinned dataset, package hashes, source and asset refs, and current probe/runner hashes. Old custom-matcher p95 no longer gates READY. A historical report collected by a different runner revision cannot qualify the current baseline; recapture after changing measurement code.
- **Validation:** Run `python -m unittest discover -s tools/meblock -p test_*.py -q` and `python tools/meblock/check_public_artifacts.py`.

Keep datasets, profiles, and reports in ignored `dist/build/`. Never publish private browser data.
