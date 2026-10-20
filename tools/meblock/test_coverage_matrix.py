"""Mutation-oriented checks for fixture-to-hot-path coverage declarations."""

from copy import deepcopy
import json
import unittest

from browser_fixtures import (
    DYNAMIC_FIREWALL_EXPECTED, FIXTURE_VERSION, PARAMETERS_WITH_ADDON,
    PARAMETERS_WITHOUT_ADDON, PRECEDENCE_EXPECTED, STATE_PERSISTENCE_KEY,
    WITH_ADDON, WITHOUT_ADDON,
)
from coverage_matrix import (
    CASE_INDEX, FAMILY_IDS, MODULE_IMPACTS, NETWORK_FAMILIES, PAGE_FAMILIES,
    UNTESTED_FAMILIES, affected_families, family_coverage,
)


def evidence():
    report = {
        "fixture_version": FIXTURE_VERSION,
        "injected_filter_rules": 158,
        "unfiltered_fixture_control": {
            "page": deepcopy(WITHOUT_ADDON),
            "parameters": dict(PARAMETERS_WITHOUT_ADDON),
        },
    }
    run = {
        "fixture_precedence": list(PRECEDENCE_EXPECTED),
        "browser_behavior": deepcopy(WITH_ADDON),
        "browser_parameters": dict(PARAMETERS_WITH_ADDON),
        "dynamic_firewall": {
            "cases": dict(DYNAMIC_FIREWALL_EXPECTED), "rollback": True,
        },
        "state_persistence": {
            "setting": STATE_PERSISTENCE_KEY,
            "changed_and_reloaded": True, "restored": True,
            "originally": False,
        },
    }
    return report, {
        "upstream": [deepcopy(run)], "meblock": [deepcopy(run)],
    }


class FamilyCoverageTests(unittest.TestCase):
    def test_every_supported_family_has_real_independent_positive_and_negative_controls(self):
        report, runs = evidence()
        result = family_coverage(report, runs)
        self.assertEqual(set(result), set(FAMILY_IDS))
        for family in (*NETWORK_FAMILIES, *PAGE_FAMILIES):
            with self.subTest(family=family):
                self.assertTrue(result[family]["verified"])
                self.assertEqual(result[family]["firefox_runs"], 2)
        for family, names in NETWORK_FAMILIES.items():
            values = {PRECEDENCE_EXPECTED[CASE_INDEX[name]] for name in names}
            self.assertIn(0, values, family)
            self.assertTrue(values.intersection({1, 2}), family)
        for family in UNTESTED_FAMILIES:
            self.assertFalse(result[family]["verified"])
            self.assertIn("no independent fixture", result[family]["reason"])
        self.assertNotIn("https", json.dumps(result))

    def test_flipping_each_network_family_oracle_case_is_detected(self):
        report, original = evidence()
        for family, names in NETWORK_FAMILIES.items():
            for name in names:
                with self.subTest(family=family, case=name):
                    runs = deepcopy(original)
                    index = CASE_INDEX[name]
                    runs["meblock"][0]["fixture_precedence"][index] = (
                        PRECEDENCE_EXPECTED[index] + 1
                    ) % 3
                    result = family_coverage(report, runs)
                    self.assertFalse(result[family]["verified"])

    def test_each_page_family_rejects_single_mutated_field_and_incomplete_vector(self):
        report, original = evidence()
        for family, fields in PAGE_FAMILIES.items():
            for field in fields:
                with self.subTest(family=family, field=field):
                    runs = deepcopy(original)
                    bucket = (
                        "browser_parameters" if family == "network-removeparam"
                        else "dynamic_firewall" if family == "dynamic-firewall"
                        else "state_persistence" if family == "settings-persistence"
                        else "browser_behavior"
                    )
                    value = runs["upstream"][0][bucket][field]
                    if isinstance(value, list):
                        runs["upstream"][0][bucket][field] = value[:-1]
                    elif field == "originally":
                        runs["upstream"][0][bucket][field] = "not-a-boolean"
                    elif isinstance(value, bool):
                        runs["upstream"][0][bucket][field] = not value
                    else:
                        runs["upstream"][0][bucket][field] = "changed"
                    self.assertFalse(family_coverage(report, runs)[family]["verified"])

    def test_source_impact_map_includes_conservative_untested_contexts(self):
        self.assertGreaterEqual(len(MODULE_IMPACTS), 10)
        for module, expected in MODULE_IMPACTS.items():
            with self.subTest(module=module):
                resolved = affected_families([module])
                self.assertTrue(resolved["resolved"])
                self.assertEqual(resolved["unmapped_count"], 0)
                self.assertEqual(resolved["families"], sorted(set(expected)))
                self.assertTrue(set(expected) <= set(FAMILY_IDS))
                # A source-file edit must not be certified solely by passing
                # a narrow synthetic fixture; each known hot path includes
                # at least one not-yet-proven behavior.
                self.assertTrue(set(expected) & set(UNTESTED_FAMILIES))

    def test_source_impact_map_never_echoes_user_supplied_paths(self):
        inputs = (
            "src/js/static-net-filtering.js",
            "src/js/unknown-module.js",
            "../private/profile/secret.js",
        )
        result = affected_families(list(inputs))
        self.assertFalse(result["resolved"])
        self.assertEqual(result["unmapped_count"], 2)
        self.assertIn("network-hostname", result["families"])
        self.assertIn("network-advanced-modifiers", result["families"])
        for value in inputs:
            self.assertNotIn(value, json.dumps(result))
        self.assertEqual(affected_families([])["resolved"], False)
        self.assertFalse(affected_families(["src/js/static-net-filtering.js/"])["resolved"])
        self.assertFalse(affected_families([None])["resolved"])

    def test_stale_fixture_no_addon_mutation_and_missing_browser_run_fail_closed(self):
        report, runs = evidence()
        report["fixture_version"] -= 1
        self.assertTrue(all(
            not result["verified"]
            for result in family_coverage(report, runs).values()
        ))
        report, runs = evidence()
        report["unfiltered_fixture_control"]["page"][
            "extra_procedural_has_hidden_by_index"
        ][0] = True
        self.assertTrue(all(
            not result["verified"]
            for result in family_coverage(report, runs).values()
        ))
        report, runs = evidence()
        runs["meblock"] = []
        self.assertTrue(all(
            not result["verified"]
            for result in family_coverage(report, runs).values()
        ))


if __name__ == "__main__":
    unittest.main()
