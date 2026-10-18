/* Test-only entrypoint. Installed only into isolated Firefox build artifacts. */
import µb from './background.js';
import { checkDynamicFirewall, checkPersistedSettings } from './meblock-state-probe.js';
import { FilteringContext } from './filtering-context.js';
import sfne from './static-net-filtering.js';
import { redirectEngine } from './redirect-engine.js';
import {
    benchmarkStaticNetFiltering,
    benchmarkCosmeticFiltering,
    benchmarkScriptletFiltering,
} from './benchmarks.js';

const config = __MEBLOCK_CONFIG__;
const headers = { 'Content-Type': 'application/json' };
const base = config.endpoint;

async function report(payload) {
    const response = await fetch(base + '/result', {
        method: 'POST',
        headers,
        body: JSON.stringify({ token: config.token, role: config.role, ...payload }),
    });
    if (!response.ok) {
        throw new Error('Benchmark result upload failed: HTTP ' + response.status);
    }
}

async function ready() {
    const end = Date.now() + 90000;
    while (µb.readyToFilter !== true) {
        if (Date.now() > end) { throw new Error('Filtering engine did not become ready'); }
        await new Promise(resolve => setTimeout(resolve, 100));
    }
}

function prepareContext(fctxt, request) {
    fctxt.setURL(request.url);
    if (fctxt.getIPAddress() === '') {
        fctxt.setIPAddress('93.184.215.14\n2606:2800:21f:cb07:6820:80da:af6b:8b2c');
    }
    fctxt.setDocOriginFromURL(request.frameUrl);
    fctxt.setType(request.cpt === 'document' ? 'main_frame' : request.cpt === 'xhr' ? 'xmlhttprequest' : request.cpt);
}

async function main() {
    await ready();
    const persistence = await checkPersistedSettings(µb);
    if (persistence.continued === false) { return; }
    const dynamicFirewall = checkDynamicFirewall();
    µb.hiddenSettings.benchmarkDatasetURL = 'assets/meblock-requests.json';
    const response = await fetch(base + '/dataset', { cache: 'no-store' });
    if (!response.ok) { throw new Error('Dataset fetch failed: HTTP ' + response.status); }
    const data = await response.json();
    if (!Array.isArray(data) || data.length === 0) {
        throw new Error('Dataset missing or empty');
    }

    const official = await benchmarkStaticNetFiltering({ redirectEngine });
    if (!official.includes('Benchmarked static network filtering engine:')) {
        throw new Error('Upstream benchmark did not execute: ' + String(official));
    }
    const documents = data.filter(row => row.cpt === 'main_frame' || row.cpt === 'document');
    const extra = {};
    if (documents.length) {
        extra.cosmetic = await benchmarkCosmeticFiltering();
        extra.scriptlet = await benchmarkScriptletFiltering();
    }

    const fctxt = new FilteringContext();
    const warmupPasses = 2;
    const timedPasses = 5;
    const elapsedPerPass = [];
    for (let pass = 0; pass < warmupPasses + timedPasses; pass++) {
        const started = performance.now();
        let checksum = 0;
        for (const row of data) {
            prepareContext(fctxt, row);
            sfne.redirectURL = undefined;
            checksum += sfne.matchRequest(fctxt);
        }
        if (pass >= warmupPasses) {
            elapsedPerPass.push(performance.now() - started);
        }
        if (checksum < 0) { throw new Error('Invalid filtering checksum'); }
    }

    const outcomes = [];
    const modifierMasks = [];
    let blocked = 0;
    let allowed = 0;
    for (const row of data) {
        prepareContext(fctxt, row);
        sfne.redirectURL = undefined;
        const decision = sfne.matchRequest(fctxt);
        if (decision === 1) { blocked += 1; }
        if (decision === 2) { allowed += 1; }
        outcomes.push(decision);

        // Same modifier calls as upstream's benchmark, outside the timed loop.
        let mask = 0;
        if (decision !== 1) {
            if (sfne.transformURL(fctxt)) { mask |= 1; }
            if (sfne.hasQuery(fctxt) && sfne.filterQuery(fctxt)) { mask |= 2; }
            if (sfne.urlSkip(fctxt, false)) { mask |= 4; }
            if (fctxt.isDocument()) {
                if (sfne.matchAndFetchModifiers(fctxt, 'csp')) { mask |= 8; }
                if (sfne.matchAndFetchModifiers(fctxt, 'permissions')) { mask |= 16; }
            }
            sfne.matchHeaders(fctxt, [], []);
            if (sfne.matchAndFetchModifiers(fctxt, 'replace')) { mask |= 32; }
        } else {
            if (sfne.redirectRequest(redirectEngine, fctxt)) { mask |= 1; }
            if (fctxt.isRootDocument() && sfne.urlSkip(fctxt, true)) { mask |= 4; }
        }
        modifierMasks.push(mask);
    }
    const elapsed = elapsedPerPass.reduce((total, ms) => total + ms, 0);
    await report({
        status: 'ok',
        dataset_rows: data.length,
        warmup_passes: warmupPasses,
        measured_passes: timedPasses,
        individual_pass_ms: elapsedPerPass,
        engine_match_ms: elapsed,
        us_per_request: elapsed * 1000 / (data.length * timedPasses),
        outcomes,
        modifier_masks: modifierMasks,
        decisions: { blocked, allowed, passed: data.length - blocked - allowed },
        official,
        extra,
        state_persistence: persistence.state,
        dynamic_firewall: dynamicFirewall,
        engine_ready: µb.readyToFilter,
        engine_ready_after: µb.supportStats.allReadyAfter,
        selected_filter_lists: Array.from(µb.selectedFilterLists).sort(),
        manifest: browser.runtime.getManifest().version,
    });
}

main().catch(async error => {
    console.error('meBlock benchmark probe failure', error);
    try { await report({ status: 'error', message: String(error), stack: String(error?.stack || '') }); }
    catch (uploadError) { console.error('Failed to report benchmark failure', uploadError); }
});
