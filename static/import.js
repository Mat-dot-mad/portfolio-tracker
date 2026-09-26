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
let balanceDraftDirty = false;
let balanceEditRevision = 0;

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
    const balances = document.getElementById('balances');
    balances.addEventListener('input', markBalancesDirty);
    balances.addEventListener('change', markBalancesDirty);
    await loadQuality();
    await loadImportHistory();
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

    select.innerHTML = '<option value="">New quarter — import positions</option>' + snapshots.map(s =>
        `<option value="${s.id}">${s.quarter} · ${s.snapshot_date}</option>`
    ).join('');
    const requested = new URLSearchParams(location.search).get('snapshot');
    select.value = snapshots.some(s => String(s.id) === requested) ? requested : (snapshots[0]?.id || '');
    select.addEventListener('change', selectedQuarterChanged);
    selectedQuarterChanged();
}

function selectedQuarterChanged() {
    const id = document.getElementById('entryQuarter').value;
    balanceDraftDirty = false;
    ++balanceEditRevision;
    if (id) loadManualEntries(id);
    else {
        ++manualLoadSeq;
        document.getElementById('cashEntries').replaceChildren();
        document.getElementById('ppkAmount').value = '';
        document.getElementById('mortgageAmount').value = '';
        document.getElementById('manualSave').disabled = true;
        document.getElementById('saveStatus').textContent = 'Import a positions CSV to create the quarter first.';
    }
    document.getElementById('coverage-through').value =
        [getSelectedSnapshotDate(), qualityData?.cash_flows.confirmed_through].filter(Boolean).sort().pop() || '';
    document.getElementById('coverage-confirmed').checked = false;
    document.getElementById('coverage-status').textContent = '';
    renderQuarterProgress();
}

function markBalancesDirty() {
    balanceDraftDirty = true;
    ++balanceEditRevision;
    renderQuarterProgress();
}

// Pure progress calculation: confirmed zero is complete, missing is not.
function quarterProgress(quality, id, dirty, today) {
    const row = quality?.snapshots.find(s => String(s.id) === String(id));
    const flows = quality?.cash_flows;
    const hasSnapshot = !!row && row.snapshot_date <= today;
    return {
        row,
        steps: [hasSnapshot,
            !!row && !dirty && ['cash','ppk','mortgage'].every(k => ['recorded','confirmed_zero'].includes(row.balances[k])),
            !!row && flows.count > 0 && !!flows.confirmed_through && flows.confirmed_through >= row.snapshot_date],
    };
}

