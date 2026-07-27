// Shared helpers used by app.js, compare.js and forecast.js.
//
// Loaded as a classic <script> before each page's own script, so everything
// here lands in the same global scope the page scripts use. Keep it free of
// page-specific state — it must be safe to load on every page.

// ── Formatting ──────────────────────────────────────

function formatPLN(value) {
    return new Intl.NumberFormat('pl-PL', {
        style: 'currency',
        currency: 'PLN',
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
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

function toggleTheme() {
    const html = document.documentElement;
    const next = html.getAttribute('data-bs-theme') === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-bs-theme', next);
    localStorage.setItem('theme', next);
    const btn = document.getElementById('themeToggle');
    if (btn) btn.textContent = next === 'dark' ? 'Light' : 'Dark';
}

// Apply the saved theme immediately on load. The toggle button's label is set
// later by each page, once its template has been cloned into the DOM.
(function() {
    const saved = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-bs-theme', saved);
})();
