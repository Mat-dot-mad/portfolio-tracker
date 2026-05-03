// ── Helpers ──────────────────────────────────────────

function formatPLN(value) {
    return new Intl.NumberFormat('pl-PL', {
        style: 'currency',
        currency: 'PLN',
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
    }).format(value);
}

function formatPct(value, decimals = 1) {
    const sign = value >= 0 ? '+' : '';
    return `${sign}${value.toFixed(decimals)}%`;
}

// e.g. "2026-Q1" → "2026-Q2", "2026-Q4" → "2027-Q1"
function nextQuarter(q) {
    const [year, qStr] = q.split('-Q');
    let y = parseInt(year, 10);
    let n = parseInt(qStr, 10);
    n += 1;
    if (n > 4) { n = 1; y += 1; }
    return `${y}-Q${n}`;
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

(function() {
    const saved = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-bs-theme', saved);
})();

// ── Forecast State ──────────────────────────────────

let timeline = null;            // History from /api/dashboard
let historicalStats = null;     // Computed once on load
let forecastChart = null;       // Chart.js instance, destroyed before re-render

// ── Computation Engine ──────────────────────────────

function computeHistoricalStats(timeline) {
    // Two return series for the portfolio component:
    //
    //   - rawReturns: total portfolio growth per quarter, INCLUDING contributions.
    //     Shown only as a transparency hint — projecting forward at this rate
    //     would assume contributions continue forever, which they probably won't
    //     at the same pace.
    //
    //   - marketReturns: ACTUAL market returns, computed by subtracting the
    //     known per-quarter net contributions (from the imported myfund.pl XLSX)
    //     from the portfolio's quarterly delta. This is the correct input for
    //     forward-looking Monte Carlo.
    //
    // Note: the FIRST snapshot's net_contributions field includes lumped pre-snapshot
    // history, so it's not usable for return computation (we'd need the portfolio
    // value before that period, which we don't have). Per-quarter market returns
    // therefore start from the SECOND snapshot onward.
    const rawReturns = [];
    const marketReturns = [];
    for (let i = 1; i < timeline.length; i++) {
        const prev = timeline[i - 1];
        const curr = timeline[i];
        if (prev.portfolio_total <= 0) continue;

        const portfolioDelta = curr.portfolio_total - prev.portfolio_total;
        rawReturns.push(portfolioDelta / prev.portfolio_total);

        // net_contributions for snapshot i covers (date_{i-1}, date_i]. Some of that
        // money may have stayed in cash and not entered the portfolio — but for a
        // first-order estimate we attribute all of it to the portfolio side.
        // (Refinement could split contributions between portfolio and cash per the
        // actual cash_total delta, but it's a small effect.)
        const netContrib = curr.net_contributions || 0;
        marketReturns.push((portfolioDelta - netContrib) / prev.portfolio_total);
    }

    // Mean per-quarter ABSOLUTE delta for cash and mortgage (from snapshots only).
    let cashDeltaSum = 0, mortgageDeltaSum = 0;
    let cashCount = 0, mortgageCount = 0;
    for (let i = 1; i < timeline.length; i++) {
        cashDeltaSum += (timeline[i].cash_total - timeline[i - 1].cash_total);
        cashCount++;
        mortgageDeltaSum += -(timeline[i].mortgage_total - timeline[i - 1].mortgage_total);
        mortgageCount++;
    }

    const meanRaw = rawReturns.length
        ? rawReturns.reduce((a, b) => a + b, 0) / rawReturns.length
        : 0;
    const meanMarket = marketReturns.length
        ? marketReturns.reduce((a, b) => a + b, 0) / marketReturns.length
        : 0;

    // Has the cash-flow XLSX been imported? Affects the "default" semantics shown to user.
    const hasCashFlowData = timeline.some(t => (t.net_contributions || 0) !== 0);

    return {
        portfolio: {
            returns: marketReturns,                            // used by Monte Carlo
            historicalMean: meanMarket,                        // per-quarter, market-only
            historicalAnnual: Math.pow(1 + meanMarket, 4) - 1, // annualized, market-only
            rawAnnual: Math.pow(1 + meanRaw, 4) - 1,           // annualized, includes contributions
            hasCashFlowData,
        },
        cash: {
            meanDelta: cashCount ? cashDeltaSum / cashCount : 0,
        },
        mortgage: {
            meanPayment: mortgageCount ? mortgageDeltaSum / mortgageCount : 0,
        },
    };
}

function runMonteCarlo(stats, assumptions, horizon, paths = 1000) {
    const { startPortfolio, startCash, startMortgage } = assumptions;

    // Always bootstrap from historical returns to preserve quarterly volatility.
    // If the user moved the return slider away from the historical default, we
    // SHIFT the bootstrap distribution so its mean equals the user's target —
    // i.e. each sampled return becomes (sample - historical_mean + override_mean).
    // This keeps the realistic shape (skewness, big-quarter outliers) of past
    // returns while honoring the user's "what if mean return were X" question.
    const histMean = stats.portfolio.historicalMean;
    const targetQuarterlyMean = Math.pow(1 + assumptions.annualReturn, 1 / 4) - 1;
    const meanShift = targetQuarterlyMean - histMean;

    const returns = stats.portfolio.returns;
    const allPaths = [];

    for (let p = 0; p < paths; p++) {
        const path = [];
        let portfolio = startPortfolio;
        let cash = startCash;
        let mortgage = startMortgage;

        for (let q = 0; q < horizon; q++) {
            // Bootstrap-sample a historical return, shift to target mean
            const idx = Math.floor(Math.random() * returns.length);
            const r = returns[idx] + meanShift;
            portfolio = portfolio * (1 + r);

            // Cash: deterministic linear extension
            cash = cash + assumptions.cashDelta;

            // Mortgage: deterministic decrease (clamped at 0)
            mortgage = Math.max(0, mortgage - assumptions.mortgagePayment);

            path.push(portfolio + cash - mortgage);
        }
        allPaths.push(path);
    }

    return allPaths;
}

function summarizePaths(paths) {
    const horizon = paths[0].length;
    const summary = [];
    for (let q = 0; q < horizon; q++) {
        const valuesAtQ = paths.map(p => p[q]).sort((a, b) => a - b);
        const idx = (frac) => Math.floor(frac * (valuesAtQ.length - 1));
        summary.push({
            p10: valuesAtQ[idx(0.10)],
            p50: valuesAtQ[idx(0.50)],
            p90: valuesAtQ[idx(0.90)],
        });
    }
    return summary;
}

// ── UI Rendering ────────────────────────────────────

function readSliderAssumptions() {
    const horizon = parseInt(document.getElementById('horizon-slider').value, 10);
    const annualReturn = parseFloat(document.getElementById('return-slider').value) / 100;
    const cashDelta = parseFloat(document.getElementById('cash-slider').value);
    const mortgagePayment = parseFloat(document.getElementById('mortgage-slider').value);

    const last = timeline[timeline.length - 1];
    return {
        horizon,
        annualReturn,
        cashDelta,
        mortgagePayment,
        startPortfolio: last.portfolio_total,
        startCash: last.cash_total,
        startMortgage: last.mortgage_total,
    };
}

function updateSliderLabels(assumptions) {
    document.getElementById('horizon-value').textContent = assumptions.horizon;
    const years = (assumptions.horizon / 4).toFixed(assumptions.horizon % 4 === 0 ? 0 : 2);
    document.getElementById('horizon-years').textContent = `(≈${years} ${years === '1' ? 'year' : 'years'})`;
    document.getElementById('return-value').textContent = formatPct(assumptions.annualReturn * 100);
    document.getElementById('cash-value').textContent = formatPLN(assumptions.cashDelta);
    document.getElementById('mortgage-value').textContent = formatPLN(assumptions.mortgagePayment);
}

function setSlidersToHistorical() {
    const h = historicalStats;
    document.getElementById('horizon-slider').value = 8;
    // Default the return slider to the market-only estimate (contributions backed out).
    // Clamp to slider range so the visible default isn't pinned to the bound.
    const slider = document.getElementById('return-slider');
    const min = parseFloat(slider.min), max = parseFloat(slider.max);
    const annualPct = h.portfolio.historicalAnnual * 100;
    slider.value = Math.max(min, Math.min(max, annualPct)).toFixed(1);
    document.getElementById('cash-slider').value = Math.round(h.cash.meanDelta);
    document.getElementById('mortgage-slider').value = Math.round(h.mortgage.meanPayment);
}

function showHistoricalDefaultsHints() {
    const h = historicalStats;
    const marketPct = formatPct(h.portfolio.historicalAnnual * 100);
    const rawPct = formatPct(h.portfolio.rawAnnual * 100);
    const sourceLabel = h.portfolio.hasCashFlowData ? 'true market' : 'estimated market';
    document.getElementById('return-default').textContent =
        `(${sourceLabel}: ${marketPct} · raw incl. contributions: ${rawPct})`;
    document.getElementById('cash-default').textContent =
        `(historical avg: ${formatPLN(h.cash.meanDelta)})`;
    document.getElementById('mortgage-default').textContent =
        `(historical avg: ${formatPLN(h.mortgage.meanPayment)})`;
}

function render() {
    const assumptions = readSliderAssumptions();
    updateSliderLabels(assumptions);

    const paths = runMonteCarlo(historicalStats, assumptions, assumptions.horizon, 1000);
    const summary = summarizePaths(paths);

    updateSummaryCards(summary, assumptions);
    renderForecastChart(summary, assumptions);
}

function updateSummaryCards(summary, assumptions) {
    const last = summary[summary.length - 1];
    const currentNW = assumptions.startPortfolio + assumptions.startCash - assumptions.startMortgage;

    function deltaDetail(value) {
        const delta = value - currentNW;
        const pct = currentNW > 0 ? (delta / currentNW) * 100 : 0;
        const sign = delta >= 0 ? '+' : '';
        return `${sign}${formatPLN(delta)} (${formatPct(pct)})`;
    }

    document.getElementById('p10-value').textContent = formatPLN(last.p10);
    document.getElementById('p10-detail').textContent = deltaDetail(last.p10);
    document.getElementById('p50-value').textContent = formatPLN(last.p50);
    document.getElementById('p50-detail').textContent = deltaDetail(last.p50);
    document.getElementById('p90-value').textContent = formatPLN(last.p90);
    document.getElementById('p90-detail').textContent = deltaDetail(last.p90);

    // Subtitle on the chart
    const summaryEl = document.getElementById('forecast-summary');
    if (summaryEl) {
        const lastQ = timeline[timeline.length - 1].quarter;
        let endQ = lastQ;
        for (let i = 0; i < assumptions.horizon; i++) endQ = nextQuarter(endQ);
        summaryEl.innerHTML =
            `Current net worth (${lastQ}): <strong>${formatPLN(currentNW)}</strong> ` +
            `→ projected ${endQ} (${assumptions.horizon}q): ` +
            `<strong>${formatPLN(last.p50)}</strong> ` +
            `<span class="text-muted">(P10–P90: ${formatPLN(last.p10)} – ${formatPLN(last.p90)})</span>`;
    }
}

function renderForecastChart(summary, assumptions) {
    const canvas = document.getElementById('forecast-chart');
    if (!canvas) return;
    if (forecastChart) forecastChart.destroy();

    // Build labels: all historical quarters, plus N projected quarters
    const labels = timeline.map(t => t.quarter);
    let nextQ = labels[labels.length - 1];
    for (let i = 0; i < assumptions.horizon; i++) {
        nextQ = nextQuarter(nextQ);
        labels.push(nextQ);
    }

    const histLen = timeline.length;
    const totalLen = labels.length;
    const histNW = timeline.map(t => t.net_worth);

    // Pad arrays so historical and forecast can share the x-axis
    const padNulls = (n) => Array(n).fill(null);

    // Connect history to forecast: include the last historical point in each forecast
    // dataset so the forecast lines start exactly where history ends (no visual gap).
    const lastHistNW = histNW[histNW.length - 1];

    const historicalData = [...histNW, ...padNulls(assumptions.horizon)];
    const medianData = [...padNulls(histLen - 1), lastHistNW, ...summary.map(s => s.p50)];
    const p10Data    = [...padNulls(histLen - 1), lastHistNW, ...summary.map(s => s.p10)];
    const p90Data    = [...padNulls(histLen - 1), lastHistNW, ...summary.map(s => s.p90)];

    forecastChart = new Chart(canvas, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                // P10 lower bound — invisible line (used as fill anchor for P90)
                {
                    label: 'P10',
                    data: p10Data,
                    borderColor: 'rgba(25, 135, 84, 0)',
                    pointRadius: 0,
                    fill: false,
                    order: 3,
                },
                // P90 upper bound — fills toward P10 (previous dataset)
                {
                    label: 'P10–P90 range',
                    data: p90Data,
                    borderColor: 'rgba(25, 135, 84, 0)',
                    backgroundColor: 'rgba(25, 135, 84, 0.15)',
                    pointRadius: 0,
                    fill: '-1',
                    order: 2,
                },
                // Median forecast — dashed line
                {
                    label: 'Median forecast',
                    data: medianData,
                    borderColor: '#198754',
                    borderDash: [6, 6],
                    borderWidth: 2,
                    pointRadius: 0,
                    fill: false,
                    tension: 0.2,
                    order: 1,
                },
                // Historical net worth — solid line
                {
                    label: 'Historical net worth',
                    data: historicalData,
                    borderColor: '#198754',
                    borderWidth: 2.5,
                    pointRadius: 2,
                    fill: false,
                    tension: 0.2,
                    order: 0,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'bottom' },
                tooltip: {
                    callbacks: {
                        label: ctx => `${ctx.dataset.label}: ${formatPLN(ctx.parsed.y)}`,
                    },
                },
            },
            scales: {
                y: {
                    beginAtZero: false,
                    ticks: { callback: v => formatPLN(v) },
                },
            },
        },
    });
}

