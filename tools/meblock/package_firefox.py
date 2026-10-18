#!/usr/bin/env python3
"""Package an existing Firefox build as a reproducible development XPI."""

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path("dist/build/uBlock0.firefox")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("dist/build/uBlock0.firefox.xpi")
    )
    args = parser.parse_args()
    root = args.source.resolve()
    target = args.output.resolve()
    if not (root / "manifest.json").is_file():
        parser.error("Source directory must contain manifest.json")
    if target.is_relative_to(root):
        parser.error("Output archive cannot be inside the source directory")
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files:
        parser.error("Source directory is empty")

    target.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(target, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            info = ZipInfo(path.relative_to(root).as_posix(), (1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)

    with ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("XPI CRC validation failed")
        if len(archive.namelist()) != len(files):
            raise RuntimeError("XPI entry count mismatch")
        manifest = json.loads(archive.read("manifest.json"))
        if not manifest.get("browser_specific_settings", {}).get("gecko", {}).get("id"):
            raise RuntimeError("XPI lacks a Firefox extension ID")
    print(
        json.dumps(
            {
                "path": str(target),
                "files": len(files),
                "bytes": target.stat().st_size,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "extension_version": manifest["version"],
                "extension_id": manifest["browser_specific_settings"]["gecko"]["id"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
