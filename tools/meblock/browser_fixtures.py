"""Private loopback-only browser fixtures for Firefox regression testing.

These fixtures are added only to throwaway XPI build outputs. Their paths,
filter rules and expected observations form one versioned behavior contract.
"""

from base64 import b64decode

FIXTURE_VERSION = 3

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
const isHidden = id => {
    const element = document.getElementById(id);
    if (!element) { return false; }
    const style = getComputedStyle(element);
    return style.display === 'none' || style.visibility === 'hidden';
};
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
}
