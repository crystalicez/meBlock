/* Disposable variance diagnostic; calls the original upstream SFNE benchmark only. */
import µb from './background.js';
import { benchmarkStaticNetFiltering } from './benchmarks.js';
import { redirectEngine } from './redirect-engine.js';

const config = __MEBLOCK_CONFIG__;

async function report(payload) {
    const response = await fetch(config.endpoint + '/result', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: config.token, role: config.role, ...payload }),
    });
    if (!response.ok) { throw new Error('Variance diagnostic post failed'); }
}

async function main() {
    const deadline = Date.now() + 90000;
    while (µb.readyToFilter !== true) {
        if (Date.now() > deadline) { throw new Error('Filter engine not ready'); }
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    await new Promise(resolve => setTimeout(resolve, 3000));
    if (µb.userSettings.autoUpdate !== false) {
        throw new Error('Disposable updates were not disabled');
    }
    µb.hiddenSettings.benchmarkDatasetURL = 'assets/meblock-requests.json';

    const warmups = 8;
    const trials = 12;
    const timing = [];
    const lastFive = [];
    for (let i = 0; i < warmups + trials; i++) {
        const text = await benchmarkStaticNetFiltering({ redirectEngine });
        const match = typeof text === 'string'
            ? /Evaluated [0-9,]+ requests in ([0-9,]+) ms/.exec(text)
            : null;
        if (match === null) { throw new Error('Original SFNE benchmark missing duration'); }
        timing.push(Number(match[1].replace(/,/g, '')));
        if (i >= warmups + trials - 5) { lastFive.push(text); }
    }
    await report({
        status: 'ok',
        engine_ready: µb.readyToFilter,
        selected_filter_lists: Array.from(µb.selectedFilterLists).sort(),
        official_runs: lastFive,
        diagnostic_warmup_ms: timing.slice(0, warmups),
        diagnostic_measured_ms: timing.slice(warmups),
        diagnostic_total_calls: timing.length,
        manifest_version: browser.runtime.getManifest().version,
    });
}

main().catch(async error => {
    try { await report({ status: 'error', message: String(error) }); }
    catch (uploadError) { console.error('Variance diagnostic post failed', uploadError); }
});
