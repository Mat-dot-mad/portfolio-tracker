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
    getComputedStyle: () => ({ getPropertyValue: name => ({
        '--pt-ike': '#0d6efd', '--pt-ikem': '#6610f2', '--pt-ikze': '#fd7e14',
        '--pt-ppk': '#6f42c1', '--pt-taxable': '#6c757d' })[name] || '' }),
});
vm.runInContext(fs.readFileSync(path.join(root, 'common.js'), 'utf8'), context);
vm.runInContext(fs.readFileSync(path.join(root, 'app.js'), 'utf8')
    .replace(/loadDashboard\(\);\s*$/, ''), context);
const { changeClass, withAlpha, computeChange, readableTextOn, categoryColors } = context;

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
    assert.equal(withAlpha('#fff', 0.12), 'rgba(255, 255, 255, 0.12)');   // --bs-emphasis-color
});

test('labels pick black or white by contrast, not a fixed white', () => {
    assert.equal(readableTextOn('#ffc107'), '#000');   // white measured 1.63:1
    assert.equal(readableTextOn('#fd7e14'), '#000');   // IKZE orange
    assert.equal(readableTextOn('#6610f2'), '#fff');
    assert.equal(readableTextOn('#495057'), '#fff');
});

test('wrappers keep their identity colour; other categories avoid red and green', () => {
    // A top-level const is a global *binding*, not a property of the global
    // object, so it is read through the sandbox rather than off `context`.
    // Spread copies it into this realm, so deepEqual compares values only.
    const palette = [...vm.runInContext('CATEGORY_COLORS', context)];
    const colors = [...categoryColors(['Interactive Brokers', 'BOSSA IKE-M', 'BOSSA IKE',
                                   'tastytrade', 'BOSSA IKZE', 'PPK'])];
    assert.deepEqual(colors.slice(1, 3), ['#6610f2', '#0d6efd']);
    assert.equal(colors[4], '#fd7e14');
    assert.equal(colors[5], '#6f42c1');
    // Non-wrappers take consecutive palette slots, so none is skipped.
    assert.deepEqual([colors[0], colors[3]], palette.slice(0, 2));
    for (const c of palette) {
        assert.ok(!['#198754', '#dc3545', '#20c997'].includes(c), `${c} reads as gain/loss`);
    }
});
