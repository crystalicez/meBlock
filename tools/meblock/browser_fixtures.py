"""Private loopback-only browser fixtures for Firefox regression testing.

These fixtures are added only to throwaway XPI build outputs. Their paths,
filter rules and expected observations form one versioned behavior contract.
"""

from base64 import b64decode

FIXTURE_VERSION = 6

DYNAMIC_FIREWALL_EXPECTED = {
    "block": 1,
    "allow": 2,
    "noop": 3,
    "destination_override": 2,
}

STATE_PERSISTENCE_KEY = "colorBlindFriendly"


RULES = (
    "||127.0.0.1^*test-blocked.js$script",
    "||127.0.0.1^*test-blocked.png$image",
    "||127.0.0.1^*test-blocked-frame.html$subdocument",
    "||127.0.0.1^*test-redirect.js$script,redirect=noop.js",
    "||127.0.0.1^*test-blocked.json$xmlhttprequest",
    "127.0.0.1##+js(set-constant, meblockFixtureConstant, true)",
    "||127.0.0.1^*fixture-params$removeparam=utm_source",
    "127.0.0.1##.meblock-probe-hide",
)


# Independently specified static-network precedence oracle, used ONLY in
# disposable correctness XPIs. None of these filters is in default-only
# RAM/speed acceptance builds.
PRECEDENCE_RULES = (
    "||mb-fixture-block.example.invalid^",
    "||mb-fixture-override.example.invalid^",
    "@@||mb-fixture-override.example.invalid^$script",
    "||mb-fixture-exception3p.example.invalid^",
    "@@||mb-fixture-exception3p.example.invalid^$third-party",
    "||mb-fixture-important.example.invalid^$important",
    "@@||mb-fixture-important.example.invalid^",
    "||mb-fixture-domain.example.invalid^$script",
    "@@||mb-fixture-domain.example.invalid^$script,domain=mb-fixture-reader.example.invalid",
    "||mb-fixture-type.example.invalid^$script",
    "||mb-fixture-party.example.invalid^$third-party",
    "||mb-fixture-disabled.example.invalid^$script",
    "||mb-fixture-disabled.example.invalid^$script,badfilter",
    "||mb-fixture-not3p.example.invalid^$~third-party",
)

# (name, request URL, document URL, request type, expected matchRequest decision)
# 0 = no match, 1 = block, 2 = explicit allow. Each expectation is an
# independent behavioral oracle, not copied from either browser run.
PRECEDENCE_CASES = (
    ("block", "mb-fixture-block", "reader", "script", 1),
    ("block-near-miss", "near-mb-fixture-block", "reader", "script", 0),
    ("type-specific-exception", "mb-fixture-override", "reader", "script", 2),
    ("type-not-excepted", "mb-fixture-override", "reader", "image", 1),
    ("exception-near-miss", "near-mb-fixture-override", "reader", "script", 0),
    ("third-party-exception", "mb-fixture-exception3p", "reader", "script", 2),
    ("same-host-not-excepted", "mb-fixture-exception3p", "self", "script", 1),
    ("important-block-over-exception", "mb-fixture-important", "reader", "script", 1),
    ("document-scoped-exception", "mb-fixture-domain", "mb-fixture-reader", "script", 2),
    ("other-document-not-excepted", "mb-fixture-domain", "reader", "script", 1),
    ("different-request-type", "mb-fixture-domain", "reader", "image", 0),
    ("script-type-block", "mb-fixture-type", "reader", "script", 1),
    ("non-script-passes", "mb-fixture-type", "reader", "image", 0),
    ("third-party-block", "mb-fixture-party", "reader", "script", 1),
    ("same-host-party-passes", "mb-fixture-party", "self", "script", 0),
    ("disabled-by-badfilter", "mb-fixture-disabled", "reader", "script", 0),
    ("first-party-only-block", "mb-fixture-not3p", "self", "script", 1),
    ("third-party-not-blocked", "mb-fixture-not3p", "reader", "script", 0),
)


def precedence_case_request(case):
    name, host, origin, kind, expected = case
    target_host = host + ".example.invalid"
    origin_host = (
        target_host if origin == "self"
        else "reader.test" if origin == "reader"
        else origin + ".example.invalid"
    )
    return {
        "frameUrl": "https://" + origin_host + "/",
        "url": "https://" + target_host + "/test." +
            ("png" if kind == "image" else "js"),
        "cpt": kind,
    }


PRECEDENCE_EXPECTED = tuple(case[4] for case in PRECEDENCE_CASES)

RULES += PRECEDENCE_RULES

EXTRA_COSMETIC_COUNT = 96
EXTRA_VISIBLE_COUNT = 32
EXTRA_SCRIPTLET_COUNT = 12


