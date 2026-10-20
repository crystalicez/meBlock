"""Public-safe, behavior-indexed hot-path readiness from private Firefox runs.

A passing family only proves the named synthetic cases, not all filter
syntax, host contexts, or real browsing. Never expose request URLs/rules.
"""

from browser_fixtures import (
    DYNAMIC_FIREWALL_EXPECTED, FIXTURE_VERSION,
    PARAMETERS_WITHOUT_ADDON, PARAMETERS_WITH_ADDON,
    PRECEDENCE_CASES, PRECEDENCE_EXPECTED,
    STATE_PERSISTENCE_KEY, WITH_ADDON, WITHOUT_ADDON,
)


NETWORK_FAMILIES = {
    "network-hostname": (
        "block", "block-near-miss", "exception-near-miss",
    ),
    "network-exceptions": (
        "type-specific-exception", "type-not-excepted",
        "exception-near-miss", "third-party-exception",
        "same-host-not-excepted",
    ),
    "network-type-party": (
        "script-type-block", "non-script-passes",
        "third-party-block", "same-host-party-passes",
        "type-and-party-matched", "type-and-party-first-party",
        "type-and-party-wrong-type", "first-party-alias",
        "first-party-alias-external",
    ),
    "network-domain-scoped": (
        "document-scoped-exception", "other-document-not-excepted",
        "multi-domain-included", "multi-domain-excluded",
        "multi-domain-unrelated", "negative-domain-other",
        "negative-domain-excluded", "multi-domain-exception",
        "multi-domain-exception-excluded",
    ),
    "network-priority-badfilter": (
        "important-block-over-exception",
        "disabled-by-badfilter", "type-not-excepted",
    ),
    "network-regex-case": (
        "match-case-exact-path", "match-case-other-case",
        "match-case-wrong-type",
    ),
    "network-wildcard-anchors": (
        "wildcard-path-positive", "wildcard-path-near-miss",
        "wildcard-path-wrong-type", "end-anchor-exact",
        "end-anchor-not-extension", "separator-url",
        "separator-near-miss",
    ),
    "network-from-scope": (
        "from-allowed-source", "from-other-source",
    ),
}

PAGE_FAMILIES = {
    "cosmetic-basic": (
        "extra_cosmetic_present_by_index",
        "extra_cosmetic_hidden_by_index",
        "extra_cosmetic_near_miss_visible_by_index",
    ),
    "cosmetic-has-text": (
        "extra_procedural_present_by_index",
        "extra_procedural_hidden_by_index",
        "extra_procedural_near_miss_visible_by_index",
    ),
    "cosmetic-has": (
        "extra_procedural_has_present_by_index",
        "extra_procedural_has_hidden_by_index",
        "extra_procedural_has_near_miss_visible_by_index",
    ),
    "cosmetic-matches-css": (
        "extra_procedural_css_present_by_index",
        "extra_procedural_css_hidden_by_index",
        "extra_procedural_css_near_miss_visible_by_index",
    ),
    "scriptlet-set-constant": (
        "extra_scriptlets_applied_by_index",
        "extra_scriptlet_near_miss_untouched_by_index",
    ),
    "network-redirect": (
        "redirected_script_executed",
        "redirected_script_loaded",
    ),
    "network-removeparam": (
        "tracking_parameter_present",
        "keep_parameter_preserved",
        "fixture_path_correct",
    ),
    "dynamic-firewall": ("cases", "rollback"),
    "settings-persistence": (
        "setting", "changed_and_reloaded", "restored", "originally",
    ),
}

UNTESTED_FAMILIES = (
    "scriptlet-other-families",
    "network-advanced-modifiers",
    "cosmetic-exceptions",
    "filter-list-update-compilation",
    "external-list-rule-contexts",
    "user-filter-rule-contexts",
    "nested-frame-contexts",
)

FAMILY_IDS = (
    *NETWORK_FAMILIES, *PAGE_FAMILIES, *UNTESTED_FAMILIES
)

CASE_INDEX = {case[0]: index for index, case in enumerate(PRECEDENCE_CASES)}


def family_coverage(report, runs):
    """Return bounded aggregate proof statuses; never serialize request content."""
    control = report.get("unfiltered_fixture_control") or {}
    base = (
        report.get("fixture_version") == FIXTURE_VERSION
        and type(report.get("injected_filter_rules")) is int
        and report["injected_filter_rules"] > 0
        and isinstance(runs, dict)
        and all(isinstance(runs.get(role), list) and len(runs[role]) >= 1
                for role in ("upstream", "meblock"))
        and control.get("page") == WITHOUT_ADDON
        and control.get("parameters") == PARAMETERS_WITHOUT_ADDON
    )
    observed = [
        run for role in ("upstream", "meblock")
        for run in runs.get(role, [])
    ] if base else []
    proof = {}
    for family, names in NETWORK_FAMILIES.items():
        index_list = [CASE_INDEX.get(name) for name in names]
        usable = (
            base and all(index is not None for index in index_list)
            and all(
                isinstance(run.get("fixture_precedence"), list)
                and len(run["fixture_precedence"]) == len(PRECEDENCE_EXPECTED)
                and all(type(run["fixture_precedence"][index]) is int
                        and run["fixture_precedence"][index] ==
                        PRECEDENCE_EXPECTED[index] for index in index_list)
                for run in observed
            )
        )
        proof[family] = {
            "verified": bool(usable),
            "oracle_cases": len(index_list),
            "firefox_runs": len(observed) if usable else 0,
        }
    for family, fields in PAGE_FAMILIES.items():
        if family == "network-removeparam":
            key = "browser_parameters"
            expected = PARAMETERS_WITH_ADDON
        elif family == "dynamic-firewall":
            key = "dynamic_firewall"
            expected = {"cases": DYNAMIC_FIREWALL_EXPECTED, "rollback": True}
        elif family == "settings-persistence":
            key = "state_persistence"
            expected = {
                "setting": STATE_PERSISTENCE_KEY, "changed_and_reloaded": True,
                "restored": True,
            }
        else:
            key = "browser_behavior"
            expected = WITH_ADDON
        def matches(run):
            value = run.get(key)
            return (
                isinstance(value, dict)
                and all(
                    field in value
                    and (
                        type(value[field]) is bool
                        if family == "settings-persistence" and field == "originally"
                        else value[field] == expected[field]
                    )
                    for field in fields
                )
            )
        usable = base and all(matches(run) for run in observed)
        proof[family] = {
            "verified": bool(usable),
            "oracle_fields": len(fields),
            "firefox_runs": len(observed) if usable else 0,
        }
    for family in UNTESTED_FAMILIES:
        proof[family] = {
            "verified": False, "reason": "no independent fixture for this family",
        }
    return proof
