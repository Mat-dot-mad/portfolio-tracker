// Add Data page — quarter imports and manual entries.
//
// Split out of app.js when these moved off the dashboard onto their own tab.
// The dashboard is a read-only view of the data; this page is the only place
// that writes it, so keeping the two apart means loading the dashboard no
// longer parses import code and vice versa.
//
// State here is deliberately just the quarter list. The dashboard payload
// carries every position of every quarter, which is far more than a selector
// needs, so this page uses /api/snapshots instead.

let snapshots = [];
let qualityData = null;
let manualLoadSeq = 0;

async function loadImportPage() {
    const app = document.getElementById('app');
    try {
        snapshots = await (await fetch('/api/snapshots')).json();
    } catch (err) {
        app.innerHTML = `<div class="alert alert-danger">Could not load quarters: ${err.message}</div>`;
        return;
    }

    app.innerHTML = '';
    app.appendChild(document.getElementById('import-template').content.cloneNode(true));
    initQuarterlyEntry();
    await loadQuality();
    for (const [kind, ui] of Object.entries(IMPORT_UI)) {
        document.getElementById(ui.input).addEventListener('change', () => {
            delete pendingImports[kind];
            document.getElementById(ui.result).replaceChildren();
        });
    }
}

// ── Quarter selector ────────────────────────────────

function initQuarterlyEntry() {
    const select = document.getElementById('entryQuarter');

    if (!snapshots.length) {
        select.innerHTML = '<option value="">No quarters yet — import a CSV first</option>';
        select.disabled = true;
        return;
    }

    select.disabled = false;
    // Quarter labels are YYYY-QQ everywhere in the app; the snapshot date is
    // implied by the quarter and only added noise here.
    select.innerHTML = snapshots.map(s =>
        `<option value="${s.id}">${s.quarter}</option>`
    ).join('');

    const requested = new URLSearchParams(location.search).get('snapshot');
    if (snapshots.some(s => String(s.id) === requested)) select.value = requested;
    loadManualEntries(select.value);
    select.addEventListener('change', () => loadManualEntries(select.value));
}

function getSelectedSnapshotDate() {
    const select = document.getElementById('entryQuarter');
    const snapshot = snapshots.find(s => s.id == select.value);
    return snapshot ? snapshot.snapshot_date : null;
}

// ── Currency conversion ─────────────────────────────

const rateCache = {};

async function fetchRate(currency, date) {
    if (currency === 'PLN') return { rate: 1.0, effective_date: date };
    const key = `${currency}/${date}`;
    if (rateCache[key]) return rateCache[key];
    const resp = await fetch(`/api/nbp-rate/${currency}/${date}`);
    if (!resp.ok) throw new Error('Could not fetch rate');
    const data = await resp.json();
    rateCache[key] = data;
    return data;
}

async function updateCashRowPreview(row) {
    const currency = row.querySelector('.cash-currency').value;
    const amount = parseFloat(row.querySelector('.cash-amount').value) || 0;
    const preview = row.querySelector('.cash-pln-preview');
    if (!amount || currency === 'PLN') { preview.textContent = ''; return; }
    const date = getSelectedSnapshotDate();
    if (!date) return;
    preview.textContent = 'loading...';
    try {
        const { rate, effective_date } = await fetchRate(currency, date);
        preview.textContent = `= ${formatPLN(amount * rate)} (rate: ${rate.toFixed(4)}, ${effective_date})`;
    } catch { preview.textContent = 'rate unavailable'; }
}

// ── Manual entries ──────────────────────────────────

