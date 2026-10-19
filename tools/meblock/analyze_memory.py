#!/usr/bin/env python3
"""Inspect and compare Firefox about:memory JSON reports without double-counting trees."""

import argparse
from bisect import bisect_left
import gzip
import json
from collections import defaultdict
from pathlib import Path

MAX_DECOMPRESSED_BYTES = 256 * 1024 * 1024
MIB = 1024 * 1024


def load_reports(path):
    path = Path(path)
    with path.open("rb") as stream:
        gzipped = stream.read(2) == b"\x1f\x8b"
    opener = gzip.open if gzipped else open
    with opener(path, "rb") as stream:
        raw = stream.read(MAX_DECOMPRESSED_BYTES + 1)
    if len(raw) > MAX_DECOMPRESSED_BYTES:
        raise ValueError("Memory report exceeds 256 MiB decompressed")
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"Invalid Firefox memory report: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("reports"), list):
        raise ValueError("Expected Firefox about:memory JSON object with reports array")
    return document["reports"]


def byte_reporters(reports):
    totals = defaultdict(int)
    for row in reports:
        if not isinstance(row, dict):
            raise ValueError("Memory reporter must be an object")
        if row.get("units") != 0:
            continue
        process, path, amount = (
            row.get("process"), row.get("path"), row.get("amount")
        )
        if not isinstance(process, str) or not isinstance(path, str) or not path:
            raise ValueError("Byte reporter needs a process and nonempty path")
        if type(amount) is not int:
            raise ValueError(f"Byte reporter has non-integer amount at {path!r}")
        if amount == -1:
            continue
        if amount < 0:
            raise ValueError(f"Byte reporter has negative amount at {path!r}")
        totals[(process or "Main Process", path)] += amount
    return totals


def subtree_summary(reports, prefix, process_contains=None, show_paths=False, top=10):
    prefix = prefix.strip("/")
    if not prefix:
        raise ValueError("Explicit nonempty reporter path prefix is required")
    memory = byte_reporters(reports)
    unknown_count = sum(
        isinstance(row, dict) and row.get("units") == 0 and row.get("amount") == -1
        and isinstance(row.get("path"), str)
        and (row["path"] == prefix or row["path"].startswith(prefix + "/"))
        and (process_contains is None or process_contains.casefold() in
             (row.get("process") or "Main Process").casefold())
        for row in reports
    )
    matched = {
        (process, path): amount for (process, path), amount in memory.items()
        if (path == prefix or path.startswith(prefix + "/"))
        and (process_contains is None or process_contains.casefold() in process.casefold())
    }
    if not matched:
        raise ValueError(f"No byte reporters found under {prefix!r}")
    paths_by_process = defaultdict(list)
    for process, path in matched:
        paths_by_process[process].append(path)
    for paths in paths_by_process.values():
        paths.sort()
    leaf_rows = []
    for (process, path), amount in matched.items():
        paths = paths_by_process[process]
        descendant = path + "/"
        index = bisect_left(paths, descendant)
        if index < len(paths) and paths[index].startswith(descendant):
            continue
        leaf_rows.append((process, path, amount))
    if not leaf_rows:
        raise ValueError("Selected subtree contains no byte-valued leaves")
    categories = defaultdict(int)
    processes = defaultdict(int)
    for process, path, amount in leaf_rows:
        relative = path[len(prefix):].lstrip("/")
        group = relative.split("/", 1)[0] if relative else "(root)"
        categories[group] += amount
        processes[process] += amount
    result = {
        "path_prefix": prefix,
        "process_filter": process_contains,
        "bytes": sum(amount for _, _, amount in leaf_rows),
        "mib": round(sum(amount for _, _, amount in leaf_rows) / MIB, 3),
        "byte_reporter_count": len(leaf_rows),
        "unavailable_byte_reporters": unknown_count,
        "by_category_bytes": dict(sorted(categories.items(), key=lambda pair: (-pair[1], pair[0]))),
        "by_process_bytes": dict(sorted(processes.items())),
        "measurement": "attributed about:memory byte reporters, not Firefox RSS or a complete heap snapshot",
    }
    if show_paths:
        result["largest_leaf_paths"] = [
            {"process": process, "path": path, "bytes": amount}
            for process, path, amount in
            sorted(leaf_rows, key=lambda row: (-row[2], row[0], row[1]))[:top]
        ]
    return result


def compare_snapshots(before, after):
    if before["unavailable_byte_reporters"] or after["unavailable_byte_reporters"]:
        raise ValueError("Cannot compare incomplete scopes with unavailable byte reporters")
    left, right = before["bytes"], after["bytes"]
    keys = before["by_category_bytes"].keys() | after["by_category_bytes"].keys()
    categories = [
        {
            "category": key,
            "before_bytes": before["by_category_bytes"].get(key, 0),
            "after_bytes": after["by_category_bytes"].get(key, 0),
            "delta_bytes": after["by_category_bytes"].get(key, 0) - before["by_category_bytes"].get(key, 0),
        }
        for key in keys
    ]
    categories.sort(key=lambda item: (-abs(item["delta_bytes"]), item["category"]))
    return {
        "before_bytes": left,
        "after_bytes": right,
        "delta_bytes": right - left,
        "delta_mib": round((right - left) / MIB, 3),
        "change_percent": round((right - left) * 100 / left, 3) if left else None,
        "category_deltas": categories,
        "warning": "Reporter subtrees are not always total extension memory; compare equivalent Firefox conditions.",
    }


def inspect_paths(reports, query, max_results):
    entries = [
        {"process": process, "path": path, "bytes": amount}
        for (process, path), amount in byte_reporters(reports).items()
        if query.casefold() in path.casefold()
    ]
    entries.sort(key=lambda item: (-item["bytes"], item["process"], item["path"]))
    return {
        "query": query,
        "matched_reporters": len(entries),
        "reporters": entries[:max_results],
        "warning": "These are raw reporters, not an additive total; parent and child paths may overlap.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    inspect = actions.add_parser("inspect", help="Find reporter paths; do not sum them")
    inspect.add_argument("report", type=Path)
    inspect.add_argument("--find", default="add-ons")
    inspect.add_argument("--top", type=int, default=20)
    summary = actions.add_parser("summarize", help="Sum byte reporters in one selected tree")
    summary.add_argument("report", type=Path)
    summary.add_argument("--prefix", required=True)
    summary.add_argument("--process")
    summary.add_argument("--show-paths", action="store_true")
    summary.add_argument("--top", type=int, default=10)
    compare = actions.add_parser("compare", help="Compare the same reporter subtree")
    compare.add_argument("before", type=Path)
    compare.add_argument("after", type=Path)
    compare.add_argument("--prefix", required=True)
    compare.add_argument("--after-prefix")
    compare.add_argument("--before-process")
    compare.add_argument("--after-process")
    args = parser.parse_args(argv)
    if getattr(args, "top", 1) < 1:
        parser.error("--top must be at least 1")
    try:
        if args.action == "inspect":
            result = inspect_paths(load_reports(args.report), args.find, args.top)
        elif args.action == "summarize":
            result = subtree_summary(
                load_reports(args.report), args.prefix, args.process,
                args.show_paths, args.top,
            )
        else:
            before = subtree_summary(
                load_reports(args.before), args.prefix, args.before_process
            )
            after = subtree_summary(
                load_reports(args.after), args.after_prefix or args.prefix,
                args.after_process,
            )
            result = compare_snapshots(before, after)
            result["before_scope"] = before["path_prefix"]
            result["after_scope"] = after["path_prefix"]
    except (OSError, ValueError, EOFError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
