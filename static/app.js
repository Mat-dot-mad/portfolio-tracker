// ── Helpers ──────────────────────────────────────────

const COLORS = [
    '#0d6efd', '#198754', '#ffc107', '#dc3545', '#6f42c1',
    '#0dcaf0', '#fd7e14', '#20c997', '#6610f2', '#d63384',
    '#0984e3', '#00b894', '#e17055', '#74b9ff', '#a29bfe',
];

function formatPLN(value) {
    return new Intl.NumberFormat('pl-PL', {
        style: 'currency',
        currency: 'PLN',
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
    }).format(value);
}

function formatPctChange(current, previous) {
    if (!previous || previous === 0) return '';
    const pct = ((current - previous) / Math.abs(previous)) * 100;
    const sign = pct >= 0 ? '+' : '';
    const cls = pct >= 0 ? 'text-positive' : 'text-negative';
    return `<span class="${cls}">${sign}${pct.toFixed(1)}%</span>`;
}

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

// ── Theme Toggle ────────────────────────────────────

function toggleTheme() {
    const html = document.documentElement;
    const current = html.getAttribute('data-bs-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-bs-theme', next);
    localStorage.setItem('theme', next);

    const btn = document.getElementById('themeToggle');
    if (btn) btn.textContent = next === 'dark' ? 'Light' : 'Dark';
}

// Apply saved theme on load
(function() {
    const saved = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-bs-theme', saved);
    // Button text will be set after template loads
})();

// ── Dashboard data ──────────────────────────────────

let dashboardData = null;

async function loadDashboard() {
    const resp = await fetch('/api/dashboard');
    dashboardData = await resp.json();

    const app = document.getElementById('app');
    const template = document.getElementById('dashboard-template');
    app.innerHTML = '';
    app.appendChild(template.content.cloneNode(true));

    // Update theme toggle button text
    const theme = document.documentElement.getAttribute('data-bs-theme');
    const btn = document.getElementById('themeToggle');
    if (btn) btn.textContent = theme === 'dark' ? 'Light' : 'Dark';

    renderSummaryCards();
    renderTimelineChart();
    renderMoneyInChart();
    initBreakdownTable();
    renderTreemap('chartTags', dashboardData.by_tags, 'Tags');
    renderTreemap('chartAccount', dashboardData.by_account, 'Account');
    initQuarterlyEntry();
}

// ── Summary Cards with QoQ change ───────────────────

function renderSummaryCards() {
    const d = dashboardData;
    const quarter = d.latest ? d.latest.quarter : '—';
    const container = document.getElementById('summary-cards');
    const tl = d.timeline;

    // Find previous quarter totals for QoQ change
    let prevPortfolio = 0, prevCash = 0, prevMortgage = 0, prevNet = 0;
    if (tl.length >= 2) {
        const prev = tl[tl.length - 2];
        prevPortfolio = prev.portfolio_total;
        prevCash = prev.cash_total;
        prevMortgage = prev.mortgage_total;
        prevNet = prev.net_worth;
    }

    const cards = [
        {
            label: `Portfolio (${quarter})`,
            value: formatPLN(d.portfolio_total),
            change: tl.length >= 2 ? formatPctChange(d.portfolio_total, prevPortfolio) : '',
            color: '',
        },
        {
            label: `Cash (${quarter})`,
            value: formatPLN(d.cash_total),
            change: tl.length >= 2 ? formatPctChange(d.cash_total, prevCash) : '',
            color: '',
        },
        {
            label: `Mortgage (${quarter})`,
            value: formatPLN(d.mortgage_total),
            change: tl.length >= 2 ? formatPctChange(d.mortgage_total, prevMortgage) : '',
            color: 'text-negative',
        },
        {
            label: `Net Worth (${quarter})`,
            value: formatPLN(d.net_worth),
            change: tl.length >= 2 ? formatPctChange(d.net_worth, prevNet) : '',
            color: d.net_worth >= 0 ? 'text-positive' : 'text-negative',
        },
    ];

    container.innerHTML = cards.map(c => `
        <div class="col-sm-6 col-lg-3">
            <div class="card h-100">
                <div class="card-body">
                    <div class="card-label">${c.label}</div>
                    <div class="card-value ${c.color}">${c.value}</div>
                    ${c.change ? `<div class="card-change">${c.change} vs prev quarter</div>` : ''}
                </div>
            </div>
        </div>
    `).join('');
}