async function loadManualEntries(snapshotId) {
    const seq = ++manualLoadSeq;
    const saveButton = document.getElementById('manualSave');
    saveButton.disabled = true;
    let entries;
    try {
        const resp = await fetch(`/api/manual-entries/${snapshotId}`);
        if (!resp.ok) throw new Error('Could not load balances. Select the quarter again to retry.');
        entries = await resp.json();
    } catch (err) {
        if (seq === manualLoadSeq) document.getElementById('saveStatus').textContent = err.message;
        return;
    }
    if (seq !== manualLoadSeq) return;
    saveButton.disabled = false;
    const container = document.getElementById('cashEntries');
    container.innerHTML = '';
    const cashEntries = entries.filter(e => e.type === 'cash');
    if (cashEntries.length === 0) { addCashRow(); }
    else { cashEntries.forEach(e => addCashRow(e.currency || 'PLN', e.original_amount ?? e.amount_pln, e.label)); }
    const mortgage = entries.find(e => e.type === 'mortgage');
    document.getElementById('mortgageAmount').value = mortgage ? mortgage.amount_pln : '';

    const ppk = entries.find(e => e.type === 'ppk');
    document.getElementById('ppkAmount').value = ppk ? ppk.amount_pln : '';
    if (ppk && ppk.label) document.getElementById('ppkLabel').value = ppk.label;
    document.getElementById('mortgageLabel').value = mortgage ? mortgage.label : 'Mortgage';
    document.getElementById('saveStatus').textContent = '';
}

function addCashRow(currency, amount, label) {
    const container = document.getElementById('cashEntries');
    const row = document.createElement('div');
    row.className = 'row g-2 mb-2 align-items-center';
    row.style.maxWidth = '700px';
    const cur = currency || 'PLN';
    row.innerHTML = `
        <div class="col-auto">
            <select class="form-select cash-currency" style="width: 90px;">
                <option value="PLN" ${cur === 'PLN' ? 'selected' : ''}>PLN</option>
                <option value="EUR" ${cur === 'EUR' ? 'selected' : ''}>EUR</option>
                <option value="USD" ${cur === 'USD' ? 'selected' : ''}>USD</option>
            </select>
        </div>
        <div class="col" style="max-width: 160px;">
            <input type="number" step="0.01" class="form-control cash-amount" placeholder="Amount" value="">
        </div>
        <div class="col">
            <input type="text" class="form-control cash-label" placeholder="Label (e.g. Savings)" value="">
        </div>
        <div class="col-auto">
            <button type="button" class="btn btn-outline-danger btn-sm" onclick="this.closest('.row').remove()" style="line-height: 1.7;">&times;</button>
        </div>
        <div class="col-12"><small class="cash-pln-preview text-muted"></small></div>
    `;
    row.querySelector('.cash-amount').value = amount ?? '';
    row.querySelector('.cash-label').value = label || '';
    container.appendChild(row);
    row.querySelector('.cash-currency').addEventListener('change', () => updateCashRowPreview(row));
    row.querySelector('.cash-amount').addEventListener('input', () => updateCashRowPreview(row));
    if (cur !== 'PLN' && amount) updateCashRowPreview(row);
}

