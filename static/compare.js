// Shared helpers (formatPLN, accountBadge, theme) live in common.js,
// loaded before this file.

// ── Helpers ──────────────────────────────────────────

// Amount + percentage change label. The dashboard's formatPctChange() renders
// percentage only — different output, so they stay separate.
function formatChange(current, previous, polarity = 1) {
    const diff = current - previous;
    const sign = diff >= 0 ? '+' : '';
    let pct = '';
    if (previous && previous !== 0) {
        const p = (diff / Math.abs(previous)) * 100;
        pct = ` (${p >= 0 ? '+' : ''}${p.toFixed(1)}%)`;
    }
    return `<span class="${changeClass(diff, polarity)}">${sign}${formatPLN(diff)}${pct}</span>`;
}

// ── Compare State ───────────────────────────────────

let compareData = null;
let sortCol = 'change';
let sortAsc = false;
let expandedGroups = new Set();

// Chart instances — kept so we can destroy them before re-rendering when
// the user changes the selected quarters. Otherwise Chart.js leaks canvases
// and tooltips from the old chart linger.
let tagDeltaChart = null;
let waterfallChart = null;

// ── Load & Render ───────────────────────────────────

async function loadCompare(idA, idB) {
    const params = (idA && idB) ? `?a=${idA}&b=${idB}` : '';
    const resp = await fetch(`/api/compare${params}`);
    const payload = await resp.json();

    const app = document.getElementById('app');

    // With no snapshots the API answers 404. Parsing that as data left the
    // page as a dead skeleton with empty selectors, because initSelectors()
    // then mapped over an undefined quarters list. Forecast and Retirement
    // already say why they are empty; this now matches them.
    if (!resp.ok) {
        app.innerHTML = `<div class="alert alert-info">
            ${payload.error || 'Comparison unavailable.'}
            Comparing quarters needs at least two snapshots —
            <a href="/import" class="alert-link">add quarter data</a> to get started.
        </div>`;
        return;
    }

    compareData = payload;
    const template = document.getElementById('compare-template');

    // Only replace contents on first load
    if (!document.getElementById('selectA')) {
        app.innerHTML = '';
        app.appendChild(template.content.cloneNode(true));

        initSelectors();
        window.addEventListener('themechange', redrawChartsForTheme);
    }

    renderCards();
    renderDiffTable();
    renderTagDeltaChart();
    renderWaterfallChart();
}

function initSelectors() {
    const selectA = document.getElementById('selectA');
    const selectB = document.getElementById('selectB');
    const quarters = compareData.quarters;

    const opts = quarters.map(q =>
        `<option value="${q.id}">${q.quarter}</option>`
    ).join('');
    selectA.innerHTML = opts;
    selectB.innerHTML = opts;

    selectA.value = compareData.snapshot_a.id;
    selectB.value = compareData.snapshot_b.id;

    const onChange = () => {
        expandedGroups.clear();
        loadCompare(selectA.value, selectB.value);
    };
    selectA.addEventListener('change', onChange);
    selectB.addEventListener('change', onChange);
}

// ── Summary Cards ───────────────────────────────────

function renderCards() {
    const t = compareData.totals;
    const container = document.getElementById('compare-cards');
    const qA = compareData.snapshot_a.quarter;
    const qB = compareData.snapshot_b.quarter;

    const items = [
        // Polarity as on the dashboard: paying down the mortgage is green
        // here, matching its bar in the Net Worth Bridge below.
        { label: 'Portfolio', key: 'portfolio', polarity: 1 },
        { label: 'Cash', key: 'cash', polarity: 0 },
        { label: 'Mortgage', key: 'mortgage', polarity: -1 },
        { label: 'Net Worth', key: 'net_worth', polarity: 1 },
    ];

    container.innerHTML = items.map(item => {
        const [valA, valB] = t[item.key];
        return `
        <div class="col-sm-6 col-lg-3">
            <div class="card h-100">
                <div class="card-body">
                    <div class="card-label">${item.label}</div>
                    <div class="card-detail">${qA}: ${formatPLN(valA)}</div>
                    <div class="card-detail">${qB}: ${formatPLN(valB)}</div>
                    <div class="card-change">${formatChange(valB, valA, item.polarity)}</div>
                </div>
            </div>
        </div>`;
    }).join('');
}

// ── Diff Table ──────────────────────────────────────

