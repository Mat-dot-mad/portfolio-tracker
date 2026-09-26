// Retirement planner. Shared helpers (formatPLN, theme) live in common.js.
//
// The engine runs server-side in retirement.py, because the age-gating and tax
// rules have enough edge cases to warrant pytest coverage. This file owns the
// controls, the results and the chart.
//
// Six sliders carry the assumptions worth exploring; everything else sits in a
// collapsed panel. Slider changes preview a draft; saving is always explicit.

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

const DEBOUNCE_MS = 250;

let planData = null;
let baselineData = null;
let savedScenarios = [];
let selectedScenarioId = null;
let retirementChart = null;
let debounceTimer = null;
// Requests can overlap while dragging; only the newest may be applied, or a
// slow earlier response would overwrite a newer one.
let requestSeq = 0;
// Set by renderChart(); renderResults() mentions it in the caption.
let chartUsesLogScale = false;

function formatPct(value, decimals = 0) {
    return `${(value * 100).toFixed(decimals)}%`;
}

// ── Lever definitions ───────────────────────────────
// Ranges live here only. `note` and `ticks` receive the live value plus the
// latest payload, so labels can reference other settings.

const LEVERS = {
    primary: [
        {
            key: 'retirement_age', label: 'Retire at', min: 45, max: 75, step: 1,
            display: v => `${v}`,
            note: (v, d) => {
                const away = v - Number(d.settings.current_age);
                return away > 0 ? `${away} years away` : 'at or before today';
            },
            // The unlock ages are what make early retirement hard, so mark them
            // on the control that decides whether you clear them.
            ticks: d => [
                { at: Number(d.settings.ike_access_age), label: 'IKE' },
                { at: Number(d.settings.ikze_access_age), label: 'IKZE' },
            ],
        },
        {
            key: 'annual_spending', label: 'Spend / year', min: 0, max: 500000, step: 5000,
            display: v => formatPLN(v),
            note: () => "today's money",
        },
        {
            key: 'annual_savings', label: 'Save / year', min: 0, max: 400000, step: 5000,
            display: v => formatPLN(v),
            note: (_v, d) => d.contribution_rate_8q != null
                ? `tracked: ${formatPLN(d.contribution_rate_8q)}`
                : 'until you retire',
        },
    ],
    secondary: [
        {
            key: 'expected_real_return', label: 'Real return', min: 0, max: 0.12, step: 0.0025,
            display: v => formatPct(v, 2),
            note: () => 'used only with a fixed rate',
            // Greyed out when returns are bootstrapped from history, because
            // then it has no effect at all.
            inactiveWhen: d => Number(d.settings.use_historical_returns) === 1,
            control: `<select class="form-select form-select-sm mt-2" id="use_historical_returns">
                        <option value="1">Bootstrap my history</option>
                        <option value="0">Fixed rate</option>
                      </select>`,
        },
        {
            key: 'inflation_rate', label: 'Assumed inflation', min: 0, max: 0.10, step: 0.0025,
            display: v => formatPct(v, 2),
            note: () => 'converts history to real terms',
            inactiveWhen: d => Number(d.settings.use_historical_returns) !== 1,
        },
        {
            key: 'horizon_age', label: 'Plan until age', min: 75, max: 105, step: 1,
            display: v => `${v}`,
            note: (v, d) => `${v - Number(d.settings.retirement_age)} years of retirement`,
        },
    ],
};

function allLevers() {
    return [...LEVERS.primary, ...LEVERS.secondary];
}

function buildLevers() {
    for (const [containerId, list] of [['levers-primary', LEVERS.primary],
                                       ['levers-secondary', LEVERS.secondary]]) {
        document.getElementById(containerId).innerHTML = list.map(l => `
            <div class="col-md-4">
                <div class="card h-100 lever" id="lever-${l.key}">
                    <div class="card-body">
                        <div class="lever-label">${l.label}</div>
                        <div class="lever-value" id="value-${l.key}">—</div>
                        <input type="range" class="form-range mt-1" id="${l.key}"
                               min="${l.min}" max="${l.max}" step="${l.step}">
                        <div class="lever-ticks" id="ticks-${l.key}"></div>
                        <div class="lever-note" id="note-${l.key}"></div>
                        ${l.control || ''}
                    </div>
                </div>
            </div>`).join('');
    }

    for (const l of allLevers()) {
        // `input` fires continuously while dragging: relabel immediately so the
        // control feels responsive, and debounce the actual recalculation.
        document.getElementById(l.key).addEventListener('input', () => {
            refreshLeverLabels();
            scheduleUpdate();
        });
    }
    const source = document.getElementById('use_historical_returns');
    if (source) source.addEventListener('change', () => { refreshLeverLabels(); scheduleUpdate(); });
}