// ── Money In vs Value Chart ─────────────────────────
// Two lines on a shared time axis:
//   1. Cumulative net invested — staircase showing how much you've put in
//   2. Total wealth (portfolio + cash − mortgage) — actual value
// The vertical gap is your real market gains.

let moneyInChartInstance = null;

function renderMoneyInChart() {
    const card = document.getElementById('moneyin-card');
    const canvas = document.getElementById('chartMoneyIn');
    if (!card || !canvas) return;

    const timeline = dashboardData.timeline;
    const lt = dashboardData.lifetime;
    if (!lt || !lt.available || !timeline.length) {
        card.classList.add('d-none');
        return;
    }
    card.classList.remove('d-none');

    // Headline stats line above the chart: market gains + total return %.
    // Annualized rate uses simple compound growth (current_wealth / net_invested)^(1/years).
    // It assumes all contributions arrived at the start of the period, so it
    // UNDERSTATES the true IRR when contributions grow over time — labelled
    // "simple" to make that clear.
    const totalReturnPct = lt.net_invested > 0
        ? (lt.market_gains / lt.net_invested) * 100
        : 0;
    const gainsCls = lt.market_gains >= 0 ? 'text-positive' : 'text-negative';
    const sign = lt.market_gains >= 0 ? '+' : '';
    let annualizedFrag = '';
    if (lt.earliest_date && lt.latest_date && lt.net_invested > 0 && lt.current_wealth > 0) {
        const start = new Date(lt.earliest_date);
        const end = new Date(lt.latest_date);
        const years = (end - start) / (365.25 * 24 * 3600 * 1000);
        if (years >= 0.5) {
            const annual = Math.pow(lt.current_wealth / lt.net_invested, 1 / years) - 1;
            annualizedFrag = ` / <strong>~${(annual * 100).toFixed(1)}%</strong> annualized`;
        }
    }
    const statsEl = document.getElementById('moneyin-stats');
    if (statsEl) {
        statsEl.innerHTML =
            `Total market gains: <strong class="${gainsCls}">${sign}${formatPLN(lt.market_gains)}</strong> ` +
            `(<strong class="${gainsCls}">${totalReturnPct >= 0 ? '+' : ''}${totalReturnPct.toFixed(1)}%</strong>` +
            `${annualizedFrag}, simple) ` +
            `<span class="text-muted small">since ${lt.earliest_date}</span>`;
    }

    if (moneyInChartInstance) moneyInChartInstance.destroy();

    const labels = timeline.map(t => t.quarter);
    const investedSeries = timeline.map(t => t.cumulative_invested);
    const wealthSeries = timeline.map(t => t.portfolio_total + t.cash_total - t.mortgage_total);

    moneyInChartInstance = new Chart(canvas, {
        type: 'line',
        data: {
            labels,
            datasets: [
                {
                    label: 'Net invested (cumulative)',
                    data: investedSeries,
                    borderColor: '#6c757d',
                    backgroundColor: 'rgba(108, 117, 125, 0.1)',
                    borderWidth: 2,
                    tension: 0,             // staircase-ish, no smoothing
                    stepped: true,
                    fill: true,
                },
                {
                    label: 'Net worth (portfolio + cash − mortgage)',
                    data: wealthSeries,
                    borderColor: '#198754',
                    backgroundColor: 'rgba(25, 135, 84, 0.15)',
                    borderWidth: 2.5,
                    tension: 0.3,
                    fill: '-1',             // fills toward dataset above (net invested)
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
                y: { ticks: { callback: v => formatPLN(v) } },
            },
        },
    });
}

// ── Timeline Line Chart (with Cash) ─────────────────

function renderTimelineChart() {
    const timeline = dashboardData.timeline;
    const ctx = document.getElementById('chartTimeline');
    if (!ctx || !timeline.length) return;

    new Chart(ctx, {
        type: 'line',
        data: {
            labels: timeline.map(t => t.quarter),
            datasets: [
                {
                    label: 'Portfolio',
                    data: timeline.map(t => t.portfolio_total),
                    borderColor: '#0d6efd',
                    backgroundColor: 'rgba(13, 110, 253, 0.1)',
                    fill: true,
                    tension: 0.3,
                },
                {
                    label: 'Cash',
                    data: timeline.map(t => t.cash_total),
                    borderColor: '#ffc107',
                    backgroundColor: 'rgba(255, 193, 7, 0.1)',
                    fill: true,
                    tension: 0.3,
                },
                {
                    label: 'Mortgage',
                    data: timeline.map(t => -t.mortgage_total),
                    borderColor: '#dc3545',
                    backgroundColor: 'rgba(220, 53, 69, 0.1)',
                    fill: true,
                    tension: 0.3,
                    borderDash: [5, 5],
                },
                {
                    label: 'Net Worth',
                    data: timeline.map(t => t.net_worth),
                    borderColor: '#198754',
                    borderWidth: 2.5,
                    tension: 0.3,
                    fill: false,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: { ticks: { callback: v => formatPLN(v) } },
            },
            plugins: {
                legend: { position: 'bottom' },
                tooltip: {
                    callbacks: {
                        label: ctx => `${ctx.dataset.label}: ${formatPLN(ctx.parsed.y)}`,
                    },
                },
            },
        },
    });
}

// ── Treemap Charts ──────────────────────────────────

function renderTreemap(canvasId, dataMap, label) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;

    const entries = Object.entries(dataMap)
        .filter(([, v]) => v > 0)
        .sort((a, b) => b[1] - a[1]);

    if (!entries.length) return;

    const total = entries.reduce((sum, [, v]) => sum + v, 0);

    new Chart(ctx, {
        type: 'treemap',
        data: {
            datasets: [{
                tree: entries.map(([key, value], i) => ({ key, value, idx: i })),
                key: 'value',
                groups: ['key'],
                backgroundColor: (ctx) => {
                    if (!ctx.raw) return COLORS[0];
                    const idx = ctx.raw._data?.idx ?? ctx.dataIndex;
                    return COLORS[idx % COLORS.length];
                },
                borderWidth: 2,
                borderColor: 'rgba(255,255,255,0.6)',
                spacing: 1,
                labels: {
                    display: true,
                    align: 'left',
                    position: 'top',
                    font: { size: 12, weight: 'bold' },
                    color: '#fff',
                    formatter: (ctx) => {
                        if (!ctx.raw) return '';
                        const v = ctx.raw.v;
                        const pct = ((v / total) * 100).toFixed(1);
                        return `${ctx.raw.g} (${pct}%)`;
                    },
                },
                captions: { display: false },
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: (items) => items[0]?.raw?.g || '',
                        label: (ctx) => {
                            const v = ctx.raw.v;
                            const pct = ((v / total) * 100).toFixed(1);
                            return `${formatPLN(v)} (${pct}%)`;
                        },
                    },
                },
            },
        },
    });
}