// ── Init ────────────────────────────────────────────

async function loadForecast() {
    const resp = await fetch('/api/dashboard');
    const data = await resp.json();
    timeline = data.timeline;

    const app = document.getElementById('app');
    const tpl = document.getElementById('forecast-template');

    // Edge case: too little history for stable stats
    if (!timeline || timeline.length < 4) {
        app.innerHTML = `<div class="alert alert-info">
            Forecast needs at least 4 quarters of history (you have ${timeline ? timeline.length : 0}).
            Import more snapshots to enable projections.
        </div>`;
        return;
    }

    app.innerHTML = '';
    app.appendChild(tpl.content.cloneNode(true));

    // Theme toggle button text reflects current theme
    const tbtn = document.getElementById('themeToggle');
    if (tbtn) tbtn.textContent = document.documentElement.getAttribute('data-bs-theme') === 'dark' ? 'Light' : 'Dark';

    historicalStats = computeHistoricalStats(timeline);
    showHistoricalDefaultsHints();
    setSlidersToHistorical();

    // Wire slider events — `input` for live drag updates
    ['horizon-slider', 'return-slider', 'cash-slider', 'mortgage-slider'].forEach(id => {
        document.getElementById(id).addEventListener('input', render);
    });

    document.getElementById('reset-btn').addEventListener('click', () => {
        setSlidersToHistorical();
        render();
    });

    render();
}

loadForecast();