function refreshLeverLabels() {
    if (!planData) return;
    // Read live from the controls so labels track the drag rather than the
    // last response.
    const live = { ...planData, settings: { ...planData.settings, ...readForm() } };

    for (const l of allLevers()) {
        const el = document.getElementById(l.key);
        if (!el) continue;
        const v = parseFloat(el.value);

        document.getElementById(`value-${l.key}`).textContent = l.display(v);
        document.getElementById(`note-${l.key}`).textContent = l.note(v, live);

        document.getElementById(`lever-${l.key}`)
            .classList.toggle('inactive', !!(l.inactiveWhen && l.inactiveWhen(live)));

        const ticks = document.getElementById(`ticks-${l.key}`);
        ticks.innerHTML = !l.ticks ? '' : l.ticks(live)
            .filter(t => t.at >= Number(el.min) && t.at <= Number(el.max))
            .map(t => {
                const pct = ((t.at - Number(el.min)) / (Number(el.max) - Number(el.min))) * 100;
                return `<span style="left:${pct}%">${t.label} ${t.at}</span>`;
            }).join('');
    }
}

// ── Results ─────────────────────────────────────────

function renderResults(d) {
    const s = d.settings;
    const chosenAge = Number(s.retirement_age);
    const threshold = Number(s.success_threshold);

    const ageEl = document.getElementById('earliest-age');
    const ageDetail = document.getElementById('earliest-detail');
    if (d.earliest_feasible_age === null) {
        ageEl.textContent = 'Not reached';
        ageEl.className = 'card-value text-negative';
        ageDetail.textContent =
            `No age up to ${Math.min(75, Number(s.horizon_age) - 1)} clears ${formatPct(threshold)}. Best was ${formatPct(d.earliest_feasible_rate)}.`;
    } else {
        ageEl.textContent = d.earliest_feasible_age;
        ageEl.className = 'card-value text-positive';
        ageDetail.textContent =
            `${formatPct(d.earliest_feasible_rate)} of runs fund spending to age ${s.horizon_age}.`;
    }

    const rateEl = document.getElementById('chosen-rate');
    const meets = d.chosen_age_success_rate >= threshold;
    rateEl.textContent = formatPct(d.chosen_age_success_rate);
    rateEl.className = `card-value ${meets ? 'text-positive' : 'text-negative'}`;
    const shortfallNote = d.median_first_shortfall_age != null
        ? ` ${formatPct(d.shortfall_run_share)} of runs fail; their median first shortfall is at age ${d.median_first_shortfall_age}.`
        : '';
    document.getElementById('chosen-detail').textContent =
        `Retiring at ${chosenAge} — ${meets ? 'meets' : 'below'} your ${formatPct(threshold)} bar.` +
        shortfallNote;

    document.getElementById('sustainable').textContent =
        formatPLN(d.sustainable_spending_at_chosen_age);

    renderFailureExplanation(d);
    renderBuckets(d);
    renderProjectionTable(d);

    document.getElementById('chart-note').innerHTML =
        `<strong class="text-positive">Accessible capital</strong> shows remaining balances after that year's spending, before withdrawal tax. ` +
        `Total capital also includes age-locked accounts. Each line is a separate percentile; subtracting their medians does not give median locked capital. ` +
        `Failed runs continue for illustration: later growth does not pay earlier unfunded spending.<br>` +
        `${d.return_source} returns compounding at ` +
        `<strong>${formatPct(d.mean_real_return, 1)}</strong> real per year ` +
        `(geometric; arithmetic mean is ${formatPct(d.arithmetic_real_return, 1)}).` +
        (chartUsesLogScale
            ? ' <span class="text-warning">Log scale</span> — the spread is too wide to read linearly.'
            : '');
}

