#!/usr/bin/env python3
"""Prepare a pinned uBO SNFE corpus and compare captured upstream benchmark runs."""

import argparse
import hashlib
import json
from pathlib import Path
import random
import re
import statistics
import sys


LABEL = "Benchmarked static network filtering engine:"
COUNTS = {
    "not_blocked": "Not blocked",
    "blocked": "Blocked",
    "unblocked": "Unblocked",
    "redirect": "redirect=",
    "removeparam": "removeparam=",
    "urlskip": "urlskip=",
    "csp": "csp=",
    "permissions": "permissions=",
    "replace": "replace=",
}
REQUEST_RE = re.compile(r"Evaluated\s+([0-9,]+)\s+requests\s+in\s+([0-9,]+)\s+ms")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def valid_rows(raw: object) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("Expected a nonempty JSON array of request records")
    records = []
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ValueError(f"Request {index} is not an object")
        if not all(isinstance(row.get(key), str) and row[key] for key in ("frameUrl", "url", "cpt")):
            raise ValueError(f"Request {index} lacks frameUrl, url, or cpt")
        records.append({"frameUrl": row["frameUrl"], "url": row["url"], "cpt": row["cpt"]})
    return records


def synthesize(n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    paths = [
        ("https://securepubads.g.doubleclick.net/tag/js/gpt.js", "script"),
        ("https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js", "script"),
        ("https://www.google-analytics.com/collect?v=1", "xmlhttprequest"),
        ("https://metrics.example/track.gif", "image"),
        ("https://static.example/assets/app.js", "script"),
        ("https://cdn.example/styles/site.css", "stylesheet"),
        ("https://images.example/photo.jpg", "image"),
        ("https://media.example/video.mp4", "media"),
    ]
    return [
        {
            "frameUrl": f"https://publisher-{rng.randrange(500)}.example/article/{i % 71}",
            "url": paths[k][0] + ("&" if "?" in paths[k][0] else "?") + f"run={i % 8191}",
            "cpt": paths[k][1],
        }
        for i in range(n)
        for k in [rng.randrange(len(paths))]
    ]


def save_dataset(path: Path, rows: list[dict], kind: str, seed: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=False) + "\n"
    path.write_text(payload, encoding="utf-8")
    manifest = {
        "schema": "meblock-snfe-dataset-v1",
        "kind": kind,
        "requests": len(rows),
        "sha256": file_sha256(path),
        "source_seed": seed,
        "warning": "Synthetic requests are a smoke-test fixture, not real-world performance evidence." if kind == "synthetic" else None,
    }
    sidecar = path.with_suffix(path.suffix + ".manifest.json")
    sidecar.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"dataset": str(path), "manifest": str(sidecar), **manifest}, indent=2))


def parse_report_text(text: str) -> list[dict]:
    results = []
    for chunk in text.split(LABEL)[1:]:
        matched = REQUEST_RE.search(chunk)
        if matched is None:
            raise ValueError("SNFE report missing request count or elapsed milliseconds")
        total_requests, elapsed_ms = (int(s.replace(",", "")) for s in matched.groups())
        if total_requests <= 0 or elapsed_ms < 0:
            raise ValueError("SNFE report has invalid request count or duration")
        counts = {}
        for key, label in COUNTS.items():
            m = re.search(rf"(?m)^\s*{re.escape(label)}:\s*([0-9,]+)\s*$", chunk)
            if m is None:
                raise ValueError(f"SNFE report missing {label}")
            counts[key] = int(m.group(1).replace(",", ""))
        if counts["not_blocked"] + counts["blocked"] + counts["unblocked"] != total_requests:
            raise ValueError("SNFE decisions do not add up to total requests")
        results.append({
            "requests": total_requests,
            "elapsed_ms_rounded": elapsed_ms,
            "us_per_request_from_total": elapsed_ms * 1000 / total_requests,
            "counts": counts,
        })
    if not results:
        raise ValueError("No 'Benchmarked static network filtering engine' reports were found")
    return results