def extra_selector(index):
    suffix = f"{index:03}"
    if index % 3 == 0:
        return f".mb-extra-class-{suffix}"
    if index % 3 == 1:
        return f"#mb-extra-{suffix}"
    return f'[data-mb-extra="{suffix}"]'


RULES += tuple(
    f"127.0.0.1##{extra_selector(index)}"
    for index in range(EXTRA_COSMETIC_COUNT)
) + tuple(
    f"127.0.0.1##+js(set-constant, mbFixtureVar{index:03}, true)"
    for index in range(EXTRA_SCRIPTLET_COUNT)
)


IMAGE_PNG = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/FHkAAAAASUVORK5CYII="
)

PAGE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>meBlock fixture</title></head>
<body>
<div class="meblock-probe-hide" id="cosmetic-target">cosmetic target</div>
<div id="control-target">visible target</div>
<script src="/test-allowed.js"></script>
<script src="/test-blocked.js"></script>
<script src="/test-redirect.js" onload="window.__meblock_redirect_loaded = true" onerror="window.__meblock_redirect_loaded = false"></script>
<img id="allowed-image" src="/test-allowed.png" alt="">
<img id="blocked-image" src="/test-blocked.png" alt="">
<iframe id="allowed-frame" src="/test-allowed-frame.html"></iframe>
<iframe id="blocked-frame" src="/test-blocked-frame.html"></iframe>
<script>
window.__meblock_scriptlet_set = window.meblockFixtureConstant === true;
fetch("/test-allowed.json").then(r => r.json()).then(r => { window.__meblock_xhr_allowed = r.value === "allowed"; }).catch(() => {});
fetch("/test-blocked.json").then(r => r.json()).then(r => { window.__meblock_xhr_blocked = r.value === "blocked"; }).catch(() => {});
</script>
</body>
</html>
"""

PAGE = PAGE.replace(
    '<div id="control-target">visible target</div>',
    '<div id="control-target">visible target</div>' +
    ''.join(
        f'<div id="mb-extra-{index:03}" class="mb-extra-class-{index:03}" '
        f'data-mb-extra="{index:03}">cosmetic probe</div>'
        for index in range(EXTRA_COSMETIC_COUNT)
    ) +
    ''.join(
        f'<div id="mb-extra-{index:03}-near" '
        f'class="mb-extra-class-{index:03}-near" '
        f'data-mb-extra="{index:03}-near">cosmetic near miss</div>'
        for index in range(EXTRA_COSMETIC_COUNT)
    ) +
    ''.join(
        f'<div class="mb-extra-visible" data-mb-control="{index:03}">'
        'visible probe</div>'
        for index in range(EXTRA_VISIBLE_COUNT)
    )
)


ROUTES = {
    "/fixture": ("text/html; charset=utf-8", PAGE.encode("utf-8")),
    "/fixture-params": ("text/html; charset=utf-8", b"<!doctype html><title>meblock parameters</title>"),
    "/test-allowed.js": (
        "application/javascript", b"window.__meblock_allowed = true;"
    ),
    "/test-blocked.js": (
        "application/javascript", b"window.__meblock_blocked = true;"
    ),
    "/test-redirect.js": (
        "application/javascript", b"window.__meblock_redirect_executed = true;"
    ),
    "/test-allowed.png": ("image/png", IMAGE_PNG),
    "/test-blocked.png": ("image/png", IMAGE_PNG),
    "/test-allowed-frame.html": (
        "text/html; charset=utf-8",
        b"<!doctype html><html><body>meblock allowed frame</body></html>",
    ),
    "/test-blocked-frame.html": (
        "text/html; charset=utf-8",
        b"<!doctype html><html><body>meblock blocked frame</body></html>",
    ),
    "/test-allowed.json": ("application/json", b'{"value":"allowed"}'),
    "/test-blocked.json": ("application/json", b'{"value":"blocked"}'),
}

OBSERVATION_SCRIPT = """
const present = id => {
    const element = document.getElementById(id);
    return Boolean(element && element.complete && element.naturalWidth > 0);
};
const frame = (id, token) => {
    const element = document.getElementById(id);
    try {
        return Boolean(element && element.contentDocument &&
            element.contentDocument.body &&
            element.contentDocument.body.textContent.includes(token));
    } catch { return false; }
};
const hidden = element => {
    if (!element) { return false; }
    const style = getComputedStyle(element);
    return style.display === 'none' || style.visibility === 'hidden';
};
const isHidden = id => hidden(document.getElementById(id));
const suffix = index => String(index).padStart(3, '0');
const cosmeticPresent = Array.from({ length: 96 }, (_, index) =>
    document.getElementById('mb-extra-' + suffix(index)) !== null
);
const cosmeticHidden = Array.from({ length: 96 }, (_, index) =>
    isHidden('mb-extra-' + suffix(index))
);
const cosmeticNearMissVisible = Array.from({ length: 96 }, (_, index) => {
    const element = document.getElementById('mb-extra-' + suffix(index) + '-near');
    return Boolean(element && !hidden(element));
});
const visibleControls = Array.from({ length: 32 }, (_, index) => {
    const element = document.querySelector('[data-mb-control="' + suffix(index) + '"]');
    return Boolean(element && !hidden(element));
});
const scriptletApplied = Array.from({ length: 12 }, (_, index) =>
    window['mbFixtureVar' + suffix(index)] === true
);
const scriptletNearMissUntouched = Array.from({ length: 12 }, (_, index) =>
    !Object.prototype.hasOwnProperty.call(window, 'mbFixtureVar' + suffix(index) + '_near')
);
return {
    allowed_script: window.__meblock_allowed === true,
    blocked_script: window.__meblock_blocked === true,
    redirected_script_executed: window.__meblock_redirect_executed === true,
    redirected_script_loaded: window.__meblock_redirect_loaded === true,
    allowed_image: present('allowed-image'),
    blocked_image: present('blocked-image'),
    allowed_frame: frame('allowed-frame', 'meblock allowed frame'),
    blocked_frame: frame('blocked-frame', 'meblock blocked frame'),
    cosmetic_hidden: isHidden('cosmetic-target'),
    xhr_allowed: window.__meblock_xhr_allowed === true,
    xhr_blocked: window.__meblock_xhr_blocked === true,
    scriptlet_applied: window.__meblock_scriptlet_set === true,
    extra_cosmetic_present_by_index: cosmeticPresent,
    extra_cosmetic_hidden_by_index: cosmeticHidden,
    extra_cosmetic_near_miss_visible_by_index: cosmeticNearMissVisible,
    extra_visible_by_index: visibleControls,
    extra_scriptlets_applied_by_index: scriptletApplied,
    extra_scriptlet_near_miss_untouched_by_index: scriptletNearMissUntouched,
    extra_cosmetic_hidden_count: cosmeticHidden.filter(Boolean).length,
    extra_visible_count: visibleControls.filter(Boolean).length,
    extra_scriptlets_applied_count: scriptletApplied.filter(Boolean).length,
    control_visible: !isHidden('control-target')
};
"""

WITHOUT_ADDON = {
    "allowed_script": True,
    "blocked_script": True,
    "redirected_script_executed": True,
    "redirected_script_loaded": True,
    "allowed_image": True,
    "blocked_image": True,
    "allowed_frame": True,
    "blocked_frame": True,
    "cosmetic_hidden": False,
    "xhr_allowed": True,
    "xhr_blocked": True,
    "scriptlet_applied": False,
    "extra_cosmetic_present_by_index": [True] * EXTRA_COSMETIC_COUNT,
    "extra_cosmetic_hidden_by_index": [False] * EXTRA_COSMETIC_COUNT,
    "extra_cosmetic_near_miss_visible_by_index": [True] * EXTRA_COSMETIC_COUNT,
    "extra_visible_by_index": [True] * EXTRA_VISIBLE_COUNT,
    "extra_scriptlets_applied_by_index": [False] * EXTRA_SCRIPTLET_COUNT,
    "extra_scriptlet_near_miss_untouched_by_index": [True] * EXTRA_SCRIPTLET_COUNT,
    "extra_cosmetic_hidden_count": 0,
    "extra_visible_count": EXTRA_VISIBLE_COUNT,
    "extra_scriptlets_applied_count": 0,
    "control_visible": True,
}

PARAMETERS_WITHOUT_ADDON = {
    "tracking_parameter_present": True,
    "keep_parameter_preserved": True,
    "fixture_path_correct": True,
}

PARAMETERS_WITH_ADDON = {
    **PARAMETERS_WITHOUT_ADDON,
    "tracking_parameter_present": False,
}

WITH_ADDON = {
    **WITHOUT_ADDON,
    "blocked_script": False,
    "redirected_script_executed": False,
    "blocked_image": False,
    "blocked_frame": False,
    "cosmetic_hidden": True,
    "xhr_blocked": False,
    "scriptlet_applied": True,
    "extra_cosmetic_hidden_by_index": [True] * EXTRA_COSMETIC_COUNT,
    "extra_scriptlets_applied_by_index": [True] * EXTRA_SCRIPTLET_COUNT,
    "extra_cosmetic_hidden_count": EXTRA_COSMETIC_COUNT,
    "extra_scriptlets_applied_count": EXTRA_SCRIPTLET_COUNT,
}