function renderFailureExplanation(d) {
    const container = document.getElementById('failure-explanation');
    container.replaceChildren();
    const add = (text, cls = 'small mb-2') => {
        const p = document.createElement('p'); p.className = cls; p.textContent = text;
        container.appendChild(p);
    };
    const f = d.failure_analysis;
    if (d.return_source === 'fixed') add('Fixed-return mode: every run follows the same return sequence. The result is a single projection, not an estimate of market uncertainty.', 'small text-muted');
    if (f.failed_count === 0) {
        add(`None of the ${f.paths} simulated runs left spending unfunded before age ${d.settings.horizon_age}.`, 'text-positive mb-2');
        add('This applies to the selected assumptions and simulated returns; it is not a guarantee.');
    } else {
        add(`${f.failed_count} of ${f.paths} runs could not fund all spending. First shortfalls range from age ${f.earliest_age} to ${f.latest_age}; the median among failed runs is ${f.median_age}.`);
        add(`${f.with_locked_capital} first shortfalls occurred with capital still locked by the model; ${f.depleted} occurred after capital was exhausted.`);
        const e = f.example;
        add(`Example failing run — first shortfall at age ${e.age}`, 'fw-semibold mt-3 mb-2');
        add(`Annual spending ${formatPLN(e.spending)} = income ${formatPLN(e.income)} + net withdrawals ${formatPLN(e.funded_from_capital)} + unfunded spending ${formatPLN(e.shortfall)}.`);
        add(`That year's gap is ${formatPLN(e.shortfall)} (equivalent to ${formatPLN(e.shortfall / 12)} per month). Withdrawal tax paid: ${formatPLN(e.withdrawal_tax)}.`, 'text-negative fw-semibold small');
        add(`After withdrawals: ${formatPLN(e.reachable_net)} accessible after estimated tax; ${formatPLN(e.locked)} still locked, before tax.`);
        for (const account of e.locked_accounts) {
            add(`${BUCKET_LABELS[account.bucket]}: ${formatPLN(account.value)} locked until configured age ${account.access_age} (${account.access_age - e.age} years later).`);
        }
        add(e.locked_accounts.length
            ? 'The model cannot use those locked balances to fill this year’s gap. Their presence does not mean they would cover all future spending.'
            : 'Accessible capital has been exhausted; income and net withdrawals cannot cover the annual spending target.');
        add('This is one actual failed run chosen at the middle first-shortfall age. Its figures belong together; the year-by-year table below uses a separate run chosen by ending capital.', 'small text-muted');
    }
    document.getElementById('mortgage-note').textContent =
        (d.mortgage_balance == null ? 'Mortgage balance is not recorded for the latest snapshot. '
            : `Recorded mortgage balance: ${formatPLN(d.mortgage_balance)}. `) +
        'The planner projects investment accounts, not net worth: it does not subtract mortgage debt or calculate repayments. ' +
        'Include any mortgage payments you expect in retirement in Spend / year. Spending stays constant in today’s money; payments do not automatically stop at a payoff date. ' +
        'Use separate spending scenarios to compare assumptions; a changing repayment schedule is not modelled.';
}

function renderBuckets(d) {
    const b = d.balances;
    const total = b.taxable + b.ike + b.ikze + (b.ppk || 0);
    const s = d.settings;
    if (!total) {
        document.getElementById('bucket-bar').replaceChildren();
        document.getElementById('bucket-summary').textContent = 'No starting capital recorded.';
        return;
    }

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

    const locked = ['ike', 'ikze', 'ppk'].reduce((sum, name) =>
        sum + (Number(s.current_age) < Number(s[`${name}_access_age`]) ? (b[name] || 0) : 0), 0);
    document.getElementById('bucket-summary').innerHTML =
        `<strong>${formatPLN(total - locked)}</strong> accessible now, before tax · ` +
        `<strong>${formatPLN(b.ike)}</strong> from age ${s.ike_access_age} · ` +
        `<strong>${formatPLN(b.ikze)}</strong> from age ${s.ikze_access_age}` +
        ((b.ppk || 0) > 0 ? ` · <strong>${formatPLN(b.ppk)}</strong> PPK from age ${s.ppk_access_age}` : '') + '. ' +
        `That is ${formatPct(locked / total)} of capital behind an age gate. ` +
        `Cost basis is estimated at ${formatPct(d.basis_ratio)} of value, so Belka applies to the rest.`;
}

// ── Year-by-year projection table ───────────────────

const BUCKET_LABELS = { taxable: 'Taxable', ike: 'IKE', ikze: 'IKZE', ppk: 'PPK' };

