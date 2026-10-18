"""Regression tests for the two-build Firefox comparison tool."""

import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import urllib.request
import unittest
from zipfile import ZipFile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("compare_original", HERE / "compare_original.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
from browser_fixtures import (
    ROUTES, RULES, WITH_ADDON, WITHOUT_ADDON,
    PARAMETERS_WITH_ADDON, PARAMETERS_WITHOUT_ADDON,
)


def official(blocked=1, redirect=0):
    return (
        "Benchmarked static network filtering engine:\n"
        "Evaluated 3 requests in 12 ms\n"
        "Average: 4.000 ms per request\n"
        f"Not blocked: {2 if blocked == 1 else 1}\n"
        f"Blocked: {blocked}\n"
        "Unblocked: 0\n"
        f"redirect=: {redirect}\nremoveparam=: 0\nurlskip=: 0\n"
        "csp=: 0\npermissions=: 0\nreplace=: 0\n"
    )


def make_run(outcomes=None, speed=7, modifier_masks=None):
    outcomes = [1, 0, 0] if outcomes is None else outcomes
    modifier_masks = [0, 0, 0] if modifier_masks is None else modifier_masks
    blocked = sum(v == 1 for v in outcomes)
    return {
        "outcomes": outcomes, "modifier_masks": modifier_masks, "us_per_request": speed,
        "browser_behavior": dict(WITH_ADDON),
        "browser_parameters": dict(PARAMETERS_WITH_ADDON),
        "dataset_rows": 3, "official": official(
            blocked, redirect=sum(bool(mask & 1) for mask in modifier_masks)
        ),
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

    def test_identical_builds_are_control_even_when_time_differs(self):
        data = {
            "upstream": [make_run(speed=2) for _ in range(5)],
            "meblock": [make_run(speed=20) for _ in range(5)],
        }
        result = runner.compare(data, DATASET, identical_build=True)
        self.assertTrue(result["builds_identical"])
        self.assertIn("CONTROL", result["verdict"])

    def test_regression_detected_even_if_aggregate_counts_match(self):
        data = {
            "upstream": [make_run() for _ in range(5)],
            "meblock": [make_run([0, 1, 0]) for _ in range(5)],
        }
        outcome = runner.compare(data, DATASET)
        self.assertEqual(outcome["verdict"], "FAIL: behavioral regression")
        self.assertEqual(outcome["correctness"]["mismatches_first_25"][0]["request_index"], 0)

    def test_modifier_regression_detected_despite_equal_block_counts(self):
        data = {
            "upstream": [make_run() for _ in range(5)],
            "meblock": [make_run() for _ in range(5)],
        }
        for row in data["meblock"]:
            row["official"] = row["official"].replace("redirect=: 0", "redirect=: 1")
            row["modifier_masks"] = [1, 0, 0]
        report = runner.compare(data, DATASET)
        self.assertEqual(report["verdict"], "FAIL: behavioral regression")
        self.assertFalse(report["correctness"]["aggregate_modifier_counts_match"])

    def test_per_request_modifier_swap_fails_even_with_identical_aggregate(self):
        data = {
            "upstream": [make_run(modifier_masks=[1, 0, 0]) for _ in range(5)],
            "meblock": [make_run(modifier_masks=[0, 1, 0]) for _ in range(5)],
        }
        result = runner.compare(data, DATASET)
        self.assertEqual(result["verdict"], "FAIL: behavioral regression")
        self.assertTrue(result["correctness"]["aggregate_modifier_counts_match"])
        self.assertFalse(result["correctness"]["modifier_per_request_match"])
        self.assertEqual(
            result["correctness"]["modifier_mismatches_first_25"][0]["request_index"], 0
        )

    def test_modifier_count_mismatch_is_rejected(self):
        data = {
            "upstream": [make_run() for _ in range(5)],
            "meblock": [make_run() for _ in range(5)],
        }
        data["meblock"][0]["modifier_masks"] = [1, 0, 0]
        with self.assertRaisesRegex(ValueError, "modifier counts disagree"):
            runner.compare(data, DATASET)

    def test_missing_modifier_vector_is_rejected(self):
        row = make_run()
        row.pop("modifier_masks")
        with self.assertRaisesRegex(ValueError, "modifier vector"):
            runner.compare({"upstream": [row], "meblock": [make_run()]}, DATASET)

    def test_document_benchmark_must_process_every_document(self):
        dataset = [dict(row) for row in DATASET]
        dataset[0]["cpt"] = "main_frame"
        data = {
            "upstream": [make_run() for _ in range(5)],
            "meblock": [make_run() for _ in range(5)],
        }
        for role in ("upstream", "meblock"):
            for row in data[role]:
                row["extra"] = {
                    "cosmetic": "Evaluated 1 retrieval in 2 ms",
                    "scriptlet": "Evaluated 1 retrieval in 2 ms",
                }
        self.assertTrue(runner.compare(data, dataset)["correctness"]["auxiliary_benchmarks_match"])
        data["meblock"][0]["extra"]["scriptlet"] = "Evaluated 0 retrieval in 2 ms"
        with self.assertRaisesRegex(ValueError, "scriptlet benchmark"):
            runner.compare(data, dataset)

    def test_browser_fixture_fails_if_control_is_not_blocked(self):
        data = {
            "upstream": [make_run() for _ in range(5)],
            "meblock": [make_run() for _ in range(5)],
        }
        data["meblock"][0]["browser_behavior"]["blocked_script"] = True
        report = runner.compare(data, DATASET)
        self.assertEqual(report["verdict"], "FAIL: behavioral regression")
        self.assertFalse(report["correctness"]["browser_page_match"])

    def test_filter_list_selection_change_is_regression(self):
        data = {
            "upstream": [make_run() for _ in range(5)],
            "meblock": [make_run() for _ in range(5)],
        }
        for role in ("upstream", "meblock"):
            for row in data[role]:
                row["selected_filter_lists"] = ["ublock-filters"]
        data["meblock"][0]["selected_filter_lists"] = ["different-list"]
        report = runner.compare(data, DATASET)
        self.assertEqual(report["verdict"], "FAIL: behavioral regression")

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

    def test_fixture_contract_has_positive_and_negative_controls(self):
        self.assertTrue(all(WITHOUT_ADDON[key] is True for key in (
            "allowed_script", "blocked_script", "allowed_image", "blocked_image",
            "allowed_frame", "blocked_frame", "control_visible",
        )))
        self.assertFalse(WITHOUT_ADDON["cosmetic_hidden"])
        self.assertEqual(WITH_ADDON["blocked_script"], False)
        self.assertEqual(WITH_ADDON["blocked_image"], False)
        self.assertEqual(WITH_ADDON["blocked_frame"], False)
        self.assertTrue(WITH_ADDON["cosmetic_hidden"])
        self.assertEqual(len(RULES), 8)
        self.assertTrue(PARAMETERS_WITHOUT_ADDON["tracking_parameter_present"])
        self.assertFalse(PARAMETERS_WITH_ADDON["tracking_parameter_present"])
        self.assertFalse(WITH_ADDON["xhr_blocked"])
        self.assertTrue(WITH_ADDON["scriptlet_applied"])
        self.assertFalse(WITH_ADDON["redirected_script_executed"])
        self.assertTrue(WITH_ADDON["redirected_script_loaded"])

    def test_image_and_cosmetic_regressions_fail(self):
        for field, wrong in (
            ("blocked_image", True),
            ("blocked_frame", True),
            ("cosmetic_hidden", False),
            ("xhr_blocked", True),
            ("scriptlet_applied", False),
        ):
            with self.subTest(field=field):
                data = {
                    "upstream": [make_run() for _ in range(5)],
                    "meblock": [make_run() for _ in range(5)],
                }
                data["meblock"][0]["browser_behavior"][field] = wrong
                result = runner.compare(data, DATASET)
                self.assertEqual(result["verdict"], "FAIL: behavioral regression")

    def test_url_parameter_regression_detected(self):
        data = {
            "upstream": [make_run() for _ in range(5)],
            "meblock": [make_run() for _ in range(5)],
        }
        data["meblock"][0]["browser_parameters"]["tracking_parameter_present"] = True
        report = runner.compare(data, DATASET)
        self.assertEqual(report["verdict"], "FAIL: behavioral regression")
        self.assertFalse(report["correctness"]["url_modifier_match"])

    def test_source_delta(self):
        self.assertEqual(runner.source_delta({"a": "1"}, {"a": "2", "b": "3"})["files_different"], 2)


class PackageTests(unittest.TestCase):
    def test_instrumented_package_tracks_fork_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "build"
            (root / "assets" / "ublock").mkdir(parents=True)
            (root / "js").mkdir()
            (root / "manifest.json").write_text(
                json.dumps({"browser_specific_settings": {
                    "gecko": {"id": "meblock@example.invalid"}
                }}), encoding="utf-8"
            )
            (root / "background.html").write_text(
                "<html><body></body></html>", encoding="utf-8"
            )
            (root / "assets" / "ublock" / "filters.min.txt").write_text(
                "! test baseline\n", encoding="utf-8"
            )
            xpi = Path(folder) / "instrumented.xpi"
            result = runner.probe_package(
                root, xpi, "meblock", "http://127.0.0.1:8888", "test-token", b"[]"
            )
            self.assertEqual(result["addon_id"], "meblock@example.invalid")
            with ZipFile(xpi) as archive:
                self.assertEqual(archive.read("assets/meblock-requests.json"), b"[]")
                self.assertIn(b"meblock-probe.js", archive.read("background.html"))
                self.assertIn(
                    b"test-blocked.js", archive.read("assets/ublock/filters.min.txt")
                )


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
            for path, (expected_type, body) in ROUTES.items():
                with self.subTest(path=path):
                    with urllib.request.urlopen(endpoint + path, timeout=5) as response:
                        self.assertEqual(response.read(), body)
                        self.assertEqual(response.headers.get_content_type(), expected_type.split(";")[0])
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
