const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const context = vm.createContext({});
const source = fs.readFileSync(path.join(__dirname, '../../static/forecast.js'), 'utf8');
// Load the calculation functions without starting a page fetch.
vm.runInContext(source.replace(/loadForecast\(\);\s*$/, ''), context);

test('forecast consumes server returns, not PPK or mortgage balance movements', () => {
    const timeline = [
        {net_worth: 15000, portfolio_total: 105000, cash_total: 0, mortgage_total: 90000,
         market_return: null, net_contributions: 100000},
        {net_worth: 50000, portfolio_total: 130000, cash_total: 0, mortgage_total: 80000,
         market_return: .05, net_contributions: 10000},
    ];
    const stats = context.computeHistoricalStats(timeline);
    assert.deepEqual(Array.from(stats.nw.returns), [.05]);
    assert.ok(Math.abs(stats.nw.historicalAnnual - (1.05 ** 4 - 1)) < 1e-10);
    assert.equal(stats.contribution.recentAvg, 10000);
    const paths = context.runMonteCarlo(stats, {
        startNW: 50000, contribution: 10000, annualReturn: 1.05 ** 4 - 1,
    }, 1, 3);
    for (const run of paths) assert.ok(Math.abs(run[0] - 62500) < 1e-8);
});

test('unavailable period returns are excluded from the sampling pool', () => {
    const stats = context.computeHistoricalStats([
        {net_worth: 0, market_return: null},
        {net_worth: 100, market_return: null},
        {net_worth: 110, market_return: .1},
    ]);
    assert.deepEqual(Array.from(stats.nw.returns), [.1]);
});