// Where the money sits today, by account. The engine has four buckets, so
// IKE-M and IKE Obligacje are simulated as IKE — same wrapper, same age gate,
// same tax. Listing the accounts here means the table can say so, rather than
// looking as though an account went missing.
function renderAccountSplit(d) {
    const el = document.getElementById('account-split');
    if (!el) return;
    const rows = d.balances_by_account || [];
    if (!rows.length) { el.innerHTML = ''; return; }

    const grouped = {};
    for (const r of rows) (grouped[r.bucket] = grouped[r.bucket] || []).push(r);

    const parts = Object.entries(grouped).map(([bucket, accounts]) => {
        const names = accounts
            .map(a => `${a.account} ${formatPLN(a.value)}`)
            .join(' · ');
        return `<div class="split-chip"><strong>${BUCKET_LABELS[bucket] || bucket}</strong>` +
               ` ← ${names}</div>`;
    });

    const ikeAccounts = (grouped.ike || []).length;
    const note = ikeAccounts > 1
        ? `<div class="split-chip text-muted mt-1">Those ${ikeAccounts} IKE accounts share one ` +
          `age gate and one tax treatment, so the projection tracks them as a single IKE column.</div>`
        : '';

    el.innerHTML = `<div class="mb-1 fw-semibold small">Starting balances by account</div>` +
                   parts.join('') + note;
}

function renderProjectionTable(d) {
    renderAccountSplit(d);

    const head = document.getElementById('projection-head');
    const body = document.getElementById('projection-body');
    const note = document.getElementById('projection-note');
    if (!head || !body) return;

    const rows = d.projection || [];
    const s = d.settings;
    const retireAge = Number(s.retirement_age);
    const gates = {
        ike: Number(s.ike_access_age),
        ikze: Number(s.ikze_access_age),
        ppk: Number(s.ppk_access_age),
    };
    const zusAge = Number(s.zus_start_age);

    if (note) {
        note.innerHTML =
            `One complete run — the one whose ending capital is the median — so every row ` +
            `reconciles: <em>spending = income + from capital + shortfall</em>. ` +
            `Per-bucket medians would come from different runs and would not add up.<br>` +
            (d.return_source === 'fixed'
                ? `Returns are fixed, so this is the only path the model produces.`
                : `Returns are resampled from your history, so another run would differ; ` +
                  `the chart's bands show that spread.`) +
            ` Greyed cells are locked at that age. Red rows cannot fund spending. ` +
            `Balances are at year end; accessible net estimates remaining cash after withdrawal tax. ` +
            `Income includes ZUS and PPK payments; from capital is net of tax. ` +
            `Unfunded spending is not borrowed or deducted from later balances. This run may differ from the failing example above.`;
    }

    head.innerHTML = `
        <tr>
            <th>Age</th>
            <th class="num">Taxable</th>
            <th class="num">IKE</th>
            <th class="num">IKZE</th>
            <th class="num">PPK</th>
            <th class="num">Total</th>
            <th class="num">Accessible net</th>
            <th class="num">Locked</th>
            <th class="num">Return</th>
            <th class="num">Paid in</th>
            <th class="num">Spending</th>
            <th class="num">Income</th>
            <th class="num">From capital</th>
            <th class="num">Withdrawal tax</th>
            <th class="num">Short</th>
            <th class="num">Unfunded so far</th>
        </tr>`;

    const cell = (value, locked) =>
        `<td class="num${locked ? ' locked' : ''}">${value ? formatPLN(value) : '—'}</td>`;

    body.innerHTML = rows.map(r => {
        const age = r.age;
        const retired = age >= retireAge;
        const isShort = r.shortfall > 1e-6;
        // A milestone row is where something changes: retirement or an unlock.
        const milestone = [retireAge, gates.ike, gates.ikze, gates.ppk, zusAge].includes(age);
        const cls = [isShort ? 'short' : (retired ? 'retired' : ''),
                     milestone ? 'milestone' : ''].filter(Boolean).join(' ');
        return `
            <tr class="${cls}">
                <td>${age}${milestone ? ' <span class="text-muted">•</span>' : ''}</td>
                ${cell(r.taxable, false)}
                ${cell(r.ike, age < gates.ike)}
                ${cell(r.ikze, age < gates.ikze)}
                ${cell(r.ppk, age < gates.ppk)}
                <td class="num fw-semibold">${formatPLN(r.total)}</td>
                <td class="num">${formatPLN(r.reachable_net)}</td>
                <td class="num">${formatPLN(r.locked)}</td>
                <td class="num">${formatPct(r.return_rate, 1)}</td>
                <td class="num">${r.contributions ? formatPLN(r.contributions) : '—'}</td>
                <td class="num">${r.spending ? formatPLN(r.spending) : '—'}</td>
                <td class="num">${r.income ? formatPLN(r.income) : '—'}</td>
                <td class="num">${r.funded_from_capital ? formatPLN(r.funded_from_capital) : '—'}</td>
                <td class="num">${formatPLN(r.withdrawal_tax)}</td>
                <td class="num ${isShort ? 'text-negative fw-semibold' : ''}">${
                    isShort ? formatPLN(r.shortfall) : '—'}</td>
                <td class="num">${formatPLN(r.cumulative_shortfall)}</td>
            </tr>`;
    }).join('');
}

