/* Test-only state tests. This module is injected into disposable Firefox builds. */
import { sessionFirewall } from './filtering-engines.js';

const markerKey = '__meblock_regression_settings_roundtrip_v1';

function assertEqual(actual, expected, label) {
    if (actual !== expected) {
        throw new Error(`${label}: expected ${JSON.stringify(expected)}, received ${JSON.stringify(actual)}`);
    }
}

async function waitForStoredSetting(name, expected, defaultValue) {
    const until = Date.now() + 7000;
    while (Date.now() < until) {
        const stored = await browser.storage.local.get(name);
        const actual = stored[name] === undefined ? defaultValue : stored[name];
        if (actual === expected) { return; }
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error('Setting did not persist: ' + name);
}

export async function checkPersistedSettings(µb) {
    const key = 'colorBlindFriendly';
    const storedMarker = await browser.storage.local.get(markerKey);
    const marker = storedMarker[markerKey];
    if (marker !== undefined) {
        if (marker.version !== 1 || typeof marker.original !== 'boolean' ||
            typeof marker.target !== 'boolean' || marker.target === marker.original) {
            throw new Error('Invalid persisted state marker');
        }
        assertEqual(µb.userSettings[key], marker.target, 'Loaded setting after extension reload');
        await waitForStoredSetting(key, marker.target, µb.userSettingsDefault[key]);
        µb.changeUserSettings(key, marker.original);
        await waitForStoredSetting(key, marker.original, µb.userSettingsDefault[key]);
        await browser.storage.local.remove(markerKey);
        return {
            continued: true,
            state: {
                setting: key,
                changed_and_reloaded: true,
                restored: µb.userSettings[key] === marker.original,
                originally: marker.original,
            },
        };
    }

    const original = µb.userSettings[key];
    const target = !original;
    if (typeof original !== 'boolean' || typeof µb.changeUserSettings !== 'function') {
        throw new Error('User setting API unavailable for roundtrip');
    }
    µb.changeUserSettings(key, target);
    await waitForStoredSetting(key, target, µb.userSettingsDefault[key]);
    await browser.storage.local.set({
        [markerKey]: { version: 1, original, target },
    });
    browser.runtime.reload();
    return { continued: false };
}

export function checkDynamicFirewall() {
    const src = 'fixture-meblock.invalid';
    const des = 'media.fixture-meblock.invalid';
    const originalType = sessionFirewall.evaluateCell(src, '*', 'image');
    const originalDest = sessionFirewall.evaluateCell(src, des, '*');
    const evaluations = {};
    try {
        assertEqual(sessionFirewall.evaluateCellZY(src, des, 'image'), 0, 'Unmodified host policy');
        for (const [name, state] of [['block', 1], ['allow', 2], ['noop', 3]]) {
            sessionFirewall.setCell(src, '*', 'image', state);
            const observed = sessionFirewall.evaluateCellZY(src, des, 'image');
            assertEqual(observed, state, name + ' image policy');
            evaluations[name] = observed;
        }
        sessionFirewall.setCell(src, '*', 'image', 1);
        sessionFirewall.setCell(src, des, '*', 2);
        const moreSpecific = sessionFirewall.evaluateCellZY(src, des, 'image');
        assertEqual(moreSpecific, 2, 'Destination override precedence');
        evaluations.destination_override = moreSpecific;
    } finally {
        sessionFirewall.setCell(src, '*', 'image', originalType);
        sessionFirewall.setCell(src, des, '*', originalDest);
    }
    assertEqual(sessionFirewall.evaluateCellZY(src, des, 'image'), 0, 'Host policy rollback');
    return {
        cases: evaluations,
        rollback: true,
        source: src,
        destination: des,
    };
}