// ── Breakdown Table ─────────────────────────────────

let breakdownState = {
    sortCol: 'tags',
    sortAsc: true,
    fromIdx: 0,
    toIdx: 0,
    accountFilter: '',
    expandedGroups: new Set(),
    quarters: [],
    groupData: [],
};

function buildBreakdownData() {
    const quarters = dashboardData.quarters;
    const allPos = dashboardData.all_positions;

    const groupMap = {};

    for (const q of quarters) {
        const positions = allPos[q.id] || [];
        for (const p of positions) {
            const tag = p.tags || 'Other';
            const account = p.account || 'Other';
            const groupKey = `${tag}||${account}`;

            if (!groupMap[groupKey]) {
                groupMap[groupKey] = {
                    tags: tag,
                    account: account,
                    values: {},
                    tickerMap: {},
                };
            }
            const group = groupMap[groupKey];
            group.values[q.id] = (group.values[q.id] || 0) + p.value_pln;

            const tickerKey = `${p.ticker || ''}||${p.name}`;
            if (!group.tickerMap[tickerKey]) {
                group.tickerMap[tickerKey] = {
                    name: p.name,
                    ticker: p.ticker || '',
                    values: {},
                };
            }
            group.tickerMap[tickerKey].values[q.id] = (group.tickerMap[tickerKey].values[q.id] || 0) + p.value_pln;
        }
    }

    const groupData = Object.values(groupMap).map(g => ({
        ...g,
        tickers: Object.values(g.tickerMap).sort((a, b) => {
            const lastQ = quarters[quarters.length - 1]?.id;
            return (b.values[lastQ] || 0) - (a.values[lastQ] || 0);
        }),
    }));

    breakdownState.quarters = quarters;
    breakdownState.groupData = groupData;
    breakdownState.toIdx = quarters.length - 1;
    breakdownState.fromIdx = Math.max(0, quarters.length - 4);
}