// ── Chart ───────────────────────────────────────────

// Shades the bridge — retired, but nothing unlocked yet — and marks the ages
// where each income source switches on. That period is the whole point of the
// module and was previously described only in prose.
const milestonesPlugin = {
    id: 'milestones',
    beforeDatasetsDraw(chart, _args, opts) {
        if (!opts || !opts.path || !opts.path.length) return;
        const { ctx, chartArea, scales } = chart;
        const ages = opts.path.map(p => p.age);
        const xFor = age => {
            const i = ages.indexOf(age);
            return i === -1 ? null : scales.x.getPixelForValue(i);
        };

        const from = xFor(opts.retireAge);
        const to = xFor(Math.min(opts.ikeAge, opts.ppkAge));
        if (from !== null && to !== null && to > from) {
            ctx.save();
            ctx.fillStyle = 'rgba(220, 53, 69, 0.10)';
            ctx.fillRect(from, chartArea.top, to - from, chartArea.bottom - chartArea.top);
            ctx.fillStyle = 'rgba(220, 53, 69, 0.85)';
            ctx.font = '11px sans-serif';
            ctx.fillText('bridge', from + 4, chartArea.top + 13);
            ctx.restore();
        }

        // Where plans start running short. Drawn solid and labelled, because
        // it is the single most important thing on the chart when it exists.
        if (opts.shortfallAge != null) {
            const sx = xFor(opts.shortfallAge);
            if (sx !== null) {
                ctx.save();
                ctx.strokeStyle = 'rgba(220, 53, 69, 0.9)';
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.moveTo(sx, chartArea.top);
                ctx.lineTo(sx, chartArea.bottom);
                ctx.stroke();
                ctx.fillStyle = 'rgba(220, 53, 69, 0.95)';
                ctx.font = 'bold 11px sans-serif';
                ctx.fillText(`median first shortfall: ${opts.shortfallAge}`, sx + 5, chartArea.top + 28);
                ctx.restore();
            }
        }

        ctx.save();
        ctx.setLineDash([4, 4]);
        ctx.lineWidth = 1;
        ctx.font = '10px sans-serif';
        for (const m of opts.markers) {
            const x = xFor(m.age);
            if (x === null) continue;
            ctx.strokeStyle = m.color;
            ctx.fillStyle = m.color;
            ctx.beginPath();
            ctx.moveTo(x, chartArea.top);
            ctx.lineTo(x, chartArea.bottom);
            ctx.stroke();
            ctx.fillText(m.label, x + 3, chartArea.bottom - 4);
        }
        ctx.restore();
    },
};