function buildDiffData() {
    const posA = compareData.positions_a;
    const posB = compareData.positions_b;

    // Group by tag+account, with ticker details
    const groupMap = {};

    function addPositions(positions, side) {
        for (const p of positions) {
            const tag = p.tags || 'Other';
            const account = p.account || 'Other';
            const key = `${tag}||${account}`;

            if (!groupMap[key]) {
                groupMap[key] = { tags: tag, account: account, valA: 0, valB: 0, tickerMap: {} };
            }
            const group = groupMap[key];
            if (side === 'A') group.valA += p.value_pln;
            else group.valB += p.value_pln;

            const tKey = `${p.ticker || ''}||${p.name}`;
            if (!group.tickerMap[tKey]) {
                group.tickerMap[tKey] = { ticker: p.ticker || '', name: p.name, valA: 0, valB: 0 };
            }
            if (side === 'A') group.tickerMap[tKey].valA += p.value_pln;
            else group.tickerMap[tKey].valB += p.value_pln;
        }
    }

    addPositions(posA, 'A');
    addPositions(posB, 'B');

    return Object.values(groupMap).map(g => ({
        ...g,
        change: g.valB - g.valA,
        tickers: Object.values(g.tickerMap)
            .map(t => ({ ...t, change: t.valB - t.valA }))
            .sort((a, b) => Math.abs(b.change) - Math.abs(a.change)),
    }));
}

function sortDiffGroups(groups) {
    return [...groups].sort((a, b) => {
        let cmp = 0;
        if (sortCol === 'tags') {
            cmp = a.tags.localeCompare(b.tags) || a.account.localeCompare(b.account);
        } else if (sortCol === 'account') {
            cmp = a.account.localeCompare(b.account) || a.tags.localeCompare(b.tags);
        } else if (sortCol === 'valA') {
            cmp = a.valA - b.valA;
        } else if (sortCol === 'valB') {
            cmp = a.valB - b.valB;
        } else if (sortCol === 'change') {
            cmp = Math.abs(a.change) - Math.abs(b.change);
        }
        // Every branch above yields ascending order; direction is applied here
        // and nowhere else. The numeric branches used to compare b to a, so
        // this line reversed them a second time and the ▼ (largest first)
        // view listed the smallest movers first.
        return sortAsc ? cmp : -cmp;
    });
}

function renderDiffTable() {
    const thead = document.getElementById('diff-head');
    const tbody = document.getElementById('diff-body');
    const tfoot = document.getElementById('diff-footer');
    const qA = compareData.snapshot_a.quarter;
    const qB = compareData.snapshot_b.quarter;

    function arrow(col) {
        const active = sortCol === col;
        return `<span class="sort-arrow">${active ? (sortAsc ? '▲' : '▼') : '▲'}</span>`;
    }
    function cls(col) { return sortCol === col ? 'sort-active' : ''; }

    thead.innerHTML = `<tr>
        <th class="${cls('tags')}" data-sort="tags">Tag ${arrow('tags')}</th>
        <th class="${cls('account')}" data-sort="account">Account ${arrow('account')}</th>
        <th class="num ${cls('valA')}" data-sort="valA">${qA} ${arrow('valA')}</th>
        <th class="num ${cls('valB')}" data-sort="valB">${qB} ${arrow('valB')}</th>
        <th class="num ${cls('change')}" data-sort="change">Change ${arrow('change')}</th>
    </tr>`;

    thead.querySelectorAll('th[data-sort]').forEach(th => {
        th.addEventListener('click', () => {
            const col = th.dataset.sort;
            if (sortCol === col) sortAsc = !sortAsc;
            else { sortCol = col; sortAsc = col === 'tags' || col === 'account'; }
            renderDiffTable();
        });
    });

    const groups = buildDiffData();

    // Split into: existing, new, removed
    const existing = groups.filter(g => g.valA > 0 && g.valB > 0);
    const added = groups.filter(g => g.valA === 0 && g.valB > 0);
    const removed = groups.filter(g => g.valA > 0 && g.valB === 0);

    const sorted = sortDiffGroups(existing);
    const sortedAdded = sortDiffGroups(added);
    const sortedRemoved = sortDiffGroups(removed);

    let totalA = 0, totalB = 0;
    let html = '';

    function renderGroup(group, status) {
        const key = `${group.tags}||${group.account}`;
        const isExpanded = expandedGroups.has(key);
        totalA += group.valA;
        totalB += group.valB;

        const valAStr = group.valA ? formatPLN(group.valA) : '<span class="text-muted">—</span>';
        const valBStr = group.valB ? formatPLN(group.valB) : '<span class="text-muted">—</span>';

        let changeStr;
        if (status === 'new') {
            changeStr = `<span class="text-new">+${formatPLN(group.valB)} (new)</span>`;
        } else if (status === 'removed') {
            changeStr = `<span class="text-sold">-${formatPLN(group.valA)} (sold)</span>`;
        } else {
            changeStr = formatChange(group.valB, group.valA);
        }

        html += `<tr class="group-row ${isExpanded ? 'expanded' : ''}" data-group="${key}">
            <td>${group.tags}</td>
            <td>${group.account} ${accountBadge(group.account)}</td>
            <td class="num">${valAStr}</td>
            <td class="num">${valBStr}</td>
            <td class="num">${changeStr}</td>
        </tr>`;

        for (const t of group.tickers) {
            const tValA = t.valA ? formatPLN(t.valA) : '<span class="text-muted">—</span>';
            const tValB = t.valB ? formatPLN(t.valB) : '<span class="text-muted">—</span>';
            let tChange;
            if (t.valA === 0) tChange = `<span class="text-new">+${formatPLN(t.valB)} (new)</span>`;
            else if (t.valB === 0) tChange = `<span class="text-sold">-${formatPLN(t.valA)} (sold)</span>`;
            else tChange = formatChange(t.valB, t.valA);

            html += `<tr class="ticker-row ${isExpanded ? 'visible' : ''}" data-parent="${key}">
                <td>${t.ticker || t.name}</td>
                <td></td>
                <td class="num">${tValA}</td>
                <td class="num">${tValB}</td>
                <td class="num">${tChange}</td>
            </tr>`;
        }
    }

    // Existing positions
    for (const g of sorted) renderGroup(g, 'existing');

    // New positions section
    if (sortedAdded.length) {
        html += `<tr><td colspan="5" class="section-label text-new">New Positions</td></tr>`;
        for (const g of sortedAdded) renderGroup(g, 'new');
    }

    // Removed positions section
    if (sortedRemoved.length) {
        html += `<tr><td colspan="5" class="section-label text-sold">Removed Positions</td></tr>`;
        for (const g of sortedRemoved) renderGroup(g, 'removed');
    }

    tbody.innerHTML = html;

    // Footer
    tfoot.innerHTML = `<tr>
        <td>Total</td>
        <td></td>
        <td class="num">${formatPLN(totalA)}</td>
        <td class="num">${formatPLN(totalB)}</td>
        <td class="num">${formatChange(totalB, totalA)}</td>
    </tr>`;

    // Accordion toggle
    tbody.querySelectorAll('.group-row').forEach(row => {
        row.addEventListener('click', () => {
            const key = row.dataset.group;
            if (expandedGroups.has(key)) expandedGroups.delete(key);
            else expandedGroups.add(key);
            row.classList.toggle('expanded');
            tbody.querySelectorAll(`.ticker-row[data-parent="${CSS.escape(key)}"]`).forEach(tr => {
                tr.classList.toggle('visible');
            });
        });
    });
}

