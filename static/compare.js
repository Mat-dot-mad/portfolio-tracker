// ── Helpers ──────────────────────────────────────────

function formatPLN(value) {
    return new Intl.NumberFormat('pl-PL', {
        style: 'currency',
        currency: 'PLN',
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
    }).format(value);
}

function formatChange(current, previous) {
    const diff = current - previous;
    const sign = diff >= 0 ? '+' : '';
    let pct = '';
    if (previous && previous !== 0) {
        const p = (diff / Math.abs(previous)) * 100;
        pct = ` (${p >= 0 ? '+' : ''}${p.toFixed(1)}%)`;
    }
    const cls = diff >= 0 ? 'text-positive' : 'text-negative';
    return `<span class="${cls}">${sign}${formatPLN(diff)}${pct}</span>`;
}

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

(function() {
    const saved = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-bs-theme', saved);
})();

// ── Compare State ───────────────────────────────────

let compareData = null;
let sortCol = 'change';
let sortAsc = false;
let expandedGroups = new Set();

// ── Load & Render ───────────────────────────────────

async function loadCompare(idA, idB) {
    const params = (idA && idB) ? `?a=${idA}&b=${idB}` : '';
    const resp = await fetch(`/api/compare${params}`);
    compareData = await resp.json();

    const app = document.getElementById('app');
    const template = document.getElementById('compare-template');

    // Only replace contents on first load
    if (!document.getElementById('selectA')) {
        app.innerHTML = '';
        app.appendChild(template.content.cloneNode(true));

        const btn = document.getElementById('themeToggle');
        if (btn) btn.textContent = document.documentElement.getAttribute('data-bs-theme') === 'dark' ? 'Light' : 'Dark';

        initSelectors();
    }

    renderCards();
    renderDiffTable();
}

function initSelectors() {
    const selectA = document.getElementById('selectA');
    const selectB = document.getElementById('selectB');
    const quarters = compareData.quarters;

    const opts = quarters.map(q =>
        `<option value="${q.id}">${q.quarter} (${q.snapshot_date})</option>`
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
        { label: 'Portfolio', key: 'portfolio' },
        { label: 'Cash', key: 'cash' },
        { label: 'Mortgage', key: 'mortgage' },
        { label: 'Net Worth', key: 'net_worth' },
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
                    <div class="card-change">${formatChange(valB, valA)}</div>
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
            cmp = b.valA - a.valA;
        } else if (sortCol === 'valB') {
            cmp = b.valB - a.valB;
        } else if (sortCol === 'change') {
            cmp = Math.abs(b.change) - Math.abs(a.change);
        }
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

// ── Init ────────────────────────────────────────────

loadCompare();