function initBreakdownTable() {
    buildBreakdownData();

    const filterFrom = document.getElementById('filterFrom');
    const filterTo = document.getElementById('filterTo');
    const quarters = breakdownState.quarters;

    const opts = quarters.map((q, i) => `<option value="${i}">${q.quarter}</option>`).join('');
    filterFrom.innerHTML = opts;
    filterTo.innerHTML = opts;
    filterFrom.value = breakdownState.fromIdx;
    filterTo.value = breakdownState.toIdx;

    filterFrom.addEventListener('change', () => {
        breakdownState.fromIdx = parseInt(filterFrom.value);
        if (breakdownState.fromIdx > breakdownState.toIdx) {
            breakdownState.toIdx = breakdownState.fromIdx;
            filterTo.value = breakdownState.toIdx;
        }
        renderBreakdownTable();
    });
    filterTo.addEventListener('change', () => {
        breakdownState.toIdx = parseInt(filterTo.value);
        if (breakdownState.toIdx < breakdownState.fromIdx) {
            breakdownState.fromIdx = breakdownState.toIdx;
            filterFrom.value = breakdownState.fromIdx;
        }
        renderBreakdownTable();
    });

    const filterAccount = document.getElementById('filterAccount');
    filterAccount.addEventListener('change', () => {
        breakdownState.accountFilter = filterAccount.value;
        renderBreakdownTable();
    });

    renderBreakdownTable();
}

function matchesAccountFilter(account) {
    const filter = breakdownState.accountFilter;
    if (!filter) return true;
    const type = getRetirementType(account);
    if (filter === 'RETIREMENT') return type !== null;
    return type === filter;
}

function getVisibleQuarters() {
    return breakdownState.quarters.slice(breakdownState.fromIdx, breakdownState.toIdx + 1);
}

function sortGroups(groups) {
    const { sortCol, sortAsc } = breakdownState;

    return [...groups].sort((a, b) => {
        let cmp = 0;
        if (sortCol === 'tags') {
            cmp = a.tags.localeCompare(b.tags) || a.account.localeCompare(b.account);
        } else if (sortCol === 'account') {
            cmp = a.account.localeCompare(b.account) || a.tags.localeCompare(b.tags);
        } else if (sortCol === 'change') {
            const visibleQs = getVisibleQuarters();
            const firstQ = visibleQs[0]?.id;
            const lastQ = visibleQs[visibleQs.length - 1]?.id;
            const aChange = (a.values[lastQ] || 0) - (a.values[firstQ] || 0);
            const bChange = (b.values[lastQ] || 0) - (b.values[firstQ] || 0);
            cmp = bChange - aChange;
        } else {
            const qId = parseInt(sortCol);
            cmp = (b.values[qId] || 0) - (a.values[qId] || 0);
        }
        return sortAsc ? cmp : -cmp;
    });
}

function computeChange(values, visibleQs) {
    if (visibleQs.length < 2) return { pln: 0, pct: '' };
    const firstVal = values[visibleQs[0].id] || 0;
    const lastVal = values[visibleQs[visibleQs.length - 1].id] || 0;
    const diff = lastVal - firstVal;
    let pctHtml = '';
    if (firstVal > 0) {
        const pct = (diff / firstVal) * 100;
        const sign = pct >= 0 ? '+' : '';
        const cls = pct >= 0 ? 'text-positive' : 'text-negative';
        pctHtml = `<span class="${cls}">${sign}${pct.toFixed(1)}%</span>`;
    } else if (lastVal > 0) {
        pctHtml = '<span class="text-positive">new</span>';
    }
    return { pln: diff, pctHtml };
}

