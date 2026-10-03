// Header sort direction must match the arrow shown: ▼ means largest first.
// Both tables used to compare b to a in their numeric branches and then
// reverse again for descending, so ▼ silently listed the smallest first.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function load(file, autostart) {
    const context = vm.createContext({});
    const source = fs.readFileSync(path.join(__dirname, '../../static', file), 'utf8');
    vm.runInContext(source.replace(new RegExp(`${autostart}\\(\\);\\s*$`), ''), context);
    return context;
}

const compare = load('compare.js', 'loadCompare');
const groups = [
    { tags: 'B', account: 'x', valA: 10, valB: 13, change: 3 },
    { tags: 'A', account: 'y', valA: 50, valB: 81, change: 31 },
    { tags: 'C', account: 'z', valA: 30, valB: 15, change: -15 },
];
const order = (ctx, fn, rows) => ctx[fn](rows).map(g => g.tags).join('');
const set = (ctx, code) => vm.runInContext(code, ctx);

test('compare: default ▼ on Change puts the largest movers first', () => {
    set(compare, "sortCol = 'change'; sortAsc = false;");
    // By magnitude: 31, then -15, then 3.
    assert.equal(order(compare, 'sortDiffGroups', groups), 'ACB');
});

test('compare: ▲ on Change reverses it', () => {
    set(compare, "sortCol = 'change'; sortAsc = true;");
    assert.equal(order(compare, 'sortDiffGroups', groups), 'BCA');
});

test('compare: ▼ on a value column is largest first', () => {
    set(compare, "sortCol = 'valB'; sortAsc = false;");
    assert.equal(order(compare, 'sortDiffGroups', groups), 'ACB');   // 81, 15, 13
    set(compare, "sortCol = 'valA'; sortAsc = false;");
    assert.equal(order(compare, 'sortDiffGroups', groups), 'ACB');   // 50, 30, 10
});

test('compare: text columns keep A→Z on ▲', () => {
    set(compare, "sortCol = 'tags'; sortAsc = true;");
    assert.equal(order(compare, 'sortDiffGroups', groups), 'ABC');
});

const dashboard = load('app.js', 'loadDashboard');
const rows = [
    { tags: 'B', account: 'x', values: { 1: 10, 2: 13 } },
    { tags: 'A', account: 'y', values: { 1: 50, 2: 81 } },
    { tags: 'C', account: 'z', values: { 1: 30, 2: 15 } },
];
// Two visible quarters, ids 1 then 2, so Change = values[2] - values[1].
set(dashboard, 'getVisibleQuarters = () => [{ id: 1 }, { id: 2 }];');

test('dashboard: ▼ on a quarter column is largest first', () => {
    set(dashboard, "breakdownState.sortCol = '2'; breakdownState.sortAsc = false;");
    assert.equal(order(dashboard, 'sortGroups', rows), 'ACB');   // 81, 15, 13
});

test('dashboard: ▼ on Change is largest increase first', () => {
    set(dashboard, "breakdownState.sortCol = 'change'; breakdownState.sortAsc = false;");
    assert.equal(order(dashboard, 'sortGroups', rows), 'ABC');   // +31, +3, -15
});
