#!/usr/bin/env python3
"""Build deterministic request probes from bundled public default filter assets.

Only writes ignored local request datasets. It never installs, injects, or
rewrites an extension's filters. Each request is evaluated without networking.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile

from filtering_benchmark import valid_rows


ROOT = Path(__file__).resolve().parents[2]
BUILD_ROOT = ROOT / "dist" / "build"
RULE_RE = re.compile(
    r"(@@)?\|\|([a-z0-9.-]+)\^(?:\$(script|image|xmlhttprequest))?",
    re.ASCII,
)
REQUEST_TYPES = {
    None: ("script", "probe.js"),
    "script": ("script", "probe.js"),
    "image": ("image", "probe.png"),
    "xmlhttprequest": ("xmlhttprequest", "probe.json"),
}
MAX_REQUESTS = 20000


def recognized_rule(line):
    matched = RULE_RE.fullmatch(line)
    if matched is None:
        return None
    host = matched.group(2)
    labels = host.split(".")
    if (
        len(labels) < 2 or len(host) > 253
        or any(not 1 <= len(label) <= 57 or label.startswith("-")
               or label.endswith("-") for label in labels)
        or not labels[-1].isalpha()
    ):
        return None
    return (line, host, matched.group(3))


def mapped_selected_assets(archive, selected_keys):
    if (
        not isinstance(selected_keys, list) or not selected_keys
        or any(not isinstance(key, str) or not key for key in selected_keys)
        or len(set(selected_keys)) != len(selected_keys)
    ):
        raise ValueError("Invalid live selected filter list keys")
    registry = json.loads(archive.read("assets/assets.dev.json"))
    available = set(archive.namelist())
    paths = set()
    unmapped = 0
    for key in selected_keys:
        entry = registry.get(key, {})
        urls = entry.get("contentURL", []) if isinstance(entry, dict) else []
        if isinstance(urls, str):
            urls = [urls]
        matching = [
            path for path in urls
            if isinstance(path, str) and path.startswith("assets/")
            and path.endswith(".txt") and path in available
            and path != "assets/ublock/badlists.txt"
        ]
        if matching:
            paths.add(matching[0])
        else:
            unmapped += 1
    if not paths:
        raise ValueError("No selected filter lists map to packaged public assets")
    return sorted(paths), {
        "selected_list_keys_sha256": hashlib.sha256(
            "\n".join(sorted(selected_keys)).encode("utf-8")
        ).hexdigest(),
        "selected_list_count": len(selected_keys),
        "selected_without_bundled_asset_count": unmapped,
    }


def classify_filter_text_line(line):
    """Syntax-only coverage categories, not engine compilation status."""
    if any(token in line for token in ("##+js(", "#@#+js(")):
        return "scriptlet"
    if any(token in line for token in ("##", "#@#", "#?#", "#$#")):
        return "cosmetic_or_html"
    if line.startswith("@@"):
        return "network_exception_or_modifier"
    if line.startswith(("||", "|", "/")):
        return "network_pattern_or_modifier"
    return "other_syntax"


def collect_bundled_rules(archive, selected_keys=None):
    files = sorted(
        name for name in archive.namelist()
        if name.startswith("assets/") and name.endswith(".txt")
        and name != "assets/ublock/badlists.txt"
    )
    selection = {}
    if selected_keys is not None:
        files, selection = mapped_selected_assets(archive, selected_keys)
    if not files:
        raise ValueError("XPI has no bundled public filter-list text assets")
    eligible = set()
    candidate_lines = 0
    categories = Counter()
    bundle = hashlib.sha256()
    for name in files:
        data = archive.read(name)
        bundle.update(name.encode("utf-8") + b"\0" + hashlib.sha256(data).digest())
        for line in data.decode("utf-8-sig", errors="replace").splitlines():
            line = line.strip()
            if (not line or line.startswith(("!", "[")) or
                line.startswith("#") and not
                line.startswith(("##", "#@#", "#?#", "#$#"))):
                continue
            candidate_lines += 1
            categories[classify_filter_text_line(line)] += 1
            if recognized_rule(line) is not None:
                eligible.add(line)
    return sorted(eligible), {
        "bundled_asset_count": len(files),
        "bundled_asset_contents_sha256": bundle.hexdigest(),
        "noncomment_filter_lines": candidate_lines,
        "simple_host_rule_unique": len(eligible),
        "unsupported_or_duplicate_lines": candidate_lines - len(eligible),
        "syntax_input_line_counts": {
            key: categories[key] for key in (
                "network_pattern_or_modifier", "network_exception_or_modifier",
                "cosmetic_or_html", "scriptlet", "other_syntax",
            )
        },
        **selection,
    }


def bundled_asset_fingerprint(build):
    files = sorted(
        path for path in (build / "assets").rglob("*.txt")
        if path.relative_to(build).as_posix() != "assets/ublock/badlists.txt"
    )
    if not files:
        raise ValueError("Build lacks bundled public filter-list assets")
    bundle = hashlib.sha256()
    for path in files:
        name = path.relative_to(build).as_posix()
        bundle.update(
            name.encode("utf-8") + b"\0" + hashlib.sha256(path.read_bytes()).digest()
        )
    return bundle.hexdigest()


def build_probes(archive, *, shard_index, shard_count, max_requests=MAX_REQUESTS,
                 selected_keys=None):
    if type(shard_count) is not int or shard_count < 1:
        raise ValueError("Shard count must be positive")
    if type(shard_index) is not int or not 0 <= shard_index < shard_count:
        raise ValueError("Shard index must lie within the shard count")
    if type(max_requests) is not int or max_requests < 4:
        raise ValueError("Request cap must be at least four")
    all_rules, coverage = collect_bundled_rules(archive, selected_keys=selected_keys)
    shard = [rule for i, rule in enumerate(all_rules) if i % shard_count == shard_index]
    if not shard:
        raise ValueError("Selected shard contains no eligible simple-host rules")
    if len(shard) * 4 > max_requests:
        raise ValueError("Selected shard exceeds request cap; use more shards")
    rows = []
    for rule in shard:
        parsed = recognized_rule(rule)
        if parsed is None:
            raise AssertionError("Eligible rule was not recognized")
        _, host, option = parsed
        request_type, filename = REQUEST_TYPES[option]
        labels = host.split(".")
        near_miss = "near-" + labels[0] + "." + ".".join(labels[1:])
        positive = f"https://{host}/{filename}"
        negative = f"https://{near_miss}/{filename}"
        for frame in ("https://reader.example.invalid/page", f"https://{host}/page"):
            rows.append({"frameUrl": frame, "url": positive, "cpt": request_type})
            rows.append({"frameUrl": frame, "url": negative, "cpt": request_type})
    return valid_rows(rows), {
        "schema": "meblock-default-rule-probes-v1",
        **coverage,
        "shard_index": shard_index,
        "shard_count": shard_count,
        "simple_host_rules_in_shard": len(shard),
        "positive_requests": len(shard) * 2,
        "near_miss_requests": len(shard) * 2,
        "request_count": len(rows),
        "behavior_scope": (
            "Bundled public simple host-anchored candidates only, which may "
            "include unselected or shadowed rules. Differential decisions and "
            "modifiers against upstream, not isolated proof each rule fired."
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--addon", type=Path, default=BUILD_ROOT / "uBlock0.firefox.xpi")
    parser.add_argument("--out", type=Path, default=BUILD_ROOT / "default-rule-probes.json")
    parser.add_argument("--shards", type=int, default=256)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument(
        "--selected-run", type=Path,
        help="Ignored, disposable Firefox run JSON with live selected-filter-list keys",
    )
    args = parser.parse_args(argv)
    output = args.out.resolve()
    if not output.is_relative_to(BUILD_ROOT.resolve()):
        parser.error("Rule-derived request data must remain inside ignored dist/build")
    try:
        selected = None
        if args.selected_run is not None:
            if not args.selected_run.resolve().is_relative_to(BUILD_ROOT.resolve()):
                raise ValueError("Selected list evidence must remain inside ignored dist/build")
            evidence = json.loads(args.selected_run.read_text(encoding="utf-8"))
            if not isinstance(evidence, dict):
                raise ValueError("Invalid live Firefox list-selection evidence")
            selected = evidence.get("selected_filter_lists")
        with ZipFile(args.addon) as archive:
            rows, coverage = build_probes(
                archive, shard_index=args.shard_index, shard_count=args.shards,
                selected_keys=selected,
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=False) + "\n"
        encoded = payload.encode("utf-8")
        output.write_bytes(encoded)
        coverage["dataset_sha256"] = hashlib.sha256(encoded).hexdigest()
        output.with_suffix(output.suffix + ".manifest.json").write_bytes(
            (json.dumps(coverage, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        )
        print(json.dumps(coverage, indent=2))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f"Default-rule probe rejected: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
