// Retirement planner. Shared helpers (formatPLN, theme) live in common.js.
//
// The engine runs server-side in retirement.py, because the age-gating and tax
// rules have enough edge cases to warrant pytest coverage. This file handles
// the form, the results and the chart.

const SETTING_KEYS = [
    'current_age', 'retirement_age', 'horizon_age', 'success_threshold',
    'annual_spending', 'annual_savings',
    'zus_annual', 'zus_start_age',
    'ppk_enabled', 'start_ppk', 'ppk_gross_salary', 'ppk_employee_rate',
    'ppk_employer_rate', 'ppk_state_annual', 'ppk_access_age',
    'ppk_lump_sum_fraction', 'ppk_installment_years',
    'ike_access_age', 'ikze_access_age', 'belka_rate', 'ikze_withdrawal_rate',
    'ike_annual_limit', 'ikze_annual_limit',
    'use_historical_returns', 'inflation_rate', 'expected_real_return',
];

let planData = null;
let retirementChart = null;

function formatPct(value, decimals = 0) {
    return `${(value * 100).toFixed(decimals)}%`;
}

// ── Results ─────────────────────────────────────────

function renderResults(d) {
    const s = d.settings;
    const chosenAge = Number(s.retirement_age);
    const threshold = Number(s.success_threshold);

    // Earliest feasible age. Null means nothing in the searched range cleared
    // the threshold — say so plainly rather than showing a misleading number.
    const ageEl = document.getElementById('earliest-age');
    const ageDetail = document.getElementById('earliest-detail');
    if (d.earliest_feasible_age === null) {
        ageEl.textContent = 'Not reached';
        ageEl.className = 'card-value text-negative';
        ageDetail.textContent =
            `No age up to 75 clears ${formatPct(threshold)}. Best was ${formatPct(d.earliest_feasible_rate)}.`;
    } else {
        ageEl.textContent = d.earliest_feasible_age;
        ageEl.className = 'card-value text-positive';
        ageDetail.textContent =
            `${formatPct(d.earliest_feasible_rate)} of runs fund spending to age ${s.horizon_age}.`;
    }

    // Success at the age they actually picked.
    const rateEl = document.getElementById('chosen-rate');
    const meets = d.chosen_age_success_rate >= threshold;
    rateEl.textContent = formatPct(d.chosen_age_success_rate);
    rateEl.className = `card-value ${meets ? 'text-positive' : 'text-negative'}`;
    document.getElementById('chosen-detail').textContent =
        `Retiring at ${chosenAge} — ${meets ? 'meets' : 'below'} your ${formatPct(threshold)} bar.`;

    document.getElementById('sustainable').textContent =
        formatPLN(d.sustainable_spending_at_chosen_age);

    renderBuckets(d);

    document.getElementById('chart-note').innerHTML =
        `Median with 10th–90th percentile band, ${d.return_source} returns ` +
        `averaging <strong>${formatPct(d.mean_real_return, 1)}</strong> real per year. ` +
        `Retirement at ${chosenAge}; IKE unlocks at ${s.ike_access_age}, IKZE at ${s.ikze_access_age}.`;
}

function renderBuckets(d) {
    const b = d.balances;
    const total = b.taxable + b.ike + b.ikze + (b.ppk || 0);
    const s = d.settings;
    if (!total) return;

    const segments = [
        { label: 'Taxable — any time', value: b.taxable, color: '#198754' },
        { label: `IKE — from ${s.ike_access_age}`, value: b.ike, color: '#0d6efd' },
        { label: `IKZE — from ${s.ikze_access_age}`, value: b.ikze, color: '#fd7e14' },
        { label: `PPK — from ${s.ppk_access_age}`, value: b.ppk || 0, color: '#6f42c1' },
    ];

    const bar = document.getElementById('bucket-bar');
    bar.innerHTML = '';
    for (const seg of segments) {
        if (seg.value <= 0) continue;
        const el = document.createElement('span');
        el.style.width = `${(seg.value / total) * 100}%`;
        el.style.background = seg.color;
        el.textContent = `${((seg.value / total) * 100).toFixed(0)}%`;
        el.title = `${seg.label}: ${formatPLN(seg.value)}`;
        bar.appendChild(el);
    }

    const locked = b.ike + b.ikze + (b.ppk || 0);
    document.getElementById('bucket-summary').innerHTML =
        `<strong>${formatPLN(b.taxable)}</strong> reachable now · ` +
        `<strong>${formatPLN(b.ike)}</strong> from age ${s.ike_access_age} · ` +
        `<strong>${formatPLN(b.ikze)}</strong> from age ${s.ikze_access_age}` +
        ((b.ppk || 0) > 0 ? ` · <strong>${formatPLN(b.ppk)}</strong> PPK from age ${s.ppk_access_age}` : '') + '. ' +
        `That is ${formatPct(locked / total)} of capital behind an age gate. ` +
        `Cost basis is estimated at ${formatPct(d.basis_ratio)} of value, so Belka applies to the rest.`;
}

