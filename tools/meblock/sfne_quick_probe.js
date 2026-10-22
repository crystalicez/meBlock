/* Disposable Firefox probe: repeat only uBO's original SFNE benchmark. */
import µb from './background.js';
import { benchmarkStaticNetFiltering } from './benchmarks.js';
import { redirectEngine } from './redirect-engine.js';

const config = __MEBLOCK_CONFIG__;
const endpoint = config.endpoint;

async function report(payload) {
    const response = await fetch(endpoint + '/result', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            token: config.token, role: config.role, ...payload,
        }),
    });
    if (!response.ok) { throw new Error('Benchmark result post failed'); }
}

async function main() {
    const deadline = Date.now() + 90000;
    while (µb.readyToFilter !== true) {
        if (Date.now() > deadline) { throw new Error('Filter engine did not become ready'); }
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    await new Promise(resolve => setTimeout(resolve, 3000));
    if (µb.userSettings.autoUpdate !== false) {
        throw new Error('Disposable build did not freeze updates');
    }
    µb.hiddenSettings.benchmarkDatasetURL = 'assets/meblock-requests.json';
    const warmups = 2;
    const trials = 5;
    const officialRuns = [];
    for (let i = 0; i < warmups + trials; i++) {
        const result = await benchmarkStaticNetFiltering({ redirectEngine });
        if (typeof result !== 'string' ||
            !result.includes('Benchmarked static network filtering engine:')) {
            throw new Error('Original uBO benchmark did not finish');
        }
        if (i >= warmups) { officialRuns.push(result); }
    }
    await report({
        status: 'ok',
        engine_ready: µb.readyToFilter,
        official_runs: officialRuns,
        selected_filter_lists: Array.from(µb.selectedFilterLists).sort(),
        manifest_version: browser.runtime.getManifest().version,
    });
}

main().catch(async error => {
    try { await report({ status: 'error', message: String(error) }); }
    catch (uploadError) { console.error('Benchmark report failed', uploadError); }
});
