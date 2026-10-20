/* Test-only entrypoint. Installed only into isolated Firefox build artifacts. */
import µb from './background.js';
import { checkDynamicFirewall, checkPersistedSettings } from './meblock-state-probe.js';
import { FilteringContext } from './filtering-context.js';
import sfne, { meblockEngineBufferFootprint } from './static-net-filtering.js';
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
    // Probe starts after filter compilation, not immediately as
    // readyToFilter flips. This does not guarantee cross-profile parity.
    await new Promise(resolve => setTimeout(resolve, 3000));
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
    if (µb.userSettings.autoUpdate !== false) {
        throw new Error('Test-only Firefox build did not freeze filter updates');
    }
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

    const engineBuffers = meblockEngineBufferFootprint();
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

    // Diagnostic per-request wall time, separate from the aggregate speed
    // benchmark. Timer reads are included and may dominate tiny lookups.
    const requestLatency = [];
    const requestLatencyPasses = 3;
    const clockReadSamples = [];
    for (let index = 0; index < 2048; index++) {
        const begin = performance.now();
        clockReadSamples.push((performance.now() - begin) * 1000);
    }
    for (let pass = 0; pass < requestLatencyPasses; pass++) {
        for (const row of data) {
            prepareContext(fctxt, row); // Context construction is not timed.
            sfne.redirectURL = undefined;
            const start = performance.now();
            sfne.matchRequest(fctxt);
            const us = (performance.now() - start) * 1000;
            if (!Number.isFinite(us) || us < 0) {
                throw new Error('Invalid matcher wall-time sample');
            }
            requestLatency.push(us);
        }
    }
    requestLatency.sort((a, b) => a - b);
    clockReadSamples.sort((a, b) => a - b);
    const nearestRank = (values, fraction) =>
        values[Math.ceil(values.length * fraction) - 1];
    const latency = {
        schema: 'meblock-request-latency-v2',
        clock_step_us: clockReadSamples.find(value => value > 0) || 0,
        sample_count: requestLatency.length,
        passes: requestLatencyPasses,
        zero_sample_count: requestLatency.filter(value => value === 0).length,
        min_us: requestLatency[0],
        median_us: nearestRank(requestLatency, 0.5),
        p95_us: nearestRank(requestLatency, 0.95),
        p99_us: nearestRank(requestLatency, 0.99),
        max_us: requestLatency[requestLatency.length - 1],
        empty_timer_pair_median_us: nearestRank(clockReadSamples, 0.5),
        empty_timer_pair_p95_us: nearestRank(clockReadSamples, 0.95),
    };

    // Amortized wall time per fixed request context, NOT single-call tail latency.
    // Each timed batch repeats a stable lookup many times to rise above the
    // approximately 20-us Firefox clock step. Every result is consumed and
    // checked against a control decision outside the timed region.
    const batchIterations = 512;
    const batchPasses = 3;
    const sensitivityOnly = config.sensitivity_control === true;
    if (sensitivityOnly && config.role !== 'meblock') {
        throw new Error('Timing sensitivity control must be applied only to the experimental role');
    }
    let sensitivityInjectedBatches = 0;
    const batchElapsed = [];
    const elapsedByType = new Map();
    const emptyLoopElapsed = [];
    let batchDecisionMismatches = 0;
    let batchChecksum = 0;
    for (let pass = 0; pass < batchPasses; pass++) {
        for (const row of data) {
            prepareContext(fctxt, row);
            sfne.redirectURL = undefined;
            const expectedDecision = sfne.matchRequest(fctxt);
            let observedSum = 0;
            const begin = performance.now();
            for (let iteration = 0; iteration < batchIterations; iteration++) {
                sfne.redirectURL = undefined;
                observedSum += sfne.matchRequest(fctxt);
            }
            // Disposable sensitivity-only positive control: add a known
            // 5ms delay after matching, inside the measured script batch.
            // This measures detection of an artificial timing signal,
            // not an engine regression or real request latency.
            if (sensitivityOnly && row.cpt === 'script') {
                const deadline = performance.now() + 5;
                let clockChecks = 0;
                while (performance.now() < deadline) { clockChecks++; }
                if (clockChecks === 0) { throw new Error('Sensitivity delay did not execute'); }
                sensitivityInjectedBatches++;
            }
            const elapsed = (performance.now() - begin) * 1000;
            if (!Number.isFinite(elapsed) || elapsed < 0) {
                throw new Error('Invalid batched matcher elapsed time');
            }
            batchDecisionMismatches += observedSum !== expectedDecision * batchIterations;
            batchChecksum += observedSum;
            batchElapsed.push(elapsed);
            if (!elapsedByType.has(row.cpt)) {
                elapsedByType.set(row.cpt, []);
            }
            elapsedByType.get(row.cpt).push(elapsed);
        }
        for (let trial = 0; trial < 32; trial++) {
            let emptySum = 0;
            const began = performance.now();
            for (let iteration = 0; iteration < batchIterations; iteration++) {
                emptySum += iteration & 1;
            }
            emptyLoopElapsed.push((performance.now() - began) * 1000);
            if (emptySum !== batchIterations / 2) {
                throw new Error('Empty-loop control did not execute');
            }
        }
    }
    if (batchChecksum < 0 || batchDecisionMismatches !== 0) {
        throw new Error('Batched matcher result changed across repeated calls');
    }
    batchElapsed.sort((a, b) => a - b);
    emptyLoopElapsed.sort((a, b) => a - b);
    const batchedTypeSamples = {};
    for (const [type, durations] of elapsedByType) {
        durations.sort((a, b) => a - b);
        batchedTypeSamples[type] = {
            sample_count: durations.length,
            minimum_batch_us: durations[0],
            p95_batch_us: nearestRank(durations, 0.95),
            maximum_batch_us: durations[durations.length - 1],
        };
    }
    const batchedMatcher = {
        schema: 'meblock-batched-context-match-v2',
        by_request_type: batchedTypeSamples,
        clock_step_us: latency.clock_step_us,
        contexts_per_pass: data.length,
        passes: batchPasses,
        iterations_per_context: batchIterations,
        sample_count: batchElapsed.length,
        decision_mismatch_count: batchDecisionMismatches,
        minimum_batch_us: batchElapsed[0],
        median_batch_us: nearestRank(batchElapsed, 0.5),
        p95_batch_us: nearestRank(batchElapsed, 0.95),
        p99_batch_us: nearestRank(batchElapsed, 0.99),
        maximum_batch_us: batchElapsed[batchElapsed.length - 1],
        empty_loop_median_us: nearestRank(emptyLoopElapsed, 0.5),
        empty_loop_p95_us: nearestRank(emptyLoopElapsed, 0.95),
    };

    const outcomes = [];
    const modifierMasks = [];
    // This emits only bit codes, never the matched filter's text or URL.
    // It is enabled only for disposable default-exception challenge XPIs.
    const challengeMatchSources = config.challenge_diagnostics ? [] : null;
    let blocked = 0;
    let allowed = 0;
    for (const row of data) {
        prepareContext(fctxt, row);
        sfne.redirectURL = undefined;
        const decision = sfne.matchRequest(fctxt);
        if (decision === 1) { blocked += 1; }
        if (decision === 2) { allowed += 1; }
        outcomes.push(decision);
        if (challengeMatchSources !== null) {
            const raw = decision === 0 ? '' : (sfne.toLogData()?.raw || '');
            // Exact log text is a *shape*, not reliable rule provenance:
            // a compiled/default rule may have identical normalized logging.
            const exactHostBlockShape = '||' + new URL(row.url).hostname + '^';
            let code = 0;
            if (raw === exactHostBlockShape) { code |= 1; }
            if (decision === 1 && sfne.isBlockImportant()) { code |= 2; }
            if (raw.startsWith('@@')) { code |= 4; }
            if (raw !== '' && raw !== exactHostBlockShape && !raw.startsWith('@@')) {
                code |= 8;
            }
            challengeMatchSources.push(code);
        }

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
    // Correctness-only oracle on disposable fixture filters. Not used in
    // default-list RAM/speed comparison builds or their request dataset.
    const fixturePrecedence = [];
    for (const ruleCase of config.precedence_cases) {
        prepareContext(fctxt, ruleCase);
        sfne.redirectURL = undefined;
        fixturePrecedence.push(sfne.matchRequest(fctxt));
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
        engine_buffer_footprint: engineBuffers,
        request_latency: latency,
        batched_matcher: batchedMatcher,
        timing_sensitivity_injected_batches: sensitivityInjectedBatches,
        outcomes,
        modifier_masks: modifierMasks,
        challenge_match_source_codes: challengeMatchSources || [],
        fixture_precedence: fixturePrecedence,
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
