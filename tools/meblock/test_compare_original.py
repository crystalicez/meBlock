"""Regression tests for the two-build Firefox comparison tool."""

import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import urllib.request
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("compare_original", HERE / "compare_original.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def official(blocked=1):
    return (
        "Benchmarked static network filtering engine:\n"
        "Evaluated 3 requests in 12 ms\n"
        "Average: 4.000 ms per request\n"
        f"Not blocked: {2 if blocked == 1 else 1}\n"
        f"Blocked: {blocked}\n"
        "Unblocked: 0\n"
        "redirect=: 0\nremoveparam=: 0\nurlskip=: 0\n"
        "csp=: 0\npermissions=: 0\nreplace=: 0\n"
    )


def make_run(outcomes=None, speed=7):
    outcomes = [1, 0, 0] if outcomes is None else outcomes
    blocked = sum(v == 1 for v in outcomes)
    return {
        "outcomes": outcomes, "us_per_request": speed,
        "dataset_rows": 3, "official": official(blocked),
        "decisions": {
            "blocked": blocked, "allowed": 0,
            "passed": 3 - blocked,
        },
    }


DATASET = [
    {"frameUrl": "https://test.invalid", "url": f"https://test.invalid/{i}", "cpt": "script"}
    for i in range(3)
]


class CompareTests(unittest.TestCase):
    def test_equal_behavior_with_identical_runs_is_inconclusive(self):
        data = {"upstream": [make_run() for _ in range(5)],
                "meblock": [make_run() for _ in range(5)]}
        report = runner.compare(data, DATASET)
        self.assertTrue(report["correctness"]["per_request_match"])
        self.assertIn("INCONCLUSIVE", report["verdict"])

    def test_regression_detected_even_if_aggregate_counts_match(self):
        data = {
            "upstream": [make_run() for _ in range(5)],
            "meblock": [make_run([0, 1, 0]) for _ in range(5)],
        }
        outcome = runner.compare(data, DATASET)
        self.assertEqual(outcome["verdict"], "FAIL: behavioral regression")
        self.assertEqual(outcome["correctness"]["mismatches_first_25"][0]["request_index"], 0)

    def test_incorrect_upstream_report_rejected(self):
        data = {
            "upstream": [make_run() for _ in range(5)],
            "meblock": [make_run() for _ in range(5)],
        }
        data["meblock"][1]["official"] = official(2)
        with self.assertRaisesRegex(ValueError, "counts disagree"):
            runner.compare(data, DATASET)

    def test_missing_outcome_not_accepted(self):
        with self.assertRaisesRegex(ValueError, "complete decision vector"):
            runner.check_behavior([make_run()], [make_run([1])], DATASET)

    def test_source_delta(self):
        self.assertEqual(runner.source_delta({"a": "1"}, {"a": "2", "b": "3"})["files_different"], 2)


class CollectorTests(unittest.TestCase):
    def test_local_dataset_and_authenticated_report(self):
        state = runner.State(b'[]', "correct-secret")
        server = runner.Collector(state)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        endpoint = f"http://127.0.0.1:{server.server_port}"
        try:
            for path in ("/dataset", "/dataset?_=8"):
                with urllib.request.urlopen(endpoint + path, timeout=5) as result:
                    self.assertEqual(result.read(), b'[]')
            body = json.dumps({"token": "correct-secret", "role": "upstream", "status": "ok"}).encode()
            request = urllib.request.Request(
                endpoint + "/result", data=body,
                headers={"Content-Type": "application/json"}, method="POST"
            )
            with urllib.request.urlopen(request, timeout=5) as result:
                self.assertEqual(result.status, 200)
            self.assertEqual(state.wait("upstream", 1)["status"], "ok")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