async function saveManualEntries() {
    const select = document.getElementById('entryQuarter');
    const snapshotId = select.value;
    if (!snapshotId) return;
    const date = getSelectedSnapshotDate();
    const status = document.getElementById('saveStatus');
    const button = document.getElementById('manualSave');
    button.disabled = true; select.disabled = true;
    status.textContent = 'Saving…';
    try {
        const entries = [];
        for (const row of document.querySelectorAll('#cashEntries .row')) {
            const input = row.querySelector('.cash-amount');
            if (!input.validity.valid) throw new Error('Enter a valid cash amount.');
            if (input.value.trim() === '') continue;
            const originalAmount = Number(input.value);
            if (!Number.isFinite(originalAmount)) throw new Error('Enter a finite cash amount.');
            const currency = row.querySelector('.cash-currency').value;
            const label = row.querySelector('.cash-label').value.trim();
            let amountPln = originalAmount;
            if (currency !== 'PLN' && date && originalAmount !== 0) {
                const {rate} = await fetchRate(currency, date);
                amountPln *= rate;
            }
            entries.push({type:'cash', label:label || `Cash ${currency}`, currency,
                original_amount:originalAmount, amount_pln:Math.round(amountPln * 100) / 100});
        }
        for (const type of ['ppk', 'mortgage']) {
            const input = document.getElementById(`${type}Amount`);
            if (!input.validity.valid) throw new Error(`Enter a valid ${type} amount.`);
            if (input.value.trim() === '') continue;
            const amount = Number(input.value);
            if (!Number.isFinite(amount) || amount < 0) throw new Error(`${type} must be zero or a positive amount.`);
            entries.push({type, label:document.getElementById(`${type}Label`).value,
                currency:'PLN', original_amount:amount, amount_pln:amount});
        }
        const resp = await fetch(`/api/manual-entries/${snapshotId}`, {
            method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({entries}),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.error || 'Could not save balances.');
        status.textContent = 'Saved. Blank balances remain unrecorded; zeros are confirmed.';
        status.className = 'ms-2 small text-success';
        await loadQuality();
    } catch (err) {
        status.textContent = err.message; status.className = 'ms-2 small text-danger';
    } finally { button.disabled = false; select.disabled = false; }
}

async function loadQuality() {
    try {
        const response = await fetch('/api/data-quality');
        if (!response.ok) throw new Error('Could not load data completeness. Reload before confirming coverage.');
        qualityData = await response.json();
        renderDataQuality(qualityData);
        const input = document.getElementById('coverage-through');
        if (!input.value) input.value = qualityData.cash_flows.confirmed_through || qualityData.holdings_as_of || '';
        const now = new Date();
        input.max = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
    } catch (err) {
        qualityData = null;
        document.getElementById('coverage-status').textContent = err.message;
    }
}

async function confirmCoverage() {
    const status = document.getElementById('coverage-status');
    const button = document.getElementById('coverage-save');
    button.disabled = true;
    try {
        if (!qualityData) throw new Error('Reload the page to review contribution history.');
        const response = await fetch('/api/cashflow-coverage', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({through:document.getElementById('coverage-through').value,
                confirmed:document.getElementById('coverage-confirmed').checked,
                revision:qualityData.cash_flows.revision}),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Could not confirm coverage.');
        await loadQuality();
        status.textContent = 'Coverage confirmed.';
    } catch (err) { status.textContent = err.message; }
    finally { button.disabled = false; }
}

async function deleteSelectedSnapshot() {
    const select = document.getElementById('entryQuarter');
    const snapshotId = select.value;
    if (!snapshotId) return;
    const label = select.options[select.selectedIndex].text;
    if (!confirm(`Delete "${label}" and all its positions? This cannot be undone.`)) return;
    const resp = await fetch(`/api/snapshots/${snapshotId}`, { method: 'DELETE' });
    if (resp.ok) loadImportPage();
    else alert('Error deleting snapshot.');
}

// ── CSV import ──────────────────────────────────────

// Keep the actual reviewed File object; selecting another file invalidates it.
const pendingImports = {};
const IMPORT_UI = {
    csv: {input: 'csvFileInput', result: 'importResult', button: 'importBtn', url: '/api/import-csv'},
    cashflows: {input: 'cashflowsFileInput', result: 'cashflowsResult', button: 'cashflowsBtn', url: '/api/import-cashflows'},
};

function importCsv() { return previewImport('csv'); }
function importCashflows() { return previewImport('cashflows'); }

function appendImportText(parent, text, className = '') {
    const p = document.createElement('p');
    p.className = className;
    p.textContent = text;
    parent.appendChild(p);
}

async function previewImport(kind) {
    const ui = IMPORT_UI[kind];
    const input = document.getElementById(ui.input);
    const result = document.getElementById(ui.result);
    const button = document.getElementById(ui.button);
    const file = input.files[0];
    delete pendingImports[kind];
    result.replaceChildren();
    if (!file) { appendImportText(result, 'Select a file first.', 'text-warning'); return; }
    button.disabled = true;
    button.textContent = 'Checking…';
    try {
        const body = new FormData();
        body.append('file', file);
        body.append('preview', '1');
        const response = await fetch(ui.url, {method: 'POST', body});
        const data = await response.json();
        if (input.files[0] !== file) return;
        if (!response.ok) {
            appendImportText(result, data.error, 'text-danger');
            [...(data.errors || []), ...(data.warnings || [])].forEach(t => appendImportText(result, t, 'small'));
            return;
        }
        pendingImports[kind] = {file, token: data.preview_token, replacing: data.replacing};
        appendImportText(result, `Review: ${data.filename}`, 'fw-semibold');
        if (kind === 'csv') {
            appendImportText(result, `${data.quarter} · ${data.snapshot_date} · ${data.positions_count} positions · ${formatPLN(data.total_value)}`);
            if (data.comparison_date) appendImportText(result, `Compared with ${data.comparison_date}: ${formatPLN(data.previous_total)} → ${formatPLN(data.total_value)}`);
            appendImportText(result, `New positions: ${data.new_positions.length}; no longer present: ${data.removed_positions.length}`);
            if (data.new_positions.length) appendImportText(result, `New: ${data.new_positions.join(', ')}`, 'small');
            if (data.removed_positions.length) appendImportText(result, `Removed: ${data.removed_positions.join(', ')}`, 'small');
            if (data.replacing) appendImportText(result, 'This replaces positions for this date. Saved cash, PPK and mortgage balances are preserved.', 'text-warning');
        } else {
            appendImportText(result, `${data.imported} events · ${data.earliest_date} to ${data.latest_date}`);
            appendImportText(result, `Deposits: ${formatPLN(data.deposited)} · Withdrawals: ${formatPLN(data.withdrawn)} · Net: ${formatPLN(data.net_invested)}`);
            appendImportText(result, `Saved history: ${data.previous.count} events, net ${formatPLN(data.previous.net_invested)}. Incoming changes: ${data.added_rows} added, ${data.removed_rows} removed.`);
            appendImportText(result, 'Saving replaces the entire cash-flow history. Check that you exported the full history.', 'text-warning');
        }
        data.warnings.forEach(t => appendImportText(result, t, 'small text-warning'));
        const save = document.createElement('button');
        save.type = 'button'; save.className = 'btn btn-success btn-sm';
        save.textContent = kind === 'csv' && data.replacing ? 'Replace snapshot' : 'Save import';
        save.onclick = () => commitImport(kind, save);
        result.appendChild(save);
        const cancel = document.createElement('button');
        cancel.type = 'button'; cancel.className = 'btn btn-outline-secondary btn-sm ms-2';
        cancel.textContent = 'Cancel';
        cancel.onclick = () => { delete pendingImports[kind]; result.replaceChildren(); };
        result.appendChild(cancel);
    } catch (err) { appendImportText(result, err.message, 'text-danger'); }
    finally { button.disabled = false; button.textContent = 'Preview import'; }
}

async function commitImport(kind, button) {
    const ui = IMPORT_UI[kind];
    const pending = pendingImports[kind];
    if (!pending) return;
    const result = document.getElementById(ui.result);
    const input = document.getElementById(ui.input);
    const previewButton = document.getElementById(ui.button);
    button.disabled = true;
    input.disabled = true;
    previewButton.disabled = true;
    result.querySelectorAll('button').forEach(b => b.disabled = true);
    try {
        const body = new FormData();
        body.append('file', pending.file);
        body.append('preview_token', pending.token);
        if (pending.replacing) body.append('replace', '1');
        const response = await fetch(ui.url, {method: 'POST', body});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Import failed');
        result.replaceChildren();
        appendImportText(result, 'Import saved.', 'text-success');
        input.value = '';
        if (kind === 'csv') await refreshQuarters(data.quarter);
        if (kind === 'cashflows') {
            document.getElementById('coverage-confirmed').checked = false;
            document.getElementById('coverage-status').textContent = 'New history imported. Review and confirm coverage again.';
        }
        await loadQuality();
    } catch (err) {
        result.replaceChildren();
        appendImportText(result, `${err.message} Preview again before saving.`, 'text-danger');
    } finally {
        delete pendingImports[kind];
        input.disabled = false;
        previewButton.disabled = false;
    }
}

// Reload the quarter list in place and select the one just imported, so step 2
// is already pointing at the quarter step 1 created.
async function refreshQuarters(selectQuarter) {
    snapshots = await (await fetch('/api/snapshots')).json();
    const select = document.getElementById('entryQuarter');
    select.disabled = false;
    select.innerHTML = snapshots.map(s =>
        `<option value="${s.id}">${s.quarter}</option>`
    ).join('');
    const match = snapshots.find(s => s.quarter === selectQuarter);
    if (match) select.value = match.id;
    if (select.value) loadManualEntries(select.value);
}

// ── Init ────────────────────────────────────────────

loadImportPage();