def describe(rows: list[dict]) -> dict:
    vals = [r["us_per_request_from_total"] for r in rows]
    return {
        "runs": len(rows),
        "median_us_per_request": round(statistics.median(vals), 5),
        "min_us_per_request": round(min(vals), 5),
        "max_us_per_request": round(max(vals), 5),
    }


def compare(left: list[dict], right: list[dict]) -> dict:
    expected = left[0]["requests"]
    if any(r["requests"] != expected for r in left + right):
        raise ValueError("Request counts differ between benchmark runs")
    all_counts = [r["counts"] for r in left + right]
    counts_equal = all(c == all_counts[0] for c in all_counts)
    a = describe(left)
    b = describe(right)
    adequate_runs = len(left) >= 5 and len(right) >= 5
    if not counts_equal:
        verdict = "invalid: aggregate filtering decisions differ"
    elif not adequate_runs:
        verdict = "inconclusive: fewer than five runs on either side"
    elif not (
        a["max_us_per_request"] < b["min_us_per_request"]
        or b["max_us_per_request"] < a["min_us_per_request"]
    ):
        verdict = "inconclusive: run ranges overlap"
    else:
        verdict = "candidate difference: inspect profiles and paired-run noise"
    return {
        "verdict": verdict,
        "requests_per_run": expected,
        "aggregate_counts_equal": counts_equal,
        "per_request_decisions_verified": False,
        "upstream": a,
        "meblock": b,
        "median_time_delta_percent": round((b["median_us_per_request"] / a["median_us_per_request"] - 1) * 100, 2) if a["median_us_per_request"] else None,
        "limitations": [
            "Upstream reports round elapsed time to whole milliseconds.",
            "Equal aggregate counts do not prove that each request has the same result.",
            "Filtering lists, dataset, browser version, and runtime configuration must be pinned outside this parser.",
            "Use interleaved A/B sessions. A difference is not proof of user-perceived speedup.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    synth = sub.add_parser("synthetic", help="Generate an illustrative deterministic request corpus")
    synth.add_argument("--out", type=Path, default=Path("dist/build/meblock-requests.json"))
    synth.add_argument("--count", type=int, default=50000)
    synth.add_argument("--seed", type=int, default=20261018)
    normalize = sub.add_parser("normalize", help="Normalize an existing JSON or JSONL dataset")
    normalize.add_argument("--input", type=Path, required=True)
    normalize.add_argument("--out", type=Path, default=Path("dist/build/meblock-requests.json"))
    cmp = sub.add_parser("compare", help="Compare captured SNFE text reports from both builds")
    cmp.add_argument("--upstream", type=Path, required=True)
    cmp.add_argument("--meblock", type=Path, required=True)
    cmp.add_argument("--dataset", type=Path, required=True)
    cmp.add_argument("--out", type=Path, default=Path("dist/build/meblock-filtering-comparison.json"))
    args = parser.parse_args()
    try:
        if args.command == "synthetic":
            if args.count < 1:
                raise ValueError("--count must be positive")
            save_dataset(args.out, synthesize(args.count, args.seed), "synthetic", args.seed)
        elif args.command == "normalize":
            text = args.input.read_text(encoding="utf-8")
            rows = json.loads(text) if text.lstrip().startswith("[") else [json.loads(line) for line in text.splitlines() if line.strip()]
            save_dataset(args.out, valid_rows(rows), "imported")
        else:
            if not args.dataset.is_file():
                raise ValueError("Dataset file does not exist")
            left = parse_report_text(args.upstream.read_text(encoding="utf-8"))
            right = parse_report_text(args.meblock.read_text(encoding="utf-8"))
            report = compare(left, right)
            report["dataset_sha256"] = file_sha256(args.dataset)
            report["upstream_log_sha256"] = file_sha256(args.upstream)
            report["meblock_log_sha256"] = file_sha256(args.meblock)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report, indent=2))
            return 2 if not report["aggregate_counts_equal"] else 0
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