function renderChart(d) {
    const canvas = document.getElementById('retirement-chart');
    if (!canvas) return;
    if (retirementChart) retirementChart.destroy();

    const path = d.path;
    const s = d.settings;
    const retireAge = Number(s.retirement_age);

    // A heavily over-funded plan compounds instead of depleting, so the P90
    // band can span two orders of magnitude and squash the median — the line
    // that actually matters — onto the axis. Switch to a log scale in that
    // case. Not usable once a path reaches zero, which is exactly when the
    // linear view is informative anyway.
    const values = path.flatMap(p => [p.p10, p.p50, p.p90]).filter(v => v > 0);
    const spread = values.length ? Math.max(...values) / Math.min(...values) : 1;
    // Spendable capital reaching zero is the thing worth seeing, and a log axis
    // cannot plot zero — so any depletion, of either measure, forces linear.
    const anyDepleted = path.some(p => p.p10 <= 0 || p.reachable_p10 <= 0
                                       || p.reachable_p50 <= 0);
    const useLog = !anyDepleted && spread > 20;
    chartUsesLogScale = useLog;

    const anyFailure = path.some(p => p.failed_share > 0);

    retirementChart = new Chart(canvas, {
        type: 'line',
        plugins: [milestonesPlugin],
        data: {
            labels: path.map(p => p.age),
            datasets: [
                { label: 'P10', data: path.map(p => p.p10),
                  borderColor: 'rgba(13,110,253,0)', pointRadius: 0, fill: false, order: 5 },
                { label: 'P10–P90 range', data: path.map(p => p.p90),
                  borderColor: 'rgba(13,110,253,0)', backgroundColor: 'rgba(13,110,253,0.13)',
                  pointRadius: 0, fill: '-1', order: 4 },
                // Total capital is now the muted, dashed reference line. It is
                // the number that misleads: it keeps rising through a bridge
                // failure because the locked buckets carry on compounding.
                { label: 'Total capital (median)', data: path.map(p => p.p50),
                  borderColor: 'rgba(13,110,253,0.55)', borderWidth: 2,
                  borderDash: [6, 4], pointRadius: 0, fill: false, tension: 0.2, order: 3 },
                // The line that answers "can I actually pay for my life?".
                { label: 'Accessible capital (median, before tax)', data: path.map(p => p.reachable_p50),
                  borderColor: '#198754', borderWidth: 3, pointRadius: 0,
                  fill: false, tension: 0.2, order: 1 },
                { label: 'Accessible capital (P10, before tax)', data: path.map(p => p.reachable_p10),
                  borderColor: '#dc3545', borderWidth: 2, borderDash: [3, 3],
                  pointRadius: 0, fill: false, tension: 0.2, order: 2 },
                // Only drawn when something actually fails, so a healthy plan
                // is not cluttered by a flat zero line.
                ...(anyFailure ? [{
                    label: 'Runs already short',
                    data: path.map(p => p.failed_share),
                    yAxisID: 'y1',
                    borderColor: 'rgba(220,53,69,0.65)', borderWidth: 1,
                    backgroundColor: 'rgba(220,53,69,0.13)',
                    pointRadius: 0, fill: 'origin', tension: 0.2, order: 6,
                }] : []),
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,      // no re-animation on every slider move
            interaction: { mode: 'index', intersect: false },
            plugins: {
                milestones: {
                    path,
                    retireAge,
                    ikeAge: Number(s.ike_access_age),
                    ppkAge: Number(s.ppk_access_age),
                    shortfallAge: d.median_first_shortfall_age,
                    markers: [
                        { age: retireAge, label: 'retire', color: 'rgba(108,117,125,0.85)' },
                        { age: Number(s.ike_access_age), label: 'IKE', color: 'rgba(13,110,253,0.7)' },
                        { age: Number(s.ikze_access_age), label: 'IKZE', color: 'rgba(253,126,20,0.7)' },
                        { age: Number(s.zus_start_age), label: 'ZUS', color: 'rgba(25,135,84,0.7)' },
                    ],
                },
                legend: { position: 'bottom', labels: { filter: i => i.text !== 'P10' } },
                tooltip: {
                    callbacks: {
                        title: items => {
                            const age = Number(items[0].label);
                            const pt = path[items[0].dataIndex];
                            const short = pt && pt.failed_share > 0
                                ? ` · ${formatPct(pt.failed_share)} short by here` : '';
                            return `Age ${age}${age >= retireAge ? ' (retired)' : ''}${short}`;
                        },
                        label: ctx => {
                            if (ctx.dataset.label === 'P10') return null;
                            if (ctx.dataset.label === 'P10–P90 range') {
                                const p10 = ctx.chart.data.datasets[0].data[ctx.dataIndex];
                                return `Range: ${formatPLN(p10)} – ${formatPLN(ctx.parsed.y)}`;
                            }
                            if (ctx.dataset.label === 'Runs already short') {
                                return `Runs already short: ${formatPct(ctx.parsed.y)}`;
                            }
                            return `${ctx.dataset.label}: ${formatPLN(ctx.parsed.y)}`;
                        },
                        // Use the locked-balance percentile itself: subtracting
                        // separately computed medians would not be valid.
                        afterBody: items => {
                            const pt = path[items[0].dataIndex];
                            if (!pt) return '';
                            const locked = pt.locked_p50;
                            if (locked <= 1) return '';
                            return `Locked capital (median): ${formatPLN(locked)}`;
                        },
                    },
                },
            },
            scales: {
                x: { title: { display: true, text: 'Age' } },
                y: useLog
                    ? { type: 'logarithmic', ticks: { callback: v => formatPLN(v) } }
                    : { beginAtZero: true, ticks: { callback: v => formatPLN(v) } },
                y1: {
                    display: anyFailure,
                    position: 'right',
                    min: 0,
                    max: 1,
                    grid: { drawOnChartArea: false },
                    ticks: { callback: v => `${Math.round(v * 100)}%` },
                    title: { display: true, text: 'runs already short' },
                },
            },
        },
    });
}

// ── Settings ────────────────────────────────────────

function fillForm(settings, data) {
    for (const key of SETTING_KEYS) {
        const el = document.getElementById(key);
        if (el) {
            const lever = allLevers().find(l => l.key === key);
            if (lever) {
                // Do not silently clamp a saved assumption to a slider's
                // suggested range or round it to the nearest step.
                const value = Number(settings[key]);
                el.min = Math.min(lever.min, value);
                el.max = Math.max(lever.max, value);
                const steps = (value - Number(el.min)) / lever.step;
                el.step = Math.abs(steps - Math.round(steps)) < 1e-8 ? lever.step : 'any';
            }
            el.value = settings[key];
        }
    }

    // The tracked quarterly PPK balance wins over the planner setting, and the
    // field becomes read-only so the two cannot drift apart.
    const ppkEl = document.getElementById('start_ppk');
    const note = document.getElementById('start_ppk_note');
    if (ppkEl && data && data.ppk_from_snapshot != null) {
        ppkEl.value = Math.round(data.ppk_from_snapshot);
        ppkEl.readOnly = true;
        ppkEl.classList.add('bg-body-secondary');
        if (note) note.textContent = 'from the latest Quarterly Entry';
    } else if (note) {
        note.textContent = 'no quarterly PPK entry yet — enter it on the Dashboard';
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

function setStatus(text, cls = 'text-muted') {
    const el = document.getElementById('update-status');
    if (el) { el.textContent = text; el.className = `small ${cls}`; }
}

function setBusy(busy) {
    for (const id of ['results-row', 'chart-note', 'failure-explanation']) {
        const el = document.getElementById(id);
        if (el) el.classList.toggle('recalculating', busy);
    }
}

function scheduleUpdate() {
    clearTimeout(debounceTimer);
    ++requestSeq; // invalidate in-flight results immediately, before debounce
    setStatus('Unsaved changes');
    debounceTimer = setTimeout(runUpdate, DEBOUNCE_MS);
}

async function runUpdate() {
    const seq = ++requestSeq;
    setBusy(true);
    setStatus('calculating…');

    try {
        const data = await retirementRequest('/api/retirement/preview', 'POST', readForm());
        // A newer drag superseded this request — drop the stale result.
        if (seq !== requestSeq) return;
        if (!data.available) throw new Error(data.reason || 'unavailable');

        planData = data;
        renderChart(data);      // sets chartUsesLogScale, read by renderResults
        renderResults(data);
        refreshLeverLabels();
        renderScenarioComparison();
        setStatus('Draft calculated · baseline unchanged', 'text-success');
    } catch (err) {
        if (seq !== requestSeq) return;
        setStatus(err.message, 'text-danger');
    } finally {
        if (seq === requestSeq) setBusy(false);
    }
}

async function resetSettings() {
    if (!confirm('Reset your saved baseline to defaults? Named scenarios will be kept.')) return;
    cancelPendingCalculation();
    const btn = document.getElementById('reset-btn');
    btn.disabled = true;
    try {
        await retirementRequest('/api/retirement', 'DELETE');
        await loadRetirement();      // rebuild from defaults
        setStatus('reset to defaults', 'text-success');
    } catch (err) {
        setStatus(err.message, 'text-danger');
    } finally {
        const b = document.getElementById('reset-btn');
        if (b) b.disabled = false;
    }
}

// ── Explicit baseline and scenario actions ──────────────────────

async function retirementRequest(url, method = 'GET', body) {
    const response = await fetch(url, {
        method,
        ...(body === undefined ? {} : {
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
        }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
}

function cancelPendingCalculation() {
    clearTimeout(debounceTimer);
    ++requestSeq;
    setBusy(false);
}

async function refreshScenarios() {
    savedScenarios = await retirementRequest('/api/retirement/scenarios');
    const select = document.getElementById('scenario-select');
    select.replaceChildren(new Option('Baseline', ''));
    savedScenarios.forEach(s => select.add(new Option(s.name, s.id)));
    select.value = selectedScenarioId || '';
    document.getElementById('delete-scenario').disabled = !selectedScenarioId;
}

function renderScenarioComparison() {
    if (!baselineData || !planData) return;
    const b = baselineData, d = planData;
    const table = document.createElement('table');
    table.className = 'table table-sm small mb-0';
    const rows = [
        ['Input / result', 'Baseline', 'Current draft'],
        ['Retirement age', b.settings.retirement_age, d.settings.retirement_age],
        ['Annual spending', formatPLN(b.settings.annual_spending), formatPLN(d.settings.annual_spending)],
        ['Annual savings', formatPLN(b.settings.annual_savings), formatPLN(d.settings.annual_savings)],
        ['Success at chosen age', formatPct(b.chosen_age_success_rate), formatPct(d.chosen_age_success_rate)],
        ['First shortfall age (median among failed runs)', b.median_first_shortfall_age ?? 'None', d.median_first_shortfall_age ?? 'None'],
        ['Sustainable annual spending', formatPLN(b.sustainable_spending_at_chosen_age), formatPLN(d.sustainable_spending_at_chosen_age)],
    ];
    rows.forEach((values, index) => {
        const row = table.insertRow();
        values.forEach(value => {
            const cell = document.createElement(index === 0 ? 'th' : 'td');
            cell.textContent = value;
            row.appendChild(cell);
        });
    });
    document.getElementById('scenario-comparison').replaceChildren(table);
    document.getElementById('scenario-note').textContent =
        `Balances as of ${d.snapshot_date}. Comparisons use the same random seed. Inputs above are compared after calculation completes.`;
}

async function loadSelectedScenario() {
    cancelPendingCalculation();
    const select = document.getElementById('scenario-select');
    selectedScenarioId = select.value ? Number(select.value) : null;
    const scenario = savedScenarios.find(s => s.id === selectedScenarioId);
    document.getElementById('scenario-name').value = scenario ? scenario.name : '';
    document.getElementById('delete-scenario').disabled = !scenario;
    fillForm(scenario ? scenario.settings : baselineData.settings, baselineData);
    refreshLeverLabels();
    await runUpdate();
}

async function discardScenario() {
    await loadSelectedScenario();
}

async function saveScenario(asNew = false) {
    cancelPendingCalculation();
    const id = asNew ? null : selectedScenarioId;
    try {
        const result = await retirementRequest('/api/retirement/scenarios' + (id ? `/${id}` : ''), id ? 'PUT' : 'POST', {
            name: document.getElementById('scenario-name').value,
            settings: readForm(),
        });
        selectedScenarioId = result.id;
        await refreshScenarios();
        await runUpdate();
        setStatus('Scenario saved · baseline unchanged', 'text-success');
    } catch (err) { setStatus(err.message, 'text-danger'); }
}

async function updateBaseline() {
    if (!confirm('Replace your saved baseline with the current inputs?')) return;
    cancelPendingCalculation();
    try {
        await retirementRequest('/api/retirement', 'POST', readForm());
        await loadRetirement();
        setStatus('Baseline updated', 'text-success');
    } catch (err) { setStatus(err.message, 'text-danger'); }
}

async function deleteScenario() {
    if (!selectedScenarioId || !confirm('Delete this saved scenario?')) return;
    cancelPendingCalculation();
    try {
        await retirementRequest(`/api/retirement/scenarios/${selectedScenarioId}`, 'DELETE');
        selectedScenarioId = null;
        await refreshScenarios();
        await loadSelectedScenario();
    } catch (err) { setStatus(err.message, 'text-danger'); }
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
    baselineData = data;
    selectedScenarioId = null;
    appEl.innerHTML = '';
    appEl.appendChild(document.getElementById('retirement-template').content.cloneNode(true));

    buildLevers();
    fillForm(data.settings, data);
    refreshLeverLabels();
    renderDataQuality(data.data_quality);
    renderChart(data);          // sets chartUsesLogScale, read by renderResults
    renderResults(data);

    await refreshScenarios();
    renderScenarioComparison();

    // Collapsed fields preview too, on change rather than per keystroke.
    for (const key of SETTING_KEYS) {
        const el = document.getElementById(key);
        if (el && el.type !== 'range' && el.id !== 'use_historical_returns') {
            el.addEventListener('change', scheduleUpdate);
        }
    }
}

loadRetirement();