// ── Chart ───────────────────────────────────────────

function renderChart(d) {
    const canvas = document.getElementById('retirement-chart');
    if (!canvas) return;
    if (retirementChart) retirementChart.destroy();

    const path = d.path;
    const retireAge = Number(d.settings.retirement_age);

    retirementChart = new Chart(canvas, {
        type: 'line',
        data: {
            labels: path.map(p => p.age),
            datasets: [
                { label: 'P10', data: path.map(p => p.p10),
                  borderColor: 'rgba(13,110,253,0)', pointRadius: 0, fill: false, order: 3 },
                { label: 'P10–P90 range', data: path.map(p => p.p90),
                  borderColor: 'rgba(13,110,253,0)', backgroundColor: 'rgba(13,110,253,0.15)',
                  pointRadius: 0, fill: '-1', order: 2 },
                { label: 'Median capital', data: path.map(p => p.p50),
                  borderColor: '#0d6efd', borderWidth: 2.5, pointRadius: 0,
                  fill: false, tension: 0.2, order: 1 },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { position: 'bottom', labels: { filter: i => i.text !== 'P10' } },
                tooltip: {
                    callbacks: {
                        title: items => {
                            const age = Number(items[0].label);
                            return `Age ${age}${age >= retireAge ? ' (retired)' : ''}`;
                        },
                        label: ctx => {
                            if (ctx.dataset.label === 'P10') return null;
                            if (ctx.dataset.label === 'P10–P90 range') {
                                const p10 = ctx.chart.data.datasets[0].data[ctx.dataIndex];
                                return `Range: ${formatPLN(p10)} – ${formatPLN(ctx.parsed.y)}`;
                            }
                            return `${ctx.dataset.label}: ${formatPLN(ctx.parsed.y)}`;
                        },
                    },
                },
            },
            scales: {
                x: { title: { display: true, text: 'Age' } },
                y: { beginAtZero: true, ticks: { callback: v => formatPLN(v) } },
            },
        },
    });
}

// ── Settings form ───────────────────────────────────

function fillForm(settings) {
    for (const key of SETTING_KEYS) {
        const el = document.getElementById(key);
        if (el) el.value = settings[key];
    }
}

function readForm() {
    const out = {};
    for (const key of SETTING_KEYS) {
        const el = document.getElementById(key);
        if (el && el.value !== '') out[key] = el.value;
    }
    return out;
}

async function saveAndRecalculate() {
    const btn = document.getElementById('save-btn');
    const status = document.getElementById('save-status');
    btn.disabled = true;
    btn.textContent = 'Calculating…';
    status.textContent = '';

    try {
        const save = await fetch('/api/retirement', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(readForm()),
        });
        if (!save.ok) throw new Error(`save failed (${save.status})`);

        const resp = await fetch('/api/retirement');
        planData = await resp.json();
        if (!planData.available) throw new Error(planData.reason || 'unavailable');

        renderResults(planData);
        renderChart(planData);
        status.textContent = 'Saved.';
        status.className = 'ms-2 small text-success';
    } catch (err) {
        status.textContent = err.message;
        status.className = 'ms-2 small text-danger';
    } finally {
        btn.disabled = false;
        btn.textContent = 'Save & recalculate';
    }
}

// ── Init ────────────────────────────────────────────

async function loadRetirement() {
    const appEl = document.getElementById('app');
    let data;
    try {
        data = await (await fetch('/api/retirement')).json();
    } catch (err) {
        appEl.innerHTML = `<div class="alert alert-danger">Could not load: ${err.message}</div>`;
        return;
    }

    if (!data.available) {
        appEl.innerHTML =
            `<div class="alert alert-info">Retirement planning needs at least one snapshot. ${data.reason || ''}</div>`;
        return;
    }

    planData = data;
    appEl.innerHTML = '';
    appEl.appendChild(document.getElementById('retirement-template').content.cloneNode(true));

    const btn = document.getElementById('themeToggle');
    if (btn) btn.textContent =
        document.documentElement.getAttribute('data-bs-theme') === 'dark' ? 'Light' : 'Dark';

    fillForm(data.settings);
    renderResults(data);
    renderChart(data);
}

loadRetirement();