function renderQuarterProgress() {
    const container = document.getElementById('quarter-progress');
    if (!container) return;
    container.replaceChildren();
    const body = document.createElement('div'); body.className = 'card-body'; container.appendChild(body);
    if (!qualityData) {
        appendImportText(body, 'Progress unavailable until saved data can be loaded.', 'text-muted mb-0');
        return;
    }
    const now = new Date();
    const today = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
    const {row, steps} = quarterProgress(qualityData, document.getElementById('entryQuarter').value, balanceDraftDirty, today);
    const complete = steps.every(Boolean);
    appendImportText(body, row ? `Update for ${row.snapshot_date}: ${steps.filter(Boolean).length} of 3 steps complete` : 'New quarter: start with a positions CSV', 'fw-semibold');
    const list = document.createElement('ol'); list.className = 'list-unstyled d-flex flex-wrap gap-3 mb-2';
    const labels = ['Positions saved', 'Balances saved', 'Contributions confirmed'];
    const targets = ['positions', 'balances', 'contributions'];
    labels.forEach((label, i) => {
        const item = document.createElement('li');
        const link = document.createElement('a'); link.href = '#' + targets[i];
        link.textContent = `${i+1}. ${label} — ${steps[i] ? 'Done' : 'Pending'}`;
        link.className = steps[i] ? 'text-success' : 'fw-semibold';
        item.appendChild(link); list.appendChild(item);
    });
    body.appendChild(list);
    if (balanceDraftDirty) appendImportText(body, 'Balance edits are not saved yet.', 'small text-warning');
    if (row && row.snapshot_date > today) appendImportText(body, 'This snapshot is future-dated. Check the date in the CSV filename.', 'small text-warning');
    if (row && !steps[1]) {
        const missing = ['cash','ppk','mortgage'].filter(k => row.balances[k] === 'missing');
        if (missing.length) appendImportText(body, `Still needed: ${missing.join(', ')}. Enter 0 where there is no balance.`, 'small');
    }
    if (complete) {
        appendImportText(body, 'This quarter’s update is complete. You can return to the dashboard.', 'text-success mb-1');
        const link = document.createElement('a'); link.href = '/'; link.textContent = 'View dashboard'; body.appendChild(link);
    } else {
        const next = steps.findIndex(done => !done);
        const link = document.createElement('a'); link.href = '#' + targets[next];
        link.className = 'btn btn-primary btn-sm';
        link.textContent = ['Next: import positions', 'Next: save balances', 'Next: review contributions'][next];
        body.appendChild(link);
    }
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
            <button type="button" class="btn btn-outline-danger btn-sm" onclick="this.closest('.row').remove(); markBalancesDirty()" style="line-height: 1.7;">&times;</button>
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
    const savingRevision = balanceEditRevision;
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
        if (balanceEditRevision === savingRevision) balanceDraftDirty = false;
        status.textContent = 'Saved. Blank balances remain unrecorded; zeros are confirmed.';
        status.className = 'ms-2 small text-success';
        await loadQuality();
        await loadImportHistory();
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
        renderQuarterProgress();
        const input = document.getElementById('coverage-through');
        if (!input.value) input.value = [getSelectedSnapshotDate(), qualityData.cash_flows.confirmed_through].filter(Boolean).sort().pop() || '';
        const now = new Date();
        input.max = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
    } catch (err) {
        qualityData = null;
        renderQuarterProgress();
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
        await loadImportHistory();
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
    const previous = select.value;
    select.innerHTML = '<option value="">New quarter — import positions</option>' + snapshots.map(s =>
        `<option value="${s.id}">${s.quarter} · ${s.snapshot_date}</option>`
    ).join('');
    const match = snapshots.find(s => s.quarter === selectQuarter);
    select.value = match ? match.id : snapshots.some(s => String(s.id) === previous) ? previous : (snapshots[0]?.id || '');
    selectedQuarterChanged();
}

// ── Import journal and guarded undo ──────────────────

let historyLoading = false;

function historyButton(parent, label, action, className = 'btn-outline-secondary') {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `btn btn-sm ${className} me-2`;
    button.textContent = label;
    button.onclick = () => action(button);
    parent.appendChild(button);
    return button;
}

function historySummary(kind, state) {
    if (kind === 'csv') return `${state.count} positions · ${formatPLN(state.total)}`;
    return `${state.count} events · ${state.first || 'no dates'} to ${state.last || 'no dates'} · deposits ${formatPLN(state.deposits)} · withdrawals ${formatPLN(state.withdrawals)} · coverage ${state.confirmed_through || 'unconfirmed'}`;
}

