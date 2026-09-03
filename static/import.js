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

    loadManualEntries(snapshots[0].id);
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
    const resp = await fetch(`/api/manual-entries/${snapshotId}`);
    const entries = await resp.json();
    const container = document.getElementById('cashEntries');
    container.innerHTML = '';
    const cashEntries = entries.filter(e => e.type === 'cash');
    if (cashEntries.length === 0) { addCashRow(); }
    else { cashEntries.forEach(e => addCashRow(e.currency || 'PLN', e.original_amount || e.amount_pln, e.label)); }
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
            <input type="number" step="0.01" class="form-control cash-amount" placeholder="Amount" value="${amount || ''}">
        </div>
        <div class="col">
            <input type="text" class="form-control cash-label" placeholder="Label (e.g. Savings)" value="${label || ''}">
        </div>
        <div class="col-auto">
            <button type="button" class="btn btn-outline-danger btn-sm" onclick="this.closest('.row').remove()" style="line-height: 1.7;">&times;</button>
        </div>
        <div class="col-12"><small class="cash-pln-preview text-muted"></small></div>
    `;
    container.appendChild(row);
    row.querySelector('.cash-currency').addEventListener('change', () => updateCashRowPreview(row));
    row.querySelector('.cash-amount').addEventListener('input', () => updateCashRowPreview(row));
    if (cur !== 'PLN' && amount) updateCashRowPreview(row);
}

async function saveManualEntries() {
    const snapshotId = document.getElementById('entryQuarter').value;
    if (!snapshotId) return;
    const date = getSelectedSnapshotDate();
    const entries = [];

    for (const row of document.querySelectorAll('#cashEntries .row')) {
        const currency = row.querySelector('.cash-currency').value;
        const originalAmount = parseFloat(row.querySelector('.cash-amount').value);
        const label = row.querySelector('.cash-label').value.trim();
        if (!originalAmount) continue;
        let amountPln = originalAmount;
        if (currency !== 'PLN' && date) {
            try { const { rate } = await fetchRate(currency, date); amountPln = originalAmount * rate; }
            catch { alert(`Could not fetch ${currency} rate. Save aborted.`); return; }
        }
        entries.push({ type: 'cash', label: label || `Cash ${currency}`, currency, original_amount: originalAmount, amount_pln: Math.round(amountPln * 100) / 100 });
    }

    const ppkAmount = parseFloat(document.getElementById('ppkAmount').value);
    const ppkLabel = document.getElementById('ppkLabel').value;
    if (ppkAmount) {
        entries.push({ type: 'ppk', label: ppkLabel || 'PPK', currency: 'PLN', original_amount: ppkAmount, amount_pln: ppkAmount });
    }

    const mortgageAmount = parseFloat(document.getElementById('mortgageAmount').value);
    const mortgageLabel = document.getElementById('mortgageLabel').value.trim();
    if (mortgageAmount) {
        entries.push({ type: 'mortgage', label: mortgageLabel || 'Mortgage', currency: 'PLN', original_amount: mortgageAmount, amount_pln: mortgageAmount });
    }

    const resp = await fetch(`/api/manual-entries/${snapshotId}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ entries }) });
    const status = document.getElementById('saveStatus');
    if (resp.ok) { status.textContent = 'Saved.'; status.className = 'ms-2 small text-success'; }
    else { status.textContent = 'Error saving.'; status.className = 'ms-2 small text-danger'; }
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

async function importCsv() {
    const fileInput = document.getElementById('csvFileInput');
    const resultDiv = document.getElementById('importResult');
    const btn = document.getElementById('importBtn');

    if (!fileInput.files.length) {
        resultDiv.innerHTML = '<div class="alert alert-warning py-2">Please select a CSV file first.</div>';
        return;
    }

    btn.disabled = true;
    btn.textContent = 'Importing...';
    resultDiv.innerHTML = '';

    const formData = new FormData();
    formData.append('file', fileInput.files[0]);

    try {
        const resp = await fetch('/api/import-csv', { method: 'POST', body: formData });
        const data = await resp.json();

        if (resp.ok) {
            // The date is still shown here: it is parsed from the filename, so
            // this is the one place it is worth confirming rather than assuming.
            resultDiv.innerHTML = `<div class="alert alert-success py-2">
                Imported <strong>${data.quarter}</strong> (dated ${data.snapshot_date})
                — ${data.positions_count} positions, total: ${formatPLN(data.total_value)}.
                Now fill in step 2 for this quarter.
            </div>`;
            fileInput.value = '';
            await refreshQuarters(data.quarter);
        } else {
            resultDiv.innerHTML = `<div class="alert alert-danger py-2">${data.error}</div>`;
        }
    } catch (err) {
        resultDiv.innerHTML = `<div class="alert alert-danger py-2">Network error: ${err.message}</div>`;
    } finally {
        btn.disabled = false;
        btn.textContent = 'Import';
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

// ── Cash flow import ────────────────────────────────

async function importCashflows() {
    const fileInput = document.getElementById('cashflowsFileInput');
    const resultDiv = document.getElementById('cashflowsResult');
    const btn = document.getElementById('cashflowsBtn');

    if (!fileInput.files.length) {
        resultDiv.innerHTML = '<div class="alert alert-warning py-2">Please select an XLSX file first.</div>';
        return;
    }

    btn.disabled = true;
    btn.textContent = 'Importing...';
    resultDiv.innerHTML = '';

    const formData = new FormData();
    formData.append('file', fileInput.files[0]);

    try {
        const resp = await fetch('/api/import-cashflows', { method: 'POST', body: formData });
        const data = await resp.json();

        if (resp.ok) {
            resultDiv.innerHTML = `<div class="alert alert-success py-2">
                Imported <strong>${data.imported}</strong> cash-flow events
                (${data.earliest_date} → ${data.latest_date}).<br>
                Deposited: ${formatPLN(data.deposited)} · Withdrawn: ${formatPLN(data.withdrawn)}
                · <strong>Net invested: ${formatPLN(data.net_invested)}</strong>.
                ${data.skipped ? `Skipped ${data.skipped} unrecognized rows.` : ''}
            </div>`;
            fileInput.value = '';
        } else {
            resultDiv.innerHTML = `<div class="alert alert-danger py-2">${data.error}</div>`;
        }
    } catch (err) {
        resultDiv.innerHTML = `<div class="alert alert-danger py-2">Network error: ${err.message}</div>`;
    } finally {
        btn.disabled = false;
        btn.textContent = 'Import';
    }
}

// ── Init ────────────────────────────────────────────

loadImportPage();
