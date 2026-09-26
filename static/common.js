// Shared helpers used by app.js, compare.js and forecast.js.
//
// Loaded as a classic <script> before each page's own script, so everything
// here lands in the same global scope the page scripts use. Keep it free of
// page-specific state — it must be safe to load on every page.

// ── Formatting ──────────────────────────────────────

// useGrouping 'always' overrides the pl-PL default, which omits the separator
// for four-digit numbers: a column reading 1962 / 12 345 / 636 377 breaks the
// digit alignment that makes a table of figures scannable.
function formatPLN(value) {
    return new Intl.NumberFormat('pl-PL', {
        style: 'currency',
        currency: 'PLN',
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
        useGrouping: 'always',
    }).format(value);
}

// ── Account badges ──────────────────────────────────

// "IKE Obligacje" is a bond sub-account under IKE-M (maklerskie)
const IKE_M_ACCOUNTS = ['IKE-M', 'IKE OBLIGACJE'];

function getRetirementType(account) {
    if (!account) return null;
    const upper = account.toUpperCase();
    if (IKE_M_ACCOUNTS.some(a => upper.includes(a))) return 'IKE-M';
    if (upper.includes('IKZE')) return 'IKZE';
    if (upper.includes('IKE')) return 'IKE';
    return null;
}

function accountBadge(account) {
    const type = getRetirementType(account);
    if (type === 'IKE-M') return '<span class="badge badge-ikem ms-1">IKE-M</span>';
    if (type === 'IKZE')  return '<span class="badge badge-ikze ms-1">IKZE</span>';
    if (type === 'IKE')   return '<span class="badge badge-ike ms-1">IKE</span>';
    return '';
}

// ── Theme ───────────────────────────────────────────

// The button names where you are going, not where you are, so its label is the
// opposite of the active theme.
function syncThemeButton() {
    const btn = document.getElementById('themeToggle');
    if (btn) {
        btn.textContent =
            document.documentElement.getAttribute('data-bs-theme') === 'dark' ? 'Light' : 'Dark';
    }
}

function toggleTheme() {
    const html = document.documentElement;
    const next = html.getAttribute('data-bs-theme') === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-bs-theme', next);
    localStorage.setItem('theme', next);
    syncThemeButton();
}

// Apply the saved theme and label the button before any page script runs.
(function() {
    const saved = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-bs-theme', saved);
    // The nav sits above the scripts, so the button already exists. Syncing
    // here means every page gets a correct label without repeating the logic —
    // the Add Data page had no copy of it and showed "Dark" while in dark mode.
    syncThemeButton();
})();

// The last event date is not proof of export coverage; show both explicitly.
function renderDataQuality(quality) {
    const container = document.getElementById('data-quality');
    if (!container || !quality) return;
    container.replaceChildren();
    const rows = quality.snapshots;
    const flows = quality.cash_flows;
    const missing = rows.filter(r => Object.values(r.balances).includes('missing'));
    const needsAttention = missing.length || !flows.covers_snapshot || quality.holdings_behind || quality.future_snapshot || quality.missing_quarters.length;
    const details = document.createElement('details');
    details.className = `alert ${needsAttention ? 'alert-warning' : 'alert-success'} mb-4`;
    const heading = document.createElement('summary');
    heading.className = 'fw-semibold';
    heading.textContent = `Data completeness — ${needsAttention ? 'review missing or unconfirmed inputs' : 'recorded inputs are up to date'}`;
    details.appendChild(heading);
    function note(text) {
        const p = document.createElement('p'); p.className = 'small mt-2 mb-1';
        p.textContent = text; details.appendChild(p);
    }
    note(`Holdings as of ${quality.holdings_as_of || 'not imported'}.`);
    if (quality.holdings_behind) note(`Holdings precede the last completed quarter (${quality.last_completed_quarter_end}).`);
    if (quality.future_snapshot) note('The latest snapshot is future-dated. Check the imported filename.');
    note(flows.count ? `Contribution events: ${flows.first_event} to ${flows.last_event}.` : 'No contribution history imported.');
    note(flows.confirmed_through ? `Full contribution history confirmed through ${flows.confirmed_through}.` : 'Full contribution history has not been confirmed. The last event date does not establish coverage.');
    if (!flows.covers_snapshot) note('Review the full export and confirm coverage through the latest snapshot in Add Data to enable XIRR.');
    if (quality.missing_quarters.length) note(`No holdings snapshot for: ${quality.missing_quarters.join(', ')}.`);
    if (missing.length) note(`${missing.length} snapshot(s) have unrecorded balances. Calculations currently treat these missing amounts as zero. Enter 0 to confirm no balance; leave blank only when unknown.`);
    if (rows.length) {
        const wrapper = document.createElement('div'); wrapper.className = 'table-responsive';
        const table = document.createElement('table'); table.className = 'table table-sm small mt-2 mb-0';
        const header = table.insertRow();
        ['Snapshot', 'Cash', 'PPK', 'Mortgage'].forEach(text => {
            const cell = document.createElement('th'); cell.textContent = text; header.appendChild(cell);
        });
        const labels = {missing: 'Not recorded', confirmed_zero: 'Confirmed zero', recorded: 'Recorded'};
        rows.forEach(row => {
            const tr = table.insertRow(); const first = tr.insertCell();
            const link = document.createElement('a'); link.href = `/import?snapshot=${row.id}#balances`;
            link.textContent = row.snapshot_date; first.appendChild(link);
            ['cash', 'ppk', 'mortgage'].forEach(kind => {
                const cell = tr.insertCell(); cell.textContent = labels[row.balances[kind]];
                if (row.balances[kind] === 'missing') cell.className = 'fw-semibold';
            });
        });
        wrapper.appendChild(table); details.appendChild(wrapper);
    }
    const link = document.createElement('a'); link.href = '/import'; link.textContent = 'Review inputs in Add Data';
    link.className = 'small d-inline-block mt-2'; details.appendChild(link);
    const overview = document.createElement('p');
    overview.className = 'small text-muted mb-2';
    overview.textContent = `Holdings: ${quality.holdings_as_of || 'not imported'} · Last contribution event: ${flows.last_event || 'none'} · Full history confirmed through: ${flows.confirmed_through || 'not confirmed'}`;
    container.appendChild(overview);
    container.appendChild(details);
}

// Preserve exact typed values rather than allowing range inputs to snap them.
function setExactRangeValue(slider, value) {
    slider.min = Math.min(Number(slider.min), value);
    slider.max = Math.max(Number(slider.max), value);
    slider.dataset.baseStep ||= slider.step;
    const step = Number(slider.dataset.baseStep);
    const steps = (value - Number(slider.min)) / step;
    slider.step = step > 0 && Math.abs(steps - Math.round(steps)) < 1e-8 ? String(step) : 'any';
    slider.value = value;
}

function syncNumberFromRange(slider, input, scale = 1) {
    input.value = Number((Number(slider.value) * scale).toPrecision(12));
}

function bindNumberToRange(slider, input, {scale = 1, onValid, onInvalid}) {
    slider.addEventListener('input', () => {
        syncNumberFromRange(slider, input, scale);
        onValid();
    });
    input.addEventListener('input', () => {
        if (!input.value.trim() || !input.validity.valid || !Number.isFinite(Number(input.value))) {
            onInvalid();
            return;
        }
        setExactRangeValue(slider, Number(input.value) / scale);
        onValid();
    });
    syncNumberFromRange(slider, input, scale);
}