function renderBreakdownTable() {
    const thead = document.getElementById('breakdown-head');
    const tbody = document.getElementById('breakdown-body');
    const tfoot = document.getElementById('breakdown-footer');
    const visibleQs = getVisibleQuarters();
    const { sortCol, sortAsc } = breakdownState;
    const showChange = visibleQs.length >= 2;

    // Header
    function sortArrow(col) {
        const isActive = sortCol === col;
        const arrow = isActive ? (sortAsc ? '▲' : '▼') : '▲';
        return `<span class="sort-arrow">${arrow}</span>`;
    }
    function thClass(col) {
        return sortCol === col ? 'sort-active' : '';
    }

    thead.innerHTML = `<tr>
        <th class="${thClass('tags')}" data-sort="tags">Tag ${sortArrow('tags')}</th>
        <th class="${thClass('account')}" data-sort="account">Account ${sortArrow('account')}</th>
        ${visibleQs.map(q => `<th class="num ${thClass(String(q.id))}" data-sort="${q.id}">${q.quarter} ${sortArrow(String(q.id))}</th>`).join('')}
        ${showChange ? `<th class="num ${thClass('change')}" data-sort="change">Change ${sortArrow('change')}</th>` : ''}
    </tr>`;

    // Sort click handlers
    thead.querySelectorAll('th[data-sort]').forEach(th => {
        th.addEventListener('click', () => {
            const col = th.dataset.sort;
            if (breakdownState.sortCol === col) {
                breakdownState.sortAsc = !breakdownState.sortAsc;
            } else {
                breakdownState.sortCol = col;
                breakdownState.sortAsc = col === 'tags' || col === 'account';
            }
            renderBreakdownTable();
        });
    });

    // Body
    const filtered = breakdownState.groupData.filter(group =>
        matchesAccountFilter(group.account) && visibleQs.some(q => group.values[q.id])
    );
    const sorted = sortGroups(filtered);
    const totals = {};
    visibleQs.forEach(q => totals[q.id] = 0);

    let html = '';
    for (const group of sorted) {
        const groupKey = `${group.tags}||${group.account}`;
        const isExpanded = breakdownState.expandedGroups.has(groupKey);
        const change = showChange ? computeChange(group.values, visibleQs) : null;

        html += `<tr class="group-row ${isExpanded ? 'expanded' : ''}" data-group="${groupKey}">
            <td>${group.tags}</td>
            <td>${group.account} ${accountBadge(group.account)}</td>
            ${visibleQs.map(q => {
                const val = group.values[q.id] || 0;
                totals[q.id] += val;
                return `<td class="num">${val ? formatPLN(val) : '<span class="text-muted">—</span>'}</td>`;
            }).join('')}
            ${showChange ? `<td class="num col-change">${change.pctHtml}</td>` : ''}
        </tr>`;

        const visibleTickers = group.tickers.filter(t => visibleQs.some(q => t.values[q.id]));
        for (const t of visibleTickers) {
            const tChange = showChange ? computeChange(t.values, visibleQs) : null;
            html += `<tr class="ticker-row ${isExpanded ? 'visible' : ''}" data-parent="${groupKey}">
                <td>${t.ticker ? t.ticker : t.name}</td>
                <td></td>
                ${visibleQs.map(q => {
                    const val = t.values[q.id] || 0;
                    return `<td class="num">${val ? formatPLN(val) : '<span class="text-muted">—</span>'}</td>`;
                }).join('')}
                ${showChange ? `<td class="num col-change">${tChange.pctHtml}</td>` : ''}
            </tr>`;
        }
    }
    tbody.innerHTML = html;

    // Footer
    const totalChange = showChange ? computeChange(totals, visibleQs) : null;
    tfoot.innerHTML = `<tr>
        <td>Total</td>
        <td></td>
        ${visibleQs.map(q => `<td class="num">${formatPLN(totals[q.id])}</td>`).join('')}
        ${showChange ? `<td class="num col-change">${totalChange.pctHtml}</td>` : ''}
    </tr>`;

    // Accordion toggle
    tbody.querySelectorAll('.group-row').forEach(row => {
        row.addEventListener('click', () => {
            const key = row.dataset.group;
            if (breakdownState.expandedGroups.has(key)) {
                breakdownState.expandedGroups.delete(key);
            } else {
                breakdownState.expandedGroups.add(key);
            }
            row.classList.toggle('expanded');
            tbody.querySelectorAll(`.ticker-row[data-parent="${CSS.escape(key)}"]`).forEach(tr => {
                tr.classList.toggle('visible');
            });
        });
    });
}

