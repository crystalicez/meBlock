#!/usr/bin/env python3
"""Fail closed if tracked meBlock files expose private diagnostic artifacts."""

import argparse
import re
import subprocess

BLOCKED_NAME = re.compile(
    r"(?i)(?:^|/)(?:HANDOFF-NEXT-SESSION\.md|memory-reports|private-memory|dist/build|profiles|private|\.audit)(?:/|$)"
)
BLOCKED_SUFFIX = (
    ".json.gz", ".heapsnapshot", ".cpuprofile", ".sqlite",
    ".sqlite-wal", ".sqlite-shm", ".dmp", ".memory.json",
    ".log", ".har", ".netlog", ".profraw", ".profdata", ".trace",
)
CONTENT_PATTERNS = {
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "service-token": re.compile(rb"\b(?:github_pat_|ghp_|gho_|ghu_|ghs_)[A-Za-z0-9_]{12,}"),
    "private-home-directory": re.compile(
        rb"(?i)(?:[A-Z]:[\\/]Users[\\/]|/home/|/Users/)(?!example\b|runneradmin\b)[A-Za-z0-9._-]{2,}[\\/]"
    ),
}

EMAIL_PATTERN = re.compile(rb"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PUBLIC_EXAMPLE_SUFFIXES = (b"@example.invalid", b"@example.com")
PUBLIC_UPSTREAM_IDS = {b"ublock0@raymondhill.net"}

# Limit content scanning to project-owned work, not vendored upstream resources.
OWNED_PREFIXES = (
    "tools/meblock/", "docs/meblock", ".github/workflows/meblock-", "AGENTS.md"
)


def blocked_path(path):
    normalized = path.replace("\\", "/")
    lower = normalized.lower()
    if BLOCKED_NAME.search(normalized):
        return "private path"
    if lower.endswith(BLOCKED_SUFFIX) or ".memory-report" in lower:
        return "raw diagnostics"
    return None


def blocked_content(path, data):
    if not path.startswith(OWNED_PREFIXES):
        return None
    for kind, pattern in CONTENT_PATTERNS.items():
        if pattern.search(data):
            return kind
    for match in EMAIL_PATTERN.finditer(data):
        address = match.group().lower()
        if address in PUBLIC_UPSTREAM_IDS or address.endswith(PUBLIC_EXAMPLE_SUFFIXES):
            continue
        return "email-address"
    return None


def verify_tracked_files(repository="."):
    raw = subprocess.check_output(["git", "-C", repository, "ls-files", "--cached", "-z"])
    paths = [p.decode("utf-8") for p in raw.split(b"\0") if p]
    violations = []
    for path in paths:
        problem = blocked_path(path)
        if problem:
            violations.append((path, problem))
            continue
        if path.startswith(OWNED_PREFIXES):
            data = subprocess.check_output(["git", "-C", repository, "show", ":" + path])
            problem = blocked_content(path, data)
            if problem:
                violations.append((path, problem))
    return violations


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=".")
    args = parser.parse_args(argv)
    errors = verify_tracked_files(args.repository)
    if errors:
        kinds = ", ".join(sorted({label for _, label in errors}))
        print(
            f"DENIED: {len(errors)} potentially private tracked artifact(s) "
            f"({kinds}). Remove them from Git's index before pushing. "
            "Filenames and contents are withheld from public CI logs."
        )
        return 1
    print("Public-repository artifact guard: no blocked tracked artifacts found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
