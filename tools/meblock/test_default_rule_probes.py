"""Exercise derived default-list network probes without a browser or private data."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import default_rule_probes as probes


PUBLIC_RULES = "\n".join([
    "! Public test-only rules",
    "||alpha.example^",
    "||alpha.example^",
    "@@||beta.example^$image",
    "||gamma.example^$script",
    "||complex.example^$third-party",
    "sample.example##.hide",
    "||-invalid.example^",
    "",
])


def archive(path, reverse=False):
    with ZipFile(path, "w") as destination:
        items = [
            ("assets/ublock/filters.min.txt", PUBLIC_RULES),
            ("assets/thirdparties/easylist/easylist.txt", "! public fixture\n"),
            ("assets/ublock/badlists.txt", "||not-default.example^\n"),
        ]
        for name, body in reversed(items) if reverse else items:
            destination.writestr(name, body)


class DefaultRuleProbeTests(unittest.TestCase):
    def test_each_simple_rule_yields_exact_positive_and_near_miss_contexts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stock.xpi"
            archive(path)
            with ZipFile(path) as data:
                rows, coverage = probes.build_probes(
                    data, shard_index=0, shard_count=1
                )
        self.assertEqual(coverage["simple_host_rule_unique"], 3)
        self.assertEqual(coverage["simple_host_rules_in_shard"], 3)
        self.assertEqual(coverage["request_count"], 12)
        self.assertEqual(coverage["positive_requests"], 6)
        self.assertEqual(coverage["near_miss_requests"], 6)
        self.assertEqual(coverage["bundled_asset_count"], 2)
        self.assertEqual(coverage["noncomment_filter_lines"], 7)
        self.assertEqual(coverage["unsupported_or_duplicate_lines"], 4)
        self.assertEqual(coverage["syntax_input_line_counts"], {
            "network_pattern_or_modifier": 5,
            "network_exception_or_modifier": 1,
            "cosmetic_or_html": 1,
            "scriptlet": 0,
            "other_syntax": 0,
        })
        self.assertEqual(
            probes.classify_filter_text_line("example.org##+js(set,abc,true)"),
            "scriptlet",
        )
        self.assertEqual(rows[:4], [
            {"frameUrl": "https://reader.example.invalid/page",
             "url": "https://beta.example/probe.png", "cpt": "image"},
            {"frameUrl": "https://reader.example.invalid/page",
             "url": "https://near-beta.example/probe.png", "cpt": "image"},
            {"frameUrl": "https://beta.example/page",
             "url": "https://beta.example/probe.png", "cpt": "image"},
            {"frameUrl": "https://beta.example/page",
             "url": "https://near-beta.example/probe.png", "cpt": "image"},
        ])
        self.assertEqual({r["cpt"] for r in rows}, {"image", "script"})

    def test_every_eligible_rule_is_assigned_to_exactly_one_shard(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.xpi"
            second = Path(directory) / "second.xpi"
            archive(first)
            archive(second, reverse=True)
            with ZipFile(first) as a, ZipFile(second) as b:
                all_rows, _ = probes.build_probes(
                    a, shard_index=0, shard_count=1
                )
                shards = [
                    probes.build_probes(a, shard_index=i, shard_count=2)[0]
                    for i in range(2)
                ]
                repeated, _ = probes.build_probes(
                    b, shard_index=0, shard_count=1
                )
        self.assertEqual(all_rows, repeated)
        self.assertEqual(len(shards[0]) + len(shards[1]), len(all_rows))
        self.assertEqual(
            set(tuple(row.values()) for row in shards[0]).intersection(
                tuple(row.values()) for row in shards[1]
            ), set(),
        )

    def test_unsupported_and_bad_hostname_shapes_do_not_become_requests(self):
        for line in (
            "||-bad.example^", "||no-suffix^", "||bad..example^",
            "||nested.example^$third-party", "/ads[.]example/",
            "sample.example##.cosmetic", "||mismatch.example^$redirect=noop.js",
        ):
            with self.subTest(line=line):
                self.assertIsNone(probes.recognized_rule(line))
        self.assertEqual(probes.recognized_rule("||alpha.example^"),
                         ("||alpha.example^", "alpha.example", None))
        self.assertEqual(probes.recognized_rule("@@||beta.example^$image"),
                         ("@@||beta.example^$image", "beta.example", "image"))

    def test_live_enabled_list_mapping_excludes_unselected_bundled_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.xpi"
            with ZipFile(source, "w") as output:
                output.writestr(
                    "assets/assets.dev.json",
                    json.dumps({
                        "chosen": {"contentURL": [
                            "https://public.example.invalid/list.txt",
                            "assets/ublock/filters.min.txt",
                        ]},
                        "disabled": {"contentURL": [
                            "assets/thirdparties/easylist/easylist.txt",
                        ]},
                        "remote": {"contentURL": [
                            "https://remote.example.invalid/remote.txt",
                        ]},
                    }),
                )
                output.writestr(
                    "assets/ublock/filters.min.txt",
                    "||selected.example^\n",
                )
                output.writestr(
                    "assets/thirdparties/easylist/easylist.txt",
                    "||unselected.example^\n",
                )
            with ZipFile(source) as handle:
                rows, coverage = probes.build_probes(
                    handle, shard_index=0, shard_count=1,
                    selected_keys=["chosen", "remote"],
                )
                self.assertEqual(coverage["simple_host_rule_unique"], 1)
                self.assertEqual(coverage["selected_list_count"], 2)
                self.assertEqual(coverage["selected_without_bundled_asset_count"], 1)
                self.assertEqual(
                    coverage["selected_list_keys_sha256"],
                    hashlib.sha256(b"chosen\nremote").hexdigest(),
                )
                self.assertTrue(all("unselected" not in r["url"] for r in rows))
                with self.assertRaisesRegex(ValueError, "No selected"):
                    probes.build_probes(
                        handle, shard_index=0, shard_count=1,
                        selected_keys=["remote"],
                    )

    def test_bundled_source_digest_matches_unmodified_built_asset_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "public.xpi"
            archive(source)
            built = root / "build"
            with ZipFile(source) as handle:
                _, coverage = probes.collect_bundled_rules(handle)
                for name in handle.namelist():
                    if name.endswith(".txt"):
                        destination = built / name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(handle.read(name))
            self.assertEqual(
                probes.bundled_asset_fingerprint(built),
                coverage["bundled_asset_contents_sha256"],
            )
            (built / "assets" / "ublock" / "filters.min.txt").write_text(
                "||changed.example^", encoding="utf-8"
            )
            self.assertNotEqual(
                probes.bundled_asset_fingerprint(built),
                coverage["bundled_asset_contents_sha256"],
            )

    def test_invalid_shards_and_oversized_requests_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stock.xpi"
            archive(path)
            with ZipFile(path) as content:
                for index, count in ((-1, 2), (2, 2), (0, 0)):
                    with self.assertRaisesRegex(ValueError, "Shard"):
                        probes.build_probes(content, shard_index=index, shard_count=count)
                with self.assertRaisesRegex(ValueError, "cap"):
                    probes.build_probes(
                        content, shard_index=0, shard_count=1, max_requests=4,
                    )

    def test_cli_stays_ignored_and_pins_numeric_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / "dist" / "build"
            build.mkdir(parents=True)
            addon = build / "stock.xpi"
            archive(addon)
            output = build / "derived.json"
            with patch.object(probes, "BUILD_ROOT", build):
                with redirect_stdout(StringIO()):
                    self.assertEqual(
                        probes.main([
                            "--addon", str(addon), "--out", str(output),
                            "--shards", "1", "--shard-index", "0",
                        ]), 0,
                    )
                with redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit) as caught:
                        probes.main([
                            "--addon", str(addon), "--out",
                            str(root / "outside.json"),
                        ])
                self.assertEqual(caught.exception.code, 2)
            payload = output.read_bytes()
            records = json.loads(payload)
            report = json.loads(output.with_suffix(".json.manifest.json").read_text())
            self.assertEqual(len(records), 12)
            self.assertEqual(report["schema"], "meblock-default-rule-probes-v1")
            self.assertEqual(report["request_count"], len(records))
            self.assertEqual(hashlib.sha256(payload).hexdigest(), report["dataset_sha256"])
            self.assertEqual(report["shard_count"], 1)
            self.assertNotIn("source_path", report)
            self.assertNotIn("private", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