// ── Tag Delta Bar Chart ─────────────────────────────

function buildTagDeltas() {
    const aggA = {};
    const aggB = {};
    for (const p of compareData.positions_a) {
        const tag = p.tags || 'Other';
        aggA[tag] = (aggA[tag] || 0) + p.value_pln;
    }
    for (const p of compareData.positions_b) {
        const tag = p.tags || 'Other';
        aggB[tag] = (aggB[tag] || 0) + p.value_pln;
    }
    const allTags = new Set([...Object.keys(aggA), ...Object.keys(aggB)]);
    const deltas = [];
    for (const tag of allTags) {
        const valA = aggA[tag] || 0;
        const valB = aggB[tag] || 0;
        const change = valB - valA;
        if (change !== 0) deltas.push({ tag, valA, valB, change });
    }
    // Sort by absolute change descending — biggest movers first (top of horizontal chart)
    deltas.sort((a, b) => Math.abs(b.change) - Math.abs(a.change));
    return deltas;
}

// Charts take their colours when created; see applyChartTheme in common.js.
function redrawChartsForTheme() {
    if (!compareData) return;
    renderTagDeltaChart();
    renderWaterfallChart();
}

function renderTagDeltaChart() {
    const canvas = document.getElementById('tag-delta-chart');
    if (!canvas) return;
    if (tagDeltaChart) tagDeltaChart.destroy();

    const data = buildTagDeltas();
    if (!data.length) {
        // No movement at all — leave the canvas blank rather than showing an empty axis
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        return;
    }

    tagDeltaChart = new Chart(canvas, {
        type: 'bar',
        data: {
            labels: data.map(d => d.tag),
            datasets: [{
                label: 'Change (PLN)',
                data: data.map(d => d.change),
                backgroundColor: data.map(d =>
                    withAlpha(cssColor(d.change >= 0 ? '--pt-good' : '--pt-bad'), 0.7)),
                borderColor: data.map(d => cssColor(d.change >= 0 ? '--pt-good' : '--pt-bad')),
                borderWidth: 1,
            }],
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function(ctx) {
                            const d = data[ctx.dataIndex];
                            const sign = d.change >= 0 ? '+' : '';
                            const pct = d.valA > 0
                                ? ` (${sign}${(d.change / d.valA * 100).toFixed(1)}%)`
                                : '';
                            return [
                                `Change: ${sign}${formatPLN(d.change)}${pct}`,
                                `From: ${formatPLN(d.valA)}`,
                                `To: ${formatPLN(d.valB)}`,
                            ];
                        },
                    },
                },
            },
            scales: {
                x: { ticks: { callback: v => formatPLN(v) } },
            },
        },
    });
}

