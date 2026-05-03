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
    // We model NET WORTH directly (not portfolio + cash + mortgage separately).
    // For each quarter from snapshot 2 onwards, we split ΔNW into:
    //   - Contributions: known from the imported myfund.pl XLSX (net deposits to wealth system)
    //   - Market gains: ΔNW − contributions (residual that came from market performance)
    //
    // The market-return rate = market_gains / NW_prev. This is what we sample
    // from for Monte Carlo paths. Mortgage paydown is NW-neutral (cash − X,
    // mortgage − X, NW unchanged), so it doesn't enter the math.
    //
    // The first snapshot's net_contributions field includes lumped pre-snapshot
    // history, so per-quarter return computation starts from the SECOND snapshot.
    const nwMarketReturns = [];
    const rawNwReturns = [];
    for (let i = 1; i < timeline.length; i++) {
        const prev = timeline[i - 1];
        const curr = timeline[i];
        const prevNW = prev.portfolio_total + prev.cash_total - prev.mortgage_total;
        const currNW = curr.portfolio_total + curr.cash_total - curr.mortgage_total;
        if (prevNW <= 0) continue;

        const dNW = currNW - prevNW;
        rawNwReturns.push(dNW / prevNW);

        const netContrib = curr.net_contributions || 0;
        nwMarketReturns.push((dNW - netContrib) / prevNW);
    }

    // Default contribution slider: average of the LAST 4 quarters of net_contributions.
    // Reflects current saving pace (which has grown over time) rather than the
    // long-run average that includes 2019-2020 when contributions were smaller.
    // Excludes the first snapshot (which is artificially inflated by the pre-snapshot lump).
    const recentN = Math.min(4, Math.max(0, timeline.length - 1));
    let recentSum = 0;
    for (let i = timeline.length - recentN; i < timeline.length; i++) {
        recentSum += (timeline[i].net_contributions || 0);
    }
    const recentContribAvg = recentN > 0 ? recentSum / recentN : 0;

    const meanRaw = rawNwReturns.length
        ? rawNwReturns.reduce((a, b) => a + b, 0) / rawNwReturns.length
        : 0;
    const meanMarket = nwMarketReturns.length
        ? nwMarketReturns.reduce((a, b) => a + b, 0) / nwMarketReturns.length
        : 0;

    const hasCashFlowData = timeline.some(t => (t.net_contributions || 0) !== 0);

    return {
        nw: {
            returns: nwMarketReturns,
            historicalMean: meanMarket,                        // per-quarter, market-only
            historicalAnnual: Math.pow(1 + meanMarket, 4) - 1, // annualized, market-only
            rawAnnual: Math.pow(1 + meanRaw, 4) - 1,           // annualized, raw incl. contribs
            hasCashFlowData,
        },
        contribution: {
            recentAvg: recentContribAvg,                       // per quarter
            recentN: recentN,
        },
    };
}