// ── CSV Export ───────────────────────────────────────

function exportCSV() {
    const visibleQs = getVisibleQuarters();
    const showChange = visibleQs.length >= 2;

    const filtered = breakdownState.groupData.filter(group =>
        matchesAccountFilter(group.account) && visibleQs.some(q => group.values[q.id])
    );
    const sorted = sortGroups(filtered);

    // Header row
    const headers = ['Tag', 'Account', ...visibleQs.map(q => q.quarter)];
    if (showChange) headers.push('Change %');
    const rows = [headers];

    // Data rows
    for (const group of sorted) {
        const row = [group.tags, group.account];
        for (const q of visibleQs) {
            row.push((group.values[q.id] || 0).toFixed(2));
        }
        if (showChange) {
            const firstVal = group.values[visibleQs[0].id] || 0;
            const lastVal = group.values[visibleQs[visibleQs.length - 1].id] || 0;
            if (firstVal > 0) {
                row.push((((lastVal - firstVal) / firstVal) * 100).toFixed(1));
            } else {
                row.push('');
            }
        }
        rows.push(row);
    }

    // Totals row
    const totals = ['Total', ''];
    for (const q of visibleQs) {
        const sum = sorted.reduce((s, g) => s + (g.values[q.id] || 0), 0);
        totals.push(sum.toFixed(2));
    }
    if (showChange) totals.push('');
    rows.push(totals);

    // Build CSV string and download
    const csv = rows.map(r => r.map(cell => `"${cell}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `portfolio_breakdown_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
}

// ── Quarterly Entry ─────────────────────────────────

function initQuarterlyEntry() {
    const select = document.getElementById('entryQuarter');
    const snapshots = dashboardData.snapshots;

    select.innerHTML = snapshots.map(s =>
        `<option value="${s.id}">${s.quarter} (${s.snapshot_date})</option>`
    ).join('');

    if (snapshots.length) {
        loadManualEntries(snapshots[0].id);
    }

    select.addEventListener('change', () => {
        loadManualEntries(select.value);
    });
}

const rateCache = {};

function getSelectedSnapshotDate() {
    const select = document.getElementById('entryQuarter');
    const snapshot = dashboardData.snapshots.find(s => s.id == select.value);
    return snapshot ? snapshot.snapshot_date : null;
}

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

    const mortgageAmount = parseFloat(document.getElementById('mortgageAmount').value);
    const mortgageLabel = document.getElementById('mortgageLabel').value.trim();
    if (mortgageAmount) {
        entries.push({ type: 'mortgage', label: mortgageLabel || 'Mortgage', currency: 'PLN', original_amount: mortgageAmount, amount_pln: mortgageAmount });
    }

    const resp = await fetch(`/api/manual-entries/${snapshotId}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ entries }) });
    const status = document.getElementById('saveStatus');
    if (resp.ok) { status.textContent = 'Saved! Reloading...'; status.className = 'ms-2 small text-success'; setTimeout(() => loadDashboard(), 500); }
    else { status.textContent = 'Error saving.'; status.className = 'ms-2 small text-danger'; }
}

// ── Delete Snapshot ─────────────────────────────────

async function deleteSelectedSnapshot() {
    const select = document.getElementById('entryQuarter');
    const snapshotId = select.value;
    const label = select.options[select.selectedIndex].text;
    if (!confirm(`Delete "${label}" and all its positions? This cannot be undone.`)) return;
    const resp = await fetch(`/api/snapshots/${snapshotId}`, { method: 'DELETE' });
    if (resp.ok) loadDashboard();
    else alert('Error deleting snapshot.');
}

// ── CSV Import ─────────────────────────────────────

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
            resultDiv.innerHTML = `<div class="alert alert-success py-2">
                Imported <strong>${data.quarter}</strong> (${data.snapshot_date})
                — ${data.positions_count} positions, total: ${formatPLN(data.total_value)}.
                Reloading dashboard...
            </div>`;
            fileInput.value = '';
            setTimeout(() => loadDashboard(), 800);
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

// ── Cash Flow Import ───────────────────────────────

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
                Reloading dashboard...
            </div>`;
            fileInput.value = '';
            setTimeout(() => loadDashboard(), 1200);
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

loadDashboard();