// ── Waterfall (Net Worth Bridge) ────────────────────

function buildWaterfallStops() {
    const t = compareData.totals;
    const portfolioChange = t.portfolio[1] - t.portfolio[0];
    const cashChange = t.cash[1] - t.cash[0];
    const mortgageChange = t.mortgage[1] - t.mortgage[0];
    const netWorthA = t.net_worth[0];

    // Each stop is a Chart.js floating bar [low, high]. The bars form a staircase
    // from netWorthA to netWorthB, one step per category. Start/end totals live in
    // the subtitle text rather than as full bars (the deltas are usually so small
    // relative to net worth that full-bar totals would dominate the chart).
    const stops = [];
    let running = netWorthA;

    // Portfolio change
    let next = running + portfolioChange;
    stops.push({
        label: 'Portfolio',
        range: [Math.min(running, next), Math.max(running, next)],
        type: portfolioChange >= 0 ? 'positive' : 'negative',
        delta: portfolioChange,
    });
    running = next;

    // Cash change
    next = running + cashChange;
    stops.push({
        label: 'Cash',
        range: [Math.min(running, next), Math.max(running, next)],
        type: cashChange >= 0 ? 'positive' : 'negative',
        delta: cashChange,
    });
    running = next;

    // Mortgage impact (mortgage is SUBTRACTED from net worth — a positive
    // mortgage change reduces net worth, so we negate the delta sign here)
    next = running - mortgageChange;
    stops.push({
        label: 'Mortgage',
        range: [Math.min(running, next), Math.max(running, next)],
        type: -mortgageChange >= 0 ? 'positive' : 'negative',
        delta: -mortgageChange,
    });

    return stops;
}

function renderWaterfallChart() {
    const canvas = document.getElementById('waterfall-chart');
    if (!canvas) return;
    if (waterfallChart) waterfallChart.destroy();

    // Populate the subtitle with start → end net worth (since the chart no longer
    // shows them as full bars).
    const summaryEl = document.getElementById('waterfall-summary');
    if (summaryEl) {
        const t = compareData.totals;
        const nwA = t.net_worth[0];
        const nwB = t.net_worth[1];
        const totalDelta = nwB - nwA;
        const sign = totalDelta >= 0 ? '+' : '';
        const pct = nwA !== 0 ? ` (${sign}${(totalDelta / Math.abs(nwA) * 100).toFixed(1)}%)` : '';
        const cls = totalDelta >= 0 ? 'text-positive' : 'text-negative';
        const qA = compareData.snapshot_a.quarter;
        const qB = compareData.snapshot_b.quarter;
        summaryEl.innerHTML =
            `Net Worth ${qA}: <strong>${formatPLN(nwA)}</strong> ` +
            `→ ${qB}: <strong>${formatPLN(nwB)}</strong> ` +
            `<span class="${cls}">${sign}${formatPLN(totalDelta)}${pct}</span>`;
    }

    const stops = buildWaterfallStops();

    waterfallChart = new Chart(canvas, {
        type: 'bar',
        data: {
            labels: stops.map(s => s.label),
            datasets: [{
                label: 'Net Worth Bridge',
                data: stops.map(s => s.range),  // floating bars: [start, end]
                backgroundColor: stops.map(s => {
                    if (s.type === 'total') return 'rgba(13, 110, 253, 0.7)';
                    // Each step is coloured by its effect on net worth, the
                    // usual waterfall convention.
                    if (s.type === 'positive') return withAlpha(cssColor('--pt-good'), 0.7);
                    return withAlpha(cssColor('--pt-bad'), 0.7);
                }),
                borderColor: stops.map(s => {
                    if (s.type === 'total') return '#0d6efd';
                    if (s.type === 'positive') return cssColor('--pt-good');
                    return cssColor('--pt-bad');
                }),
                borderWidth: 1,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function(ctx) {
                            const stop = stops[ctx.dataIndex];
                            if (stop.type === 'total') {
                                return formatPLN(stop.delta);
                            }
                            const sign = stop.delta >= 0 ? '+' : '';
                            return `${sign}${formatPLN(stop.delta)}`;
                        },
                    },
                },
            },
            scales: {
                // Auto-scale tightly to the bars instead of starting at 0 — the
                // running totals span only ~5–10% of net worth, so a 0-anchored
                // axis would visually crush the deltas.
                y: {
                    beginAtZero: false,
                    ticks: { callback: v => formatPLN(v) },
                },
            },
        },
    });
}

// ── Init ────────────────────────────────────────────

loadCompare();