function runMonteCarlo(stats, assumptions, horizon, paths = 1000) {
    const { startNW, contribution, annualReturn } = assumptions;

    // Bootstrap NW market returns to preserve the historical volatility profile.
    // If the user moved the return slider, SHIFT the distribution so its mean
    // equals the user's target — keeps real-world skew/outliers while honoring
    // the "what if mean return were X" question.
    const histMean = stats.nw.historicalMean;
    const targetQuarterlyMean = Math.pow(1 + annualReturn, 1 / 4) - 1;
    const meanShift = targetQuarterlyMean - histMean;

    const returns = stats.nw.returns;
    const allPaths = [];

    for (let p = 0; p < paths; p++) {
        const path = [];
        let nw = startNW;
        for (let q = 0; q < horizon; q++) {
            const idx = Math.floor(Math.random() * returns.length);
            const r = returns[idx] + meanShift;
            // Each quarter: existing NW earns market return, then add contribution
            nw = nw * (1 + r) + contribution;
            path.push(nw);
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
    const contribution = parseFloat(document.getElementById('contribution-slider').value);

    const last = timeline[timeline.length - 1];
    const startNW = last.portfolio_total + last.cash_total - last.mortgage_total;
    return { horizon, annualReturn, contribution, startNW };
}

function updateSliderLabels(assumptions) {
    document.getElementById('horizon-value').textContent = assumptions.horizon;
    const years = (assumptions.horizon / 4).toFixed(assumptions.horizon % 4 === 0 ? 0 : 2);
    document.getElementById('horizon-years').textContent = `(≈${years} ${years === '1' ? 'year' : 'years'})`;
    document.getElementById('return-value').textContent = formatPct(assumptions.annualReturn * 100);
    document.getElementById('contribution-value').textContent = formatPLN(assumptions.contribution);
}

function setSlidersToHistorical() {
    const h = historicalStats;
    document.getElementById('horizon-slider').value = 8;

    // Return slider: NW market return (true historical, contributions backed out).
    // Clamp to slider range so the visible default isn't pinned to a bound.
    const returnSlider = document.getElementById('return-slider');
    const rMin = parseFloat(returnSlider.min), rMax = parseFloat(returnSlider.max);
    const annualPct = h.nw.historicalAnnual * 100;
    returnSlider.value = Math.max(rMin, Math.min(rMax, annualPct)).toFixed(1);

    // Contribution slider: recent (last 4q) average net contribution rate.
    const contribSlider = document.getElementById('contribution-slider');
    const cMin = parseFloat(contribSlider.min), cMax = parseFloat(contribSlider.max);
    const contrib = Math.round(h.contribution.recentAvg);
    contribSlider.value = Math.max(cMin, Math.min(cMax, contrib));
}

function showHistoricalDefaultsHints() {
    const h = historicalStats;
    const marketPct = formatPct(h.nw.historicalAnnual * 100);
    const rawPct = formatPct(h.nw.rawAnnual * 100);
    const sourceLabel = h.nw.hasCashFlowData ? 'true market' : 'estimated market';
    document.getElementById('return-default').textContent =
        `(${sourceLabel}: ${marketPct} · raw incl. contributions: ${rawPct})`;
    document.getElementById('contribution-default').textContent =
        `(last ${h.contribution.recentN}q avg: ${formatPLN(h.contribution.recentAvg)} / quarter)`;
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
    const currentNW = assumptions.startNW;

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
            interaction: {
                mode: 'index',
                intersect: false,
            },
            plugins: {
                legend: {
                    position: 'bottom',
                    // Hide the invisible P10 baseline from the legend (it's only there
                    // as a fill anchor for the P10–P90 range above it).
                    labels: { filter: (item) => item.text !== 'P10' },
                },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    callbacks: {
                        title: (items) => {
                            if (!items.length) return '';
                            const idx = items[0].dataIndex;
                            const isForecast = idx >= histLen;
                            return items[0].label + (isForecast ? ' (projected)' : '');
                        },
                        label: (ctx) => {
                            // Skip the invisible P10 baseline (its values are reused by
                            // the P10–P90 range line below).
                            if (ctx.dataset.label === 'P10') return null;
                            if (ctx.parsed.y == null) return null;
                            if (ctx.dataset.label === 'P10–P90 range') {
                                const p10 = ctx.chart.data.datasets[0].data[ctx.dataIndex];
                                const p90 = ctx.parsed.y;
                                if (p10 == null) return null;
                                return `Range (P10–P90): ${formatPLN(p10)} – ${formatPLN(p90)}`;
                            }
                            return `${ctx.dataset.label}: ${formatPLN(ctx.parsed.y)}`;
                        },
                        afterBody: (items) => {
                            if (!items.length) return [];
                            const idx = items[0].dataIndex;
                            // Extra context only for HISTORICAL points after the first
                            // (we need the previous snapshot to compute Δ; first snapshot's
                            // net_contributions includes lumped pre-snapshot history).
                            if (idx >= histLen || idx === 0) return [];

                            const tl = timeline[idx];
                            const prevTl = timeline[idx - 1];
                            const netContrib = tl.net_contributions || 0;
                            const dNW = (tl.portfolio_total + tl.cash_total - tl.mortgage_total) -
                                        (prevTl.portfolio_total + prevTl.cash_total - prevTl.mortgage_total);
                            const marketGain = dNW - netContrib;

                            const fmt = (v) => `${v >= 0 ? '+' : ''}${formatPLN(v)}`;
                            const lines = [`Net contributions: ${fmt(netContrib)}`];
                            if (Math.abs(marketGain) >= 1) {
                                lines.push(`Market gain (implied): ${fmt(marketGain)}`);
                            }
                            return lines;
                        },
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
    ['horizon-slider', 'return-slider', 'contribution-slider'].forEach(id => {
        document.getElementById(id).addEventListener('input', render);
    });

    document.getElementById('reset-btn').addEventListener('click', () => {
        setSlidersToHistorical();
        render();
    });

    render();
}

loadForecast();
