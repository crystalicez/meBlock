"""Behavioral tests for Firefox about:memory reporter-tree analysis."""

import contextlib
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest

from analyze_memory import (
    MIB, byte_reporters, compare_snapshots, inspect_paths, load_reports,
    main, subtree_summary,
)


PREFIX = "explicit/add-ons/uBlock0@raymondhill.net"


def reporter(path, amount, *, process="Extensions (pid 101)", units=0):
    return {
        "process": process,
        "path": path,
        "kind": 1,
        "units": units,
        "amount": amount,
        "description": "test-only reporter",
    }


def sample(js=40, styles=20):
    return [
        reporter(PREFIX, (js + styles) * MIB),
        reporter(PREFIX + "/js", js * MIB),
        reporter(PREFIX + "/js/strings", 10 * MIB),
        reporter(PREFIX + "/js/objects", (js - 10) * MIB),
        reporter(PREFIX + "/style", styles * MIB),
        reporter(PREFIX + "/counter", 150, units=1),
        reporter("explicit/other-extension/js", 999 * MIB),
        reporter("heap-allocated", 2000 * MIB),
    ]


class MemoryAnalysisTests(unittest.TestCase):
    def test_subtree_sums_only_leaf_byte_reporters(self):
        result = subtree_summary(sample(), PREFIX)
        self.assertEqual(result["bytes"], 60 * MIB)
        self.assertEqual(result["mib"], 60.0)
        self.assertEqual(result["byte_reporter_count"], 3)
        self.assertEqual(result["by_category_bytes"], {"js": 40 * MIB, "style": 20 * MIB})

    def test_process_filter_and_duplicate_reporters(self):
        rows = [
            reporter(PREFIX + "/js", MIB, process="Extensions (pid 101)"),
            reporter(PREFIX + "/js", MIB, process="Extensions (pid 101)"),
            reporter(PREFIX + "/js", 7 * MIB, process="Extensions (pid 202)"),
        ]
        self.assertEqual(subtree_summary(rows, PREFIX)["bytes"], 9 * MIB)
        self.assertEqual(
            subtree_summary(rows, PREFIX, process_contains="PID 101")["bytes"], 2 * MIB
        )

    def test_sorted_descendant_detection_handles_nearby_prefixes(self):
        rows = [
            reporter(PREFIX + "/a", 6 * MIB),
            reporter(PREFIX + "/a-/other", 100 * MIB),
            reporter(PREFIX + "/a/child", 6 * MIB),
        ]
        result = subtree_summary(rows, PREFIX)
        self.assertEqual(result["bytes"], 106 * MIB)

    def test_compare_reports_signed_categories(self):
        before = subtree_summary(sample(js=40, styles=20), PREFIX)
        after = subtree_summary(sample(js=30, styles=18), PREFIX)
        output = compare_snapshots(before, after)
        self.assertEqual(output["delta_bytes"], -12 * MIB)
        self.assertEqual(output["delta_mib"], -12)
        self.assertEqual(output["change_percent"], -20.0)
        self.assertEqual(output["category_deltas"][0]["category"], "js")
        self.assertEqual(output["category_deltas"][0]["delta_bytes"], -10 * MIB)

    def test_missing_matching_reporters_fails_instead_of_zero(self):
        with self.assertRaisesRegex(ValueError, "No byte reporters"):
            subtree_summary(sample(), "explicit/add-ons/missing")
        with self.assertRaisesRegex(ValueError, "No byte reporters"):
            subtree_summary([reporter(PREFIX + "/count", 2, units=1)], PREFIX)

    def test_invalid_or_unavailable_values(self):
        self.assertEqual(byte_reporters([reporter(PREFIX, -1)]), {})
        for amount in (-2, 1.5, True, "15"):
            with self.subTest(amount=amount):
                with self.assertRaises(ValueError):
                    byte_reporters([reporter(PREFIX, amount)])

    def test_unknown_byte_reporters_make_comparison_inconclusive(self):
        before = subtree_summary(sample(), PREFIX)
        incomplete = subtree_summary(
            sample() + [reporter(PREFIX + "/unmeasured", -1)], PREFIX
        )
        self.assertEqual(incomplete["unavailable_byte_reporters"], 1)
        self.assertEqual(incomplete["bytes"], 60 * MIB)
        with self.assertRaisesRegex(ValueError, "incomplete scopes"):
            compare_snapshots(before, incomplete)

    def test_inspect_is_explicitly_non_additive(self):
        result = inspect_paths(sample(), "add-ons", 3)
        self.assertEqual(result["matched_reporters"], 5)
        self.assertEqual(len(result["reporters"]), 3)
        self.assertIn("not an additive total", result["warning"])

    def test_plain_and_gzip_exports_both_load(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            payload = json.dumps({"version": 1, "reports": sample()}).encode()
            (directory / "report.json").write_bytes(payload)
            (directory / "report-with-wrong-suffix.bin").write_bytes(gzip.compress(payload))
            for name in ("report.json", "report-with-wrong-suffix.bin"):
                with self.subTest(name=name):
                    self.assertEqual(len(load_reports(directory / name)), len(sample()))

    def test_unsupported_document_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "bad.json"
            file.write_text('{"records": []}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "reports array"):
                load_reports(file)
            file.write_text('not json', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid Firefox"):
                load_reports(file)

    def test_cli_summarize_produces_machine_readable_json(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "export.json.gz"
            with gzip.open(file, "wb") as stream:
                stream.write(json.dumps({"version": 1, "reports": sample()}).encode())
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(
                    main(["summarize", str(file), "--prefix", PREFIX]), 0
                )
            parsed = json.loads(stdout.getvalue())
            self.assertEqual(parsed["bytes"], 60 * MIB)
            self.assertNotIn("largest_leaf_paths", parsed)

    def test_cli_compare_accepts_different_addon_identity(self):
        other_prefix = "explicit/add-ons/meBlock@example.invalid"
        with tempfile.TemporaryDirectory() as temp:
            original, fork = Path(temp) / "a.json", Path(temp) / "b.json"
            original.write_text(json.dumps({"reports": sample()}), encoding="utf-8")
            other = [
                {**row, "path": row["path"].replace(PREFIX, other_prefix)}
                for row in sample(js=30, styles=20)
            ]
            fork.write_text(json.dumps({"reports": other}), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(
                    main([
                        "compare", str(original), str(fork), "--prefix", PREFIX,
                        "--after-prefix", other_prefix,
                    ]), 0
                )
            result = json.loads(stdout.getvalue())
            self.assertEqual(result["delta_bytes"], -10 * MIB)


if __name__ == "__main__":
    unittest.main()
