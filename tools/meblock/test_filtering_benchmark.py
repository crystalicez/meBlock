"""Behavior checks for the offline uBlock SNFE benchmark helpers."""

import importlib.util
from pathlib import Path
import tempfile
import unittest


TOOL = Path(__file__).with_name("filtering_benchmark.py")
spec = importlib.util.spec_from_file_location("filtering_benchmark", TOOL)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def result_text(count=100000, ms=120, blocked=30000):
    return (
        "Benchmarked static network filtering engine:\n"
        f"Evaluated {count} requests in {ms} ms\n"
        "Average: 0.001 ms per request\n"
        f"Not blocked: {count - blocked}\n"
        f"Blocked: {blocked}\n"
        "Unblocked: 0\n"
        "redirect=: 0\n"
        "removeparam=: 0\n"
        "urlskip=: 0\n"
        "csp=: 0\n"
        "permissions=: 0\n"
        "replace=: 0\n"
    )


class DatasetTests(unittest.TestCase):
    def test_synthetic_fixture_is_deterministic(self):
        records = benchmark.synthesize(100, 42)
        self.assertEqual(records, benchmark.synthesize(100, 42))
        self.assertNotEqual(records, benchmark.synthesize(100, 43))
        self.assertEqual(len(records), 100)
        self.assertEqual(benchmark.valid_rows(records), records)

    def test_rejects_invalid_dataset(self):
        for value in ([], [{}], [{"url": "https://a.test"}]):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    benchmark.valid_rows(value)

    def test_same_output_has_stable_sha(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data.json"
            rows = benchmark.synthesize(100, 20)
            benchmark.save_dataset(path, rows, "synthetic", 20)
            digest = benchmark.file_sha256(path)
            benchmark.save_dataset(path, rows, "synthetic", 20)
            self.assertEqual(benchmark.file_sha256(path), digest)
            self.assertEqual(path.with_suffix(".json.manifest.json").is_file(), True)


class ReportTests(unittest.TestCase):
    def test_parse_multiple_reports_and_compute_units(self):
        parsed = benchmark.parse_report_text(result_text(ms=120) + result_text(ms=140))
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["requests"], 100000)
        self.assertEqual(parsed[0]["us_per_request_from_total"], 1.2)
        self.assertEqual(parsed[0]["counts"]["blocked"], 30000)

    def test_aggregate_counts_must_add_up(self):
        with self.assertRaisesRegex(ValueError, "do not add up"):
            benchmark.parse_report_text(result_text().replace("Blocked: 30000", "Blocked: 1"))

    def test_different_decisions_are_not_performance_pass(self):
        a = benchmark.parse_report_text(result_text() * 5)
        b = benchmark.parse_report_text(result_text(blocked=20000) * 5)
        outcome = benchmark.compare(a, b)
        self.assertEqual(outcome["aggregate_counts_equal"], False)
        self.assertIn("invalid", outcome["verdict"])
        self.assertFalse(outcome["per_request_decisions_verified"])

    def test_overlap_is_inconclusive(self):
        a = benchmark.parse_report_text(result_text(ms=120) * 5)
        b = benchmark.parse_report_text(result_text(ms=120) * 5)
        outcome = benchmark.compare(a, b)
        self.assertIn("inconclusive", outcome["verdict"])

    def test_insufficient_runs_is_inconclusive(self):
        a = benchmark.parse_report_text(result_text())
        b = benchmark.parse_report_text(result_text(ms=90))
        outcome = benchmark.compare(a, b)
        self.assertIn("fewer than five", outcome["verdict"])

    def test_request_count_mismatch_fails(self):
        a = benchmark.parse_report_text(result_text())
        b = benchmark.parse_report_text(result_text(count=200000))
        with self.assertRaises(ValueError):
            benchmark.compare(a, b)


if __name__ == "__main__":
    unittest.main()
