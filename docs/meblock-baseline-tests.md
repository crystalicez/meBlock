# meBlock baseline test selection

**Primary speed test:** `sfne_quick.py` calls uBlock Origin's original SFNE benchmark in isolated Firefox profiles, with 2 warmups and 5 timed trials per profile, alternating A/B order.

```powershell
python tools/meblock/sfne_quick.py --dataset dist/build/default-rule-probes-sfne-primary.json --runs 3
```

- **Workload:** Only default enabled filter lists; no injected rules. The pinned, public-list-derived corpus has approximately 19,824 requests. Generate it with `tools/meblock/default_rule_probes.py` as described in [the benchmark workflow](meblock-filtering-benchmark.md).
- **Correctness (separate):** Use `tools/meblock/compare_original.py --default-lists` to verify per-request block/allow decisions, modifiers and Firefox browser behavior before accepting an engine change.
- **Archived diagnostics:** Custom five-pass matcher (`us_per_request`), per-call/batched p95, artificial timing delays and `candidate_noise_preflight.py` remain for investigation, **not** the primary speed verdict.
- **Limits:** The original uBO benchmark has been exercised in Firefox, but sensitivity to a 3% change is **not established**. Use Firefox Profiler for CPU hotspots; measure RAM separately. Never claim a speedup from one quick run.
- **Validation:** Run `python -m unittest discover -s tools/meblock -p test_*.py -q` and `python tools/meblock/check_public_artifacts.py`.

Keep datasets, profiles, and reports in ignored `dist/build/`. Never publish private browser data.
