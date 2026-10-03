// Red and green mean bad and good for you, not down and up.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.join(__dirname, '../../static');

// Just enough of a browser for common.js's load-time theme setup.
const context = vm.createContext({
    localStorage: { getItem: () => null, setItem() {} },
    document: { documentElement: { setAttribute() {}, getAttribute: () => 'light' },
                getElementById: () => null },
    window: {},
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
});
vm.runInContext(fs.readFileSync(path.join(root, 'common.js'), 'utf8'), context);
vm.runInContext(fs.readFileSync(path.join(root, 'app.js'), 'utf8')
    .replace(/loadDashboard\(\);\s*$/, ''), context);
const { changeClass, withAlpha, computeChange } = context;

test('assets and net worth: up is good', () => {
    assert.equal(changeClass(500), 'text-positive');
    assert.equal(changeClass(-500), 'text-negative');
});

test('debt: paying it down is good', () => {
    assert.equal(changeClass(-7532, -1), 'text-positive');
    assert.equal(changeClass(7532, -1), 'text-negative');
});

test('neutral figures and no change get no colour', () => {
    assert.equal(changeClass(-9506, 0), '');   // cash moved into investments
    assert.equal(changeClass(0), '');
});

test('selling a position out is "sold", not a red -100%', () => {
    const qs = [{ id: 1 }, { id: 2 }];
    assert.match(computeChange({ 1: 26411, 2: 0 }, qs).pctHtml, /text-sold">sold</);
    assert.match(computeChange({ 2: 5864 }, qs).pctHtml, /text-new">new</);
    assert.match(computeChange({ 1: 100, 2: 90 }, qs).pctHtml, /text-negative">-10\.0%/);
});

test('withAlpha accepts the colour forms the tokens resolve to', () => {
    assert.equal(withAlpha('#198754', 0.7), 'rgba(25, 135, 84, 0.7)');
    assert.equal(withAlpha('rgb(234, 134, 143)', 0.13), 'rgba(234, 134, 143, 0.13)');
});