async function loadImportHistory(before = null) {
    const container = document.getElementById('import-history');
    if (!container || historyLoading) return;
    historyLoading = true;
    if (!before) container.replaceChildren();
    try {
        const response = await fetch('/api/import-history' + (before ? `?before=${before}` : ''));
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not load history.');
        if (!before && !data.items.length) appendImportText(container, 'No imports recorded yet.', 'text-muted');
        for (const item of data.items) {
            const row = document.createElement('div');
            row.className = 'border-top py-3';
            container.appendChild(row);
            appendImportText(row, `${item.filename} — ${item.kind === 'csv' ? 'Positions: ' + item.snapshot_date : 'Contribution history'}`, 'fw-semibold mb-1 text-break');
            appendImportText(row, `Saved ${new Date(item.imported_at).toLocaleString()}` + (item.undone_at ? ` · Undone ${new Date(item.undone_at).toLocaleString()}` : ''), 'small text-muted');
            const summary = item.summary;
            if (item.kind === 'csv') {
                appendImportText(row, `${summary.replacing ? 'Replaced' : 'Created'} snapshot · ${summary.positions_count} positions · ${formatPLN(summary.total_value)}`, 'small');
                if (summary.comparison_date) appendImportText(row, `Compared with ${summary.comparison_date}: ${formatPLN(summary.previous_total)} → ${formatPLN(summary.total_value)}`, 'small');
            } else {
                appendImportText(row, `${summary.previous.count} → ${summary.count} events · net invested ${formatPLN(summary.previous.net_invested)} → ${formatPLN(summary.net_invested)} · ${summary.added_rows} added, ${summary.removed_rows} removed`, 'small');
            }
            const details = document.createElement('details');
            const heading = document.createElement('summary');
            heading.textContent = 'Import details';
            details.appendChild(heading);
            row.appendChild(details);
            if (item.kind === 'csv') {
                appendImportText(details, `New positions: ${summary.new_positions.join(', ') || 'none'}`, 'small');
                appendImportText(details, `Removed positions: ${summary.removed_positions.join(', ') || 'none'}`, 'small');
            } else {
                appendImportText(details, `Imported dates: ${summary.earliest_date} to ${summary.latest_date}. Deposits ${formatPLN(summary.deposited)}; withdrawals ${formatPLN(summary.withdrawn)}.`, 'small');
            }
            (summary.warnings || []).forEach(w => appendImportText(details, w, 'small text-warning'));
            if (item.undo_reason) appendImportText(row, item.undo_reason, 'small text-muted mb-0');
            else {
                const preview = document.createElement('div');
                historyButton(row, 'Preview undo', button => previewUndo(item.id, preview, button));
                row.appendChild(preview);
            }
        }
        if (data.next_before) historyButton(container, 'Load older imports', async button => {
            button.remove(); await loadImportHistory(data.next_before);
        });
    } catch (err) {
        appendImportText(container, err.message, 'text-danger');
        historyButton(container, 'Retry history', () => loadImportHistory());
    } finally { historyLoading = false; }
}

async function previewUndo(id, container, button) {
    button.disabled = true;
    container.replaceChildren();
    try {
        const response = await fetch(`/api/import-history/${id}/undo`, {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({preview: true}),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not preview undo.');
        appendImportText(container, 'Undo preview', 'fw-semibold mt-3');
        appendImportText(container, `Current: ${historySummary(data.kind, data.current)}`, 'small');
        appendImportText(container, `Restore: ${historySummary(data.kind, data.restored)}`, 'small');
        appendImportText(container, data.kind === 'csv'
            ? (data.restored.snapshot_exists ? 'Saved cash, PPK and mortgage balances will be preserved.' : 'This removes the quarter created by this import and any generated review. It has no saved manual balances.')
            : 'This restores the previous contribution history and its previous coverage confirmation.', 'small text-warning');
        historyButton(container, 'Confirm undo', confirm => commitUndo(id, data.preview_token, container, confirm), 'btn-danger');
        historyButton(container, 'Cancel', () => container.replaceChildren());
    } catch (err) { appendImportText(container, err.message, 'text-danger'); }
    finally { button.disabled = false; }
}

async function commitUndo(id, token, container, button) {
    button.disabled = true;
    container.querySelectorAll('button').forEach(b => b.disabled = true);
    try {
        const response = await fetch(`/api/import-history/${id}/undo`, {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({preview_token: token}),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Undo failed.');
        for (const [kind, ui] of Object.entries(IMPORT_UI)) {
            delete pendingImports[kind];
            document.getElementById(ui.result).replaceChildren();
        }
        await refreshQuarters();
        document.getElementById('coverage-through').value = '';
        document.getElementById('coverage-confirmed').checked = false;
        document.getElementById('coverage-status').textContent = '';
        await loadQuality();
        await loadImportHistory();
    } catch (err) {
        container.replaceChildren();
        appendImportText(container, `${err.message} Preview again before undoing.`, 'text-danger');
    }
}

// ── Init ────────────────────────────────────────────

loadImportPage();
