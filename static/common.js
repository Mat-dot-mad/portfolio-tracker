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

// ── Gain / loss colour ──────────────────────────────

// Red and green mean "bad for you" and "good for you", never just "down" and
// "up". Each figure declares its polarity — dashboards call this metric (or
// KPI) polarity:
//    1  higher is better: assets, net worth
//   -1  lower is better: debt, so paying down the mortgage is green
//    0  neither: cash moving between your own accounts
function changeClass(delta, polarity = 1) {
    if (!polarity || !delta) return '';
    return delta * polarity > 0 ? 'text-positive' : 'text-negative';
}

// A CSS custom property as resolved for the current theme. Lets canvas
// drawing — which CSS cannot reach — use the same tokens as the markup,
// e.g. cssColor('--pt-bad').
function cssColor(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// The same colour at a given opacity, for chart fills. Accepts the #rrggbb and
// rgb()/rgba() forms Bootstrap's tokens resolve to.
function withAlpha(color, alpha) {
    // #rgb (Bootstrap writes --bs-emphasis-color as #000/#fff) -> #rrggbb.
    if (/^#[0-9a-f]{3}$/i.test(color)) color = '#' + [...color.slice(1)].map(c => c + c).join('');
    const hex = color.match(/^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
    const [r, g, b] = hex
        ? hex.slice(1).map(h => parseInt(h, 16))
        : color.match(/[\d.]+/g).slice(0, 3).map(Number);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

// Black or white, whichever has the higher contrast on `color`, using WCAG's
// relative luminance. Contrast with black is (L + 0.05) / 0.05 and with white
// 1.05 / (L + 0.05). With fixed white labels, 9 of the old treemap's 15
// colours fell below 4.5:1.
function readableTextOn(color) {
    const [r, g, b] = withAlpha(color, 1).match(/[\d.]+/g).slice(0, 3).map(Number)
        .map(c => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; });
    const L = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    return (L + 0.05) / 0.05 >= 1.05 / (L + 0.05) ? '#000' : '#fff';
}

// Categories that are not wrappers — tags, plain taxable accounts. No red or
// green, and none of the wrapper hues, so colour never implies a gain, a loss
// or a particular account type.
const CATEGORY_COLORS = ['#0dcaf0', '#ffc107', '#d63384', '#8c6a4f',
                         '#74b9ff', '#a29bfe', '#495057', '#adb5bd'];

function wrapperOf(name) {
    if (!name) return null;
    if (name.trim().toUpperCase() === 'PPK') return 'ppk';
    return { 'IKE': 'ike', 'IKE-M': 'ikem', 'IKZE': 'ikze' }[getRetirementType(name)] || null;
}

// A colour per name, in order. Wrappers get their identity colour; everything
// else takes the palette in turn, so the palette never skips a slot.
function categoryColors(names) {
    let next = 0;
    return names.map(name => {
        const wrapper = wrapperOf(name);
        return wrapper ? cssColor(`--pt-${wrapper}`) : CATEGORY_COLORS[next++ % CATEGORY_COLORS.length];
    });
}

// Chart.js text and gridline colours. Its built-in #666 measured 2.69:1 on the
// dark background, a WCAG AA fail. Chart.js resolves these when a chart is
// created and does not re-read them on update(), so a theme switch announces
// itself with a 'themechange' event and each page redraws its charts.
function applyChartTheme() {
    if (!window.Chart) return;
    Chart.defaults.color = cssColor('--bs-secondary-color');
    Chart.defaults.borderColor = cssColor('--bs-border-color-translucent');
}

function toggleTheme() {
    const html = document.documentElement;
    const next = html.getAttribute('data-bs-theme') === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-bs-theme', next);
    localStorage.setItem('theme', next);
    syncThemeButton();
    applyChartTheme();
    window.dispatchEvent(new Event('themechange'));
}

// Apply the saved theme and label the button before any page script runs.
(function() {
    const saved = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-bs-theme', saved);
    // The nav sits above the scripts, so the button already exists. Syncing
    // here means every page gets a correct label without repeating the logic —
    // the Add Data page had no copy of it and showed "Dark" while in dark mode.
    syncThemeButton();
    // Before any page script creates a chart, so every chart starts themed.
    applyChartTheme();
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
    input.value = scale === 1 ? slider.value : Number((Number(slider.value) * scale).toPrecision(15));
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
