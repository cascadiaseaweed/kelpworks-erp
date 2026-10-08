/* KelpWorks ERP — vanilla JS front end */
'use strict';

const TOKEN_KEY = 'kelp_erp_token';
const State = { token: localStorage.getItem(TOKEN_KEY) || null, user: null, ref: null, tab: 'dashboard' };

/* ---------------- API ---------------- */
async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (State.token) opts.headers['Authorization'] = 'Bearer ' + State.token;
  if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const res = await fetch('/api' + path, opts);
  let data = {};
  try { data = await res.json(); } catch (e) {}
  if (res.status === 401) { logout(); throw new Error(data.error || 'Session expired'); }
  if (!res.ok) { const err = new Error(data.error || ('HTTP ' + res.status)); err.code = data.code; throw err; }
  return data;
}

/* ---------------- helpers ---------------- */
const $ = (sel, el = document) => el.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const k in attrs) {
    if (k === 'class') n.className = attrs[k];
    else if (k === 'html') n.innerHTML = attrs[k];
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), attrs[k]);
    else if (attrs[k] != null) n.setAttribute(k, attrs[k]);
  }
  for (const kid of kids.flat()) { if (kid != null) n.append(kid.nodeType ? kid : document.createTextNode(kid)); }
  return n;
};
const fmt = (n, d = 0) => (n == null ? '—' : Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
const speciesName = c => { const s = (State.ref?.species || []).find(x => x.code === c); return s ? (s.common || s.name) : (c || '—'); };
const skuName = c => { const s = (State.ref?.skus || []).find(x => x.code === c); return s ? s.name : (c || '—'); };
const siteName = c => { const s = (State.ref?.sites || []).find(x => x.code === c); return s ? s.name : (c || '—'); };
function toast(msg, isErr) {
  const t = el('div', { class: 'summary-line', style: 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);z-index:99;box-shadow:var(--shadow);' + (isErr ? 'background:#fbe3df;color:#c0392b' : 'background:#e2f3ef;color:#15564F') }, msg);
  document.body.append(t); setTimeout(() => t.remove(), 3200);
}

/* ---------------- Code128 barcode ---------------- */
const C128 = ["11011001100","11001101100","11001100110","10010011000","10010001100","10001001100","10011001000","10011000100","10001100100","11001001000","11001000100","11000100100","10110011100","10011011100","10011001110","10111001100","10011101100","10011100110","11001110010","11001011100","11001001110","11011100100","11001110100","11101101110","11101001100","11100101100","11100100110","11101100100","11100110100","11100110010","11011011000","11011000110","11000110110","10100011000","10001011000","10001000110","10110001000","10001101000","10001100010","11010001000","11000101000","11000100010","10110111000","10110001110","10001101110","10111011000","10111000110","10001110110","11101110110","11010001110","11000101110","11011101000","11011100010","11011101110","11101011000","11101000110","11100010110","11101101000","11101100010","11100011010","11101111010","11001000010","11110001010","10100110000","10100001100","10010110000","10010000110","10000101100","10000100110","10110010000","10110000100","10011010000","10011000010","10000110100","10000110010","11000010010","11001010000","11110111010","11000010100","10001111010","10100111100","10010111100","10010011110","10111100100","10011110100","10011110010","11110100100","11110010100","11110010010","11011011110","11011110110","11110110110","10101111000","10100011110","10001011110","10111101000","10111100010","11110101000","11110100010","10111011110","10111101110","11101011110","11110101110","11010000100","11010010000","11010011100","1100011101011"];
function code128SVG(data, opts = {}) {
  data = String(data); const mw = opts.mw || 1.5, h = opts.h || 46;
  const codes = [104];                       // Start B
  for (const ch of data) codes.push(ch.charCodeAt(0) - 32);
  let sum = 104;
  for (let i = 0; i < data.length; i++) sum += (data.charCodeAt(i) - 32) * (i + 1);
  codes.push(sum % 103); codes.push(106);    // checksum + stop
  let bits = ''; for (const c of codes) bits += C128[c];
  const quiet = 10, totalW = bits.length * mw + quiet * 2 * mw;
  let x = quiet * mw, rects = '';
  let run = 0;
  for (let i = 0; i <= bits.length; i++) {
    if (bits[i] === '1') { run++; }
    else { if (run) { rects += `<rect x="${x.toFixed(2)}" y="0" width="${(run*mw).toFixed(2)}" height="${h}"/>`; x += run * mw; run = 0; } x += mw; }
  }
  return `<svg viewBox="0 0 ${totalW.toFixed(0)} ${h}" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg"><g fill="#000">${rects}</g></svg>`;
}

/* ---------------- Auth / shell ---------------- */
function logout() { State.token = null; State.user = null; localStorage.removeItem(TOKEN_KEY); show('login'); }
function show(which) {
  $('#login').classList.toggle('hidden', which !== 'login');
  $('#app').classList.toggle('hidden', which === 'login');
}
$('#loginForm').addEventListener('submit', async e => {
  e.preventDefault(); $('#loginError').textContent = '';
  try {
    const r = await api('POST', '/auth/login', { email: $('#email').value, password: $('#password').value });
    State.token = r.token; localStorage.setItem(TOKEN_KEY, r.token); State.user = r.user;
    await boot();
  } catch (err) { $('#loginError').textContent = err.message; }
});
$('#logout').addEventListener('click', logout);
$('#changePw').addEventListener('click', () => changePasswordModal(false));
$('#tabs').addEventListener('click', e => { const b = e.target.closest('button'); if (b) selectTab(b.dataset.tab); });
function selectTab(tab) {
  State.tab = tab;
  $('#tabs').querySelectorAll('button[data-tab]').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  render();
}

async function boot() {
  try {
    State.user = (await api('GET', '/me'));
    State.ref = await api('GET', '/refdata');
    $('#whoName').textContent = State.user.name;
    $('#tabs [data-tab=admin]').closest('.tab-group').classList.toggle('hidden', State.user.role !== 'admin');
    if (State.tab === 'admin' && State.user.role !== 'admin') State.tab = 'dashboard';
    show('app'); selectTab(State.tab);
    if (State.user.mustChange) changePasswordModal(true);
  } catch (err) { logout(); }
}

/* ---------------- Router ---------------- */
function render() {
  const v = $('#view'); v.innerHTML = '';
  v.classList.toggle('wrap-wide', State.tab === 'stabilized');   // scopes the Feedstock Inventory table styling (page width stays the standard .wrap, like Inventory Items)
  ({ dashboard: pageDashboard, stabilized: pageStabilized, preproc: pagePreproc, samples: pageSamples, production: pageProduction, cip: pageCIP,
     qc: pageQC, fg: pageFG, shipping: pageShipping, consumables: pageConsumables, reports: pageReports,
     yieldusage: pageYield, release: pageRelease, calculations: pageCalculations, admin: pageAdmin }[State.tab])(v);
}

/* ---------------- Dashboard ---------------- */
async function pageDashboard(v) {
  v.append(el('div', { class: 'page-head' }, el('h2', {}, 'Dashboard')));
  const d = await api('GET', '/dashboard');
  v.append(el('div', { class: 'tiles' },
    tile('Stabilized totes', fmt(d.stabilized.totes), 'in stock', true),
    tile('Stabilized kelp', fmt(d.stabilized.kg, 0), 'kg on hand'),
    tile('Finished goods', fmt(d.finishedGoods.litres, 0), 'litres on hand'),
    tile('Low stock alerts', fmt(d.lowStock.length), 'reagents & packaging')
  ));
  const left = el('div', { class: 'card' }, el('h3', {}, 'Stabilized inventory by species'),
    table(['Species', 'Totes', 'Kg'], d.stabilized.bySpecies.map(r => [speciesName(r.species), fmt(r.totes), num(fmt(r.kg, 0))]), [false, true, true]));
  const fgRows = d.finishedGoods.lines.map(r => [skuName(r.sku), r.packageSize, fmt(r.qty), num(fmt(r.litres, 0))]);
  const right = el('div', { class: 'card' }, el('h3', {}, 'Finished goods on hand'),
    fgRows.length ? table(['SKU', 'Pack', 'Units', 'Litres'], fgRows, [false, false, true, true]) : el('div', { class: 'empty' }, 'No finished goods yet — run a production batch.'));
  v.append(el('div', { class: 'grid2' }, left, right));

  // Reagents & packaging: two groups (finished-good labels are not listed here), each sorted from the lowest on-hand quantity up
  const consRows = [];
  [['reagent', 'Reagents', 1], ['packaging', 'Packaging', 0]].forEach(([cat, title, dec]) => {
    const list = d.consumables.filter(c => c.category === cat).sort((a, b) => (a.onHand - b.onHand) || a.name.localeCompare(b.name));
    if (!list.length) return;
    consRows.push({ group: el('span', { class: 'group-title' }, el('b', {}, title), el('span', { class: 'muted' }, '  ·  lowest stock first')) });
    list.forEach(c => consRows.push([c.name, fmt(c.onHand, dec) + ' ' + c.unit, badge(c.low ? 'low' : 'ok', c.low ? 'LOW' : 'OK')]));
  });
  const cons = el('div', { class: 'card' }, el('h3', {}, 'Reagents & packaging'), table(['Item', 'On hand', ''], consRows, [false, true, false]));
  const runRows = d.recentRuns.map(r => [mono(r.processingLot), r.runDate, skuName(r.sku), fmt(r.outputLitres, 0) + ' L']);
  const runs = el('div', { class: 'card' }, el('h3', {}, 'Recent production runs'),
    runRows.length ? table(['Processing lot', 'Date', 'SKU', 'Output'], runRows, [false, false, false, true]) : el('div', { class: 'empty' }, 'No runs yet.'));
  v.append(el('div', { class: 'grid2' }, cons, runs));
}
function tile(k, val, u, accent) { return el('div', { class: 'tile' + (accent ? ' accent' : '') }, el('div', { class: 'k' }, k), el('div', { class: 'v' }, val), el('div', { class: 'u' }, u)); }

/* ---------------- Feedstock inventory ---------------- */
let stabCache = [];
const STATUS_LABELS = { in_stock: 'In stock', hold: 'Hold', wip: 'WIP', consumed: 'Consumed', disposed: 'Disposed' };
function statusLabel(s) { return STATUS_LABELS[s] || s || '—'; }
// One entry per real table column (checkbox + actions are handled separately).
// `value(t)` is what's sorted/filtered on — the human-readable form, so a
// filter/sort on "Site" or "Status" matches what's actually displayed.
// Whole weeks (nearest week) a tote stabilized: from its harvest date to today while it is still in inventory, fixed at the
// processing date once it is consumed and at the disposal date once it is disposed. null when a date it needs is missing.
function stabilizationWeeks(t) {
  // start of the clock: harvest date, or for a fine-grind blend its Pre-Processing batch date
  const startDate = t.preprocBatchId ? t.batchDate : t.harvestDate;
  if (!startDate) return null;
  const h = new Date(String(startDate).slice(0, 10) + 'T00:00:00');
  if (isNaN(h)) return null;
  let end;
  if (t.status === 'consumed') end = t.processedDate ? new Date(String(t.processedDate).slice(0, 10) + 'T00:00:00') : null;
  else if (t.status === 'disposed') end = t.disposedDate ? new Date(String(t.disposedDate).slice(0, 10) + 'T00:00:00') : null;
  else { const now = new Date(); end = new Date(now.getFullYear(), now.getMonth(), now.getDate()); }
  if (!end || isNaN(end)) return null;
  const days = Math.round((end - h) / 86400000);
  return days < 0 ? null : Math.round(days / 7);
}
const STAB_COLUMNS = [
  { key: 'lot', label: 'Lot number', value: t => t.lot,
    cell: t => el('span', {}, t.lot, el('button', { type: 'button', class: 'stab-info-btn', title: 'Feedstock details',
      onclick: e => { e.stopPropagation(); showToteDetails(t); } }, 'ⓘ')) },
  { key: 'site', label: 'Site', value: t => siteName(t.site), options: () => (State.ref.sites || []).map(s => s.name),
    cell: t => siteName(t.site) },
  { key: 'species', label: 'Species', value: t => speciesName(t.species),
    options: () => [...new Set((State.ref.species || []).map(s => s.common || s.name))], cell: t => speciesName(t.species) },
  { key: 'grind', label: 'Grind', value: t => t.grind || 'Coarse', options: () => ['Coarse', 'Fine'],
    cell: t => t.grind === 'Fine' ? badge('ok', 'Fine') : 'Coarse' },
  { key: 'stabMethod', label: 'Stabilization method', value: t => t.stabilizationMethod || '',
    options: () => ['Citric acid', 'Fresh'], cell: t => t.stabilizationMethod || '—', hidden: true },
  { key: 'harvestDate', label: 'Harvest date', value: t => t.harvestDate || '', cell: t => t.harvestDate || '—' },
  { key: 'stabWeeks', label: 'Stabilization period (weeks)', value: t => stabilizationWeeks(t), numeric: true, center: true, wrapHead: true,
    cell: t => { const w = stabilizationWeeks(t); return w == null ? '—' : String(w); } },
  { key: 'receivedDate', label: 'Received date', value: t => t.receivedDate || '', cell: t => t.receivedDate || '—', hidden: true },
  { key: 'avgKg', label: 'Avg kg', value: t => t.avgWeightKg, numeric: true, cell: t => fmt(t.avgWeightKg, 1), hidden: true },
  { key: 'ph', label: 'pH', value: t => t.ph, numeric: true, center: true, cell: t => phCell(t) },
  { key: 'orp', label: 'ORP (mV)', value: t => t.orp, numeric: true, center: true, cell: t => orpCell(t) },
  { key: 'lastUpdated', label: 'Last updated', value: t => t.lastUpdated ? fmtWhen(t.lastUpdated) : '',
    cell: t => t.lastUpdated ? fmtWhen(t.lastUpdated) : '—', hidden: true },
  { key: 'location', label: 'Location', value: t => t.location || '', cell: t => t.location || '—' },
  { key: 'status', label: 'Status', value: t => statusLabel(t.status), options: () => Object.values(STATUS_LABELS),
    cell: t => badge(t.status, statusLabel(t.status)) },
];
// Which columns are shown is a per-browser preference. Stabilization method, Received date, Avg kg and Last updated
// start hidden (they live in each row's ⓘ details window) and can be switched on from "Columns".
const STAB_COLS_KEY = 'kelp.stabCols';
function loadStabCols() {
  try { const saved = JSON.parse(localStorage.getItem(STAB_COLS_KEY)); if (Array.isArray(saved)) return new Set(saved); } catch (e) { /* default */ }
  return new Set(STAB_COLUMNS.filter(c => !c.hidden).map(c => c.key));
}
function showToteDetails(t) {
  const rows = [
    ['Lot number', mono(t.lot)], ['Site', siteName(t.site)], ['Species', speciesName(t.species)],
    ['Grind', t.grind || 'Coarse'], ['Stabilization method', t.stabilizationMethod || '—'],
    ['Storage unit', t.storageUnit || '—'], ['Avg weight', t.avgWeightKg != null ? fmt(t.avgWeightKg, 1) + ' kg' : '—'],
    ['Volume', t.volumeL != null ? fmt(t.volumeL, 0) + ' L' : '—'],
    ['Harvest date', t.harvestDate || '—'],
    ['Stabilization period', stabilizationWeeks(t) != null ? stabilizationWeeks(t) + ' week' + (stabilizationWeeks(t) === 1 ? '' : 's') : '—'],
    ['Received date', t.receivedDate || '—'],
    ['pH', t.ph != null ? t.ph + (t.phUpdated ? '  (' + t.phUpdated + ')' : '') : '—'],
    ['ORP (mV)', t.orp != null ? t.orp + (t.orpUpdated ? '  (' + t.orpUpdated + ')' : '') : '—'],
    ['Solids', t.solidsPct != null ? fmt(t.solidsPct, 2) + ' %' : '—'],
    ['Location', t.location || '—'], ['Status', badge(t.status, statusLabel(t.status))],
    ['Last updated', t.lastUpdated ? fmtWhen(t.lastUpdated) : '—'], ['Notes', t.notes || '—']];
  if (t.batchLot) rows.splice(4, 0, ['Pre-processing batch', mono(t.batchLot)]);
  const body = el('div', {}, table(['Field', 'Value'], rows));
  if (t.grind === 'Fine' || (t.status === 'consumed' && !t.runId))
    body.append(el('div', { style: 'margin-top:10px' }, el('button', { class: 'secondary', onclick: () => showPreprocTrace(t) }, 'Trace to source totes')));
  modal('Feedstock details — ' + t.lot, body, async () => {}, 'Close', { noCancel: true });
}
async function pageStabilized(v) {
  v.append(el('div', { class: 'page-head' },
    el('h2', {}, 'Feedstock Inventory'),
    el('div', { class: 'actions' },
      el('button', { class: 'secondary', onclick: openFeedstockImport }, '📤 Bulk import CSV'),
      el('button', { onclick: openHarvest }, '+ Check in harvest'))));
  const r = await api('GET', '/totes');
  stabCache = r.totes;
  const countEl = el('span', { class: 'muted' });
  const bar = el('div', { class: 'toolbar' }, countEl);
  const bulkBar = el('div', { class: 'bulkbar hidden' });
  const host = el('div', {});
  v.append(bar, bulkBar, host);
  const selected = new Set();
  // Consumed and disposed totes are hidden by default (their status is un-ticked); tick them in the Status filter to include them.
  const filters = { status: Object.entries(STATUS_LABELS).filter(([k]) => k !== 'consumed' && k !== 'disposed').map(([, label]) => label) };
  let sortKey = null, sortDir = 1;
  let visibleSelectable = [];
  let shownCols = loadStabCols();
  const cols = () => STAB_COLUMNS.filter(c => shownCols.has(c.key));
  // "Columns" chooser: tick the columns to show (saved in this browser).
  const colMenu = el('details', { class: 'col-menu' }, el('summary', {}, '⚙ Columns'),
    el('div', { class: 'col-menu-panel' }, ...STAB_COLUMNS.map(c => {
      const cb = el('input', { type: 'checkbox', disabled: c.key === 'lot' ? 'disabled' : null });
      cb.checked = shownCols.has(c.key);
      cb.addEventListener('change', () => {
        cb.checked ? shownCols.add(c.key) : shownCols.delete(c.key);
        if (!shownCols.has(sortKey)) sortKey = null;
        delete filters[c.key];
        try { localStorage.setItem(STAB_COLS_KEY, JSON.stringify([...shownCols])); } catch (e) { /* ignore */ }
        drawStab();
      });
      return el('label', {}, cb, ' ' + c.label);
    })));
  bar.append(colMenu);

  function updateBulk() {
    const n = selected.size;
    bulkBar.classList.toggle('hidden', n === 0);
    bulkBar.innerHTML = '';
    if (!n) return;
    bulkBar.append(
      el('span', {}, el('b', {}, n), ' tote' + (n === 1 ? '' : 's') + ' selected'),
      el('button', { onclick: () => bulkMoveTotes([...selected]) }, 'Move selected'),
      el('button', { class: 'danger', onclick: () => disposeSimple('tote', [...selected], 'tote') }, 'Dispose / write off'),
      el('button', { class: 'secondary', onclick: () => { selected.clear(); drawStab(); } }, 'Clear'));
  }
  // Multi-select dropdown filters. The panel lives on <body> (position: fixed) so the scrolling table can't clip it, and
  // stays open while boxes are ticked even though every change redraws the table (placeMultiFilter re-anchors it).
  let multiPanel = null;
  function closeMultiFilter() {
    if (!multiPanel) return;
    multiPanel.el.remove(); multiPanel = null;
    document.removeEventListener('mousedown', outsideMultiFilter, true);
    window.removeEventListener('scroll', placeMultiFilter, true); window.removeEventListener('resize', placeMultiFilter);
  }
  function outsideMultiFilter(e) { if (multiPanel && !multiPanel.el.contains(e.target) && !e.target.closest('.ms-filter')) closeMultiFilter(); }
  function placeMultiFilter() {
    if (!multiPanel) return;
    const btn = host.querySelector('button.ms-filter[data-key="' + multiPanel.key + '"]');
    if (!btn) { closeMultiFilter(); return; }
    const r = btn.getBoundingClientRect();
    multiPanel.el.style.left = Math.max(4, Math.min(r.left, innerWidth - 230)) + 'px';
    multiPanel.el.style.top = (r.bottom + 2) + 'px';
    multiPanel.el.style.minWidth = Math.max(r.width, 170) + 'px';
  }
  function openMultiFilter(c) {
    if (multiPanel && multiPanel.key === c.key) { closeMultiFilter(); return; }
    closeMultiFilter();
    const panel = el('div', { class: 'ms-panel' });
    const fill = () => {
      panel.innerHTML = '';
      panel.append(el('button', { type: 'button', class: 'ghost ms-clear', onclick: () => { filters[c.key] = []; drawStab(); fill(); } }, 'Clear selection'),
        ...c.options().map(o => {
          const cb = el('input', { type: 'checkbox' });
          cb.checked = (filters[c.key] || []).includes(o);
          cb.addEventListener('change', () => {
            const cur = new Set(filters[c.key] || []);
            cb.checked ? cur.add(o) : cur.delete(o);
            filters[c.key] = [...cur]; drawStab();
          });
          return el('label', {}, cb, ' ' + o);
        }));
    };
    fill();
    document.body.append(panel);
    multiPanel = { key: c.key, el: panel };
    document.addEventListener('mousedown', outsideMultiFilter, true);
    window.addEventListener('scroll', placeMultiFilter, true); window.addEventListener('resize', placeMultiFilter);
    placeMultiFilter();
  }
  function drawStab() {
    let rows = stabCache.filter(t => cols().every(c => {
      if (c.options) {      // dropdown filters are multi-select: a row matches if its value is any of the ticked ones
        const sel = filters[c.key];
        return !sel || !sel.length || sel.includes(String(c.value(t) ?? ''));
      }
      const f = (filters[c.key] || '').toLowerCase();
      if (!f) return true;
      return String(c.value(t) ?? '').toLowerCase().includes(f);
    }));
    if (sortKey) {
      const col = STAB_COLUMNS.find(c => c.key === sortKey);
      rows = rows.slice().sort((a, b) => {
        const av = col.value(a), bv = col.value(b);
        const cmp = col.numeric ? (av ?? -Infinity) - (bv ?? -Infinity) : String(av ?? '').localeCompare(String(bv ?? ''));
        return cmp * sortDir;
      });
    }
    // Totes on hold are still selectable (for moving/releasing/disposing) —
    // only consumed/disposed items drop out of bulk actions.
    visibleSelectable = rows.filter(t => t.status === 'in_stock' || t.status === 'hold');
    [...selected].forEach(id => { if (!visibleSelectable.some(t => t.id === id)) selected.delete(id); });
    const kgInStock = rows.filter(x => x.status === 'in_stock').reduce((a, b) => a + (b.avgWeightKg || 0), 0);
    countEl.textContent = rows.length + ' totes shown · ' + fmt(kgInStock, 0) + ' kg in stock';
    host.innerHTML = '';

    const allCb = el('input', { type: 'checkbox', title: 'Select all shown', onchange: () => {
      visibleSelectable.forEach(t => allCb.checked ? selected.add(t.id) : selected.delete(t.id));
      drawStab();
    } });
    allCb.checked = visibleSelectable.length > 0 && visibleSelectable.every(t => selected.has(t.id));

    const headRow = el('tr', {}, el('th', { class: 'checkcol' }, allCb),
      ...cols().map(c => {
        const arrow = sortKey === c.key ? (sortDir === 1 ? ' ▲' : ' ▼') : '';
        return el('th', {
          class: (c.center ? 'ctr ' : (c.numeric ? 'num ' : '')) + (c.wrapHead ? 'wraphead ' : '') + 'sortable', title: 'Click to sort',
          onclick: () => { sortKey === c.key ? (sortDir = -sortDir) : (sortKey = c.key, sortDir = 1); drawStab(); }
        }, c.label + arrow);
      }), el('th', { class: 'actions-head' }, 'Actions'));

    const filterRow = el('tr', { class: 'filter-row' }, el('th', {}, ''),
      ...cols().map(c => {
        const cell = el('th', {});
        if (c.options) {
          const chosen = filters[c.key] || [];
          cell.append(el('button', { type: 'button', class: 'ms-filter' + (chosen.length ? ' active' : ''), 'data-key': c.key,
            title: chosen.length ? chosen.join(', ') : 'Filter (pick one or more)',
            onclick: e => { e.stopPropagation(); openMultiFilter(c); } },
            el('span', {}, (() => {
              if (!chosen.length) return 'All';
              if (chosen.length <= 2) return chosen.join(', ');
              const left = c.options().filter(o => !chosen.includes(o));
              return left.length && left.length <= 2 ? 'All except ' + left.join(', ') : chosen.length + ' selected';
            })()), el('span', { class: 'ms-caret' }, '▾')));
        } else {
          const inp = el('input', { placeholder: 'Filter…', value: filters[c.key] || '' });
          inp.addEventListener('input', () => { filters[c.key] = inp.value; drawStab(); });
          cell.append(inp);
        }
        return cell;
      }), el('th', {}));

    const tbody = el('tbody', {});
    if (!rows.length) tbody.append(el('tr', {}, el('td', { colspan: cols().length + 2, class: 'empty' }, 'No totes match.')));
    rows.forEach(t => {
      const movable = t.status === 'in_stock' || t.status === 'hold';
      tbody.append(el('tr', {
        class: 'clickable',
        title: 'Click for the full Feedstock Stability log',
        onclick: e => { if (!e.target.closest('.checkcol, .row-actions')) showHistory(t); }
      },
        el('td', { class: 'checkcol' }, rowCheck(t, selected, updateBulk)),
        ...cols().map(c => el('td', { class: c.key === 'lot' ? 'mono' : (c.center ? 'ctr' : (c.numeric ? 'num' : '')) }, c.cell(t))),
        el('td', {}, rowActions([
          movable ? ['Move', () => moveTote(t)] : null,
          movable ? ['Update', () => updateCondition(t)] : null,
          ['Label', () => printLabels([toteLabel(t)])],
          (t.grind === 'Fine' || (t.status === 'consumed' && !t.runId)) ? ['Trace', () => showPreprocTrace(t)] : null,
          movable ? ['Delete', () => delTote(t), 'danger'] : null
        ]))));
    });

    const wrapEl = el('div', { class: 'tablewrap sticky-actions' },
      el('table', {}, el('thead', {}, headRow, filterRow), tbody));
    host.append(wrapEl);
    // the filter row freezes directly under the column headings: offset it by the heading row's height
    const pinFilters = () => wrapEl.style.setProperty('--stab-head-h', headRow.getBoundingClientRect().height + 'px');
    pinFilters();
    if (window.ResizeObserver) new ResizeObserver(pinFilters).observe(headRow);
    updateBulk();
    placeMultiFilter();
  }
  drawStab();
}
function rowCheck(item, selected, onChange) {
  if (item.status === 'consumed' || item.status === 'sold' || item.status === 'disposed') return '';
  const cb = el('input', {
    type: 'checkbox', class: 'rowcheck',
    // Stops a checkbox click from also firing the row's own onclick (e.g. a
    // table's rowClick, used elsewhere to open a row's history) when both
    // are present on the same row -- same reasoning as rowActions below.
    onclick: e => e.stopPropagation(),
    onchange: () => { cb.checked ? selected.add(item.id) : selected.delete(item.id); onChange(); }
  });
  cb.checked = selected.has(item.id);
  return cb;
}
function disposeSimple(type, ids, noun) {
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Writing off', ids.length + ' ' + noun + (ids.length === 1 ? '' : 's'))),
    field('Reason / description (required)', el('textarea', { id: 'dz_reason', rows: '2', placeholder: 'e.g. spoilage, failed QA, contamination, expired' })),
    field('Date', el('input', { type: 'date', id: 'dz_date', value: todayStr() })),
    el('div', { class: 'help' }, 'Permanently writes the selected ' + noun + (ids.length === 1 ? '' : 's') + ' off inventory. The reason is logged with your name.'));
  modal('Dispose / write off ' + ids.length + ' ' + noun + (ids.length === 1 ? '' : 's'), body, async () => {
    const reason = body.querySelector('#dz_reason').value.trim();
    if (!reason) throw new Error('A reason / description is required.');
    const r = await api('POST', '/dispose', { type, itemIds: ids, reason, date: body.querySelector('#dz_date').value });
    State.ref = await api('GET', '/refdata');
    toast('Wrote off ' + r.disposed + ' ' + noun + (r.disposed === 1 ? '' : 's')); render();
  }, 'Dispose');
}
function disposeConsumables(items) {
  const qty = {};
  const grid = el('div', {}, ...items.map(c => {
    const inp = el('input', { type: 'number', min: '0', max: c.onHand, step: '0.1', value: '0', style: 'width:90px' });
    qty[c.id] = inp;
    return el('div', { class: 'form-row', style: 'align-items:center' },
      field('', el('span', {}, el('b', {}, c.name), ' ', el('span', { class: 'muted' }, '(' + fmt(c.onHand, 1) + ' ' + c.unit + ' on hand)'))),
      field('Qty to write off', inp));
  }));
  const body = el('div', {},
    field('Reason / description (required)', el('textarea', { id: 'dz_reason', rows: '2', placeholder: 'e.g. expired, spilled, contaminated' })),
    field('Date', el('input', { type: 'date', id: 'dz_date', value: todayStr() })),
    el('h3', { style: 'margin:14px 0 6px;font-size:14px' }, 'Quantities to write off'), grid);
  modal('Dispose / write off items', body, async () => {
    const reason = body.querySelector('#dz_reason').value.trim();
    if (!reason) throw new Error('A reason / description is required.');
    const lines = items.map(c => ({ id: c.id, qty: +qty[c.id].value || 0 })).filter(l => l.qty > 0);
    if (!lines.length) throw new Error('Enter a quantity for at least one item.');
    const r = await api('POST', '/dispose', { type: 'consumable', items: lines, reason, date: body.querySelector('#dz_date').value });
    toast('Wrote off ' + r.disposed + ' item' + (r.disposed === 1 ? '' : 's')); render();
  }, 'Dispose');
}
function bulkMoveTotes(ids) {
  const locs = State.ref.locations.map(l => [l, l]);
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Moving', ids.length + ' tote' + (ids.length === 1 ? '' : 's'))),
    el('div', { class: 'form-row' },
      field('Move to', editableSelect(locs, 'mb_loc')),
      field('Date', el('input', { type: 'date', id: 'mb_date', value: todayStr() }))),
    field('Note (optional)', el('input', { id: 'mb_note', placeholder: 'reason / carrier' })));
  modal('Move ' + ids.length + ' totes', body, async () => {
    const to = body.querySelector('#mb_loc').value.trim();
    if (!to) throw new Error('Choose a destination location.');
    const r = await api('POST', '/totes/move-bulk', { ids, toLocation: to, date: body.querySelector('#mb_date').value, note: body.querySelector('#mb_note').value || null });
    State.ref = await api('GET', '/refdata');
    toast('Moved ' + r.moved + ' tote' + (r.moved === 1 ? '' : 's') + ' to ' + to); render();
  }, 'Move');
}
async function delTote(t) {
  if (!confirm('Delete tote ' + t.lot + '?')) return;
  try { await api('DELETE', '/totes/' + t.id); toast('Tote deleted'); render(); }
  catch (e) { toast(e.message, true); }
}
function phCell(t) {
  if (t.ph == null) return el('span', { class: 'muted' }, '—');
  return el('span', {}, String(t.ph));
}
function orpCell(t) {
  if (t.orp == null) return el('span', { class: 'muted' }, '—');
  return el('span', {}, String(t.orp));
}
function stabilityLogTable(log, toteId) {
  if (!log.length) return el('div', { class: 'help' }, 'No changes logged yet.');
  // Every change is kept, but the table itself only shows ~10 rows before
  // scrolling (sticky header stays visible), same pattern as the QC results
  // table -- a tote's log can grow long over its lifetime.
  return el('div', { class: 'tablewrap stability-log-scroll', style: 'margin-top:6px' },
    el('table', {},
      el('thead', {}, el('tr', {}, el('th', {}, 'When'), el('th', {}, 'Field'), el('th', {}, 'From'), el('th', {}, 'To'), el('th', {}, 'Note'), el('th', {}, 'By'))),
      el('tbody', {}, ...log.map(r => {
        // A row logged with a photo attachment -- from a tote rejected during
        // a production run (runId set) or from Feedstock Inventory's own
        // Detail card (no run, just this tote's own attachments) -- links
        // straight to the image instead of showing an inert filename.
        let toCell;
        if (r.attachmentId && r.runId) toCell = el('a', { href: attDownloadUrl(r.runId, r.attachmentId, false), target: '_blank', rel: 'noopener' }, r.newValue || 'View photo');
        else if (r.attachmentId && toteId) toCell = el('a', { href: toteAttDownloadUrl(toteId, r.attachmentId, false), target: '_blank', rel: 'noopener' }, r.newValue || 'View photo');
        else toCell = el('b', {}, r.newValue ?? '—');
        return el('tr', {},
          el('td', { class: 'muted' }, fmtWhen(r.at)), el('td', {}, r.field),
          el('td', { class: 'muted' }, r.oldValue ?? '—'), el('td', {}, toCell),
          el('td', { class: 'muted' }, r.note || '—'), el('td', {}, r.by || '—'));
      }))));
}
async function showHistory(t) {
  const data = await api('GET', '/totes/' + t.id + '/ph');
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Tote', t.lot)),
    stabilityLogTable(data.stabilityLog, t.id));
  modal('Feedstock Stability log — ' + t.lot, body, async () => {}, 'Close', { noCancel: true });
}
async function updateCondition(t) {
  const data = await api('GET', '/totes/' + t.id + '/ph');
  function summaryLines(d) {
    return [sl('Tote', t.lot),
      sl('Current pH', d.ph == null ? '—' : d.ph),
      sl('Current ORP', d.orp == null ? '—' : d.orp + ' mV'),
      sl('ORP meter range', d.orp == null ? '—' : (classifyOrp(d.orp) || '—')),
      sl('Last updated', d.lastUpdated ? fmtWhen(d.lastUpdated) : 'never')];
  }
  const summary = el('div', { class: 'summary-line' }, ...summaryLines(data));
  const history = el('div', {}, stabilityLogTable(data.stabilityLog, t.id));
  // The Details card has no Save button of its own here -- everything in
  // this window (the Details fields below plus the New pH/ORP reading
  // fields above) is saved together by the modal's own "Save & close",
  // using whatever the card's fields currently hold (tracked via onChange,
  // seeded with the prefilled values immediately so it's never empty).
  let detailVals = null;
  const detailCard = buildFeedstockCard({
    label: 'Details',
    // pH/ORP live in the summary + quick-update fields above instead of
    // here, so the same reading isn't captured twice in this modal.
    omit: ['ph', 'orp', 'orpRange'],
    // Pre-fills from whatever was captured most recently (this tote's own
    // reading for pH/ORP, its latest Feedstock Stability log entry for
    // everything else) rather than starting blank every time.
    initial: data.latestCharacterization,
    mode: 'draft',
    onChange: vals => { detailVals = vals; },
    uploadPhoto: async (slot, file, b64) => {
      const r = await api('POST', '/totes/' + t.id + '/photo',
        { slot, filename: file.name, contentType: file.type || 'image/jpeg', dataB64: b64 });
      return r.attachmentId;
    },
    photoUrl: attId => toteAttDownloadUrl(t.id, attId, false)
  });
  const body = el('div', {},
    summary,
    el('div', { class: 'form-row' },
      field('New pH reading', el('input', { type: 'number', step: '0.1', id: 'p_ph', placeholder: 'e.g. 3.7' })),
      field('New ORP reading', el('input', { type: 'number', step: '1', id: 'p_orp', placeholder: 'e.g. -150' }))),
    field('Reading date', el('input', { type: 'date', id: 'p_date', value: new Date().toISOString().slice(0, 10) })),
    field('Note (optional)', el('input', { id: 'p_note', placeholder: 'who / instrument / observation' })),
    detailCard,
    el('label', {}, 'Feedstock Stability log'), history);
  modal('Update feedstock conditions — ' + t.lot, body, async () => {
    const ph = body.querySelector('#p_ph').value;
    const orp = body.querySelector('#p_orp').value;
    let rejected = false;
    if (detailVals) {
      const r = await api('POST', '/totes/' + t.id + '/characterize', detailVals);
      rejected = r.rejected;
    }
    if (ph !== '' || orp !== '') {
      await api('POST', '/totes/' + t.id + '/ph', {
        ph: ph === '' ? null : +ph, orp: orp === '' ? null : +orp,
        date: body.querySelector('#p_date').value, note: body.querySelector('#p_note').value || null
      });
    }
    toast(rejected ? t.lot + ' rejected — moved to QAQC Hold.' : 'Feedstock conditions saved for ' + t.lot);
    render();
  }, 'Save & close', { wide: true });
}
const todayStr = () => new Date().toISOString().slice(0, 10);
function drawMoveHistory(host, log, showQty) {
  host.innerHTML = '';
  if (!log.length) { host.append(el('div', { class: 'help' }, 'No moves recorded yet.')); return; }
  const headers = showQty ? ['Date', 'From', 'To', 'Units', 'Note'] : ['Date', 'From', 'To', 'Note'];
  host.append(el('div', { class: 'tablewrap', style: 'margin-top:6px' }, el('table', {},
    el('thead', {}, el('tr', {}, ...headers.map(h => el('th', { class: h === 'Units' ? 'num' : '' }, h)))),
    el('tbody', {}, ...log.map(r => el('tr', {},
      el('td', {}, r.date), el('td', {}, r.from || '—'), el('td', {}, r.to || '—'),
      ...(showQty ? [el('td', { class: 'num' }, fmt(r.qty))] : []),
      el('td', { class: 'muted' }, r.note || '—')))))));
}
async function moveTote(t) {
  const data = await api('GET', '/totes/' + t.id + '/move');
  const locs = State.ref.locations.map(l => [l, l]);
  const history = el('div', {}); drawMoveHistory(history, data.moveLog, false);
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Tote', t.lot), sl('Current location', data.location || '—')),
    el('div', { class: 'form-row' },
      field('Move to', editableSelect(locs, 'm_loc')),
      field('Date', el('input', { type: 'date', id: 'm_date', value: todayStr() }))),
    field('Note (optional)', el('input', { id: 'm_note', placeholder: 'reason / carrier' })),
    el('label', {}, 'Move history'), history);
  modal('Move tote — ' + t.lot, body, async () => {
    const to = body.querySelector('#m_loc').value.trim();
    if (!to) throw new Error('Choose a destination location.');
    const r = await api('POST', '/totes/' + t.id + '/move', { toLocation: to, date: body.querySelector('#m_date').value, note: body.querySelector('#m_note').value || null });
    State.ref = await api('GET', '/refdata');
    toast('Moved to ' + r.tote.location); render();
  }, 'Move');
}
async function moveFG(f) {
  const data = await api('GET', '/fg/' + f.id + '/move');
  const locs = State.ref.locations.map(l => [l, l]);
  const history = el('div', {}); drawMoveHistory(history, data.moveLog, true);
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('FG lot', f.lot), sl('Pack', f.packageSize),
      sl('On hand', fmt(f.qty) + ' units'), sl('Current location', data.location || '—')),
    el('div', { class: 'form-row' },
      field('Units to move', el('input', { type: 'number', id: 'm_qty', min: '0', max: f.qty, value: f.qty })),
      field('Move to', editableSelect(locs, 'm_loc'))),
    el('div', { class: 'form-row' },
      field('Date', el('input', { type: 'date', id: 'm_date', value: todayStr() })),
      field('Note (optional)', el('input', { id: 'm_note' }))),
    el('div', { class: 'help' }, 'Moving fewer than ' + fmt(f.qty) + ' units splits the lot; the remainder stays put.'),
    el('label', {}, 'Move history'), history);
  modal('Move finished goods — ' + f.lot, body, async () => {
    const to = body.querySelector('#m_loc').value.trim();
    if (!to) throw new Error('Choose a destination location.');
    const qty = +body.querySelector('#m_qty').value;
    if (!(qty > 0)) throw new Error('Enter a quantity to move.');
    await api('POST', '/fg/' + f.id + '/move', { toLocation: to, qty, date: body.querySelector('#m_date').value, note: body.querySelector('#m_note').value || null });
    State.ref = await api('GET', '/refdata');
    toast('Moved ' + fmt(qty) + ' × ' + f.packageSize + ' to ' + to); render();
  }, 'Move');
}
async function openHarvest() {
  const sites = State.ref.sites.filter(s => s.code !== 'MIX').map(s => [s.code, s.code + ' — ' + s.name]);
  const species = State.ref.species.filter(s => s.code !== 'MIX').map(s => [s.code, s.common || s.name]);
  const locs = State.ref.locations.map(l => [l, l]);
  // Stabilization method drives a sensible default for the storage unit:
  // Citric acid → Tote; Fresh → Bag (fresh kelp is commonly bagged).
  const onStabChange = () => {
    body.querySelector('#h_unit').value = body.querySelector('#h_stab').value === 'Fresh' ? 'Bag' : 'Tote';
  };
  const body = el('div', {},
    el('div', { class: 'form-row' },
      field('Stabilization method', selectFrom('', [['Citric acid', 'Citric acid'], ['Fresh', 'Fresh']], () => onStabChange(), 'h_stab')),
      field('Farm site', selectFrom('', sites, null, 'h_site'))),
    el('div', { class: 'form-row-3' },
      field('Species', selectFrom('', species, null, 'h_species')),
      field('Harvest date', el('input', { type: 'date', id: 'h_date', value: new Date().toISOString().slice(0, 10) })),
      field('Received date', el('input', { type: 'date', id: 'h_received', value: new Date().toISOString().slice(0, 10) }))),
    el('div', { class: 'form-row' },
      field('Storage location', editableSelect(locs, 'h_loc')),
      field('Number of storage units', el('input', { type: 'number', id: 'h_count', min: '1', value: '1' }))),
    el('div', { class: 'form-row' },
      field('Total harvest (kg)', el('input', { type: 'number', id: 'h_kg', min: '0', step: '0.01', placeholder: 'averaged across storage units' })),
      field('Storage unit', selectFrom('', [['Tote', 'Tote'], ['Bag', 'Bag']], null, 'h_unit'))),
    el('div', { class: 'form-row' },
      field('Grind', selectFrom('', [['Coarse', 'Coarse'], ['Fine', 'Fine']], null, 'h_grind')),
      field('pH', el('input', { type: 'number', id: 'h_ph', step: '0.1', placeholder: 'e.g. 3.7' }))),
    field('ORP (mV) — optional', el('input', { type: 'number', id: 'h_orp', step: '1', placeholder: 'e.g. -150' })),
    field('Notes', el('textarea', { id: 'h_notes', rows: '2', placeholder: 'Optional' })),
    el('div', { class: 'help', id: 'h_preview' }));
  const c_count = body.querySelector('#h_count'), c_kg = body.querySelector('#h_kg');
  const upd = () => {
    const n = +c_count.value || 0, kg = +c_kg.value || 0;
    body.querySelector('#h_preview').textContent = n > 0 ? `Creates ${n} storage unit(s); average weight ${n ? (kg / n).toFixed(2) : 0} kg each.` : '';
  };
  c_count.addEventListener('input', upd); c_kg.addEventListener('input', upd); upd();
  onStabChange();
  modal('Check in a harvest batch', body, async () => {
    const payload = {
      site: body.querySelector('#h_site').value, species: body.querySelector('#h_species').value,
      harvestDate: body.querySelector('#h_date').value, receivedDate: body.querySelector('#h_received').value,
      location: body.querySelector('#h_loc').value,
      toteCount: +body.querySelector('#h_count').value, totalKg: +body.querySelector('#h_kg').value,
      stabilizationMethod: body.querySelector('#h_stab').value, storageUnit: body.querySelector('#h_unit').value,
      grind: body.querySelector('#h_grind').value,
      ph: body.querySelector('#h_ph').value || null, orp: body.querySelector('#h_orp').value || null,
      notes: body.querySelector('#h_notes').value || null
    };
    const r = await api('POST', '/harvest', payload);
    State.ref = await api('GET', '/refdata');
    toast(`Created ${r.count} storage unit(s) · ${r.avgWeightKg} kg each`);
    render();
  }, 'Check in');
}
// Bulk CSV import: one row per tote (unlike the single-batch Check in
// harvest form above, which shares one average weight across a count of
// totes) -- lets many already-known totes be added in one file instead of
// one form submission each. Parsing/validation/lot-numbering all happen
// server-side (POST /api/harvest/bulk); this just reads the file as text
// and shows the row-level error the backend reports if any row is bad.
async function openFeedstockImport() {
  let csvText = '';
  const fileInput = el('input', { type: 'file', accept: '.csv,text/csv' });
  const status = el('div', { class: 'help' });
  fileInput.addEventListener('change', () => {
    csvText = ''; status.textContent = '';
    const f = fileInput.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => {
      csvText = String(reader.result || '');
      const rows = csvText.split(/\r\n|\r|\n/).filter(l => l.trim() !== '').length - 1;
      status.textContent = f.name + ' — ' + Math.max(rows, 0) + ' row(s) ready to import.';
    };
    reader.onerror = () => { status.textContent = 'Could not read ' + f.name; };
    reader.readAsText(f);
  });
  const body = el('div', {},
    el('div', { class: 'help' },
      'Add many feedstock totes at once — one row per tote. Download the template, fill it in, then upload it here.'),
    el('div', { style: 'margin:10px 0' },
      el('a', { href: 'templates/feedstock_bulk_import_template.csv', download: 'feedstock_bulk_import_template.csv' },
        '⬇ Download CSV template')),
    field('CSV file', fileInput),
    status,
    el('div', { class: 'help' },
      'Required columns: site, species, harvestDate (YYYY-MM-DD), avgWeightKg. ' +
      'Optional: receivedDate, stabilizationMethod, storageUnit, grind (Coarse or Fine, default Coarse), location, ph, orp, storageSource, notes. ' +
      'Rows for the same site/species/harvest date are numbered in the order they appear in the file.'));
  modal('Bulk import feedstock (CSV)', body, async () => {
    if (!csvText.trim()) throw new Error('Choose a CSV file to import.');
    const r = await api('POST', '/harvest/bulk', { csvText });
    State.ref = await api('GET', '/refdata');
    toast('Imported ' + r.count + ' tote(s).');
    render();
  }, 'Import');
}

/* ---------------- Production ---------------- */
async function pageProduction(v) {
  v.append(el('div', { class: 'page-head' },
    el('h2', {}, 'Production Runs'),
    el('div', { class: 'actions' }, el('button', { onclick: () => openNewRun() }, '+ New production run'))));
  const [r, dr] = await Promise.all([api('GET', '/production'), api('GET', '/production/drafts')]);
  if (dr.drafts.length) {
    v.append(el('h3', { style: 'margin:0 0 8px' }, 'In progress'));
    for (const d of dr.drafts) v.append(draftCard(d));
  }
  if (!r.runs.length && !dr.drafts.length) { v.append(el('div', { class: 'empty card' }, 'No production runs yet. Click “New production run” to process stabilized totes into finished goods.')); return; }
  if (!r.runs.length) return;
  for (const run of r.runs) {
    const card = el('div', { class: 'card' },
      el('div', { class: 'page-head', style: 'margin:0 0 8px' },
        el('h3', { style: 'margin:0' }, mono(run.processingLot),
          run.excludeFromStats ? el('span', { class: 'pill', style: 'margin-left:6px', title: run.excludeReason || '' }, 'Excluded from analysis') : null,
          run.release && run.release.state && run.release.state !== 'legacy' ? [' ', releaseBadge(run.release.state)] : null,
          ' ', analysisInfoButton(run)),
        el('div', { class: 'actions' },
          canAmendLog() ? (run.amendment
            ? el('button', { onclick: () => openRunLog(run.id) }, '✏️ Continue amendment')
            : el('button', { onclick: () => openAmendDialog(run) }, '✏️ Amend run')) : null,
          el('button', { class: 'secondary', onclick: () => openProcessLog(run) }, '📋 Process log'),
          el('button', { class: 'secondary', onclick: () => openQcForRun(run) },
            '🧪 QC'),
          el('button', { class: 'secondary', onclick: () => openAttachments(run) },
            '📎 Documents' + (run.attachments && run.attachments.length ? ' (' + run.attachments.length + ')' : '')),
          el('button', { class: 'secondary', onclick: () => openLabResults(run) }, '🧫 Lab results'),
          el('button', { class: 'secondary', onclick: () => printLabels(run.fgLots.map(f => fgLabel(f, run))) }, 'Print FG labels'),
          el('button', { class: 'secondary', onclick: () => openSampleLabels(run) }, 'Print sample labels'))),
      run.amendment ? amendBanner(run) : null,
      runSummaryGrid(run),
      stageProgress(run, { onSelect: key => openProcessLog(run, key) }),
      revisionTracker(run));
    v.append(card);
  }
}
// The key production figures shown on a run's summary card, all derived from the run's
// own log: feedstock farms, IBCs (totes) consumed, weights, volume out, final QC, extraction
// efficiency and the two conversion rates (process = measured weights, harvest = stored
// batch-average weight -- the same definitions as the Yield & Usage report).
function runSummaryStats(run) {
  const inputs = (run.inputs || []).filter(i => i.decision !== 'rejected');
  const siteCodes = inputs.length ? inputs.map(i => i.site) : (run.inputTotes || []).map(l => String(l).split('-')[0]);
  const farms = [...new Set(siteCodes.filter(Boolean).map(siteName))];
  const toteCount = inputs.length || (run.inputTotes || []).length;
  const measuredKg = inputs.length && inputs.every(i => i.weightKg != null) ? inputs.reduce((a, i) => a + i.weightKg, 0) : null;
  const out = run.outputLitres || 0;
  const st = run.stages || {};
  const tdsBefore = st.homogenization && st.homogenization.tdsPct, tdsAfter = st.extraction && st.extraction.tdsPct;
  return {
    farms, toteCount, measuredKg, out,
    finalPh: st.packaging ? st.packaging.qcPh : null, finalTds: st.packaging ? st.packaging.tdsPct : null,
    extractionEff: (tdsBefore && tdsAfter != null) ? (tdsAfter - tdsBefore) / tdsBefore * 100 : null,
    processRate: measuredKg && out > 0 ? out / measuredKg : null,
    harvestRate: run.inputKg > 0 && out > 0 ? out / run.inputKg : null,
  };
}
function runSummaryGrid(run) {
  const x = runSummaryStats(run);
  const fgList = (run.fgLots || []).map(f => fmt(f.qty) + ' × ' + f.packageSize).join(', ') || '—';
  const cell = (k, v, cls) => el('div', { class: 'rs' + (cls ? ' ' + cls : '') }, el('span', { class: 'rs-k' }, k), el('span', { class: 'rs-v' }, v));
  const dash = '—';
  return el('div', { class: 'run-stats' },
    cell('Run date', run.runDate || dash),
    cell('Operators', run.operators || dash),
    cell('Product', skuName(run.sku)),
    cell('Feedstock farms', x.farms.join(', ') || dash, 'rs-wide'),
    cell('IBCs consumed', x.toteCount ? fmt(x.toteCount) : dash),
    cell('Total feedstock weight', x.measuredKg != null ? [fmt(x.measuredKg, 1) + ' kg', el('small', {}, 'measured')]
      : (run.inputKg ? [fmt(run.inputKg, 1) + ' kg', el('small', {}, 'batch-average')] : dash)),
    cell('Packaged', fgList, 'rs-wide'),
    cell('Product volume out', x.out ? fmt(x.out, 0) + ' L' : dash),
    cell('Final pH', x.finalPh != null ? fmt(x.finalPh, 2) : dash),
    cell('Final TDS', x.finalTds != null ? fmt(x.finalTds, 2) + ' %' : dash),
    cell('Extraction efficiency', x.extractionEff != null ? fmt(x.extractionEff, 1) + ' %' : dash),
    cell('Conversion rate — process', x.processRate != null ? [fmt(x.processRate, 3), el('small', {}, 'L/kg · measured weight')] : dash),
    cell('Conversion rate — harvest', x.harvestRate != null ? [fmt(x.harvestRate, 3), el('small', {}, 'L/kg · batch-average weight')] : dash));
}
// Required-field marking. The server owns the list of required fields
// (State.ref.requiredFields, from REQUIRED_FIELDS/PROGRESS_SECTIONS in
// kelp_erp_server.py) so the asterisks, the progress chips and the finalize
// check can never disagree. A red * follows the label of every required field.
function isReq(stage, key) {
  const r = State.ref && State.ref.requiredFields;
  return !!(r && Array.isArray(r[stage]) && r[stage].includes(key));
}
function reqLabel(label) { return el('span', { class: 'req-label' }, label); }
function rfield(stage, key, label, control) { return field(isReq(stage, key) ? reqLabel(label) : label, control); }
function reqLegend() {
  return el('div', { class: 'help req-legend' }, el('span', { class: 'req-star' }, '*'),
    ' Required to finalize the run. Notes and the Homogenization, Separation and Pasteurization sample points are optional.');
}
// At-a-glance progress for the production log's 7 sections: one labelled chip each --
// green with a tick only once every required field in that section has a value,
// amber with "filled/total" while partly done, grey when not started. Hover a chip
// to see exactly which required fields are still missing.
function stageProgress(run, opts) {
  opts = opts || {};
  const prog = run.progress;
  if (!prog || !(prog.sections || []).length) return null;
  const pct = prog.requiredTotal ? Math.round(prog.requiredFilled / prog.requiredTotal * 100) : 0;
  return el('div', { class: 'stage-progress' },
    el('div', { class: 'stage-steps' }, ...prog.sections.map(sec => {
      const state = sec.done ? 'done' : (sec.started ? 'partial' : 'empty');
      const kind = sec.optional ? 'optional' : 'required';
      const tip = sec.done ? sec.label + ' — all ' + sec.total + ' ' + kind + ' fields complete'
        : sec.label + ' — ' + (sec.total - sec.filled) + ' of ' + sec.total + ' ' + kind + ' field(s) missing:\n• ' + sec.missing.join('\n• ');
      // With opts.onSelect each chip is a button that opens/jumps to its section.
      return el(opts.onSelect ? 'button' : 'span', Object.assign({ class: 'stage-step ' + state, title: tip + (opts.onSelect ? '\n(click to open this section)' : '') },
        opts.onSelect ? { type: 'button', onclick: () => opts.onSelect(sec.key) } : {}),
        el('span', { class: 'stage-dot' }, sec.done ? '✓' : (sec.started ? sec.filled + '/' + sec.total : '')),
        el('span', { class: 'stage-name' }, sec.label));
    })),
    el('div', { class: 'stage-meter' + (prog.complete ? ' complete' : '') },
      el('span', { class: 'stage-meter-track' }, el('span', { class: 'stage-meter-bar', style: 'width:' + pct + '%' })),
      el('span', {}, prog.complete ? (opts.readyText || 'All required fields complete — ready to finalize')
        : prog.requiredFilled + ' of ' + prog.requiredTotal + ' required fields complete')));
}
// ---- Amend run: a finalized run's production log is locked; changing it needs an
// amendment (reason + category). Documents and label printing never do. ----
async function openRunLog(id, section) {
  const run = (await api('GET', '/production')).runs.find(x => x.id === id);
  if (run) openProcessLog(run, section);
}
// Presses every section's own Save button inside `root`, one at a time, so anything typed but not yet saved is saved. Returns the messages of
// the sections that could not be saved (empty = all saved). Used by Save & close and Finalize so no unsaved entry is silently dropped.
async function pressSectionSaves(root) {
  const failed = [];
  for (const btn of root.querySelectorAll('button.section-save')) {
    if (btn.disabled) continue;
    btn.click();
    for (let i = 0; i < 400 && btn.disabled; i++) await new Promise(r => setTimeout(r, 50));
    const msg = btn.nextElementSibling ? btn.nextElementSibling.textContent.trim() : '';
    if (msg && msg !== 'Saved.') failed.push(msg);
  }
  return failed;
}
function closeAllModals() { document.querySelectorAll('#modalRoot .modal-bg').forEach(m => m.remove()); }
// Greys out every control in a production-log view. Progress chips, the amend
// banner and per-sample label printing stay usable; controls added later (tables
// redrawn) are locked too.
function lockLogBody(root) {
  const apply = () => root.querySelectorAll('input, select, textarea, button').forEach(c => {
    if (!c.closest('.allow-locked') && !c.classList.contains('stage-step')) c.disabled = true;
  });
  apply();
  new MutationObserver(apply).observe(root, { childList: true, subtree: true });
}
function logLockBanner(run, extra) {
  if (run.amendment && !canAmendLog()) {
    return el('div', { class: 'lock-banner allow-locked' }, el('span', {},
      '🔒 Under amendment by ' + (run.amendment.openedBy || '—') + ' — read only. Only users with the Production Log Amender permission can edit it.'));
  }
  if (run.amendment) {
    return el('div', { class: 'amend-banner allow-locked' },
      el('b', {}, '✏️ Amendment open'), ' — ' + (run.amendment.categoryLabel || '') + ': ' + run.amendment.reason
      + ' (opened by ' + (run.amendment.openedBy || '—') + '). Edit below, then submit the amendment. Product stays on hold until it is re-reviewed.',
      el('div', { class: 'actions' }, el('button', { type: 'button', onclick: () => openSubmitAmendment(run) }, 'Submit amendment…')));
  }
  return el('div', { class: 'lock-banner allow-locked' },
    el('span', {}, '🔒 Finalized — read only. ' + (extra || 'To change any production-log entry, the run must be amended (a reason is required and the change is recorded as a revision). Documents and printing labels are not affected.')
      + (canAmendLog() ? '' : ' You don’t have the Production Log Amender permission — ask an administrator.')),
    canAmendLog() ? el('button', { type: 'button', onclick: () => openAmendDialog(run) }, '✏️ Amend run…') : null);
}
function amendBanner(run) {
  const a = run.amendment;
  return el('div', { class: 'amend-banner' },
    el('b', {}, 'Under amendment'), ' — ' + (a.categoryLabel || '') + ': ' + a.reason + '  (opened ' + fmtWhen(a.openedAt) + ' by ' + (a.openedBy || '—') + ')',
    el('div', { class: 'help', style: 'color:inherit' }, 'The log is unlocked for editing; unsold finished goods are held (Pending Release) until the amendment is submitted and re-reviewed.'),
    canAmendLog() ? el('div', { class: 'actions' },
      el('button', { onclick: () => openRunLog(run.id) }, 'Continue editing'),
      el('button', { class: 'secondary', onclick: () => openSubmitAmendment(run) }, 'Submit amendment…'),
      el('button', { class: 'secondary', onclick: async () => {
        if (!confirm('Cancel this amendment? Only possible if nothing has been changed; the run returns to its previous status.')) return;
        try { await api('POST', '/production/' + run.id + '/amendments/' + a.id + '/cancel', {}); toast('Amendment cancelled'); render(); }
        catch (e) { toast(e.message, true); }
      } }, 'Cancel amendment')) : null);
}
async function openAmendDialog(run) {
  const info = await api('GET', '/production/' + run.id + '/amendments');
  const imp = info.impact;
  const me = State.user || {};
  const catSel = selectFrom('', [['', 'Select a category…'], ...Object.entries(info.categories)]);
  const reason = el('textarea', { rows: '3', placeholder: 'What is being changed, and why?' });
  const pw = el('input', { type: 'password', autocomplete: 'off', placeholder: 'Your password (signature)' });
  const heldUnits = imp.lots.filter(l => l.status === 'on_hand').reduce((a, l) => a + (l.qty || 0), 0);
  const effects = [
    'The production log is unlocked for editing until the amendment is submitted.',
    'Changes are recorded as ONE revision with this reason and a field-by-field before/after.',
    ['pending_release', 'released', 'legacy', 'rejected'].includes(imp.state) ? 'The current review / release sign-off no longer stands; the run is re-reviewed after you submit.' : 'The run goes back to production-log review after you submit.',
    heldUnits > 0 ? fmt(heldUnits) + ' unsold unit(s) will be held (Pending Release) until re-released.' : null,
  ].filter(Boolean);
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Run', run.processingLot), el('span', {}, 'Status: ', releaseBadge(imp.state))),
    el('ul', { style: 'margin:6px 0 10px 18px;font-size:13px' }, ...effects.map(t => el('li', {}, t))),
    imp.shipped.length ? el('div', { class: 'amend-banner' }, '⚠ ' + imp.shipped.reduce((a, x) => a + x.qty, 0) + ' unit(s) from this run have already shipped ('
      + imp.shipped.map(x => x.qty + ' × ' + x.lot + ' on ' + x.shipment).join('; ') + '). The app cannot recall them — Quality should decide whether a deviation notice is needed.') : null,
    field(reqLabel('Category'), catSel), field(reqLabel('Reason'), reason),
    imp.needsSignature ? el('div', {},
      el('div', { class: 'help' }, 'This run has been reviewed/released, so amending it is a signed act: re-enter your password. It is re-reviewed before product can be sold again.'),
      field(reqLabel('Password'), pw)) : null);
  modal('Amend run — ' + run.processingLot, body, async () => {
    if (!catSel.value) throw new Error('Choose a category.');
    if (reason.value.trim().length < 5) throw new Error('Enter the reason for the amendment.');
    await api('POST', '/production/' + run.id + '/amendments', { category: catSel.value, reason: reason.value.trim(), password: pw.value || undefined });
    toast('Amendment opened — the production log is now editable.');
    closeAllModals(); render(); openRunLog(run.id);
  }, 'Open amendment');
}
async function openSubmitAmendment(run) {
  const a = run.amendment;
  const pv = await api('GET', '/production/' + run.id + '/amendments/' + a.id + '/preview');
  const comment = el('textarea', { rows: '2', placeholder: 'Optional note for the reviewer' });
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Run', run.processingLot), sl('Category', a.categoryLabel), sl('Reason', a.reason)),
    el('h3', { style: 'margin:10px 0 4px;font-size:14px' }, 'Changes in this amendment (' + pv.changes.length + ')'),
    pv.changes.length ? el('div', { class: 'tablewrap' }, el('table', {},
      el('thead', {}, el('tr', {}, el('th', {}, 'Field'), el('th', {}, 'From'), el('th', {}, 'To'))),
      el('tbody', {}, ...pv.changes.map(c => el('tr', {}, el('td', {}, c.field), el('td', { class: 'muted' }, c.old ?? '—'), el('td', {}, el('b', {}, c.new ?? '—')))))))
      : el('div', { class: 'help' }, 'No changes have been made yet. If none are needed, cancel the amendment instead.'),
    pv.missingRequired.length ? el('div', { class: 'error' }, 'This run was complete before the amendment — required fields can’t be left blank: ' + pv.missingRequired.join('; ')) : null,
    field('Note', comment),
    el('div', { class: 'help' }, 'On submit the changes become a new revision and the run goes back for production-log review; finished goods stay on hold until it is re-released.'));
  modal('Submit amendment — ' + run.processingLot, body, async () => {
    if (!pv.changes.length) throw new Error('No changes have been made — cancel the amendment instead.');
    if (pv.missingRequired.length) throw new Error('Required fields are missing — complete them first.');
    await api('POST', '/production/' + run.id + '/amendments/' + a.id + '/submit', { comment: comment.value || null });
    toast('Amendment submitted for review.');
    closeAllModals(); render();
  }, 'Submit for review');
}
// ---- Data integrity check (admin / Quality Manager) ----
function askPassword(title, message) {
  return new Promise(resolve => {
    const pw = el('input', { type: 'password', autocomplete: 'off' });
    modal(title, el('div', {}, el('div', { class: 'help' }, message), field('Password', pw)), async () => {
      if (!pw.value) throw new Error('Enter your password.');
      resolve(pw.value);
    }, 'Confirm');
  });
}
async function openIntegrityCheck() {
  const host = el('div', {});
  async function draw(r) {
    host.innerHTML = '';
    const sev = { error: 'low', warning: 'hold', info: 'wip' };
    host.append(el('div', { class: 'summary-line' }, sl('Checked', fmtWhen(r.checkedAt)),
      sl('Errors', r.summary.error), sl('Warnings', r.summary.warning), sl('Info', r.summary.info),
      el('span', {}, 'Audit chain: ', badge(r.chain.ok ? 'on_hand' : 'low', r.chain.ok ? 'intact (' + r.chain.events + ' events)' : 'BROKEN'))));
    if (!r.issues.length) { host.append(el('div', { class: 'empty card' }, '✓ No inconsistencies found.')); return; }
    host.append(table(['', 'Area', 'Run', 'Finding', ''], r.issues.map(i => [
      el('span', { class: 'int-sev' }, badge(sev[i.severity], i.severity)), i.area, i.run ? mono(i.run) : '—',
      el('div', {}, i.message, i.action ? el('div', { class: 'help' }, '→ ' + i.action) : null),
      i.repair ? el('button', { class: 'secondary', onclick: async () => {
        const pwd = await askPassword('Repair — ' + i.area, 'Applies the fix and records it in the audit trail. Enter your password to confirm.');
        try { draw(await api('POST', '/integrity/repair', { kind: i.repair, runId: i.runId, password: pwd })); toast('Repair applied'); }
        catch (e) { toast(e.message, true); }
      } }, 'Repair') : null]), [false, false, false, false, false]));
  }
  const body = el('div', {}, el('div', { class: 'help' }, 'Cross-checks each finalized run against the records derived from it: finished-goods lots vs packaging entries, output litres, container/label stock commits, release status vs lot status, the signed log vs the current log, open amendments and the audit chain. Documents are not part of the check.'),
    host);
  modal('Data integrity check', body, async () => {}, 'Close', { wide: true, noCancel: true });
  host.append(el('div', { class: 'help' }, 'Checking…'));
  try { await draw(await api('GET', '/integrity')); } catch (e) { host.innerHTML = ''; host.append(el('div', { class: 'error' }, e.message)); }
}
const canAmendLog = () => !!(State.user && State.user.canAmendLog);
const canIntegrity = () => !!(State.user && (State.user.role === 'admin' || State.user.isQualityManager));

// Quiet bar-chart icon in the card's title row (not an action button): hover explains the Yield & Usage
// analysis setting and shows this run's current state; click opens the Analysis window.
function analysisInfoButton(run) {
  const tip = run.excludeFromStats
    ? 'Yield & Usage analysis: this run is EXCLUDED' + (run.excludeReason ? ' (' + run.excludeReason + ')' : '') + '. Click to change.'
    : 'Yield & Usage analysis: this run is included in the statistics. Click to exclude a test, spoiled or unrepresentative run.';
  // bar-chart glyph = "analysis"; a slash through it when the run is excluded
  const svg = '<svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden="true">'
    + '<rect x="2" y="9" width="3" height="5" rx=".6"/><rect x="6.5" y="5" width="3" height="9" rx=".6"/><rect x="11" y="2" width="3" height="12" rx=".6"/>'
    + (run.excludeFromStats ? '<path d="M1.5 14.5 14.5 1.5" stroke="#fff" stroke-width="3.2" stroke-linecap="round"/><path d="M1.5 14.5 14.5 1.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>' : '')
    + '</svg>';
  return el('button', { type: 'button', class: 'info-btn' + (run.excludeFromStats ? ' on' : ''), 'data-tip': tip, 'aria-label': tip,
    onclick: () => editRun(run), html: svg });
}
// Opens (and scrolls to) one section of a production-log modal: sections are the
// top-level accordions, identified by their summary text.
const LOG_SECTION_TITLES = { feedstock: 'Feedstock', homogenization: 'Homogenization', extraction: 'Extraction',
  separation: 'Separation', pasteurization: 'Pasteurization', dilution: 'Dilution & Preservation', packaging: 'Packaging' };
function jumpToSection(root, key) {
  const title = LOG_SECTION_TITLES[key];
  const target = [...root.querySelectorAll(':scope > details.accordion')]
    .find(d => (d.querySelector('summary')?.textContent || '').trim() === title);
  if (!target) return;
  target.open = true;
  setTimeout(() => target.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60);
}
// Revision tracker for a finalized run's production log: Rev 1 is the finalized
// record; every later change (any section) adds a revision with who/when and a
// field-by-field old -> new diff.
function revisionTracker(run) {
  const revs = (run.revisions || []).slice().reverse();   // newest first
  const latest = run.revision || 1;
  const head = revs[0];
  const wrap = el('details', { class: 'rev-tracker' },
    el('summary', {}, el('span', { class: 'rev-badge' }, 'Rev ' + latest), 'Revision tracker',
      el('span', { class: 'muted', style: 'font-weight:normal' },
        latest > 1 && head ? '  ·  last changed ' + fmtWhen(head.at) + ' by ' + (head.by || '—') : '  ·  no changes since finalized')));
  revs.forEach((r, i) => {
    const n = (r.changes || []).length;
    wrap.append(el('details', { class: 'rev-item' + (i === 0 ? ' current' : '') },
      el('summary', {}, el('span', { class: 'rev-badge', style: 'background:' + (i === 0 ? 'var(--teal-dark)' : '#8aa39f') }, 'Rev ' + r.rev),
        el('b', {}, fmtWhen(r.at)), ' · ', r.by || '—', ' — ', el('span', { class: 'muted' }, r.summary || (r.kind === 'finalized' ? 'Finalized' : ''))),
      n ? el('div', { class: 'rev-body' },
        el('div', { class: 'tablewrap' }, el('table', {},
        el('thead', {}, el('tr', {}, el('th', {}, 'Field'), el('th', {}, 'From'), el('th', {}, 'To'))),
        el('tbody', {}, ...r.changes.map(c => el('tr', {}, el('td', {}, c.field),
          el('td', { class: 'muted' }, c.old ?? '—'), el('td', {}, el('b', {}, c.new ?? '—')))))))) : null));
  });
  return wrap;
}
function draftCard(d) {
  const pkgSummary = (d.packages || []).filter(p => p.qty > 0).map(p => `${fmt(p.qty)} × ${p.size}`).join(', ') || '—';
  return el('div', { class: 'card' },
    el('div', { class: 'page-head', style: 'margin:0 0 8px' },
      el('h3', { style: 'margin:0' }, mono(d.processingLot), '  ', el('span', { class: 'badge hold' }, 'Not yet submitted')),
      el('div', { class: 'actions' },
        el('button', { onclick: () => openRun(d) }, 'Resume'),
        el('button', { class: 'secondary', onclick: () => openSampleLabels(d) }, 'Print sample labels'),
        el('button', { class: 'danger', onclick: () => discardDraft(d) }, 'Discard'))),
    el('div', { class: 'summary-line' },
      sl('Run date', d.runDate), sl('SKU', d.sku ? skuName(d.sku) : '—'),
      sl('Totes selected', d.toteLots.length ? d.toteLots.join(', ') : '—'),
      sl('Packaging', pkgSummary), d.operators ? sl('Operators', d.operators) : null),
    stageProgress(d, { onSelect: key => openRun(d, { section: key }) }),
    el('div', { class: 'muted', style: 'margin-top:6px;font-size:12px' }, 'Click a section above to open it. Revision tracking starts when the run is finalized.'),
    d.notes ? el('div', { class: 'muted', style: 'margin-top:4px;font-size:12px' }, '“' + d.notes + '”') : null);
}
async function discardDraft(d) {
  if (!confirm('Discard this in-progress run? This cannot be undone.')) return;
  await api('DELETE', '/production/drafts/' + d.id);
  toast('Draft discarded');
  render();
}
// Every timestamp we display is stamped in UTC (now_iso() / Date#toISOString())
// — render it in Pacific time (DST-aware) rather than showing raw UTC, which
// otherwise reads 7-8 hrs ahead of the plant's actual local time.
const DISPLAY_TZ = 'America/Vancouver';
function fmtWhen(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.replace('T', ' ').replace('Z', '').slice(0, 16);
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: DISPLAY_TZ, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
  }).formatToParts(d);
  const get = t => (parts.find(p => p.type === t) || {}).value;
  return get('year') + '-' + get('month') + '-' + get('day') + ' ' + get('hour') + ':' + get('minute');
}
// "Analysis": the run's Yield & Usage analysis setting (exclude a test / spoiled / unrepresentative run).
// It is not a production-log entry, so it never needs an amendment. Amending the log is the card's
// "Amend run" button; run date / location / operators / notes live in the Process log's Initiation section.
async function editRun(run) {
  // One tidy option row (same look as the user-permission checklist); the reason field only
  // appears while the option is ticked, and is required then.
  const cb = el('input', { type: 'checkbox', id: 'e_excl' });
  cb.checked = !!run.excludeFromStats;
  const reason = el('input', { id: 'e_excl_reason', value: run.excludeReason || '', placeholder: 'e.g. test run, spoiled batch, unrepresentative yield' });
  const reasonField = field(reqLabel('Reason for excluding'), reason);
  const row = el('label', { class: 'perm-item' + (cb.checked ? ' on' : '') }, cb,
    el('span', { class: 'perm-text' }, el('b', {}, 'Exclude this run from the analysis'),
      el('small', {}, 'Leaves it out of the Yield & Usage statistics (test, spoiled or unrepresentative runs). It stays visible behind “Show excluded”.')));
  const sync = () => { row.classList.toggle('on', cb.checked); reasonField.classList.toggle('hidden', !cb.checked); };
  cb.addEventListener('change', () => { sync(); if (cb.checked) reason.focus(); });
  sync();
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Run', run.processingLot), sl('Product', skuName(run.sku))),
    el('div', { class: 'perm-box' }, el('div', { class: 'perm-title' }, 'Yield & Usage analysis'),
      el('div', { class: 'perm-list' }, row)),
    reasonField,
    el('div', { class: 'perm-note' }, 'Not a production-log entry, so no amendment is needed. The change is logged with your name.'));
  modal('Analysis — ' + run.processingLot, body, async () => {
    if (cb.checked && !reason.value.trim()) throw new Error('Enter a reason for excluding this run.');
    const r = await api('PUT', '/production/' + run.id, { excludeFromStats: cb.checked ? 1 : 0, excludeReason: cb.checked ? reason.value : '' });
    State.ref = await api('GET', '/refdata');
    toast(r.changed ? 'Analysis setting saved' : 'No changes');
    render();
  }, 'Save');
}
function fmtBytes(n) {
  if (n == null) return '—';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(0) + ' KB';
  return (n / 1024 / 1024).toFixed(1) + ' MB';
}
function fileIcon(ct) {
  ct = ct || '';
  if (ct.includes('pdf')) return '📄';
  if (ct.startsWith('image/')) return '🖼️';
  if (ct.includes('sheet') || ct.includes('excel') || ct.includes('csv')) return '📊';
  if (ct.includes('word') || ct.includes('document')) return '📝';
  return '📎';
}
function attDownloadUrl(rid, aid, dl) {
  return '/api/production/' + rid + '/attachments/' + aid + '/download?' +
    (dl ? 'dl=1&' : '') + 'token=' + encodeURIComponent(State.token);
}
function toteAttDownloadUrl(tid, aid, dl) {
  return '/api/totes/' + tid + '/attachments/' + aid + '/download?' +
    (dl ? 'dl=1&' : '') + 'token=' + encodeURIComponent(State.token);
}
function sopDownloadUrl(sid, dl) {
  return '/api/sop-documents/' + sid + '/download?' +
    (dl ? 'dl=1&' : '') + 'token=' + encodeURIComponent(State.token);
}
// A QC Check's link to its governing SOP, resolved by its stable reference
// key (not its display name) against the admin-managed list
// (State.ref.sops) -- so an admin renaming the document in Admin > SOP
// Documents never breaks this link, and the current name always shows here.
// Reads sensibly even before an admin has uploaded the file yet, too.
function sopLinkEl(key) {
  const sop = (State.ref.sops || []).find(s => s.key === key);
  if (!sop) return el('div', { class: 'qc-check-sop help' }, '📄 SOP not configured (Admin → SOP Documents)');
  if (!sop.hasFile) return el('div', { class: 'qc-check-sop help' }, '📄 ' + sop.name + ' — not yet uploaded (Admin → SOP Documents)');
  return el('div', { class: 'qc-check-sop' }, el('a', { href: sopDownloadUrl(sop.id, false), target: '_blank', rel: 'noopener' }, '📄 ' + sop.name));
}
function uploadAtt(rid, file) {
  return new Promise((resolve, reject) => {
    if (file.size > 25 * 1024 * 1024) return reject(new Error(file.name + ' exceeds the 25 MB limit'));
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const b64 = String(reader.result).split(',')[1];
        const resp = await api('POST', '/production/' + rid + '/attachments',
          { filename: file.name, contentType: file.type || 'application/octet-stream', dataB64: b64 });
        resolve(resp);
      } catch (e) { reject(e); }
    };
    reader.onerror = () => reject(new Error('Could not read ' + file.name));
    reader.readAsDataURL(file);
  });
}
function coaPdfUrl(rid, dl) {
  return '/api/production/' + rid + '/coa.pdf?' + (dl ? 'dl=1&' : '') + 'token=' + encodeURIComponent(State.token);
}
function summaryPdfUrl(rid, dl) {
  return '/api/production/' + rid + '/summary.pdf?' + (dl ? 'dl=1&' : '') + 'token=' + encodeURIComponent(State.token);
}
async function openAttachments(run) {
  const listHost = el('div', {});
  const status = el('div', { class: 'help' });
  const fileInput = el('input', { type: 'file', multiple: 'multiple',
    accept: '.pdf,image/*,.doc,.docx,.xls,.xlsx,.txt,.csv,.heic' });
  // capture="environment" hands off straight to the rear camera on phones/tablets
  // instead of the general photo/file picker; desktop browsers just ignore it.
  const cameraInput = el('input', { type: 'file', accept: 'image/*', capture: 'environment', style: 'display:none' });
  const cameraBtn = el('button', { class: 'secondary', type: 'button', onclick: () => cameraInput.click() }, '📷 Take photo');
  async function refresh() { drawList((await api('GET', '/production/' + run.id + '/attachments')).attachments); }
  function drawList(atts) {
    listHost.innerHTML = '';
    if (!atts.length) { listHost.append(el('div', { class: 'help' }, 'No documents attached yet.')); return; }
    listHost.append(table(['Document', 'Size', 'Added by', 'When', ''], atts.map(a => [
      el('span', {}, fileIcon(a.contentType) + ' ', a.filename),
      fmtBytes(a.size), a.uploadedBy || '—', fmtWhen(a.uploadedAt),
      rowActions([
        ['View', () => window.open(attDownloadUrl(run.id, a.id, false), '_blank')],
        /\.(docx|xlsx)$/i.test(a.filename || '') ? ['Preview', () => previewAttachment(run.id, a.id, a.filename)] : null,
        ['Download', () => { const l = el('a', { href: attDownloadUrl(run.id, a.id, true), download: a.filename }); document.body.append(l); l.click(); l.remove(); }],
        ['Delete', async () => { if (!confirm('Remove “' + a.filename + '”?')) return; await api('DELETE', '/production/' + run.id + '/attachments/' + a.id); toast('Removed'); refresh(); }, 'danger']
      ])
    ]), [false, true, false, false, false]));
  }
  async function handleFiles(fileList) {
    const files = [...fileList]; if (!files.length) return;
    status.textContent = 'Uploading ' + files.length + ' file(s)…';
    try {
      for (const f of files) await uploadAtt(run.id, f);
      status.textContent = ''; await refresh(); toast('Uploaded'); State.attachmentsChanged = true;
    } catch (e) { status.textContent = ''; toast(e.message, true); await refresh(); }
  }
  fileInput.addEventListener('change', async () => { await handleFiles(fileInput.files); fileInput.value = ''; });
  cameraInput.addEventListener('change', async () => { await handleFiles(cameraInput.files); cameraInput.value = ''; });
  // The Production Log Summary is generated on demand from the current log (never stale), so it sits above the uploaded files.
  const summaryCard = run.status === 'completed' ? el('div', { class: 'card', style: 'margin:10px 0;padding:12px 14px' },
    el('div', { style: 'display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap' },
      el('div', {}, el('b', {}, '📄 Production log summary (PDF)'),
        el('div', { class: 'help' }, 'Key results for every section, a quality scorecard and a sample summary — generated from the current log each time.')),
      el('div', { style: 'display:flex;gap:8px' },
        el('button', { type: 'button', onclick: () => window.open(summaryPdfUrl(run.id, false), '_blank') }, 'View'),
        el('button', { type: 'button', class: 'secondary', onclick: () => { const l = el('a', { href: summaryPdfUrl(run.id, true), download: run.processingLot + '_Production-Log-Summary.pdf' }); document.body.append(l); l.click(); l.remove(); } }, '⬇ Download')))) : null;
  const coaCard = run.status === 'completed' ? el('div', { class: 'card', style: 'margin:10px 0;padding:12px 14px' },
    el('div', { style: 'display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap' },
      el('div', {}, el('b', {}, '🧫 Certificate of Analysis (PDF)'),
        el('div', { class: 'help' }, 'Results against the product specification, from the lab results entered for this run. Upload the lab reports below, then enter their values under Lab results.')),
      el('div', { style: 'display:flex;gap:8px' },
        el('button', { type: 'button', onclick: () => window.open(coaPdfUrl(run.id, false), '_blank') }, 'View'),
        el('button', { type: 'button', class: 'secondary', onclick: () => { const l = el('a', { href: coaPdfUrl(run.id, true), download: run.processingLot + '_Certificate-of-Analysis.pdf' }); document.body.append(l); l.click(); l.remove(); } }, '⬇ Download'),
        el('button', { type: 'button', class: 'secondary', onclick: () => openLabResults(run) }, 'Lab results…')))) : null;
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Run', run.processingLot),
      el('span', { class: 'muted' }, 'lab results, paper logs, images — PDF, images, Office docs (max 25 MB each)')),
    summaryCard, coaCard,
    el('label', {}, 'Attached documents'), listHost,
    el('div', { style: 'margin-top:16px' },
      el('label', {}, 'Add documents'),
      el('div', { style: 'display:flex;gap:10px;align-items:center;flex-wrap:wrap' }, fileInput, cameraBtn, cameraInput),
      status));
  await refresh();
  modal('Documents — ' + run.processingLot, body, async () => { if (State.attachmentsChanged) { State.attachmentsChanged = false; render(); } }, 'Done');
}
function sl(k, val) { return el('span', {}, k + ': ', el('b', {}, val)); }

/* ---------------- Quality control log ---------------- */
// Restricts a Value input to digits and one decimal point, groups the integer
// part with commas as the user types, and caps decimal digits at maxDecimals
// (a number, or a function returning one — density measurements get 3, every
// other measurement 2, per QC policy). `allowNegative` (off by default, so
// every existing QC call site is unaffected) permits a leading "-", needed
// for signed readings like ORP (mV).
function attachNumericMask(input, maxDecimals, allowNegative) {
  const getMax = typeof maxDecimals === 'function' ? maxDecimals : () => maxDecimals;
  input.addEventListener('input', () => {
    const caretFromEnd = input.value.length - input.selectionStart;
    const neg = allowNegative && input.value.trim().startsWith('-');
    let raw = input.value.replace(/[^0-9.]/g, '');
    const firstDot = raw.indexOf('.');
    if (firstDot !== -1) raw = raw.slice(0, firstDot + 1) + raw.slice(firstDot + 1).replace(/\./g, '');
    let [intPart, fracPart] = raw.split('.');
    if (fracPart !== undefined) fracPart = fracPart.slice(0, getMax());
    const intGrouped = intPart ? Number(intPart).toLocaleString('en-US') : '';
    input.value = (neg ? '-' : '') + intGrouped + (fracPart !== undefined ? '.' + fracPart : '');
    const newCaret = Math.max(0, input.value.length - caretFromEnd);
    input.setSelectionRange(newCaret, newCaret);
  });
}
function qcMaxDecimals(unit) { return unit === 'g/mL' ? 3 : 2; }   // density (ρ) gets 3 places, everything else 2
function qcParseValue(s) { return parseFloat(String(s).replace(/,/g, '')); }
function formatQcValue(n, maxDecimals) {
  const num = Number(n);
  return Number.isNaN(num) ? '' : num.toLocaleString('en-US', { maximumFractionDigits: maxDecimals });
}

async function pageQC(v) {
  v.append(el('div', { class: 'page-head' }, el('h2', {}, 'Quality Control')));
  v.append(el('div', { class: 'empty card' },
    'QC data now lives on each production run’s Process Log — open a run’s 🧪 QC button (Production tab) to view it.'));
}

// Two-line stacked single-select dropdown (native <select> can't render
// multi-line option text) -- shows each option's title + optional subtitle
// stacked, both in the closed button and the open list of choices.
function buildStackedSelect(options, initialValue, onChange) {
  let value = options.some(o => o.value === initialValue) ? initialValue : options[0].value;
  const btn = el('button', { type: 'button', class: 'qc-loc-select-btn' });
  const panel = el('div', { class: 'qc-loc-panel hidden' });
  const wrap = el('div', { class: 'qc-loc-select' }, btn, panel);
  function labelFor(o) {
    return el('span', { class: 'qc-group-label' },
      el('span', { class: 'qc-group-title' }, o.title),
      o.subtitle ? el('span', { class: 'qc-group-subtitle' }, o.subtitle) : null);
  }
  function onDocClick(e) { if (!wrap.contains(e.target)) close(); }
  function open() {
    panel.innerHTML = '';
    options.forEach(o => panel.append(el('div', {
      class: 'qc-loc-option' + (o.value === value ? ' selected' : ''),
      onclick: () => { value = o.value; renderBtn(); close(); onChange(value); }
    }, labelFor(o))));
    panel.classList.remove('hidden');
    document.addEventListener('click', onDocClick, true);
  }
  function close() {
    panel.classList.add('hidden');
    document.removeEventListener('click', onDocClick, true);
  }
  function renderBtn() {
    btn.innerHTML = '';
    btn.append(labelFor(options.find(o => o.value === value)), el('span', { class: 'qc-loc-caret' }, '▾'));
  }
  btn.addEventListener('click', () => { panel.classList.contains('hidden') ? open() : close(); });
  renderBtn();
  return { el: wrap, get value() { return value; } };
}

// Read-only: every "QC Check" field's current value, grouped by production-log
// section + QC Check subtitle (State.ref.qcFields, in registry order — see
// QC_FIELD_REGISTRY, kelp_erp_server.py). Editing only happens on the Process
// Log's own QC Check fields (save_stage) — this modal just displays what's
// already been recorded there, plus who/when last changed it. Labels are
// rendered as html, not text -- QC_FIELD_REGISTRY is a fixed, code-authored
// list (never user input), and a couple of labels embed a real <sub> (e.g.
// "%Moisture<sub>solids</sub>") that plain text would print literally.
async function openQcForRun(run) {
  const groups = [];
  const seen = new Set();
  for (const f of State.ref.qcFields) {
    const key = f.stage + '|' + f.subtitle;
    if (!seen.has(key)) { seen.add(key); groups.push({ key, stage: f.stage, stageLabel: f.stageLabel, subtitle: f.subtitle }); }
  }
  const groupSel = buildStackedSelect(
    [{ value: 'all', title: 'All groups' },
     ...groups.map(g => ({ value: g.key, title: g.stageLabel, subtitle: g.subtitle }))],
    'all', () => draw());
  const listHost = el('div', {});
  const { qcChecks } = await api('GET', '/production/' + run.id + '/qc-checks');
  function draw() {
    listHost.innerHTML = '';
    const filter = groupSel.value;
    groups.forEach(g => {
      if (filter !== 'all' && filter !== g.key) return;
      const fields = qcChecks.filter(e => e.stage === g.stage && e.subtitle === g.subtitle);
      const box = el('div', { class: 'qc-log-card' },
        el('div', { class: 'qc-log-head' }, el('b', {}, g.stageLabel), el('span', {}, g.subtitle)));
      fields.forEach(e => {
        const recorded = e.value != null;
        box.append(el('div', { class: 'qc-log-row' + (recorded ? '' : ' empty') },
          el('span', { class: 'qc-log-label', html: e.label }),
          el('span', { class: 'qc-log-value' }, recorded ? formatQcValue(e.value, qcMaxDecimals(e.unit)) : '—',
            recorded && e.unit ? el('small', {}, e.unit) : null),
          recorded ? el('span', { class: 'qc-log-meta' }, (e.recordedBy || 'Unknown') + ' · ' + fmtWhen(e.recordedAt)) : null));
      });
      listHost.append(box);
    });
  }
  draw();
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Run', run.processingLot), sl('SKU', skuName(run.sku))),
    field('Group', groupSel.el), listHost);
  modal('Quality Control Log — ' + run.processingLot, body, async () => {}, 'Close', { noCancel: true, closeX: true });
}

/* ---- Process-stage building blocks, shared by openRun (pre-finalize) and
   openProcessLog (post-finalize) ---- */
// Every stage (Homogenization, Extraction, Separation, Pasteurization) is
// bespoke -- each needs a QC Check/Sample Point card and/or custom per-field
// placeholders/defaults a generic field-list renderer can't do.
// ---- Tank 5A/5B -> Tank 6A/6B dilution ------------------------------------------------
// The operator pushes product from 5A/5B into 6A, 6B or both (connected = double capacity) and
// dilutes it to the SKU's target TDS. Mass balance (c1V1 = c2V2): c1 = Separation filtrate TDS,
// c2 = target TDS, so the diluted volume is V1 x c1/c2. The receiving tanks' current contents
// are part of the fit check.
const RECEIVING_TANKS = [['6A', 'Tank 6A only'], ['6B', 'Tank 6B only'], ['6AB', 'Tank 6A + 6B connected']];
const usesTank = (receiving, t) => receiving === '6AB' || receiving === t;
function dilutionPlanCalc(p) {
  const cap = settingValue('dilution_tank_capacity_each_l', 5000);
  const n = p.receiving === '6AB' ? 2 : (p.receiving ? 1 : 0);
  const existing = (usesTank(p.receiving, '6A') ? (p.start6a || 0) : 0) + (usesTank(p.receiving, '6B') ? (p.start6b || 0) : 0);
  const out = { cap, n, existing, available: null, space: n ? Math.max(0, cap * n - existing) : null,
    maxTransfer: null, transfer: null, water: null, expectedFinal: null, remaining: null, fits: null };
  if (p.v5a != null || p.v5b != null) out.available = (p.v5a || 0) + (p.v5b || 0);
  if (!n || !p.c1 || !p.c2) return out;
  const ratio = p.c1 / p.c2;
  out.maxTransfer = ratio > 1 ? out.space / ratio : out.space;     // most product that still fits once diluted
  if (out.available == null) return out;
  out.transfer = Math.min(out.available, out.maxTransfer);
  out.water = ratio > 1 ? out.transfer * (ratio - 1) : 0;         // 0 when already at/below target
  out.expectedFinal = existing + out.transfer + out.water;
  out.remaining = out.available - out.transfer;
  out.fits = out.remaining <= 0.5;
  return out;
}
const round10 = v => Math.round(v / 10) * 10;
const fmtL = v => v == null ? null : fmt(round10(v), 0) + ' L';

// Pasteurization: "Start Conditions" groups the stage's process parameters
// (Total volume (L) was removed); "Pasteurization Out" holds a Sample Point
// box subtitled "Post-pasteurization microbial check". Pasteurization In's
// Process Check is the DILUTION PLAN for pushing product from Tanks 5A/5B into
// 6A/6B: the levels in 5A/5B, which receiving tank(s), what is already in them,
// and from those the maximum product that can be transferred before the tanks
// reach capacity once diluted, plus the recommended transfer and dilution water
// (see dilutionPlanCalc). The actuals live in Dilution & Preservation.
// `getTdsTarget` / `getSeparationTds` are getters (not plain values) so a caller
// with a live-changing SKU or a just-saved Separation can call `refresh()` /
// `refreshTds()` again.
function buildPasteurizationSection(getRunId, values, samplePoints, processingLot, getTdsTarget, getSeparationTds) {
  values = values || {};
  const startedAt = el('input', { type: 'datetime-local', value: values.startedAt || '' });
  const productSetpointInp = el('input', { inputmode: 'decimal', placeholder: 'Product set-point (°C)' }); attachNumericMask(productSetpointInp, 2);
  productSetpointInp.value = values.productSetpointC != null ? formatQcValue(values.productSetpointC, 2)
    : String(settingValue('pasteurization_default_product_setpoint_c', 80));
  const boilerSetpointInp = el('input', { inputmode: 'decimal', placeholder: 'Boiler set-point (°C)' }); attachNumericMask(boilerSetpointInp, 2);
  boilerSetpointInp.value = values.boilerSetpointC != null ? formatQcValue(values.boilerSetpointC, 2)
    : String(settingValue('pasteurization_default_boiler_setpoint_c', 90));

  // ---- Dilution plan
  const levelInp = v => { const i = el('input', { inputmode: 'decimal', placeholder: 'Measured using level sensor' }); attachNumericMask(i, 2); if (v != null) i.value = formatQcValue(v, 2); return i; };
  const numOf = inp => inp.value.trim() === '' ? null : qcParseValue(inp.value);
  const tank5aInp = levelInp(values.tank5aL), tank5bInp = levelInp(values.tank5bL);
  const receivingSel = el('select', {}, el('option', { value: '' }, 'Select…'), ...RECEIVING_TANKS.map(([v, l]) => el('option', { value: v }, l)));
  receivingSel.value = values.receivingTanks || '';
  const start6aInp = levelInp(values.tank6aStartL != null ? values.tank6aStartL : 0);
  const start6bInp = levelInp(values.tank6bStartL != null ? values.tank6bStartL : 0);
  const start6aField = rfield('pasteurization', 'tank6aStartL', 'Tank 6A level before transfer (L)', start6aInp);
  const start6bField = rfield('pasteurization', 'tank6bStartL', 'Tank 6B level before transfer (L)', start6bInp);
  let currentTds = null, lastCalc = null;
  const planCbs = [];
  const tile = () => el('span', { class: 'help' });
  const T = { available: tile(), space: tile(), max: tile(), transfer: tile(), water: tile(), expected: tile() };
  const summaryHost = el('div', { class: 'summary-line' });
  const statusLine = el('div', { class: 'plan-status' });
  const resultRow = (label, node, cls) => el('div', { class: 'qc-check-result' + (cls ? ' ' + cls : '') },
    el('span', { class: 'qc-check-result-label' }, label), node);
  function getPlan() {
    return { receiving: receivingSel.value, v5a: numOf(tank5aInp), v5b: numOf(tank5bInp), start6a: numOf(start6aInp), start6b: numOf(start6bInp),
      c1: currentTds, c2: getTdsTarget(), calc: lastCalc };
  }
  const setTile = (span, text) => { span.className = text != null ? 'qc-check-result-value' : 'help'; span.textContent = text != null ? text : '—'; };
  function refreshDilutionReq() {
    start6aField.classList.toggle('hidden', !usesTank(receivingSel.value, '6A'));
    start6bField.classList.toggle('hidden', !usesTank(receivingSel.value, '6B'));
    const p = getPlan();
    const c = lastCalc = dilutionPlanCalc(p);
    summaryHost.innerHTML = '';
    summaryHost.append(sl('TDS concentrated (Separation filtrate)', p.c1 != null ? formatQcValue(p.c1, 2) + '%' : '—'),
      sl('TDS target', p.c2 != null ? formatQcValue(p.c2, 2) + '%' : '—'), sl('Capacity per tank', fmt(c.cap, 0) + ' L'));
    setTile(T.available, fmtL(c.available));
    setTile(T.space, fmtL(c.space));
    setTile(T.max, c.maxTransfer != null ? fmt(Math.floor(c.maxTransfer / 10) * 10, 0) + ' L' : null);
    setTile(T.transfer, fmtL(c.transfer));
    setTile(T.water, fmtL(c.water));
    setTile(T.expected, fmtL(c.expectedFinal));
    if (p.c1 == null) { statusLine.className = 'plan-status'; statusLine.textContent = 'Needs the Separation filtrate TDS (Separation → Filtrate QC Check).'; }
    else if (!p.c2) { statusLine.className = 'plan-status'; statusLine.textContent = 'Select a product SKU with a target TDS.'; }
    else if (!c.n) { statusLine.className = 'plan-status'; statusLine.textContent = 'Choose the receiving tank(s) to see how much can be transferred.'; }
    else if (c.fits === null) { statusLine.className = 'plan-status'; statusLine.textContent = 'Enter the Tank 5A / 5B levels to see the recommended transfer.'; }
    else if (c.fits) {
      statusLine.className = 'plan-status ok';
      statusLine.textContent = '✓ All ' + fmtL(c.available) + ' in 5A/5B fits once diluted.';
    } else {
      statusLine.className = 'plan-status warn';
      statusLine.textContent = '⚠ Only ' + fmt(Math.floor(c.maxTransfer / 10) * 10, 0) + ' L of the ' + fmt(round10(c.available), 0)
        + ' L in 5A/5B can be pushed through before the receiving tank(s) reach capacity after dilution — about ' + fmtL(c.remaining) + ' stays behind.';
    }
    planCbs.forEach(cb => cb());
  }
  function refreshTds() { currentTds = getSeparationTds ? getSeparationTds() : null; refreshDilutionReq(); }
  [tank5aInp, tank5bInp, start6aInp, start6bInp].forEach(i => i.addEventListener('input', refreshDilutionReq));
  receivingSel.addEventListener('change', refreshDilutionReq);
  const dilutionProcessCheckBox = el('div', { class: 'qc-check-box' },
    el('div', { class: 'qc-check-title' }, 'Process Check'),
    el('div', { class: 'qc-check-subtitle' }, 'Dilution plan — Tanks 5A/5B → 6A/6B'),
    summaryHost,
    el('div', { class: 'form-row' },
      rfield('pasteurization', 'tank5aL', 'Tank 5A level (L)', tank5aInp),
      rfield('pasteurization', 'tank5bL', 'Tank 5B level (L)', tank5bInp)),
    el('div', { class: 'form-row' }, rfield('pasteurization', 'receivingTanks', 'Receiving tanks', receivingSel)),
    el('div', { class: 'form-row' }, start6aField, start6bField),
    resultRow('Product available in 5A / 5B', T.available),
    resultRow('Available space in receiving tank(s)', T.space),
    resultRow('Maximum product to transfer', T.max, 'plan-key'),
    resultRow('Recommended product transfer', T.transfer),
    resultRow('Recommended dilution water', T.water),
    resultRow('Expected final volume in receiving tank(s)', T.expected),
    statusLine);

  const postCollectedInp = el('input', { type: 'datetime-local',
    value: values.postSampleCollectedAt ? values.postSampleCollectedAt.replace('Z', '').slice(0, 16) : '' });
  const postSamplePointBox = el('div', { class: 'qc-check-box theme-sample' },
    el('div', { class: 'qc-check-title' }, 'Sample Point'),
    el('div', { class: 'qc-check-subtitle' }, 'Post-pasteurization microbial check'),
    field('Collection date and time', postCollectedInp),
    buildSamplePointsSection(samplePoints, getRunId, processingLot, () => postCollectedInp.value, 'pasteurization_post'));

  const status = el('span', { class: 'help' });
  const saveBtn = el('button', { type: 'button', class: 'secondary section-save', onclick: save }, 'Save');
  async function save() {
    status.textContent = ''; saveBtn.disabled = true;
    try {
      const rid = await getRunId();
      const p = getPlan(), c = p.calc || {};
      await api('PUT', '/production/' + rid + '/stages/pasteurization', {
        startedAt: startedAt.value || null,
        productSetpointC: numOf(productSetpointInp),
        boilerSetpointC: numOf(boilerSetpointInp),
        postSampleCollectedAt: postCollectedInp.value || null,
        tdsPct: currentTds,
        tank5aL: p.v5a, tank5bL: p.v5b, receivingTanks: p.receiving || null,
        tank6aStartL: usesTank(p.receiving, '6A') ? p.start6a : null,
        tank6bStartL: usesTank(p.receiving, '6B') ? p.start6b : null,
        maxTransferL: c.maxTransfer != null ? c.maxTransfer : null,
        recommendedTransferL: c.transfer != null ? c.transfer : null,
        recommendedWaterL: c.water != null ? c.water : null,
      });
      status.textContent = 'Saved.';
    } catch (e) { status.textContent = e.message; }
    saveBtn.disabled = false;
  }
  refreshTds();
  return {
    refreshTds,
    box: el('details', { class: 'accordion' }, el('summary', {}, 'Pasteurization'),
      el('div', { class: 'accordion-body' },
        el('div', { class: 'qc-check-section-title', style: 'margin-top:0' }, 'Start Conditions'),
        el('div', { class: 'form-row' }, rfield('pasteurization', 'startedAt', 'Started at', startedAt)),
        // Standard 2-column form-row (not the compact 108px fields) so both
        // labels fit on one line and the two input boxes stay level.
        el('div', { class: 'form-row' },
          rfield('pasteurization', 'productSetpointC', 'Product set-point (°C)', productSetpointInp),
          rfield('pasteurization', 'boilerSetpointC', 'Boiler set-point (°C)', boilerSetpointInp)),
        el('div', { class: 'qc-check-section-title' }, 'Pasteurization In'),
        dilutionProcessCheckBox,
        el('div', { class: 'qc-check-section-title' }, 'Pasteurization Out'),
        postSamplePointBox,
        el('div', { style: 'margin-top:6px' }, saveBtn, status))),
    refresh: refreshDilutionReq,
    getPlan,
    onPlanChange: cb => planCbs.push(cb)
  };
}
// "solids" renders as a subscript -- Unicode has no subscript i/d, so this
// needs a real <sub>, unlike the H/2 swap above which a plain Unicode
// subscript digit could handle. Shared by Homogenization/Extraction's Total
// Solids group (Separation's Solids characterization box has its own two
// distinctly-subscripted labels -- centrifuge_solids/screw_solids -- not
// this one). A fresh node is returned each call since a DOM node can't be
// reused across multiple boxes.
function moistureSolidsLabel() {
  return el('span', { html: '%Moisture<sub>solids</sub>' });
}
// The "QC Check" card (purple, Liquid + Slurry/Solids readings): shared
// between Homogenization Output, Extraction Out and Separation Liquid Out
// -- only the subtitle and which stage's values it reads/saves differ per
// caller. Pass `opts.omitSlurrySolids` to render/save the Liquid fields
// only (Separation's Liquid Out has no Slurry/Solids section).
function buildQualityCheckBox(subtitle, values, opts) {
  opts = opts || {};
  function pctInput() { const i = el('input', { inputmode: 'decimal', placeholder: '%' }); attachNumericMask(i, 1); return i; }
  function densityInput() { const i = el('input', { inputmode: 'decimal', placeholder: 'g/mL' }); attachNumericMask(i, 3); return i; }
  const qcPhInp = el('input', { inputmode: 'decimal', placeholder: 'pH' }); attachNumericMask(qcPhInp, 1);
  if (values.qcPh != null) qcPhInp.value = formatQcValue(values.qcPh, 1);
  const tdsInp = pctInput(); if (values.tdsPct != null) tdsInp.value = formatQcValue(values.tdsPct, 1);
  const brixInp = pctInput(); if (values.brixPct != null) brixInp.value = formatQcValue(values.brixPct, 1);
  const mannitolInp = pctInput(); if (values.mannitolPct != null) mannitolInp.value = formatQcValue(values.mannitolPct, 1);
  const tsLiquidInp = pctInput(); if (values.tsLiquidPct != null) tsLiquidInp.value = formatQcValue(values.tsLiquidPct, 1);
  const rhoLiquidInp = densityInput(); if (values.rhoLiquidGMl != null) rhoLiquidInp.value = formatQcValue(values.rhoLiquidGMl, 3);
  let tsSlurryInp, rhoSlurryInp, tsSolidsInp;
  if (!opts.omitSlurrySolids) {
    tsSlurryInp = pctInput(); if (values.tsSlurryPct != null) tsSlurryInp.value = formatQcValue(values.tsSlurryPct, 1);
    rhoSlurryInp = densityInput(); if (values.rhoSlurryGMl != null) rhoSlurryInp.value = formatQcValue(values.rhoSlurryGMl, 3);
    tsSolidsInp = pctInput(); if (values.tsSolidsPct != null) tsSolidsInp.value = formatQcValue(values.tsSolidsPct, 1);
  }
  // Solids Loading (%) -- Homogenization only (opts.showSolidsLoading), sits
  // right next to %Moisturesolids in the Total Solids group.
  let solidsLoadingInp;
  if (opts.showSolidsLoading) {
    solidsLoadingInp = pctInput();
    if (values.solidsLoadingPct != null) solidsLoadingInp.value = formatQcValue(values.solidsLoadingPct, 1);
  }
  // Asterisk a field when the server's required-field registry lists it for
  // opts.reqStage (opts.keyMap renames keys, e.g. Separation's liquid* fields).
  const qf = (key, label, input) => opts.reqStage
    ? rfield(opts.reqStage, (opts.keyMap || {})[key] || key, label, input) : field(label, input);
  const boxChildren = [
    el('div', { class: 'qc-check-title' }, 'QC Check'),
    el('div', { class: 'qc-check-subtitle' }, subtitle),
    el('div', { class: 'qc-check-section-title' }, 'Liquid'),
    el('div', { class: 'form-row-compact' },
      qf('qcPh', 'pH', qcPhInp), qf('tdsPct', 'TDS (%)', tdsInp), qf('brixPct', 'Brix (%)', brixInp),
      qf('mannitolPct', 'Mannitol (%)', mannitolInp)),
    el('div', { class: 'qc-check-section-title' }, 'Total Solids'),
    el('div', { class: 'form-row-compact' },
      qf('tsLiquidPct', 'TSliquid (%)', tsLiquidInp),
      ...(opts.omitSlurrySolids ? [] : [qf('tsSlurryPct', 'TSslurry (%)', tsSlurryInp), qf('tsSolidsPct', moistureSolidsLabel(), tsSolidsInp)]),
      ...(opts.showSolidsLoading ? [qf('solidsLoadingPct', 'Solids Loading (%)', solidsLoadingInp)] : [])),
    el('div', { class: 'qc-check-section-title' }, 'Density'),
    el('div', { class: 'form-row-compact' },
      ...(opts.omitSlurrySolids ? [] : [qf('rhoSlurryGMl', 'ρslurry (g/mL)', rhoSlurryInp)]),
      qf('rhoLiquidGMl', 'ρliquid (g/mL)', rhoLiquidInp)),
  ];
  const box = el('div', { class: 'qc-check-box theme-quality' }, ...boxChildren);
  function getPayload() {
    const payload = {
      qcPh: qcPhInp.value.trim() === '' ? null : qcParseValue(qcPhInp.value),
      tdsPct: tdsInp.value.trim() === '' ? null : qcParseValue(tdsInp.value),
      brixPct: brixInp.value.trim() === '' ? null : qcParseValue(brixInp.value),
      mannitolPct: mannitolInp.value.trim() === '' ? null : qcParseValue(mannitolInp.value),
      tsLiquidPct: tsLiquidInp.value.trim() === '' ? null : qcParseValue(tsLiquidInp.value),
      rhoLiquidGMl: rhoLiquidInp.value.trim() === '' ? null : qcParseValue(rhoLiquidInp.value),
    };
    if (!opts.omitSlurrySolids) {
      payload.tsSlurryPct = tsSlurryInp.value.trim() === '' ? null : qcParseValue(tsSlurryInp.value);
      payload.rhoSlurryGMl = rhoSlurryInp.value.trim() === '' ? null : qcParseValue(rhoSlurryInp.value);
      payload.tsSolidsPct = tsSolidsInp.value.trim() === '' ? null : qcParseValue(tsSolidsInp.value);
    }
    if (opts.showSolidsLoading) {
      payload.solidsLoadingPct = solidsLoadingInp.value.trim() === '' ? null : qcParseValue(solidsLoadingInp.value);
    }
    return payload;
  }
  return { box, getPayload };
}
// Extraction: "Start Conditions" groups the stage's existing process
// parameters (compact, with a couple of fields given sensible defaults/
// placeholders per plant SOP); "Extraction Out" is the same QC Check card
// used in Homogenization Output, just under its own subtitle. Bespoke like
// Homogenization for the same reason -- the generic field-list renderer
// can't do custom defaults/placeholders or a QC Check card.
function buildExtractionSection(getRunId, values, onSaved) {
  values = values || {};
  const startedAt = el('input', { type: 'datetime-local', value: values.startedAt || '' });
  const amplitudeInp = el('input', { inputmode: 'decimal', placeholder: 'Amplitude (%)' }); attachNumericMask(amplitudeInp, 2);
  amplitudeInp.value = values.amplitudePct != null ? formatQcValue(values.amplitudePct, 2)
    : String(settingValue('extraction_default_amplitude_pct', 100));
  const flowrateInp = el('input', { inputmode: 'decimal', placeholder: 'Flow rate (L/min)' }); attachNumericMask(flowrateInp, 2);
  flowrateInp.value = values.flowrateLpm != null ? formatQcValue(values.flowrateLpm, 2)
    : String(settingValue('extraction_default_flowrate_lpm', 15));
  const pressureInp = el('input', { inputmode: 'decimal', placeholder: 'Target pressure is 20-30psi without exceeding 6000W' });
  attachNumericMask(pressureInp, 2);
  if (values.pressurePsi != null) pressureInp.value = formatQcValue(values.pressurePsi, 2);
  const startingPowerInp = el('input', { inputmode: 'decimal', placeholder: 'Target power is 4000-5000W' });
  attachNumericMask(startingPowerInp, 2);
  if (values.startingPowerW != null) startingPowerInp.value = formatQcValue(values.startingPowerW, 2);

  const qcCheck = buildQualityCheckBox('Extraction Performance', values, { reqStage: 'extraction' });

  const status = el('span', { class: 'help' });
  const saveBtn = el('button', { type: 'button', class: 'secondary section-save', onclick: save }, 'Save');
  async function save() {
    status.textContent = ''; saveBtn.disabled = true;
    try {
      const rid = await getRunId();
      const payload = {
        startedAt: startedAt.value || null,
        amplitudePct: amplitudeInp.value.trim() === '' ? null : qcParseValue(amplitudeInp.value),
        flowrateLpm: flowrateInp.value.trim() === '' ? null : qcParseValue(flowrateInp.value),
        pressurePsi: pressureInp.value.trim() === '' ? null : qcParseValue(pressureInp.value),
        startingPowerW: startingPowerInp.value.trim() === '' ? null : qcParseValue(startingPowerInp.value),
        ...qcCheck.getPayload(),
      };
      await api('PUT', '/production/' + rid + '/stages/extraction', payload);
      // Keep the shared stages.extraction object (the same object `values`
      // already is) in sync with what was just saved, then let Pasteurization
      // know its mirrored TDS display may need to change right now -- not
      // just next time the modal is reopened.
      Object.assign(values, payload);
      if (onSaved) onSaved();
      status.textContent = 'Saved.';
    } catch (e) { status.textContent = e.message; }
    saveBtn.disabled = false;
  }
  return el('details', { class: 'accordion' }, el('summary', {}, 'Extraction'),
    el('div', { class: 'accordion-body' },
      el('div', { class: 'qc-check-section-title', style: 'margin-top:0' }, 'Start Conditions'),
      el('div', { class: 'form-row' }, rfield('extraction', 'startedAt', 'Started at', startedAt)),
      el('div', { class: 'form-row-compact' },
        rfield('extraction', 'amplitudePct', 'Amplitude (%)', amplitudeInp),
        rfield('extraction', 'flowrateLpm', 'Flow rate (L/min)', flowrateInp)),
      // Pressure/Starting power each get the full row width (not shared
      // 2-up, not the compact 108px fields above) so their long SOP-target
      // placeholder text is never clipped.
      rfield('extraction', 'pressurePsi', 'Pressure (psi)', pressureInp),
      rfield('extraction', 'startingPowerW', 'Starting power (W)', startingPowerInp),
      el('div', { class: 'qc-check-section-title' }, 'Extraction Out'),
      qcCheck.box,
      el('div', { style: 'margin-top:6px' }, saveBtn, status)));
}
// Separation: "Start Conditions" groups the stage's process parameters
// (Started at, Flow rate, Mesh size -- Water addition (L) was removed);
// "Solids Out" is the Total wet-solids weight plus a %Moisture-only QC
// Check and a Sample Point box; "Liquid Out" is a QC Check with only the
// Liquid fields (no Slurry/Solids section). Same one-accordion/section-
// title/single-Save formatting as Extraction.
function buildSeparationSection(getRunId, values, samplePoints, processingLot, onSaved) {
  values = values || {};
  const startedAt = el('input', { type: 'datetime-local', value: values.startedAt || '' });
  const flowrateInp = el('input', { inputmode: 'decimal', placeholder: 'Flow rate (L/min)' }); attachNumericMask(flowrateInp, 2);
  flowrateInp.value = values.flowrateLpm != null ? formatQcValue(values.flowrateLpm, 2)
    : String(settingValue('separation_default_flowrate_lpm', 40));
  const meshInp = el('input', { inputmode: 'decimal', placeholder: 'Mesh size (micron)' }); attachNumericMask(meshInp, 2);
  meshInp.value = values.meshMicron != null ? formatQcValue(values.meshMicron, 2)
    : String(settingValue('separation_default_mesh_micron', 74));

  // Solids Out
  const wetSolidsWtInp = el('input', { inputmode: 'decimal', placeholder: 'kg' }); attachNumericMask(wetSolidsWtInp, 2);
  if (values.wetSolidsWtKg != null) wetSolidsWtInp.value = formatQcValue(values.wetSolidsWtKg, 2);
  const moistureInp = el('input', { inputmode: 'decimal', placeholder: '%' }); attachNumericMask(moistureInp, 1);
  if (values.pctMoisture != null) moistureInp.value = formatQcValue(values.pctMoisture, 1);
  // Two separate dewatering mechanisms, each with its own moisture reading --
  // "centrifuge" is the original/existing field (just relabeled), "screw" is
  // new, on its own line below it (two direct field() children, no
  // form-row-compact wrapper, so each stacks on its own row).
  const moistureScrewInp = el('input', { inputmode: 'decimal', placeholder: '%' }); attachNumericMask(moistureScrewInp, 1);
  if (values.pctMoistureScrew != null) moistureScrewInp.value = formatQcValue(values.pctMoistureScrew, 1);
  const solidsQcBox = el('div', { class: 'qc-check-box theme-quality' },
    el('div', { class: 'qc-check-title' }, 'QC Check'),
    el('div', { class: 'qc-check-subtitle' }, 'Solids characterization'),
    rfield('separation', 'pctMoisture', el('span', { html: '%Moisture<sub>centrifuge_solids</sub>' }), moistureInp),
    rfield('separation', 'pctMoistureScrew', el('span', { html: '%Moisture<sub>screw_solids</sub>' }), moistureScrewInp));
  const solidsCollectedInp = el('input', { type: 'datetime-local',
    value: values.solidsSampleCollectedAt ? values.solidsSampleCollectedAt.replace('Z', '').slice(0, 16) : '' });
  const solidsSamplePointBox = el('div', { class: 'qc-check-box theme-sample' },
    el('div', { class: 'qc-check-title' }, 'Sample Point'),
    el('div', { class: 'qc-check-subtitle' }, 'Separation Characterization'),
    field('Collection date and time', solidsCollectedInp),
    buildSamplePointsSection(samplePoints, getRunId, processingLot, () => solidsCollectedInp.value, 'separation_solids'));

  // Liquid Out: Liquid fields only, no Slurry/Solids section.
  const liquidQc = buildQualityCheckBox('Filtrate characterization', {
    qcPh: values.liquidQcPh, tdsPct: values.liquidTdsPct, brixPct: values.liquidBrixPct,
    mannitolPct: values.liquidMannitolPct, tsLiquidPct: values.liquidTsLiquidPct,
    rhoLiquidGMl: values.liquidRhoLiquidGMl,
  }, { omitSlurrySolids: true, reqStage: 'separation', keyMap: {
    qcPh: 'liquidQcPh', tdsPct: 'liquidTdsPct', brixPct: 'liquidBrixPct', mannitolPct: 'liquidMannitolPct',
    tsLiquidPct: 'liquidTsLiquidPct', rhoLiquidGMl: 'liquidRhoLiquidGMl' } });

  const status = el('span', { class: 'help' });
  const saveBtn = el('button', { type: 'button', class: 'secondary section-save', onclick: save }, 'Save');
  async function save() {
    status.textContent = ''; saveBtn.disabled = true;
    try {
      const rid = await getRunId();
      const liquidPayload = liquidQc.getPayload();
      const sepPayload = {
        startedAt: startedAt.value || null,
        flowrateLpm: flowrateInp.value.trim() === '' ? null : qcParseValue(flowrateInp.value),
        meshMicron: meshInp.value.trim() === '' ? null : qcParseValue(meshInp.value),
        wetSolidsWtKg: wetSolidsWtInp.value.trim() === '' ? null : qcParseValue(wetSolidsWtInp.value),
        pctMoisture: moistureInp.value.trim() === '' ? null : qcParseValue(moistureInp.value),
        pctMoistureScrew: moistureScrewInp.value.trim() === '' ? null : qcParseValue(moistureScrewInp.value),
        liquidQcPh: liquidPayload.qcPh, liquidTdsPct: liquidPayload.tdsPct,
        liquidBrixPct: liquidPayload.brixPct, liquidMannitolPct: liquidPayload.mannitolPct,
        liquidTsLiquidPct: liquidPayload.tsLiquidPct, liquidRhoLiquidGMl: liquidPayload.rhoLiquidGMl,
        solidsSampleCollectedAt: solidsCollectedInp.value || null,
      };
      await api('PUT', '/production/' + rid + '/stages/separation', sepPayload);
      // keep the shared stages.separation object current and let Pasteurization's dilution plan
      // re-read the filtrate TDS right now, not just next time the window is opened
      Object.assign(values, sepPayload);
      if (onSaved) onSaved();
      status.textContent = 'Saved.';
    } catch (e) { status.textContent = e.message; }
    saveBtn.disabled = false;
  }
  return el('details', { class: 'accordion' }, el('summary', {}, 'Separation'),
    el('div', { class: 'accordion-body' },
      el('div', { class: 'qc-check-section-title', style: 'margin-top:0' }, 'Start Conditions'),
      el('div', { class: 'form-row' }, rfield('separation', 'startedAt', 'Started at', startedAt)),
      el('div', { class: 'form-row-compact' },
        rfield('separation', 'flowrateLpm', 'Flow rate (L/min)', flowrateInp),
        rfield('separation', 'meshMicron', 'Mesh size (micron)', meshInp)),
      el('div', { class: 'qc-check-section-title' }, 'Solids Out'),
      rfield('separation', 'wetSolidsWtKg', 'Total wet-solids weight (kg)', wetSolidsWtInp),
      solidsQcBox,
      solidsSamplePointBox,
      el('div', { class: 'qc-check-section-title' }, 'Liquid Out'),
      liquidQc.box,
      el('div', { style: 'margin-top:6px' }, saveBtn, status)));
}
// Homogenization: "Homogenization In" groups the input fields (start time,
// water/slurry inputs, its Process Check) and "Homogenization Out" groups
// the output fields (dilution target, QC Check, Sample Point) -- one
// accordion with section-title-divided groups and a single shared Save
// button, same formatting as Extraction's Start Conditions/Extraction Out.
function buildHomogenizationSection(getRunId, values, samplePoints, processingLot) {
  values = values || {};
  const startedAt = el('input', { type: 'datetime-local', value: values.startedAt || '' });

  const rinsingInp = el('input', { inputmode: 'decimal', placeholder: 'Measured using garden hose' }); attachNumericMask(rinsingInp, 2);
  if (values.rinsingWaterL != null) rinsingInp.value = formatQcValue(values.rinsingWaterL, 2);
  // Pre-Dilution Tank Level (L) is also V1, the starting volume the Recommended Dilution
  // Water calc (in the Output section, below) works from.
  const tankInp = el('input', { inputmode: 'decimal', placeholder: 'Measured using level sensor' }); attachNumericMask(tankInp, 2);
  if (values.slurryL != null) tankInp.value = formatQcValue(values.slurryL, 2);
  // Process Check (Solids loading): Measured %Solids Loading, (w/w) = Wet-solids-wt /
  // (Wet-solids-wt + Liquid-wt) -- shown as a percentage, but kept internally
  // as the raw 0-1 ratio so the Output section's dilution calc (which reuses
  // this function) can use it directly.
  const wetInp = el('input', { inputmode: 'decimal', placeholder: 'g' });
  attachNumericMask(wetInp, 2);
  if (values.wetSolidsWtG != null) wetInp.value = formatQcValue(values.wetSolidsWtG, 2);
  const liquidInp = el('input', { inputmode: 'decimal', placeholder: 'g' });
  attachNumericMask(liquidInp, 2);
  if (values.liquidWtG != null) liquidInp.value = formatQcValue(values.liquidWtG, 2);
  function calcPctWetSolids() {
    const w = wetInp.value.trim() === '' ? null : qcParseValue(wetInp.value);
    const l = liquidInp.value.trim() === '' ? null : qcParseValue(liquidInp.value);
    return (w != null && l != null && (w + l) > 0) ? w / (w + l) : null;
  }
  function fmtPct1(ratio) { return ratio != null ? (ratio * 100).toFixed(1) + '%' : '—'; }
  const resultValue = el('span', { class: 'qc-check-result-value' }, fmtPct1(values.pctWetSolids));
  [wetInp, liquidInp].forEach(inp => inp.addEventListener('input', () => {
    resultValue.textContent = fmtPct1(calcPctWetSolids());
    refreshDilutionTarget();
  }));
  const processCheck1Box = el('div', { class: 'qc-check-box' },
    el('div', { class: 'qc-check-title' }, 'Process Check'),
    el('div', { class: 'qc-check-subtitle' }, 'Solids loading'),
    el('div', { class: 'form-row' },
      rfield('homogenization', 'wetSolidsWtG', 'Wet-solids-wt (g)', wetInp),
      rfield('homogenization', 'liquidWtG', 'Liquid-wt (g)', liquidInp)),
    el('div', { class: 'qc-check-result' },
      el('span', { class: 'qc-check-result-label' }, 'Measured %Solids Loading, (w/w)'), resultValue),
    sopLinkEl('wet_solids_sop'));

  // Output: the Target %Solids Loading, (w/w) to dilute the tank down to, and the
  // Recommended Dilution Water (L) to get there. Mass-conservation dilution
  // (c1V1 = c2V2): c1 = Measured %Solids Loading, V1 = Tank level (L), c2 = target,
  // so V2 = V1 x c1 / c2 and the water to add is V2 - V1 = V1 x (c1/c2 - 1) --
  // 0 when the measured loading is already at or below the target.
  const targetPctInp = el('input', { inputmode: 'decimal', placeholder: '%' }); attachNumericMask(targetPctInp, 1);
  targetPctInp.value = values.targetPctWetSolids != null ? formatQcValue(values.targetPctWetSolids, 1)
    : formatQcValue(settingValue('homog_default_target_pct_wet_solids', 50.0), 1);
  // Shown with an explicit "%" suffix beside the input, not just implied by
  // the label, so the value's units are unambiguous at a glance.
  const targetPctField = el('div', { style: 'display:flex;align-items:center;gap:6px' },
    targetPctInp, el('span', { class: 'help' }, '%'));
  // Shown large once calculable, rounded to the nearest 10 L with no decimals --
  // an operator adds a round number, not a precise fraction of a litre.
  const dilutionTargetValue = el('span', { class: 'help' });
  function calcDilutionTarget() {
    const c1 = calcPctWetSolids();
    const c2Raw = targetPctInp.value.trim() === '' ? null : qcParseValue(targetPctInp.value);
    const v1 = tankInp.value.trim() === '' ? null : qcParseValue(tankInp.value);
    if (c1 == null || !c2Raw || v1 == null) return null;
    const c2 = c2Raw / 100;
    if (c1 <= c2) return 0;
    return v1 * c1 / c2 - v1;
  }
  function refreshDilutionTarget() {
    const v = calcDilutionTarget();
    dilutionTargetValue.className = v != null ? 'qc-check-result-value' : 'help';
    dilutionTargetValue.textContent = v != null ? fmt(Math.round(v / 10) * 10, 0) + ' L'
      : 'Needs the tank level, Process Check and target';
  }
  refreshDilutionTarget();
  targetPctInp.addEventListener('input', refreshDilutionTarget);
  tankInp.addEventListener('input', refreshDilutionTarget);

  const dilutionInp = el('input', { inputmode: 'decimal', placeholder: 'Measured using dilution totalizer' }); attachNumericMask(dilutionInp, 2);
  if (values.dilutionWaterL != null) dilutionInp.value = formatQcValue(values.dilutionWaterL, 2);
  const postTankInp = el('input', { inputmode: 'decimal', placeholder: 'Measured using level sensor' }); attachNumericMask(postTankInp, 2);
  if (values.postDilutionTankL != null) postTankInp.value = formatQcValue(values.postDilutionTankL, 2);
  // Lot %Solids Loading, (w/w) (calculated): the measured loading after dilution. Mass
  // balance c1V1 = c2V2 with V2 = the final (Post-Dilution) tank volume and V1 = V2 minus
  // the dilution water added, so lot %solids = measured x (V2 - water) / V2.
  const lotPctValue = el('span', { class: 'help' });
  function calcLotPctSolids() {
    const c1 = calcPctWetSolids();
    const water = dilutionInp.value.trim() === '' ? null : qcParseValue(dilutionInp.value);
    const v2 = postTankInp.value.trim() === '' ? null : qcParseValue(postTankInp.value);
    if (c1 == null || water == null || !v2 || water > v2) return null;
    return c1 * (v2 - water) / v2;
  }
  function refreshLotPct() {
    const v = calcLotPctSolids();
    lotPctValue.className = v != null ? 'qc-check-result-value' : 'help';
    lotPctValue.textContent = v != null ? (v * 100).toFixed(1) + '%'
      : 'Needs the Process Check, water added and final level';
  }
  [wetInp, liquidInp, dilutionInp, postTankInp].forEach(inp => inp.addEventListener('input', refreshLotPct));
  refreshLotPct();

  // QC Check (lot characterization): liquid-phase and slurry/solids-phase
  // readings, each its own compact wrapping row -- shared with Extraction
  // Out, which uses the same card under its own subtitle.
  const qcCheck = buildQualityCheckBox('Lot characterization', values, { showSolidsLoading: true, reqStage: 'homogenization' });
  const qcCheckBox = qcCheck.box;

  // Sample Point (lot input): a repeatable table of samples taken at this
  // point in the process -- see buildSamplePointsSection, below.
  const collectedInp = el('input', { type: 'datetime-local',
    value: values.sampleCollectedAt ? values.sampleCollectedAt.replace('Z', '').slice(0, 16) : '' });
  const samplePointBox = el('div', { class: 'qc-check-box theme-sample' },
    el('div', { class: 'qc-check-title' }, 'Sample Point'),
    el('div', { class: 'qc-check-subtitle' }, 'Lot input'),
    field('Collection date and time', collectedInp),
    buildSamplePointsSection(samplePoints, getRunId, processingLot, () => collectedInp.value, 'homogenization'));

  const status = el('span', { class: 'help' });
  const saveBtn = el('button', { type: 'button', class: 'secondary section-save', onclick: save }, 'Save');
  async function save() {
    status.textContent = ''; saveBtn.disabled = true;
    try {
      const rid = await getRunId();
      await api('PUT', '/production/' + rid + '/stages/homogenization', {
        startedAt: startedAt.value || null,
        rinsingWaterL: rinsingInp.value.trim() === '' ? null : qcParseValue(rinsingInp.value),
        slurryL: tankInp.value.trim() === '' ? null : qcParseValue(tankInp.value),
        wetSolidsWtG: wetInp.value.trim() === '' ? null : qcParseValue(wetInp.value),
        liquidWtG: liquidInp.value.trim() === '' ? null : qcParseValue(liquidInp.value),
        pctWetSolids: calcPctWetSolids(),
        targetPctWetSolids: targetPctInp.value.trim() === '' ? null : qcParseValue(targetPctInp.value),
        dilutionWaterTargetL: calcDilutionTarget(),
        dilutionWaterL: dilutionInp.value.trim() === '' ? null : qcParseValue(dilutionInp.value),
        postDilutionTankL: postTankInp.value.trim() === '' ? null : qcParseValue(postTankInp.value),
        lotPctSolids: calcLotPctSolids(),
        ...qcCheck.getPayload(),
        sampleCollectedAt: collectedInp.value || null,
      });
      status.textContent = 'Saved.';
    } catch (e) { status.textContent = e.message; }
    saveBtn.disabled = false;
  }
  return el('details', { class: 'accordion' }, el('summary', {}, 'Homogenization'),
    el('div', { class: 'accordion-body' },
      el('div', { class: 'qc-check-section-title', style: 'margin-top:0' }, 'Homogenization In'),
      el('div', { class: 'form-row' }, rfield('homogenization', 'startedAt', 'Started at', startedAt)),
      el('div', { class: 'form-row' },
        rfield('homogenization', 'rinsingWaterL', 'Rinse water (L)', rinsingInp),
        rfield('homogenization', 'slurryL', 'Pre-Dilution Tank Level (L)', tankInp)),
      processCheck1Box,
      el('div', { class: 'qc-check-section-title' }, 'Homogenization Out'),
      // Dilution card: the target and the water recommended for it, then what was actually
      // added and the resulting tank level, then the lot %solids that follows from them.
      el('div', { class: 'qc-check-box' },
        el('div', { class: 'qc-check-title' }, 'Dilution'),
        el('div', { class: 'qc-check-subtitle' }, 'Target, water added and final volume'),
        el('div', { class: 'form-row' },
          rfield('homogenization', 'targetPctWetSolids', 'Target %Solids Loading, (w/w)', targetPctField),
          field('Recommended Dilution Water (L)', el('div', { class: 'calc-tile' }, dilutionTargetValue))),
        el('div', { class: 'form-row' },
          rfield('homogenization', 'dilutionWaterL', 'Dilution water added (L)', dilutionInp),
          rfield('homogenization', 'postDilutionTankL', 'Post-Dilution Tank Level (L)', postTankInp)),
        el('div', { class: 'qc-check-result' },
          el('span', { class: 'qc-check-result-label' }, 'Lot %Solids Loading, (w/w) (calculated)'), lotPctValue)),
      qcCheckBox,
      samplePointBox,
      el('div', { style: 'margin-top:6px' }, saveBtn, status)));
}

const SAMPLE_TYPES = ['Slurry', 'Liquid', 'Solid'];
const SAMPLE_DESCRIPTIONS = ['Microbial', 'Retention', 'Metals & Nutrients', 'Proximate Analysis', 'R&D', 'Other'];
// Sample Point container options are the container consumables flagged
// isSampleContainer (Inventory Items -> Packaging); picking one here
// live-adjusts that container's on-hand stock (see update_sample_point).
function sampleContainerOptions() {
  const names = (State.ref.containers || []).filter(c => c.isSampleContainer).map(c => c.name);
  return names.length ? names : [''];
}
// The Sample Point table itself: a repeatable list of samples (Type/
// Description/Qty/Container), each row saved immediately on add/edit/remove
// via its own run_sample_points row (mirrors buildDilutionsSection). Labels are
// not printed from here -- use the run's "Print sample labels" button on the
// Production tab. A run can have more than one
// Sample Point box (Homogenization, Separation Solids Out, ...); `stage`
// tags which one this instance is, and every mutation re-filters the
// server's full (all-stages) sample-points list back down to just this
// box's rows -- untagged legacy rows count as 'homogenization'.
function buildSamplePointsSection(initial, getRunId, processingLot, getCollectedAt, stage) {
  function filterStage(list) { return (list || []).filter(s => (s.stage || 'homogenization') === stage); }
  let items = filterStage(initial);
  const tbody = el('tbody', {});
  const status = el('div', { class: 'help' });
  function selectCell(options, value, onChange) {
    const sel = el('select', {}, ...options.map(o => el('option', { value: o }, o)));
    sel.value = options.includes(value) ? value : (options[0] || '');
    sel.addEventListener('change', onChange);
    return sel;
  }
  async function patch(id, payload) {
    try {
      const rid = await getRunId();
      const r = await api('PUT', '/production/' + rid + '/sample-points/' + id, payload);
      items = filterStage(r.samplePoints);
      status.textContent = '';
      // description / type changes set a default qty + container (Microbial: 1 x 50 mL falcon tube;
      // Metals & Nutrients: 2 x 50 mL falcon tube; Solid: 100 g sample bag) -- show what was saved
      if ('description' in payload || 'type' in payload) draw();
    } catch (e) { status.textContent = e.message; }
  }
  function draw() {
    tbody.innerHTML = '';
    if (!items.length) {
      tbody.append(el('tr', {}, el('td', { colspan: 6, class: 'empty' }, 'No samples added yet.')));
    }
    items.forEach(it => {
      const typeSel = selectCell(SAMPLE_TYPES, it.type, () => patch(it.id, { type: typeSel.value }));
      const descSel = selectCell(SAMPLE_DESCRIPTIONS, it.description, () => patch(it.id, { description: descSel.value }));
      const qtyInp = el('input', { type: 'number', min: '1', max: '10', value: String(it.qty || 1) });
      qtyInp.addEventListener('change', () => {
        const q = Math.max(1, Math.min(10, +qtyInp.value || 1));
        qtyInp.value = String(q);
        patch(it.id, { qty: q });
      });
      const containerSel = selectCell(sampleContainerOptions(), it.container, () => patch(it.id, { container: containerSel.value }));
      const removeBtn = el('button', {
        type: 'button', class: 'icon-btn remove', title: 'Remove sample', onclick: async () => {
          if (!confirm('Remove this sample?')) return;
          const rid = await getRunId();
          const r = await api('DELETE', '/production/' + rid + '/sample-points/' + it.id);
          items = filterStage(r.samplePoints); draw();
        }
      }, '−');
      tbody.append(el('tr', {},
        el('td', {}, typeSel), el('td', {}, descSel), el('td', {}, qtyInp), el('td', {}, containerSel),
        el('td', { style: 'display:flex;gap:6px;justify-content:flex-end' }, removeBtn)));
    });
  }
  draw();
  const addBtn = el('button', {
    type: 'button', class: 'icon-btn add', title: 'Add sample', onclick: async () => {
      try {
        const rid = await getRunId();
        const r = await api('POST', '/production/' + rid + '/sample-points', { stage });
        items = filterStage(r.samplePoints); draw();
      } catch (e) { status.textContent = e.message; }
    }
  }, '+');
  return el('div', {},
    el('table', { class: 'qc-check-checklist' },
      el('thead', {}, el('tr', {}, el('th', {}, 'Type'), el('th', {}, 'Description'), el('th', {}, 'Qty'),
        el('th', {}, 'Container'), el('th', {}, ''))),
      tbody),
    el('div', { style: 'margin-top:8px' }, addBtn),
    stage === 'packaging' ? el('div', { class: 'help' }, reqLabel('At least one sample is required')) : null, status);
}

// Packaging: a run may package output across several container units and
// quantities -- same add/remove-row format as Sample Point, but with just a
// "Container unit"/Qty pair per row (no type/description, no print). The
// box's single "Packaging date and time" applies to the whole table and
// lives on production_runs (packaging_packaged_at) instead, saved alongside
// it by the Packaging accordion's own Save button. "Container unit" options
// are the container consumables (Inventory Items -> Packaging) that
// have a litres-each value, e.g. IBC = 1000 L. Freely adding/editing/
// removing rows here never touches container stock by itself -- only
// clicking the Packaging accordion's own Save button (or finalize) commits
// the *net* change per container as a single ledger entry (see the backend's
// _commit_packaging_stock); a container whose total is unchanged since the
// last save needs no entry at all. This table's entries are also what
// finalize reads to create this run's Finished Goods lots (see `hasEntries`,
// used for the "enter at least one packaged output quantity" check).
function buildPackagingEntriesSection(initial, getRunId) {
  let items = (initial || []).slice();
  const tbody = el('tbody', {});
  const status = el('div', { class: 'help' });
  function unitOptions() {
    const names = (State.ref.containers || []).filter(c => c.litresEach != null).map(c => c.name);
    return names.length ? names : [''];
  }
  async function patch(id, payload) {
    try {
      const rid = await getRunId();
      const r = await api('PUT', '/production/' + rid + '/packaging-entries/' + id, payload);
      items = r.packagingEntries;
      status.textContent = '';
    } catch (e) {
      // The server rolled the change back (e.g. not enough stock, or units already
      // shipped): show what is actually saved, not what was typed.
      status.textContent = e.message;
      try { const rid = await getRunId(); items = (await api('GET', '/production/' + rid + '/packaging-entries')).packagingEntries; draw(); } catch (_) { /* keep message */ }
    }
  }
  function draw() {
    tbody.innerHTML = '';
    if (!items.length) {
      tbody.append(el('tr', {}, el('td', { colspan: 3, class: 'empty' }, 'No packaging entries added yet.')));
    }
    items.forEach(it => {
      const options = unitOptions();
      const unitSel = el('select', {}, ...options.map(o => el('option', { value: o }, o)));
      unitSel.value = options.includes(it.containerUnit) ? it.containerUnit : (options[0] || '');
      unitSel.addEventListener('change', () => patch(it.id, { containerUnit: unitSel.value }));
      const qtyInp = el('input', { type: 'number', min: '0', step: 'any', value: String(it.qty ?? 1) });
      qtyInp.addEventListener('change', () => patch(it.id, { qty: +qtyInp.value || 0 }));
      const removeBtn = el('button', {
        type: 'button', class: 'icon-btn remove', title: 'Remove entry', onclick: async () => {
          if (!confirm('Remove this packaging entry?')) return;
          const rid = await getRunId();
          const r = await api('DELETE', '/production/' + rid + '/packaging-entries/' + it.id);
          items = r.packagingEntries; draw();
        }
      }, '−');
      tbody.append(el('tr', {}, el('td', {}, unitSel), el('td', {}, qtyInp),
        el('td', { style: 'text-align:right' }, removeBtn)));
    });
  }
  draw();
  const addBtn = el('button', {
    type: 'button', class: 'icon-btn add', title: 'Add packaging entry', onclick: async () => {
      try {
        const rid = await getRunId();
        const r = await api('POST', '/production/' + rid + '/packaging-entries', {});
        items = r.packagingEntries; draw();
      } catch (e) { status.textContent = e.message; }
    }
  }, '+');
  return {
    box: el('div', {},
      el('div', { class: 'help' }, reqLabel('At least one packaged output quantity is required')),
      el('table', { class: 'qc-check-checklist' },
        el('thead', {}, el('tr', {}, el('th', {}, 'Container unit'), el('th', {}, 'Qty'), el('th', {}, ''))),
        tbody),
      el('div', { style: 'margin-top:8px' }, addBtn), status),
    hasEntries: () => items.some(it => (it.qty || 0) > 0)
  };
}

const ODOUR_INTENSITIES = ['', 'Mild', 'Medium', 'Strong'];
// REF_odour.pdf — the standard odour vocabulary offered in the Feedstock
// dropdown; "Other" reveals a free-text field for anything not on this list.
const REF_ODOURS = ['Marine', 'Sweet (Apple Juice)', 'Sulfuric (Rotten Eggs)', 'Butyric (Sour Milk, Parmesan Cheese)', 'Other'];
// Reads an admin-editable calculation constant (see the Calculations page)
// from State.ref.settings, falling back to a sane default if it hasn't
// loaded yet or the key is somehow missing.
function settingValue(key, fallback) {
  const s = State.ref && State.ref.settings && State.ref.settings[key];
  return s && s.value != null ? s.value : fallback;
}
// REF_ORP_classification.pdf — ORP (mV) is classified into one of these
// bands; the "ORP meter range" field is calculated from this, never typed
// by hand. The 3 boundaries between bands are admin-editable settings (see
// the Calculations page); -400/400 are hardcoded sanity bounds on a real
// meter reading, not a tunable calculation constant.
function classifyOrp(mv) {
  if (mv == null || Number.isNaN(mv)) return null;
  if (mv < -400 || mv > 400) return 'Out of range';
  const b1 = settingValue('orp_spoiled_below', -200);
  const b2 = settingValue('orp_spoilage_underway_below', -50);
  const b3 = settingValue('orp_watch_closely_below', 0);
  if (mv < b1) return 'Spoiled';
  if (mv < b2) return 'Spoilage underway';
  if (mv < b3) return 'Watch closely';
  return 'Stable / safe zone';
}
// REF_operators.pdf — the plant's operator roster, used for the Initiation
// "Operators" field and any other operator picker.
const REF_OPERATORS = [
  { last: 'Pedde', first: 'Dan', initials: 'DP' },
  { last: 'Llewellyn', first: 'Andrew', initials: 'AL' },
  { last: 'Wrana', first: 'Nathan', initials: 'NW' },
  { last: 'Boire', first: 'Dalton', initials: 'DB' },
  { last: 'Kinsman', first: 'Cam', initials: 'CK' },
  { last: 'Obee', first: 'Matt', initials: 'MO' },
  { last: 'Martin', first: 'Sean', initials: 'SM' },
  { last: 'Claxton', first: 'Adam', initials: 'AC' },
  { last: 'Ismael', first: 'Imronn', initials: 'II' },
  { last: 'Clark', first: 'Jared', initials: 'JC' },
];
// Facilities a production run can be logged at. Only one today (Port Edward),
// kept as its own list — separate from the general warehouse/tote `locations`
// reference data — so more facilities can be added here later without
// touching storage-location pickers elsewhere in the app.
const PRODUCTION_LOCATIONS = ['Port Edward Facility'];
function productionLocationSelect(id, currentValue) {
  const sel = selectFrom('', PRODUCTION_LOCATIONS.map(l => [l, l]), null, id);
  sel.value = PRODUCTION_LOCATIONS.includes(currentValue) ? currentValue : PRODUCTION_LOCATIONS[0];
  return sel;
}
// Generic multi-select checkbox dropdown (Operators, Odour, ...): a floating
// panel of checkboxes plus a free-text "Other" field, all folded into one
// comma-joined value so it round-trips through a single existing text column
// unchanged. `items` is [{value, label, aliases}] — aliases are the lowercase
// strings an existing comma-joined value may match against, so old data
// (e.g. a bare initials or a full odour name) keeps parsing correctly.
function buildMultiSelectDropdown(items, initialValue, opts) {
  opts = opts || {};
  const known = new Set();
  let otherText = '';
  (initialValue || '').split(',').map(s => s.trim()).filter(Boolean).forEach(tok => {
    const low = tok.toLowerCase();
    const match = items.find(it => (it.aliases || [it.value.toLowerCase()]).includes(low));
    if (match) known.add(match.value); else otherText = otherText ? otherText + ', ' + tok : tok;
  });
  const btn = el('button', { type: 'button', class: 'qc-loc-select-btn' });
  const panel = el('div', { class: 'qc-loc-panel hidden' });
  const wrap = el('div', { class: 'qc-loc-select' }, btn, panel);
  const otherInput = el('input', { placeholder: opts.otherPlaceholder || 'Other', value: otherText });
  function currentValue() {
    const parts = items.filter(it => known.has(it.value)).map(it => it.value);
    if (otherInput.value.trim()) parts.push(otherInput.value.trim());
    return parts.join(', ');
  }
  function renderBtn() {
    btn.innerHTML = '';
    btn.append(el('span', {}, currentValue() || opts.placeholder || 'Select…'), el('span', { class: 'qc-loc-caret' }, '▾'));
  }
  function onDocClick(e) { if (!wrap.contains(e.target)) close(); }
  function open() {
    panel.innerHTML = '';
    items.forEach(it => {
      const cb = el('input', { type: 'checkbox', style: 'width:auto;flex:none' });
      cb.checked = known.has(it.value);
      cb.addEventListener('change', () => { cb.checked ? known.add(it.value) : known.delete(it.value); renderBtn(); });
      panel.append(el('label', { class: 'qc-loc-option', style: 'display:flex;align-items:center;gap:8px;cursor:pointer' },
        cb, it.label));
    });
    panel.append(el('div', { class: 'qc-loc-option' }, field(opts.otherFieldLabel || 'Other', otherInput)));
    panel.classList.remove('hidden');
    document.addEventListener('click', onDocClick, true);
  }
  function close() { panel.classList.add('hidden'); document.removeEventListener('click', onDocClick, true); }
  btn.addEventListener('click', () => { panel.classList.contains('hidden') ? open() : close(); });
  otherInput.addEventListener('input', renderBtn);
  renderBtn();
  return { el: wrap, get value() { return currentValue(); } };
}
// Multi-select operator picker (a run usually has more than one). Stores/reads
// a comma-separated string of initials (+ any free-text "Other" names) so it
// round-trips through the existing `operators` text column unchanged.
function buildOperatorsSelect(initialValue) {
  const items = REF_OPERATORS.map(o => ({
    value: o.initials, label: o.first + ' ' + o.last + ' (' + o.initials + ')',
    aliases: [o.initials.toLowerCase(), (o.first + ' ' + o.last).toLowerCase(), o.last.toLowerCase()]
  }));
  return buildMultiSelectDropdown(items, initialValue,
    { placeholder: 'Select operators…', otherPlaceholder: 'Other operator name(s)' });
}

// One tote's receiving-inspection card: photos, pH/ORP/odour readings and an
// accept/reject decision. `mode: 'draft'` keeps edits in memory (bundled into
// the outer save/finalize payload); `mode: 'completed'` saves immediately via
// its own Save button, since the run may already be finalized.
function buildFeedstockCard(opts) {
  const v = Object.assign({ loadedAt: '', ph: null, phMeasuredAt: null, orp: null, orpRange: '', odour: '',
    odourIntensity: '', weightKg: null, volumeL: null, densityKgL: null,
    decision: opts.markRequired ? '' : 'accepted', rejectionReason: '', notes: '',
    surfacePhotoId: null, striationPhotoId: null }, opts.initial || {});

  const loadedAt = el('input', { type: 'datetime-local', value: v.loadedAt ? v.loadedAt.replace('Z', '').slice(0, 16) : '' });
  const phInp = el('input', { inputmode: 'decimal', placeholder: 'pH' }); attachNumericMask(phInp, 2);
  if (v.ph != null) phInp.value = formatQcValue(v.ph, 2);
  // Automated, not operator-entered: stamped the instant the pH value changes,
  // so it always reflects when the reading was actually last taken.
  const phMeasuredNote = el('span', { class: 'help' }, v.phMeasuredAt ? 'Last measured: ' + fmtWhen(v.phMeasuredAt) : 'Not yet measured');
  const orpInp = el('input', { inputmode: 'decimal', placeholder: 'mV (can be negative)' }); attachNumericMask(orpInp, 0, true);
  if (v.orp != null) orpInp.value = formatQcValue(v.orp, 0);
  // Calculated from REF_ORP_classification.pdf — never typed by hand.
  const orpRangeNote = el('span', { class: 'help' }, v.orpRange || classifyOrp(v.orp) || 'Enter ORP to classify');
  const weightInp = el('input', { inputmode: 'decimal', placeholder: 'kg' }); attachNumericMask(weightInp, 1);
  if (v.weightKg != null) weightInp.value = formatQcValue(v.weightKg, 1);
  const volumeInp = el('input', { inputmode: 'decimal', placeholder: 'L' }); attachNumericMask(volumeInp, 1);
  if (v.volumeL != null) volumeInp.value = formatQcValue(v.volumeL, 1);
  function calcDensity() {
    const w = weightInp.value.trim() === '' ? null : qcParseValue(weightInp.value);
    const vol = volumeInp.value.trim() === '' ? null : qcParseValue(volumeInp.value);
    return (w != null && vol) ? Math.round((w / vol) * 1000) / 1000 : null;
  }
  const densityNote = el('span', { class: 'help' }, v.densityKgL != null ? formatQcValue(v.densityKgL, 3) + ' kg/L' : 'Enter weight & volume to calculate');
  const odourMultiSelect = buildMultiSelectDropdown(
    REF_ODOURS.filter(o => o !== 'Other').map(o => ({ value: o, label: o })), v.odour,
    { placeholder: 'Select odour(s)…', otherPlaceholder: 'Other odour', otherFieldLabel: 'Other odour' });
  const intensitySel = el('select', {}, ...ODOUR_INTENSITIES.map(i => el('option', { value: i }, i || '—')));
  intensitySel.value = v.odourIntensity || '';
  // In a production log the accept/reject decision is a required, explicit choice
  // (no preselected answer); Feedstock Inventory's own card keeps its default.
  const decisionSel = el('select', {}, ...(opts.markRequired ? [el('option', { value: '' }, 'Select…')] : []),
    el('option', { value: 'accepted' }, 'Accepted'), el('option', { value: 'rejected' }, 'Rejected'));
  decisionSel.value = v.decision || (opts.markRequired ? '' : 'accepted');
  const reasonInp = el('input', { placeholder: 'Reason for rejection', value: v.rejectionReason || '' });
  const reasonField = field('Rejection reason', reasonInp);
  reasonField.classList.toggle('hidden', decisionSel.value !== 'rejected');
  const notesInp = el('textarea', { rows: '2', placeholder: 'Notes' }, v.notes || '');

  function currentValues() {
    return {
      loadedAt: loadedAt.value || null,
      ph: phInp.value.trim() === '' ? null : qcParseValue(phInp.value),
      phMeasuredAt: v.phMeasuredAt,
      orp: orpInp.value.trim() === '' ? null : qcParseValue(orpInp.value),
      orpRange: classifyOrp(orpInp.value.trim() === '' ? null : qcParseValue(orpInp.value)),
      weightKg: weightInp.value.trim() === '' ? null : qcParseValue(weightInp.value),
      volumeL: volumeInp.value.trim() === '' ? null : qcParseValue(volumeInp.value),
      densityKgL: calcDensity(),
      odour: odourMultiSelect.value || null,
      odourIntensity: intensitySel.value || null,
      decision: decisionSel.value || null,
      rejectionReason: reasonInp.value.trim() || null,
      notes: notesInp.value.trim() || null,
      surfacePhotoId: v.surfacePhotoId, striationPhotoId: v.striationPhotoId
    };
  }
  function notifyChange() { if (opts.onChange) opts.onChange(currentValues()); }
  phInp.addEventListener('change', () => {
    v.phMeasuredAt = phInp.value.trim() === '' ? null : new Date().toISOString();
    phMeasuredNote.textContent = v.phMeasuredAt ? 'Last measured: ' + fmtWhen(v.phMeasuredAt) : 'Not yet measured';
    notifyChange();
  });
  orpInp.addEventListener('input', () => {
    orpRangeNote.textContent = classifyOrp(orpInp.value.trim() === '' ? null : qcParseValue(orpInp.value)) || 'Enter ORP to classify';
  });
  orpInp.addEventListener('change', notifyChange);
  [weightInp, volumeInp].forEach(inp => inp.addEventListener('input', () => {
    const d = calcDensity();
    densityNote.textContent = d != null ? formatQcValue(d, 3) + ' kg/L' : 'Enter weight & volume to calculate';
  }));
  odourMultiSelect.el.addEventListener('change', notifyChange);
  decisionSel.addEventListener('change', () => { reasonField.classList.toggle('hidden', decisionSel.value !== 'rejected'); notifyChange(); });
  [loadedAt, weightInp, volumeInp, intensitySel, notesInp]
    .forEach(inp => inp.addEventListener('change', notifyChange));

  function photoSlot(slotKey, label) {
    const img = el('img', {});
    if (v[slotKey] && opts.photoUrl) img.src = opts.photoUrl(v[slotKey]); else img.style.display = 'none';
    const fileInput = el('input', { type: 'file', accept: 'image/*', style: 'display:none' });
    // capture="environment" hands off to the rear camera on phones/tablets.
    const cameraInput = el('input', { type: 'file', accept: 'image/*', capture: 'environment', style: 'display:none' });
    const status = el('span', { class: 'help' });
    async function handle(file) {
      if (!file) return;
      const reader = new FileReader();
      reader.onload = async () => {
        img.src = String(reader.result); img.style.display = '';
        status.textContent = 'Uploading…';
        try {
          const b64 = String(reader.result).split(',')[1];
          v[slotKey] = await opts.uploadPhoto(slotKey === 'surfacePhotoId' ? 'surface' : 'striation', file, b64);
          status.textContent = '';
          notifyChange();
        } catch (e) { status.textContent = e.message; }
      };
      reader.readAsDataURL(file);
    }
    fileInput.addEventListener('change', () => { handle(fileInput.files[0]); fileInput.value = ''; });
    cameraInput.addEventListener('change', () => { handle(cameraInput.files[0]); cameraInput.value = ''; });
    const photoKey = slotKey === 'surfacePhotoId' ? 'surfacePhoto' : 'striationPhoto';
    return el('div', { class: 'photo-slot', style: 'flex:1 1 160px' },
      el('div', { class: 'help' }, opts.markRequired && isReq('feedstock', photoKey) ? reqLabel(label) : label), img, fileInput, cameraInput,
      el('div', { style: 'display:flex;gap:6px;margin-top:4px' },
        el('button', { type: 'button', class: 'secondary', onclick: () => fileInput.click() }, 'Upload'),
        el('button', { type: 'button', class: 'secondary', onclick: () => cameraInput.click() }, '📷 Photo')),
      status);
  }

  // Feedstock Inventory's own Details card omits pH/ORP entirely -- those
  // live in the "Current pH/ORP" summary and quick-update fields just above
  // it in that modal instead, so the same reading isn't captured twice.
  const omit = opts.omit || [];
  // opts.markRequired (production-log callers only -- not Feedstock Inventory's
  // own Details card) asterisks the fields the finalize check requires.
  const fl = (key, label, control) => opts.markRequired && isReq('feedstock', key) ? field(reqLabel(label), control) : field(label, control);
  const allFields = [
    ['loadedAt', fl('loadedAt', 'Loaded at', loadedAt)],
    ['ph', fl('ph', 'pH', el('div', {}, phInp, phMeasuredNote))],
    ['orp', fl('orp', 'ORP (mV)', orpInp)],
    ['orpRange', field('ORP meter range (calculated)', orpRangeNote)],
    ['weightKg', fl('weightKg', 'Weight (kg)', weightInp)],
    ['volumeL', fl('volumeL', 'Volume (L)', volumeInp)],
    ['densityKgL', field('Density (calculated)', densityNote)],
    ['odour', fl('odour', 'Odour', odourMultiSelect.el)],
    ['odourIntensity', fl('odourIntensity', 'Odour intensity', intensitySel)],
    ['decision', fl('decision', 'Decision', decisionSel)],
  ];
  const fieldsRow = el('div', { class: 'form-row' },
    ...allFields.filter(([key]) => !omit.includes(key)).map(([, fieldEl]) => fieldEl));
  const bodyEls = [fieldsRow, reasonField, field('Notes', notesInp),
    el('div', { style: 'display:flex;gap:14px;flex-wrap:wrap;margin-top:8px' },
      photoSlot('surfacePhotoId', 'Surface photo'), photoSlot('striationPhotoId', 'Settling / striation photo'))];

  if (opts.onSave) {
    const status = el('span', { class: 'help' });
    const saveBtn = el('button', {
      type: 'button', class: 'secondary', onclick: async () => {
        status.textContent = ''; saveBtn.disabled = true;
        try { await opts.onSave(currentValues()); status.textContent = 'Saved.'; }
        catch (e) { status.textContent = e.message; }
        saveBtn.disabled = false;
      }
    }, 'Save');
    bodyEls.push(el('div', { style: 'margin-top:10px' }, saveBtn, status));
  }
  // Seed the caller's onChange with the initial (possibly prefilled) values
  // right away, not just on the first edit -- so a caller that saves from
  // its own outer button (no per-field Save here) always has a current
  // snapshot to send, even if the user never touches this card at all.
  if (opts.onChange) notifyChange();
  return el('details', { class: 'accordion feedstock-tote' },
    el('summary', {}, opts.label + (v.decision === 'rejected' ? '  ⚠ Rejected' : '')),
    el('div', { class: 'accordion-body' }, ...bodyEls));
}

// Dilution & Preservation -> "Dilution", "Preservatives" and "pH Balancing". Dilution records
// what actually happened against the Dilution plan in Pasteurization In: the product transferred
// from 5A/5B, the dilution water added (measured, e.g. totalizer) and the final level in each
// receiving tank, with checks against the plan (final volume variance, expected TDS). Preservative
// doses are worked out PER TANK from that tank's final volume; the run totals (which drive the
// reagent deduction) are the sums. `getPlan` returns the Pasteurization plan (receiving tanks,
// starting levels, TDS) live. Everything here saves together through the "dilution" stage
// endpoint, called by the section's single Save.
// Reagent type -> inventory items. A run draws each reagent (citric acid, potassium sorbate, sodium benzoate) from the
// inventory item picked here (default: the type's original item), and every field that adds a reagent shows a note
// directly under it when the run's total would exceed what is in stock (the run still proceeds; stock goes negative).
const REAGENT_TYPES = ['Citric Acid', 'Potassium Sorbate', 'Sodium Benzoate'];
function buildReagentWatch(runId, selected) {
  const w = { runId: runId || null, types: {}, commits: {}, selected: Object.assign({}, selected), usage: {}, subs: [], loadSubs: [] };
  const items = t => (w.types[t] && w.types[t].items) || [];
  w.item = t => items(t).find(i => i.id === w.selected[t]) || items(t).find(i => i.id === (w.types[t] || {}).defaultItemId) || null;
  w.itemId = t => { const i = w.item(t); return i ? i.id : null; };
  // what this run can still draw: the item's on-hand plus what this run already deducted from that same item
  w.available = t => { const i = w.item(t); if (!i) return null; const c = w.commits[t]; return i.onHand + (c && c.itemId === i.id ? c.kg : 0); };
  w.total = t => Object.values(w.usage[t] || {}).reduce((a, b) => a + (b || 0), 0);
  w.notify = () => w.subs.forEach(f => f());
  w.setUsage = (t, key, kg) => { (w.usage[t] = w.usage[t] || {})[key] = kg || 0; w.notify(); };
  w.clearUsage = prefix => { Object.values(w.usage).forEach(m => Object.keys(m).forEach(k => { if (k.startsWith(prefix)) delete m[k]; })); w.notify(); };
  w.load = async () => {
    try {
      const r = await api('GET', '/reagents' + (w.runId ? '?runId=' + w.runId : ''));
      w.types = {}; r.types.forEach(t => { w.types[t.type] = t; }); w.commits = r.commits || {};
      w.loadSubs.forEach(f => f()); w.notify();
    } catch (e) { /* the notes simply stay hidden */ }
  };
  w.noteEl = t => {
    const n = el('div', { class: 'stock-note hidden' });
    w.subs.push(() => {
      const it = w.item(t), avail = w.available(t), tot = w.total(t);
      const over = !!it && avail != null && tot > avail + 1e-6;
      n.classList.toggle('hidden', !over);
      n.textContent = over ? '⚠ Exceeds inventory: this run uses ' + fmt(tot, 2) + ' ' + it.unit + ' of ' + it.name + ' but only '
        + fmt(Math.max(avail, 0), 2) + ' ' + it.unit + ' is in stock. The run can continue; inventory will go to ' + fmt(avail - tot, 2) + ' ' + it.unit + '.' : '';
    });
    return n;
  };
  w.itemSelect = t => {
    const sel = el('select', {});
    const rebuild = () => {
      const list = items(t), cur = w.itemId(t);
      sel.innerHTML = '';
      if (!list.length) sel.append(el('option', { value: '' }, 'No ' + t + ' item in Inventory Items'));
      list.forEach(i => sel.append(el('option', { value: i.id }, i.name + (i.itemNumber ? ' (#' + i.itemNumber + ')' : '') + ' — ' + fmt(i.onHand, 1) + ' ' + i.unit + ' on hand')));
      if (cur != null) sel.value = String(cur);
    };
    sel.addEventListener('change', () => { w.selected[t] = sel.value ? +sel.value : null; w.notify(); });
    w.loadSubs.push(rebuild); rebuild();
    return sel;
  };
  return w;
}
function buildDilutionAndPreservativesBox(getRunId, values, getTargetPh, getKsorbateTarget, getPlan, getNabenzoateTarget, runId) {
  values = values || {};
  const numOf = inp => inp.value.trim() === '' ? null : qcParseValue(inp.value);
  const lvl = (v, ph) => { const i = el('input', { inputmode: 'decimal', placeholder: ph || 'Measured using level sensor' }); attachNumericMask(i, 2); if (v != null) i.value = formatQcValue(v, 2); return i; };
  const productInp = lvl(values.productTransferredL, 'From the 5A/5B level drop');
  const waterInp = lvl(values.waterAddedL, 'Measured using dilution totalizer');
  const final6aInp = lvl(values.tank6aFinalL), final6bInp = lvl(values.tank6bFinalL);
  const measuredPhInp = el('input', { inputmode: 'decimal', placeholder: 'pH' }); attachNumericMask(measuredPhInp, 1);
  if (values.measuredPh != null) measuredPhInp.value = formatQcValue(values.measuredPh, 1);
  const citricInp = el('input', { inputmode: 'decimal', placeholder: 'kg' }); attachNumericMask(citricInp, 2);
  if (values.citricKg != null) citricInp.value = formatQcValue(values.citricKg, 2);
  const reagentWatch = buildReagentWatch(runId, { 'Citric Acid': values.citricItemId, 'Potassium Sorbate': values.ksorbateItemId, 'Sodium Benzoate': values.nabenzoateItemId });
  reagentWatch.load();
  const citricNote = reagentWatch.noteEl('Citric Acid'), ksNote = reagentWatch.noteEl('Potassium Sorbate'), nbNote = reagentWatch.noteEl('Sodium Benzoate');
  const citricItemSel = reagentWatch.itemSelect('Citric Acid'), ksItemSel = reagentWatch.itemSelect('Potassium Sorbate'), nbItemSel = reagentWatch.itemSelect('Sodium Benzoate');
  citricInp.addEventListener('input', () => reagentWatch.setUsage('Citric Acid', 'main', numOf(citricInp)));
  reagentWatch.setUsage('Citric Acid', 'main', numOf(citricInp));
  const targetPhValue = el('span', { class: 'help' });
  function refreshTargetPh() {
    const targetPh = getTargetPh();
    targetPhValue.textContent = targetPh != null ? formatQcValue(targetPh, 1) : '—';
  }

  const tile = () => el('span', { class: 'help' });
  const setTile = (span, text, cls) => { span.className = text != null ? (cls || 'qc-check-result-value') : 'help'; span.textContent = text != null ? text : '—'; };
  const resultRow = (label, node, cls) => el('div', { class: 'qc-check-result' + (cls ? ' ' + cls : '') },
    el('span', { class: 'qc-check-result-label' }, label), node);
  const recapReceiving = tile(), recapTransfer = tile(), recapWater = tile();
  const totalFinalT = tile(), expectedT = tile(), varianceT = tile(), expTdsT = tile();
  const noPlanNote = el('div', { class: 'help', style: 'margin-bottom:6px' });
  const final6aField = rfield('dilution', 'tank6aFinalL', 'Final level, Tank 6A (L)', final6aInp);
  const final6bField = rfield('dilution', 'tank6bFinalL', 'Final level, Tank 6B (L)', final6bInp);

  // preservative stock concentrations
  const ksorbateStockInp = el('input', { inputmode: 'decimal', placeholder: '%' }); attachNumericMask(ksorbateStockInp, 1);
  ksorbateStockInp.value = values.ksorbateStockPct != null ? formatQcValue(values.ksorbateStockPct, 1)
    : formatQcValue(settingValue('ksorbate_stock_concentration_default_pct', 25), 1);
  const ksorbateStockField = el('div', { style: 'display:flex;align-items:center;gap:6px' }, ksorbateStockInp, el('span', { class: 'help' }, '%'));
  const nabenzoateStockInp = el('input', { inputmode: 'decimal', placeholder: '%' }); attachNumericMask(nabenzoateStockInp, 1);
  nabenzoateStockInp.value = values.nabenzoateStockPct != null ? formatQcValue(values.nabenzoateStockPct, 1)
    : formatQcValue(settingValue('nabenzoate_stock_concentration_default_pct', 25), 1);
  const nabenzoateStockField = el('div', { style: 'display:flex;align-items:center;gap:6px' }, nabenzoateStockInp, el('span', { class: 'help' }, '%'));

  // per-tank preservative rows (a row only shows for a tank being filled)
  const prInp = v => { const i = el('input', { inputmode: 'decimal', placeholder: 'L' }); attachNumericMask(i, 2); if (v != null) i.value = formatQcValue(v, 2); return i; };
  const ks = { '6A': prInp(values.ksorbateAddedL6a), '6B': prInp(values.ksorbateAddedL6b) };
  const nb = { '6A': prInp(values.nabenzoateAddedL6a), '6B': prInp(values.nabenzoateAddedL6b) };
  const rowT = {};
  ['6A', '6B'].forEach(t => {
    rowT[t] = { fin: tile(), ksCalc: tile(), nbCalc: tile() };
    rowT[t].tr = el('tr', {}, el('td', {}, el('b', {}, 'Tank ' + t)), el('td', { class: 'num' }, rowT[t].fin),
      el('td', { class: 'num' }, rowT[t].ksCalc), el('td', {}, ks[t]), el('td', { class: 'num' }, rowT[t].nbCalc), el('td', {}, nb[t]));
  });
  const ksTotalL = tile(), ksTotalKg = tile(), nbTotalL = tile(), nbTotalKg = tile();
  const changeCbs = [];

  const finalIn = t => numOf(t === '6A' ? final6aInp : final6bInp);
  const used = () => { const p = getPlan(); return { p, a: usesTank(p.receiving, '6A'), b: usesTank(p.receiving, '6B') }; };
  const usedList = () => { const u = used(); return ['6A', '6B'].filter(t => t === '6A' ? u.a : u.b); };
  function totalFinal() {
    const tanks = usedList();
    if (!tanks.length) return null;
    const vals = tanks.map(finalIn);
    return vals.some(v => v == null) ? null : vals.reduce((a, b) => a + b, 0);
  }
  function expectedFinal() {
    const { p } = used();
    const tr = numOf(productInp), w = numOf(waterInp);
    if (!p.receiving || tr == null || w == null) return null;
    return ((usesTank(p.receiving, '6A') ? (p.start6a || 0) : 0) + (usesTank(p.receiving, '6B') ? (p.start6b || 0) : 0)) + tr + w;
  }
  function variancePct() { const t = totalFinal(), e = expectedFinal(); return (t != null && e) ? (t - e) / e * 100 : null; }
  function expectedTds() {
    const { p } = used();
    const tr = numOf(productInp), w = numOf(waterInp);
    return (p.c1 != null && tr != null && w != null && tr + w > 0) ? p.c1 * tr / (tr + w) : null;
  }
  function sumAdded(map) {
    const tanks = usedList();
    if (!tanks.length) return null;
    const vals = tanks.map(t => numOf(map[t]));
    return vals.some(v => v == null) ? null : vals.reduce((a, b) => a + b, 0);
  }
  function calcPerTank(t, which) {
    const fin = finalIn(t);
    const stockPct = numOf(which === 'ks' ? ksorbateStockInp : nabenzoateStockInp);
    const target = which === 'ks' ? getKsorbateTarget() : (getNabenzoateTarget ? getNabenzoateTarget() : null);
    return (fin != null && stockPct && target != null) ? fin * target * 100 / stockPct : null;
  }

  function refresh() {
    refreshTargetPh();
    const { p, a, b } = used();
    const c = p.calc || {};
    noPlanNote.textContent = p.receiving ? '' : 'Choose the receiving tank(s) and enter the tank levels in Pasteurization → Dilution plan first; the tank fields for this section then appear here.'
      + (values.fillLevelTank6abL != null ? ' (Earlier single-tank entry on this run: final level ' + fmt(values.fillLevelTank6abL, 0) + ' L'
        + (values.ksorbateAddedL != null ? ', Ksorbate added ' + fmt(values.ksorbateAddedL, 2) + ' L' : '')
        + (values.nabenzoateAddedL != null ? ', sodium benzoate added ' + fmt(values.nabenzoateAddedL, 2) + ' L' : '') + '.)' : '');
    noPlanNote.classList.toggle('hidden', !!p.receiving);
    final6aField.classList.toggle('hidden', !a);
    final6bField.classList.toggle('hidden', !b);
    rowT['6A'].tr.classList.toggle('hidden', !a);
    rowT['6B'].tr.classList.toggle('hidden', !b);
    const recv = RECEIVING_TANKS.find(r => r[0] === p.receiving);
    setTile(recapReceiving, recv ? recv[1] : null);
    setTile(recapTransfer, fmtL(c.transfer));
    setTile(recapWater, fmtL(c.water));
    const tf = totalFinal(), ef = expectedFinal(), vp = variancePct();
    setTile(totalFinalT, tf != null ? fmt(tf, 0) + ' L' : null);
    setTile(expectedT, ef != null ? fmt(ef, 0) + ' L' : null);
    const flag = settingValue('dilution_variance_flag_pct', 5);
    if (vp != null) {
      const over = Math.abs(vp) > flag;
      varianceT.className = 'qc-check-result-value' + (over ? ' var-flag' : '');
      varianceT.textContent = (vp > 0 ? '+' : '') + vp.toFixed(1) + '%' + (over ? '  ⚠ exceeds ' + flag + '%' : '  ✓ within ' + flag + '%');
    } else { setTile(varianceT, null); }
    const et = expectedTds(); const tgt = p.c2;
    setTile(expTdsT, et != null ? formatQcValue(et, 2) + '%' + (tgt != null ? '  (target ' + formatQcValue(tgt, 2) + '%)' : '') : null);
    ['6A', '6B'].forEach(t => {
      const fin = finalIn(t);
      setTile(rowT[t].fin, fin != null ? fmt(fin, 0) + ' L' : null);
      const k = calcPerTank(t, 'ks'), n = calcPerTank(t, 'nb');
      setTile(rowT[t].ksCalc, k != null ? formatQcValue(k, 2) + ' L' : null);
      setTile(rowT[t].nbCalc, n != null ? formatQcValue(n, 2) + ' L' : null);
    });
    const kL = sumAdded(ks), nL = sumAdded(nb);
    const ksPct = numOf(ksorbateStockInp), nbPct = numOf(nabenzoateStockInp);
    setTile(ksTotalL, kL != null ? formatQcValue(kL, 2) + ' L' : null);
    setTile(ksTotalKg, kL != null && ksPct != null ? formatQcValue(kL * ksPct / 100, 2) + ' kg' : null);
    setTile(nbTotalL, nL != null ? formatQcValue(nL, 2) + ' L' : null);
    setTile(nbTotalKg, nL != null && nbPct != null ? formatQcValue(nL * nbPct / 100, 2) + ' kg' : null);
    reagentWatch.setUsage('Potassium Sorbate', 'main', kL != null && ksPct != null ? kL * ksPct / 100 : 0);
    reagentWatch.setUsage('Sodium Benzoate', 'main', nL != null && nbPct != null ? nL * nbPct / 100 : 0);
    changeCbs.forEach(cb => cb());
  }
  // Product still in Tank 5A/5B after this (first) pass: what the plan saw there minus what was
  // actually transferred (or, before that is entered, minus the recommended transfer).
  function getRemaining() {
    const p = getPlan();
    if (p.v5a == null && p.v5b == null) return null;
    const avail = (p.v5a || 0) + (p.v5b || 0), c = p.calc || {};
    const moved = numOf(productInp) != null ? numOf(productInp) : (c.transfer != null ? c.transfer : null);
    return moved == null ? null : Math.max(0, avail - moved);
  }
  const getStock = () => ({ ksPct: numOf(ksorbateStockInp), nbPct: numOf(nabenzoateStockInp),
    ksTarget: getKsorbateTarget(), nbTarget: getNabenzoateTarget ? getNabenzoateTarget() : null });
  [productInp, waterInp, final6aInp, final6bInp, ksorbateStockInp, nabenzoateStockInp, ks['6A'], ks['6B'], nb['6A'], nb['6B']]
    .forEach(i => i.addEventListener('input', refresh));

  // No Save button of its own: Dilution & Preservation has ONE Save (at the bottom of
  // the section) that calls this and then saves the LKE QC Check + Sample Point.
  async function save() {
    const rid = await getRunId();
    const { p, a, b } = used();
    const payload = {
      measuredPh: numOf(measuredPhInp), citricKg: numOf(citricInp),
      ksorbateStockPct: numOf(ksorbateStockInp), nabenzoateStockPct: numOf(nabenzoateStockInp),
      citricItemId: reagentWatch.itemId('Citric Acid'), ksorbateItemId: reagentWatch.itemId('Potassium Sorbate'),
      nabenzoateItemId: reagentWatch.itemId('Sodium Benzoate'),
    };
    // Per-tank fields only exist once the receiving tank(s) are chosen; a run still on the older
    // single-total layout keeps its existing totals untouched.
    if (p.receiving) {
      Object.assign(payload, {
        productTransferredL: numOf(productInp), waterAddedL: numOf(waterInp),
        tank6aFinalL: a ? numOf(final6aInp) : null, tank6bFinalL: b ? numOf(final6bInp) : null,
        fillLevelTank6abL: totalFinal(), finalVariancePct: variancePct(),
        ksorbateAddedL6a: a ? numOf(ks['6A']) : null, ksorbateAddedL6b: b ? numOf(ks['6B']) : null,
        nabenzoateAddedL6a: a ? numOf(nb['6A']) : null, nabenzoateAddedL6b: b ? numOf(nb['6B']) : null,
        ksorbateAddedL: sumAdded(ks), nabenzoateAddedL: sumAdded(nb),
      });
    }
    await api('PUT', '/production/' + rid + '/stages/dilution', payload);
    reagentWatch.runId = rid;
    await reagentWatch.load();          // stock + what this run has now deducted
  }

  refresh();
  return {
    reagentWatch,
    box: el('div', {},
      el('div', { class: 'qc-check-section-title', style: 'margin-top:0' }, 'Dilution'),
      noPlanNote,
      el('div', { class: 'form-row-3' },
        field('Receiving tanks (plan)', el('div', { class: 'calc-tile' }, recapReceiving)),
        field('Recommended transfer', el('div', { class: 'calc-tile' }, recapTransfer)),
        field('Recommended dilution water', el('div', { class: 'calc-tile' }, recapWater))),
      el('div', { class: 'form-row' },
        rfield('dilution', 'productTransferredL', 'Product transferred from 5A/5B (L)', productInp),
        rfield('dilution', 'waterAddedL', 'Dilution water added (L)', waterInp)),
      el('div', { class: 'form-row' }, final6aField, final6bField),
      el('div', { class: 'qc-check-box' },
        el('div', { class: 'qc-check-title' }, 'Dilution check'),
        resultRow('Total final volume, Tanks 6A/6B', totalFinalT),
        resultRow('Expected final volume (start + product + water)', expectedT),
        resultRow('Variance vs expected', varianceT),
        resultRow('Expected TDS after dilution', expTdsT)),
      el('div', { class: 'qc-check-section-title' }, 'Preservatives'),
      el('div', { class: 'form-row' },
        rfield('dilution', 'ksorbateStockPct', 'Ksorbate stock concentration (w/v)', ksorbateStockField),
        rfield('dilution', 'nabenzoateStockPct', 'Nabenzoate stock concentration (w/v)', nabenzoateStockField)),
      el('div', { class: 'form-row' }, field('Potassium sorbate item (inventory)', ksItemSel), field('Sodium benzoate item (inventory)', nbItemSel)),
      el('div', { class: 'help' }, 'Doses are sized to the volume in each tank. Enter the stock solution added to each tank.'),
      el('div', { class: 'tablewrap' }, el('table', { class: 'qc-check-checklist tank-table' },
        el('thead', {}, el('tr', {}, el('th', {}, 'Tank'), el('th', { class: 'num' }, 'Final volume'),
          el('th', { class: 'num' }, 'Ksorbate calc.'), el('th', {}, reqLabel('Ksorbate added (L)')),
          el('th', { class: 'num' }, 'Benzoate calc.'), el('th', {}, reqLabel('Benzoate added (L)')))),
        el('tbody', {}, rowT['6A'].tr, rowT['6B'].tr))),
      el('div', { class: 'qc-check-box' },
        resultRow('Ksorbate added, total', el('span', {}, ksTotalL, ' ', ksTotalKg)), ksNote,
        resultRow('Sodium benzoate added, total', el('span', {}, nbTotalL, ' ', nbTotalKg)), nbNote),
      el('div', { class: 'qc-check-section-title' }, 'pH Balancing'),
      el('div', { class: 'form-row' },
        rfield('dilution', 'measuredPh', 'Measured pH', measuredPhInp), field('Target pH', targetPhValue)),
      el('div', { class: 'form-row' },
        field('Citric acid item (inventory)', citricItemSel),
        rfield('dilution', 'citricKg', 'Citric acid added (kg)', el('div', {}, citricInp, citricNote)))),
    save,
    refresh,
    getRemaining, getStock,
    onChange: cb => changeCbs.push(cb)
  };
}
// ---- Additional dilution passes (pass 2, 3, ...) -----------------------------------------------
// When product is left in Tank 5A/5B after a pass, the operator runs another pass. Each extra pass is
// one self-contained card -- its own plan (5A/5B levels, receiving tank(s), starting levels, the
// recommended transfer/water via dilutionPlanCalc), actuals (product transferred, water added, final
// level per tank, variance) and per-tank preservative additions -- saved to /dilution-passes. The run's
// reagent totals are summed across every pass on the server. The TDS, SKU target and preservative
// stock concentrations are shared with pass 1.
function buildDilutionPassCard(pass, ctx) {
  const numOf = inp => inp.value.trim() === '' ? null : qcParseValue(inp.value);
  const lvl = (v, ph) => { const i = el('input', { inputmode: 'decimal', placeholder: ph || 'Measured using level sensor' }); attachNumericMask(i, 2); if (v != null) i.value = formatQcValue(v, 2); return i; };
  const t5a = lvl(pass.tank5aL), t5b = lvl(pass.tank5bL);
  const recvSel = el('select', {}, el('option', { value: '' }, 'Select…'), ...RECEIVING_TANKS.map(([v, l]) => el('option', { value: v }, l)));
  recvSel.value = pass.receivingTanks || '';
  const s6a = lvl(pass.tank6aStartL != null ? pass.tank6aStartL : 0), s6b = lvl(pass.tank6bStartL != null ? pass.tank6bStartL : 0);
  const productInp = lvl(pass.productTransferredL, 'From the 5A/5B level drop');
  const waterInp = lvl(pass.waterAddedL, 'Measured using dilution totalizer');
  const f6a = lvl(pass.tank6aFinalL), f6b = lvl(pass.tank6bFinalL);
  const prInp = v => { const i = el('input', { inputmode: 'decimal', placeholder: 'L' }); attachNumericMask(i, 2); if (v != null) i.value = formatQcValue(v, 2); return i; };
  const ks = { '6A': prInp(pass.ksorbateAddedL6a), '6B': prInp(pass.ksorbateAddedL6b) };
  const nb = { '6A': prInp(pass.nabenzoateAddedL6a), '6B': prInp(pass.nabenzoateAddedL6b) };
  // pH balancing for this pass (each pass needs its own citric acid correction)
  const phInp = el('input', { inputmode: 'decimal', placeholder: 'pH' }); attachNumericMask(phInp, 1);
  if (pass.measuredPh != null) phInp.value = formatQcValue(pass.measuredPh, 1);
  const citricInp = el('input', { inputmode: 'decimal', placeholder: 'kg' }); attachNumericMask(citricInp, 2);
  if (pass.citricKg != null) citricInp.value = formatQcValue(pass.citricKg, 2);
  const rw = ctx.reagentWatch, pk = 'pass' + pass.id;
  const cNote = rw ? rw.noteEl('Citric Acid') : null, kNote = rw ? rw.noteEl('Potassium Sorbate') : null, bNote = rw ? rw.noteEl('Sodium Benzoate') : null;
  const targetPhTile = el('span', { class: 'help' });
  const tile = () => el('span', { class: 'help' });
  const setTile = (span, text) => { span.className = text != null ? 'qc-check-result-value' : 'help'; span.textContent = text != null ? text : '—'; };
  const resultRow = (label, node, cls) => el('div', { class: 'qc-check-result' + (cls ? ' ' + cls : '') }, el('span', { class: 'qc-check-result-label' }, label), node);
  const sA = field(reqLabel('Tank 6A level before transfer (L)'), s6a), sB = field(reqLabel('Tank 6B level before transfer (L)'), s6b);
  const fA = field(reqLabel('Final level, Tank 6A (L)'), f6a), fB = field(reqLabel('Final level, Tank 6B (L)'), f6b);
  const T = { available: tile(), space: tile(), max: tile(), transfer: tile(), water: tile(), expected: tile(),
    totalFinal: tile(), expFinal: tile(), variance: tile(), expTds: tile() };
  const summaryHost = el('div', { class: 'summary-line' }), statusLine = el('div', { class: 'plan-status' });
  const rowT = {};
  ['6A', '6B'].forEach(t => {
    rowT[t] = { fin: tile(), ksCalc: tile(), nbCalc: tile() };
    rowT[t].tr = el('tr', {}, el('td', {}, el('b', {}, 'Tank ' + t)), el('td', { class: 'num' }, rowT[t].fin),
      el('td', { class: 'num' }, rowT[t].ksCalc), el('td', {}, ks[t]), el('td', { class: 'num' }, rowT[t].nbCalc), el('td', {}, nb[t]));
  });
  const ksTotal = tile(), nbTotal = tile();
  let lastCalc = null;

  const used = () => { const r = recvSel.value; return { r, a: usesTank(r, '6A'), b: usesTank(r, '6B') }; };
  const planOf = () => ({ receiving: recvSel.value, v5a: numOf(t5a), v5b: numOf(t5b), start6a: numOf(s6a), start6b: numOf(s6b),
    c1: ctx.getTdsConc(), c2: ctx.getTdsTarget() });
  const finalIn = t => numOf(t === '6A' ? f6a : f6b);
  const usedList = () => { const u = used(); return ['6A', '6B'].filter(t => t === '6A' ? u.a : u.b); };
  const sumOf = fn => { const l = usedList(); if (!l.length) return null; const v = l.map(fn); return v.some(x => x == null) ? null : v.reduce((a, b) => a + b, 0); };
  const totalFinal = () => sumOf(finalIn);
  const addedSum = map => sumOf(t => numOf(map[t]));
  function expectedFinal() {
    const p = planOf(), tr = numOf(productInp), w = numOf(waterInp);
    if (!p.receiving || tr == null || w == null) return null;
    return (usesTank(p.receiving, '6A') ? (p.start6a || 0) : 0) + (usesTank(p.receiving, '6B') ? (p.start6b || 0) : 0) + tr + w;
  }
  const variancePct = () => { const t = totalFinal(), e = expectedFinal(); return (t != null && e) ? (t - e) / e * 100 : null; };
  function getRemaining() {
    const p = planOf();
    if (p.v5a == null && p.v5b == null) return null;
    const avail = (p.v5a || 0) + (p.v5b || 0);
    const moved = numOf(productInp) != null ? numOf(productInp) : (lastCalc && lastCalc.transfer != null ? lastCalc.transfer : null);
    return moved == null ? null : Math.max(0, avail - moved);
  }
  function calcPerTank(t, which) {
    const st = ctx.getStock(), fin = finalIn(t);
    const pct = which === 'ks' ? st.ksPct : st.nbPct, target = which === 'ks' ? st.ksTarget : st.nbTarget;
    return (fin != null && pct && target != null) ? fin * target * 100 / pct : null;
  }
  function refresh() {
    const u = used(), p = planOf(), c = lastCalc = dilutionPlanCalc(p);
    const tph = ctx.getTargetPh ? ctx.getTargetPh() : null;
    targetPhTile.textContent = tph != null ? formatQcValue(tph, 1) : '—';
    sA.classList.toggle('hidden', !u.a); sB.classList.toggle('hidden', !u.b);
    fA.classList.toggle('hidden', !u.a); fB.classList.toggle('hidden', !u.b);
    rowT['6A'].tr.classList.toggle('hidden', !u.a); rowT['6B'].tr.classList.toggle('hidden', !u.b);
    summaryHost.innerHTML = '';
    summaryHost.append(sl('TDS concentrated (Separation filtrate)', p.c1 != null ? formatQcValue(p.c1, 2) + '%' : '—'),
      sl('TDS target', p.c2 != null ? formatQcValue(p.c2, 2) + '%' : '—'), sl('Capacity per tank', fmt(c.cap, 0) + ' L'));
    setTile(T.available, fmtL(c.available)); setTile(T.space, fmtL(c.space));
    setTile(T.max, c.maxTransfer != null ? fmt(Math.floor(c.maxTransfer / 10) * 10, 0) + ' L' : null);
    setTile(T.transfer, fmtL(c.transfer)); setTile(T.water, fmtL(c.water)); setTile(T.expected, fmtL(c.expectedFinal));
    if (p.c1 == null) { statusLine.className = 'plan-status'; statusLine.textContent = 'Needs the Separation filtrate TDS.'; }
    else if (!p.c2) { statusLine.className = 'plan-status'; statusLine.textContent = 'Select a product SKU with a target TDS.'; }
    else if (!c.n) { statusLine.className = 'plan-status'; statusLine.textContent = 'Choose the receiving tank(s).'; }
    else if (c.fits === null) { statusLine.className = 'plan-status'; statusLine.textContent = 'Enter the Tank 5A / 5B levels.'; }
    else if (c.fits) { statusLine.className = 'plan-status ok'; statusLine.textContent = '✓ All ' + fmtL(c.available) + ' in 5A/5B fits once diluted.'; }
    else { statusLine.className = 'plan-status warn';
      statusLine.textContent = '⚠ Only ' + fmt(Math.floor(c.maxTransfer / 10) * 10, 0) + ' L of the ' + fmt(round10(c.available), 0) + ' L can be pushed through — about ' + fmtL(c.remaining) + ' still stays behind (another pass).'; }
    const tf = totalFinal(), ef = expectedFinal(), vp = variancePct(), flag = settingValue('dilution_variance_flag_pct', 5);
    setTile(T.totalFinal, tf != null ? fmt(tf, 0) + ' L' : null); setTile(T.expFinal, ef != null ? fmt(ef, 0) + ' L' : null);
    if (vp != null) { const over = Math.abs(vp) > flag; T.variance.className = 'qc-check-result-value' + (over ? ' var-flag' : '');
      T.variance.textContent = (vp > 0 ? '+' : '') + vp.toFixed(1) + '%' + (over ? '  ⚠ exceeds ' + flag + '%' : '  ✓ within ' + flag + '%'); }
    else setTile(T.variance, null);
    const tr = numOf(productInp), w = numOf(waterInp);
    setTile(T.expTds, (p.c1 != null && tr != null && w != null && tr + w > 0) ? formatQcValue(p.c1 * tr / (tr + w), 2) + '%' + (p.c2 != null ? '  (target ' + formatQcValue(p.c2, 2) + '%)' : '') : null);
    ['6A', '6B'].forEach(t => {
      const fin = finalIn(t), k = calcPerTank(t, 'ks'), n = calcPerTank(t, 'nb');
      setTile(rowT[t].fin, fin != null ? fmt(fin, 0) + ' L' : null);
      setTile(rowT[t].ksCalc, k != null ? formatQcValue(k, 2) + ' L' : null); setTile(rowT[t].nbCalc, n != null ? formatQcValue(n, 2) + ' L' : null);
    });
    const kL = addedSum(ks), nL = addedSum(nb), st = ctx.getStock();
    setTile(ksTotal, kL != null ? formatQcValue(kL, 2) + ' L' + (st.ksPct != null ? '  ·  ' + formatQcValue(kL * st.ksPct / 100, 2) + ' kg' : '') : null);
    setTile(nbTotal, nL != null ? formatQcValue(nL, 2) + ' L' + (st.nbPct != null ? '  ·  ' + formatQcValue(nL * st.nbPct / 100, 2) + ' kg' : '') : null);
    if (rw) {
      rw.setUsage('Citric Acid', pk, numOf(citricInp));
      rw.setUsage('Potassium Sorbate', pk, kL != null && st.ksPct != null ? kL * st.ksPct / 100 : 0);
      rw.setUsage('Sodium Benzoate', pk, nL != null && st.nbPct != null ? nL * st.nbPct / 100 : 0);
    }
    if (ctx.onChanged) ctx.onChanged();
  }
  [t5a, t5b, s6a, s6b, productInp, waterInp, f6a, f6b, ks['6A'], ks['6B'], nb['6A'], nb['6B'], phInp, citricInp].forEach(i => i.addEventListener('input', refresh));
  recvSel.addEventListener('change', refresh);

  const status = el('span', { class: 'help' });
  const saveBtn = el('button', { type: 'button', class: 'secondary section-save', onclick: async () => {
    status.textContent = ''; saveBtn.disabled = true;
    try {
      const u = used(), p = planOf(), c = lastCalc || {};
      const rid = await ctx.getRunId();
      const r = await api('PUT', '/production/' + rid + '/dilution-passes/' + pass.id, {
        tank5aL: p.v5a, tank5bL: p.v5b, receivingTanks: p.receiving || null, tdsPct: p.c1,
        tank6aStartL: u.a ? p.start6a : null, tank6bStartL: u.b ? p.start6b : null,
        maxTransferL: c.maxTransfer != null ? c.maxTransfer : null, recommendedTransferL: c.transfer != null ? c.transfer : null,
        recommendedWaterL: c.water != null ? c.water : null,
        productTransferredL: numOf(productInp), waterAddedL: numOf(waterInp),
        tank6aFinalL: u.a ? numOf(f6a) : null, tank6bFinalL: u.b ? numOf(f6b) : null, finalVariancePct: variancePct(),
        ksorbateAddedL6a: u.a ? numOf(ks['6A']) : null, ksorbateAddedL6b: u.b ? numOf(ks['6B']) : null,
        nabenzoateAddedL6a: u.a ? numOf(nb['6A']) : null, nabenzoateAddedL6b: u.b ? numOf(nb['6B']) : null,
        measuredPh: numOf(phInp), citricKg: numOf(citricInp),
      });
      if (ctx.onItems) ctx.onItems(r.dilutionPasses);
      if (rw) rw.load();
      status.textContent = 'Saved.';
    } catch (e) { status.textContent = e.message; }
    saveBtn.disabled = false;
  } }, 'Save pass ' + pass.passNo);
  const removeBtn = ctx.isLast ? el('button', { type: 'button', class: 'danger', onclick: () => ctx.onRemove() }, 'Remove pass ' + pass.passNo) : null;
  refresh();
  const prevRem = ctx.getPrevRemaining();
  const box = el('div', { class: 'qc-check-box pass-card' },
    el('div', { class: 'qc-check-title' }, 'Dilution pass ' + pass.passNo),
    el('div', { class: 'qc-check-subtitle' }, 'Tanks 5A/5B → 6A/6B' + (prevRem != null ? ' · about ' + fmtL(prevRem) + ' was left in 5A/5B after the previous pass' : '')),
    el('div', { class: 'qc-check-section-title', style: 'margin-top:4px' }, 'Plan'),
    summaryHost,
    el('div', { class: 'form-row' }, field(reqLabel('Tank 5A level (L)'), t5a), field(reqLabel('Tank 5B level (L)'), t5b)),
    el('div', { class: 'form-row' }, field(reqLabel('Receiving tanks'), recvSel)),
    el('div', { class: 'form-row' }, sA, sB),
    resultRow('Product available in 5A / 5B', T.available), resultRow('Available space in receiving tank(s)', T.space),
    resultRow('Maximum product to transfer', T.max, 'plan-key'), resultRow('Recommended product transfer', T.transfer),
    resultRow('Recommended dilution water', T.water), resultRow('Expected final volume in receiving tank(s)', T.expected), statusLine,
    el('div', { class: 'qc-check-section-title' }, 'Actual'),
    el('div', { class: 'form-row' }, field(reqLabel('Product transferred from 5A/5B (L)'), productInp), field(reqLabel('Dilution water added (L)'), waterInp)),
    el('div', { class: 'form-row' }, fA, fB),
    resultRow('Total final volume', T.totalFinal), resultRow('Expected final volume (start + product + water)', T.expFinal),
    resultRow('Variance vs expected', T.variance), resultRow('Expected TDS after dilution', T.expTds),
    el('div', { class: 'qc-check-section-title' }, 'Preservatives (this pass)'),
    el('div', { class: 'tablewrap' }, el('table', { class: 'qc-check-checklist tank-table' },
      el('thead', {}, el('tr', {}, el('th', {}, 'Tank'), el('th', { class: 'num' }, 'Final volume'), el('th', { class: 'num' }, 'Ksorbate calc.'),
        el('th', {}, reqLabel('Ksorbate added (L)')), el('th', { class: 'num' }, 'Benzoate calc.'), el('th', {}, reqLabel('Benzoate added (L)')))),
      el('tbody', {}, rowT['6A'].tr, rowT['6B'].tr))),
    resultRow('Ksorbate added, this pass', ksTotal), kNote, resultRow('Sodium benzoate added, this pass', nbTotal), bNote,
    el('div', { class: 'qc-check-section-title' }, 'pH Balancing (this pass)'),
    el('div', { class: 'form-row-3' }, field(reqLabel('Measured pH'), phInp), field('Target pH', targetPhTile),
      field(reqLabel('Citric acid added (kg)'), el('div', {}, citricInp, cNote))),
    el('div', { style: 'margin-top:8px;display:flex;gap:10px;align-items:center' }, saveBtn, status, removeBtn));
  return { box, getRemaining, refresh };
}
function buildDilutionPassesSection(initial, getRunId, ctx) {
  let items = (initial || []).slice();
  const host = el('div', {}), addHost = el('div', {});
  const root = el('div', {}, host, addHost);
  let cards = [];
  function draw() {
    host.innerHTML = ''; cards = [];
    if (ctx.reagentWatch) ctx.reagentWatch.clearUsage('pass');   // cards below re-register their own amounts
    items.forEach((p, i) => {
      const card = buildDilutionPassCard(p, {
        reagentWatch: ctx.reagentWatch, getRunId, getTdsConc: ctx.getTdsConc, getTdsTarget: ctx.getTdsTarget, getStock: ctx.getStock, getTargetPh: ctx.getTargetPh,
        getPrevRemaining: () => i === 0 ? ctx.getPass1Remaining() : (cards[i - 1] ? cards[i - 1].getRemaining() : null),
        isLast: i === items.length - 1, onChanged: () => refreshAdd(), onItems: list => { items = list; },
        onRemove: async () => {
          if (!confirm('Remove dilution pass ' + p.passNo + '? Its preservative additions are refunded to stock.')) return;
          try { items = (await api('DELETE', '/production/' + await getRunId() + '/dilution-passes/' + p.id)).dilutionPasses; draw(); if (ctx.reagentWatch) ctx.reagentWatch.load(); }
          catch (e) { toast(e.message, true); }
        },
      });
      cards.push(card); host.append(card.box);
    });
    refreshAdd();
  }
  function lastRemaining() { return items.length ? (cards[items.length - 1] ? cards[items.length - 1].getRemaining() : null) : ctx.getPass1Remaining(); }
  function refreshAdd() {
    addHost.innerHTML = '';
    const r = lastRemaining();
    if (r == null || r <= 0.5) return;
    const next = items.length + 2;
    addHost.append(el('div', { class: 'plan-status warn', style: 'margin:8px 0' },
      '⚠ About ' + fmtL(r) + ' of product is still in Tank 5A/5B. ',
      el('button', { type: 'button', class: 'secondary', style: 'margin-left:8px', onclick: async () => {
        try {
          if (ctx.ensureSaved) await ctx.ensureSaved();      // pass 1's plan must be saved first
          items = (await api('POST', '/production/' + await getRunId() + '/dilution-passes', {})).dilutionPasses; draw(); }
        catch (e) { toast(e.message, true); }
      } }, '+ Add dilution pass ' + next)));
  }
  draw();
  return { box: root, refresh: refreshAdd };
}
// Dilution & Preservation: a run may split its output across several tanks.
// Existing entries can still be edited/saved/removed, but new ones can no
// longer be added from KelpWorks -- there is no "+ Add tank" control.
function buildDilutionsSection(initial, getRunId) {
  let items = (initial || []).slice();
  const listHost = el('div', {});
  const status = el('div', { class: 'help' });
  function numField(val, ph) { const i = el('input', { inputmode: 'decimal', placeholder: ph }); attachNumericMask(i, 2); if (val != null) i.value = formatQcValue(val, 2); return i; }
  function draw() {
    listHost.innerHTML = '';
    if (!items.length) { listHost.append(el('div', { class: 'help' }, 'No dilution / preservation entries yet.')); return; }
    items.forEach(it => {
      const tankInp = el('input', { placeholder: 'Tank', value: it.tank || '' });
      const volInitInp = numField(it.volumeInitialL, 'L');
      const waterInp = numField(it.waterRequiredL, 'L');
      const volFinalInp = numField(it.volumeFinalL, 'L');
      const sorbInp = numField(it.sorbateRequiredKg, 'kg');
      const benzInp = numField(it.benzoateRequiredKg, 'kg');
      const citricInp = numField(it.citricKg, 'kg');
      const presAddedChk = el('input', { type: 'checkbox', style: 'width:auto;flex:none' }); presAddedChk.checked = !!it.preservativesAdded;
      const presAtInp = el('input', { type: 'datetime-local', value: it.preservativesAddedAt ? it.preservativesAddedAt.replace('Z', '').slice(0, 16) : '' });
      const sampleChk = el('input', { type: 'checkbox', style: 'width:auto;flex:none' }); sampleChk.checked = !!it.samplesTaken;
      const sampleAtInp = el('input', { type: 'datetime-local', value: it.samplesTakenAt ? it.samplesTakenAt.replace('Z', '').slice(0, 16) : '' });
      const notesInp = el('input', { placeholder: 'Notes', value: it.notes || '' });
      const rowStatus = el('span', { class: 'help' });
      const saveBtn = el('button', {
        type: 'button', class: 'secondary', onclick: async () => {
          try {
            const rid = await getRunId();
            const r = await api('PUT', '/production/' + rid + '/dilutions/' + it.id, {
              tank: tankInp.value.trim() || null,
              volumeInitialL: volInitInp.value.trim() === '' ? null : qcParseValue(volInitInp.value),
              waterRequiredL: waterInp.value.trim() === '' ? null : qcParseValue(waterInp.value),
              volumeFinalL: volFinalInp.value.trim() === '' ? null : qcParseValue(volFinalInp.value),
              sorbateRequiredKg: sorbInp.value.trim() === '' ? null : qcParseValue(sorbInp.value),
              benzoateRequiredKg: benzInp.value.trim() === '' ? null : qcParseValue(benzInp.value),
              citricKg: citricInp.value.trim() === '' ? null : qcParseValue(citricInp.value),
              preservativesAdded: presAddedChk.checked, preservativesAddedAt: presAtInp.value || null,
              samplesTaken: sampleChk.checked, samplesTakenAt: sampleAtInp.value || null,
              notes: notesInp.value.trim() || null
            });
            items = r.dilutions; rowStatus.textContent = 'Saved.';
          } catch (e) { rowStatus.textContent = e.message; }
        }
      }, 'Save');
      const delBtn = el('button', {
        type: 'button', class: 'danger', onclick: async () => {
          if (!confirm('Remove this tank entry?')) return;
          const rid = await getRunId();
          const r = await api('DELETE', '/production/' + rid + '/dilutions/' + it.id);
          items = r.dilutions; draw();
        }
      }, 'Remove');
      listHost.append(el('div', { class: 'repeat-item' },
        el('div', { class: 'form-row' }, field('Tank', tankInp), field('Volume initial (L)', volInitInp)),
        el('div', { class: 'form-row' }, field('Water required (L)', waterInp), field('Volume final (L)', volFinalInp)),
        el('div', { class: 'form-row' }, field('Sorbate required (kg)', sorbInp), field('Benzoate required (kg)', benzInp)),
        field('Citric acid (kg)', citricInp),
        el('div', { class: 'form-row' },
          field('Preservatives added', el('label', { style: 'display:flex;align-items:center;gap:6px' }, presAddedChk, 'Yes')),
          field('Added at', presAtInp)),
        el('div', { class: 'form-row' },
          field('Samples taken', el('label', { style: 'display:flex;align-items:center;gap:6px' }, sampleChk, 'Yes')),
          field('Taken at', sampleAtInp)),
        field('Notes', notesInp),
        el('div', { style: 'display:flex;gap:10px;align-items:center;margin-top:6px' }, saveBtn, delBtn, rowStatus)));
    });
  }
  draw();
  return el('div', {}, listHost, status);
}

// Step 1 of creating a run: Initiation only. Every field but Notes is
// required — submitting reserves the run's permanent PR-... code (via the
// same draft-creation endpoint used for "Save & close" later) and hands off
// to openRun() for everything else, which only becomes reachable once a run
// actually exists.
async function openNewRun() {
  const skus = State.ref.skus.filter(s => s.active);
  const skuSel = selectFrom('', [['', 'Select a SKU…'], ...skus.map(s => [s.code, s.name])], () => renderSpecPanel(), 'nr_sku');
  const specPanel = el('div', { class: 'summary-line' });
  function renderSpecPanel() {
    const s = skus.find(x => x.code === skuSel.value);
    specPanel.innerHTML = '';
    if (!s) { specPanel.append(el('span', { class: 'muted' }, 'Select a SKU to see its spec.')); return; }
    specPanel.append(
      sl('Species', (s.species || []).map(speciesName).join(' & ') || '—'),
      sl('Target TDS', s.tdsTarget != null ? s.tdsTarget + '%' : '—'),
      sl('Target pH', s.phTarget ?? '—'),
      sl('Ksorbate (w/v)', s.ksorbateTarget != null ? (s.ksorbateTarget * 100).toFixed(2) + '%' : '—'),
      sl('Nabenzoate (w/v)', s.nabenzoateTarget != null ? (s.nabenzoateTarget * 100).toFixed(2) + '%' : '—'));
  }
  renderSpecPanel();
  const dateInp = el('input', { type: 'date', id: 'nr_date', value: new Date().toISOString().slice(0, 10) });
  const locSel = productionLocationSelect('nr_loc');
  const operatorsSelect = buildOperatorsSelect('');
  const body = el('div', {},
    el('div', { class: 'form-row' },
      field(reqLabel('Product SKU'), skuSel),
      field(reqLabel('Run date'), dateInp)),
    specPanel,
    el('div', { class: 'form-row', style: 'margin-top:8px' },
      field(reqLabel('Production Location'), locSel),
      field(reqLabel('Operators'), operatorsSelect.el)),
    field('Notes', el('textarea', { id: 'nr_notes', rows: '2', placeholder: 'Optional batch notes' })),
    el('div', { class: 'help req-legend' }, el('span', { class: 'req-star' }, '*'), ' Required.'),
    el('div', { class: 'help' },
      'Feedstock, process stages and packaging open up once the run is created and a run code is assigned.'));
  modal('New production run', body, async () => {
    if (!skuSel.value) throw new Error('Choose a product SKU.');
    if (!dateInp.value) throw new Error('Enter a run date.');
    if (!locSel.value) throw new Error('Choose a production location.');
    if (!operatorsSelect.value.trim()) throw new Error('Select at least one operator.');
    const r = await api('POST', '/production/drafts', {
      sku: skuSel.value, runDate: dateInp.value, location: locSel.value,
      operators: operatorsSelect.value, notes: body.querySelector('#nr_notes').value,
      toteIds: [], packages: [], feedstockDetails: {}
    });
    toast('Run ' + r.run.processingLot + ' created.');
    openRun(r.run, { fresh: true });
  }, 'Create run');
}
async function openRun(draftSummary, opts) {
  // `fresh` is only true right after openNewRun() just created this draft --
  // that's the one case that keeps its current default-open Feedstock
  // section; resuming an existing draft (or reopening any other production
  // log) always starts with every section collapsed.
  const fresh = !!(opts && opts.fresh);
  // Re-fetch a resumed draft in full (list snapshots omit stage/solids/dilution detail).
  const draft = (draftSummary && draftSummary.id) ? (await api('GET', '/production/drafts/' + draftSummary.id)).run : draftSummary;
  const totes = (await api('GET', '/totes?status=in_stock'
    + (draft && draft.id ? '&includeRunId=' + draft.id : ''))).totes;
  const skus = State.ref.skus.filter(s => s.active);
  const skuSel = selectFrom('', skus.map(s => [s.code, s.name]),
    () => { filterTotes(); renderSpecPanel(); pasteurizationSection.refresh(); dilutionSummary.refresh(); }, 'r_sku');
  if (draft && draft.sku) skuSel.value = draft.sku;
  const specPanel = el('div', { class: 'summary-line' });
  function renderSpecPanel() {
    const s = skus.find(x => x.code === skuSel.value);
    specPanel.innerHTML = '';
    if (!s) { specPanel.append(el('span', { class: 'muted' }, 'Select a SKU to see its spec.')); return; }
    specPanel.append(
      sl('Species', (s.species || []).map(speciesName).join(' & ') || '—'),
      sl('Target TDS', s.tdsTarget != null ? s.tdsTarget + '%' : '—'),
      sl('Target pH', s.phTarget ?? '—'),
      sl('Ksorbate (w/v)', s.ksorbateTarget != null ? (s.ksorbateTarget * 100).toFixed(2) + '%' : '—'),
      sl('Nabenzoate (w/v)', s.nabenzoateTarget != null ? (s.nabenzoateTarget * 100).toFixed(2) + '%' : '—'));
  }
  const generalFilterInp = el('input', { placeholder: 'Filter by lot, site, or location…' });
  generalFilterInp.addEventListener('input', () => filterTotes());
  const stabFilterSel = el('select', {}, ...['Citric acid', 'Fresh', ''].map(v =>
    el('option', { value: v }, v || 'All')));
  stabFilterSel.value = 'Citric acid';
  stabFilterSel.addEventListener('change', () => filterTotes());
  const pickFilters = { site: '', location: '' };
  const pickHost = el('div', { class: 'tote-pick' });
  const summary = el('div', { class: 'summary-line' });
  let pickFilteredCount = 0;
  const feedstockHost = el('div', {});
  let selected = new Set(draft?.toteIds || []);
  let draftId = draft ? draft.id : null;
  let feedstockState = Object.assign({}, draft?.feedstockDetails || {});
  const operatorsSelect = buildOperatorsSelect(draft?.operators || '');

  // A section that needs a real run id (photos, stages, repeatable lists) calls
  // this first — for a brand-new run it silently saves a draft to get one.
  async function ensureRunId() {
    if (draftId) return draftId;
    await saveDraft(true);
    return draftId;
  }

  // FieldKelp-style SKUs map to more than one species (e.g. FieldKelp covers
  // both Sugar Kelp and Giant Kelp) via fg_sku_species, so this can return
  // several codes -- the picker then matches a tote on any of them.
  function speciesOfSku() { const s = skus.find(x => x.code === skuSel.value); return s ? s.species : []; }
  function filterTotes() {
    const sp = speciesOfSku();
    const q = generalFilterInp.value.toLowerCase();
    // A tote already selected for this run always shows (even mid-run as
    // WIP, so it can still be unselected right up until the run finishes) —
    // otherwise it's an ordinary in-stock candidate that has to pass the
    // active filters, and any tote already WIP for some *other* run is never
    // offered here at all, same as one on QAQC Hold.
    const rows = totes.filter(t => {
      if (t.location === 'QAQC Hold') return false;
      if (selected.has(t.id)) return true;
      if (t.status === 'wip') return false;
      return (!sp.length || sp.includes(t.species)) &&
        (!stabFilterSel.value || (t.stabilizationMethod || '') === stabFilterSel.value) &&
        (!q || (t.lot + ' ' + siteName(t.site) + ' ' + (t.location || '')).toLowerCase().includes(q)) &&
        (!pickFilters.site || siteName(t.site) === pickFilters.site) &&
        (!pickFilters.location || (t.location || '') === pickFilters.location);
    });
    pickFilteredCount = rows.length;
    pickHost.innerHTML = '';
    const headRow = el('tr', {}, el('th', { class: 'checkcol' }, ''), el('th', {}, 'Lot'), el('th', {}, 'Site'),
      el('th', { class: 'num' }, 'Avg kg'), el('th', {}, 'pH'), el('th', {}, 'Location'));
    const siteOpts = (State.ref.sites || []).map(s => s.name);
    const locOpts = State.ref.locations || [];
    const siteFilterSel = el('select', {}, el('option', { value: '' }, 'All'), ...siteOpts.map(o => el('option', { value: o }, o)));
    siteFilterSel.value = pickFilters.site;
    siteFilterSel.addEventListener('change', () => { pickFilters.site = siteFilterSel.value; filterTotes(); });
    const locFilterSel = el('select', {}, el('option', { value: '' }, 'All'), ...locOpts.map(o => el('option', { value: o }, o)));
    locFilterSel.value = pickFilters.location;
    locFilterSel.addEventListener('change', () => { pickFilters.location = locFilterSel.value; filterTotes(); });
    // Leading empty cell matches the checkbox column in headRow, so each
    // filter input lines up under its own column instead of the one to its right.
    const filterRow = el('tr', { class: 'filter-row' }, el('th', {}, ''),
      el('th', {}, ''), el('th', {}, siteFilterSel),
      el('th', {}, ''), el('th', {}, ''), el('th', {}, locFilterSel));
    const tbl = el('table', {}, el('thead', {}, headRow, filterRow));
    const tb = el('tbody', {});
    for (const t of rows) {
      const cb = el('input', {
        type: 'checkbox', onchange: async () => {
          if (cb.checked) { selected.add(t.id); recompute(); renderFeedstockCards(); return; }
          // Unselecting a tote already locked in as WIP releases it back to
          // stock (for this or any other run) instead of just forgetting it
          // locally -- otherwise it'd stay tied up until the run finishes.
          selected.delete(t.id);
          if (t.status === 'wip') {
            try {
              const rid = await ensureRunId();
              await api('DELETE', '/production/' + rid + '/feedstock/' + t.id);
              const idx = totes.findIndex(x => x.id === t.id);
              if (idx !== -1) totes[idx] = Object.assign({}, totes[idx], { status: 'in_stock' });
              delete feedstockState[t.id];
              toast(t.lot + ' removed from this run — back in stock.');
            } catch (e) {
              selected.add(t.id);
              toast(e.message, true);
            }
          }
          filterTotes();
          renderFeedstockCards();
        }
      });
      cb.checked = selected.has(t.id);
      tb.append(el('tr', {}, el('td', { class: 'checkcol' }, cb), el('td', { class: 'mono' }, t.lot), el('td', {}, siteName(t.site)), el('td', { class: 'num' }, fmt(t.avgWeightKg, 1)), el('td', {}, t.ph ?? '—'), el('td', {}, t.location || '—')));
    }
    if (!rows.length) tb.append(el('tr', {}, el('td', { colspan: 6, class: 'empty' }, 'No in-stock totes match these filters.')));
    tbl.append(tb); pickHost.append(tbl); recompute();
  }
  function recompute() {
    const chosen = totes.filter(t => selected.has(t.id));
    const inputKg = chosen.reduce((a, b) => a + (b.avgWeightKg || 0), 0);
    summary.innerHTML = '';
    summary.append(sl('Totes', chosen.length), sl('Input', fmt(inputKg, 1) + ' kg'),
      sl('Totes remaining', Math.max(0, pickFilteredCount - selected.size)));
  }
  // Rebuilt only when tote selection changes (not on every keystroke elsewhere
  // in the modal) so in-progress typing inside a tote's card is never wiped.
  let feedstockRenderGen = 0;
  async function renderFeedstockCards() {
    const gen = ++feedstockRenderGen;
    const chosen = totes.filter(t => selected.has(t.id));
    // A tote with no in-memory characterization yet this session (never
    // edited/saved here) is pre-filled from its most recently captured
    // values, if any, so re-selecting an already-characterized tote doesn't
    // start every field blank.
    const needsPrefill = chosen.filter(t => !feedstockState[t.id]);
    if (needsPrefill.length) {
      const fetched = await Promise.all(needsPrefill.map(t =>
        api('GET', '/totes/' + t.id + '/ph').catch(() => null)));
      if (gen !== feedstockRenderGen) return; // a newer render started meanwhile
      needsPrefill.forEach((t, i) => {
        const r = fetched[i];
        if (r && r.latestCharacterization && !feedstockState[t.id]) feedstockState[t.id] = r.latestCharacterization;
      });
    }
    if (gen !== feedstockRenderGen) return;
    feedstockHost.innerHTML = '';
    if (!chosen.length) { feedstockHost.append(el('div', { class: 'help' }, 'Select totes above to characterize the feedstock.')); return; }
    for (const t of chosen) {
      feedstockHost.append(buildFeedstockCard({
        markRequired: true,
        label: t.lot + (t.site ? '  ·  ' + t.site : ''),
        initial: feedstockState[t.id],
        mode: 'draft',
        onChange: vals => { feedstockState[t.id] = vals; },
        // Locks this tote's characterization in immediately instead of only
        // bundling it into the next draft save/finalize, and updates its
        // Feedstock Inventory record the same way either way. An accepted
        // tote moves to WIP -- tied up in this run (never selectable in any
        // other run's pick table) until the run is finalized (-> Consumed)
        // or discarded (-> back to In stock). A rejected tote is pulled out
        // of the run right away instead: it drops out of both the picker and
        // this list, and everything captured for it (plus any photos) lands
        // in the tote's own Feedstock Stability log, since it won't have a
        // run_inputs row to carry that data once it's gone.
        onSave: async vals => {
          feedstockState[t.id] = vals;
          const rid = await ensureRunId();
          const r = await api('POST', '/production/' + rid + '/feedstock/' + t.id + '/save', vals);
          const idx = totes.findIndex(x => x.id === t.id);
          if (r.rejected) {
            selected.delete(t.id);
            delete feedstockState[t.id];
            if (idx !== -1) totes[idx] = Object.assign({}, totes[idx], { location: 'QAQC Hold', status: 'hold' });
            toast(t.lot + ' rejected — moved to QAQC Hold and removed from this run.');
            filterTotes();
            renderFeedstockCards();
          } else {
            // Just hide this tote's now-stale picker row -- other cards'
            // in-progress edits shouldn't be disturbed by this save.
            if (idx !== -1) totes[idx] = Object.assign({}, totes[idx], { status: 'wip' });
            filterTotes();
          }
        },
        uploadPhoto: async (slot, file, b64) => {
          const rid = await ensureRunId();
          const r = await api('POST', '/production/' + rid + '/feedstock-photo',
            { slot, filename: file.name, contentType: file.type || 'image/jpeg', dataB64: b64 });
          return r.attachmentId;
        },
        photoUrl: attId => attDownloadUrl(draftId, attId, false)
      }));
    }
  }

  const stages = draft?.stages || {};
  const homogenizationSection =
    buildHomogenizationSection(ensureRunId, stages.homogenization, draft?.samplePoints || [], draft?.processingLot);
  // Extraction's Save needs to tell Pasteurization to refresh its mirrored
  // TDS display right away -- pasteurizationSection doesn't exist yet at
  // this point, so the callback looks it up lazily (it's only actually
  // invoked later, after Save is clicked, by which time it's assigned below).
  let pasteurizationSectionRef;
  const extractionSection = buildExtractionSection(ensureRunId, stages.extraction);
  const separationSection =
    buildSeparationSection(ensureRunId, stages.separation, draft?.samplePoints || [], draft?.processingLot,
      () => pasteurizationSectionRef?.refreshTds());
  // TDS/pH/Ksorbate targets follow the currently-selected SKU (which can
  // still change in this draft), so they're refreshed alongside the spec
  // panel below.
  const pasteurizationSection = buildPasteurizationSection(
    ensureRunId, stages.pasteurization, draft?.samplePoints || [], draft?.processingLot,
    () => skus.find(x => x.code === skuSel.value)?.tdsTarget,
    () => stages.separation?.liquidTdsPct);
  pasteurizationSectionRef = pasteurizationSection;
  const dilutionSummary = buildDilutionAndPreservativesBox(
    ensureRunId, stages.dilution,
    () => skus.find(x => x.code === skuSel.value)?.phTarget,
    () => skus.find(x => x.code === skuSel.value)?.ksorbateTarget,
    () => pasteurizationSection.getPlan(),
    () => skus.find(x => x.code === skuSel.value)?.nabenzoateTarget, draft && draft.id);
  pasteurizationSection.onPlanChange(() => dilutionSummary.refresh());
  dilutionSummary.refresh();
  const dilutionPasses = buildDilutionPassesSection(draft?.dilutionPasses || [], ensureRunId, {
    reagentWatch: dilutionSummary.reagentWatch,
    getTdsConc: () => stages.separation?.liquidTdsPct, getTdsTarget: () => skus.find(x => x.code === skuSel.value)?.tdsTarget,
    ensureSaved: async () => {
      const b = pasteurizationSection.box.querySelector('button.section-save');
      b.click(); for (let i = 0; i < 100 && b.disabled; i++) await new Promise(r => setTimeout(r, 50));
      const msg = b.nextElementSibling ? b.nextElementSibling.textContent.trim() : '';
      if (msg && msg !== 'Saved.') throw new Error(msg);
    },
    getStock: () => dilutionSummary.getStock(), getPass1Remaining: () => dilutionSummary.getRemaining(),
    getTargetPh: () => skus.find(x => x.code === skuSel.value)?.phTarget });
  dilutionSummary.onChange(() => dilutionPasses.refresh());
  const dilutionsSection = buildDilutionsSection(draft?.dilutions || [], ensureRunId);
  const packagingEntriesSection = buildPackagingEntriesSection(draft?.packagingEntries || [], ensureRunId);
  const packagingPackagedInp = el('input', { type: 'datetime-local', value: stages.packaging?.packagedAt || '' });
  // QC Check + Sample Point ("LKE characterization") moved to the bottom of
  // Dilution & Preservation -- still packaging-stage columns/endpoints under
  // the hood (unchanged), just relocated in the UI, so they get their own
  // Save action separate from packagedAt's.
  const packagingQcCheck = buildQualityCheckBox('LKE characterization', stages.packaging || {}, { omitSlurrySolids: true, reqStage: 'packaging' });
  const packagingSampleCollectedInp = el('input', { type: 'datetime-local',
    value: stages.packaging?.sampleCollectedAt ? stages.packaging.sampleCollectedAt.replace('Z', '').slice(0, 16) : '' });
  const packagingSamplePointBox = el('div', { class: 'qc-check-box theme-sample' },
    el('div', { class: 'qc-check-title' }, 'Sample Point'),
    el('div', { class: 'qc-check-subtitle' }, 'LKE characterization'),
    rfield('packaging', 'sampleCollectedAt', 'Collection date and time', packagingSampleCollectedInp),
    buildSamplePointsSection(draft?.samplePoints || [], ensureRunId, draft?.processingLot,
      () => packagingSampleCollectedInp.value, 'packaging'));
  const packagingQcStatus = el('span', { class: 'help' });
  const packagingQcSaveBtn = el('button', {
    type: 'button', class: 'secondary section-save', onclick: async () => {
      packagingQcStatus.textContent = ''; packagingQcSaveBtn.disabled = true;
      try {
        await dilutionSummary.save();
        const rid = await ensureRunId();
        await api('PUT', '/production/' + rid + '/stages/packaging', {
          ...packagingQcCheck.getPayload(),
          sampleCollectedAt: packagingSampleCollectedInp.value || null,
        });
        packagingQcStatus.textContent = 'Saved.';
      } catch (e) { packagingQcStatus.textContent = e.message; }
      packagingQcSaveBtn.disabled = false;
    }
  }, 'Save');
  const packagingStatus = el('span', { class: 'help' });
  const packagingSaveBtn = el('button', {
    type: 'button', class: 'secondary section-save', onclick: async () => {
      packagingStatus.textContent = ''; packagingSaveBtn.disabled = true;
      try {
        const rid = await ensureRunId();
        await api('PUT', '/production/' + rid + '/stages/packaging', {
          packagedAt: packagingPackagedInp.value || null,
        });
        packagingStatus.textContent = 'Saved.';
      } catch (e) { packagingStatus.textContent = e.message; }
      packagingSaveBtn.disabled = false;
    }
  }, 'Save');

  // Section progress (chips + overall meter) at the top of the log; refreshed from
  // the server after saves so the chips only turn green once a section's required
  // fields are all saved.
  const progressHost = el('div', {});
  function drawProgress(prog) { progressHost.innerHTML = ''; const p = stageProgress({ progress: prog }, { onSelect: key => jumpToSection(body, key) }); if (p) progressHost.append(p); }
  drawProgress(draft?.progress);
  let progressTimer = null;
  async function refreshProgress() {
    if (!draftId) return;
    try { drawProgress((await api('GET', '/production/' + draftId + '/progress')).progress); } catch (e) { /* non-critical */ }
  }
  const body = el('div', {},
    draft ? el('div', { class: 'summary-line', style: 'margin-bottom:10px' },
      sl('Run code', mono(draft.processingLot)), sl('SKU', skuName(draft.sku))) : null,
    progressHost, reqLegend(),
    el('details', { class: 'accordion' }, el('summary', {}, 'Initiation'),
      el('div', { class: 'accordion-body' },
        el('div', { class: 'form-row' },
          field(reqLabel('Product SKU'), skuSel),
          field(reqLabel('Run date'), el('input', { type: 'date', id: 'r_date', value: draft?.runDate || new Date().toISOString().slice(0, 10) }))),
        specPanel,
        el('div', { class: 'form-row', style: 'margin-top:8px' },
          field(reqLabel('Production Location'), productionLocationSelect('r_loc', draft?.location)),
          field(reqLabel('Operators'), operatorsSelect.el)),
        field('Notes', el('textarea', { id: 'r_notes', rows: '2', placeholder: 'Optional batch notes' }, draft?.notes || '')))),
    el('details', { class: 'accordion', ...(fresh ? { open: '' } : {}) }, el('summary', {}, 'Feedstock'),
      el('div', { class: 'accordion-body' },
        field('Filter totes', generalFilterInp),
        field('Stabilization method', stabFilterSel),
        el('div', { class: 'help' }, reqLabel('Select at least one tote')), pickHost, summary,
        el('h4', { style: 'margin:14px 0 4px;font-size:13px' }, 'Feedstock characterization'), feedstockHost)),
    homogenizationSection, extractionSection, separationSection,
    pasteurizationSection.box,
    el('details', { class: 'accordion' }, el('summary', {}, 'Dilution & Preservation'),
      el('div', { class: 'accordion-body' },
        dilutionSummary.box, dilutionPasses.box, dilutionsSection,
        packagingQcCheck.box,
        packagingSamplePointBox,
        el('div', { style: 'margin-top:6px' }, packagingQcSaveBtn, packagingQcStatus))),
    el('details', { class: 'accordion' }, el('summary', {}, 'Packaging'),
      el('div', { class: 'accordion-body' },
        rfield('packaging', 'packagedAt', 'Packaging date and time', packagingPackagedInp),
        packagingEntriesSection.box,
        el('div', { style: 'margin-top:6px' }, packagingSaveBtn, packagingStatus))));
  filterTotes();
  renderFeedstockCards();
  renderSpecPanel();
  ['click', 'change'].forEach(ev => body.addEventListener(ev, () => {
    clearTimeout(progressTimer); progressTimer = setTimeout(refreshProgress, 1200);
  }));

  function buildPayload() {
    return {
      sku: skuSel.value, toteIds: [...selected],
      runDate: body.querySelector('#r_date').value, location: body.querySelector('#r_loc').value,
      operators: operatorsSelect.value,
      notes: body.querySelector('#r_notes').value,
      feedstockDetails: feedstockState
    };
  }
  async function saveDraft(silent) {
    const payload = buildPayload();
    const r = draftId
      ? await api('PUT', '/production/drafts/' + draftId, payload)
      : await api('POST', '/production/drafts', payload);
    draftId = r.run.id;
    // "Save & close" is the prominent, most-used save action, so it also
    // commits the Packaging table's net container changes (empty body --
    // this only triggers the commit side effect, leaving packagedAt/QC/
    // Sample Point fields untouched) rather than leaving that silently
    // stuck until someone finds the Packaging accordion's own Save button.
    await api('PUT', '/production/' + draftId + '/stages/packaging', {});
    // Every selected tote gets the same lock-in treatment here as the
    // characterization card's own Save button: accepted totes move to WIP
    // (so they drop out of every pick table until this run finishes or is
    // discarded), rejected ones are pulled out of the run and relocated.
    const rejectedIds = r.rejectedToteIds || [];
    if (rejectedIds.length) {
      rejectedIds.forEach(tid => {
        selected.delete(tid);
        delete feedstockState[tid];
        const idx = totes.findIndex(x => x.id === tid);
        if (idx !== -1) totes[idx] = Object.assign({}, totes[idx], { location: 'QAQC Hold', status: 'hold' });
      });
      toast(rejectedIds.length + ' tote' + (rejectedIds.length === 1 ? '' : 's') + ' rejected during save and moved to QAQC Hold.');
      renderFeedstockCards();
    }
    totes.forEach((t, idx) => {
      if (selected.has(t.id) && t.status !== 'wip' && !rejectedIds.includes(t.id)) {
        totes[idx] = Object.assign({}, t, { status: 'wip' });
      }
    });
    filterTotes();
    if (!silent) { toast('Progress saved — resume it anytime from “In progress”.'); render(); }
  }
  async function finalizeRun() {
    const payload = buildPayload();
    if (!payload.toteIds.length) throw new Error('Select at least one tote.');
    if (!packagingEntriesSection.hasEntries()) throw new Error('Enter at least one packaged output quantity in the Packaging table.');
    // Required fields are checked server-side against what is saved, so first
    // save the run header + feedstock, then press every section's own Save
    // (so anything typed but not yet saved counts), and stop if one fails.
    await saveDraft(true);
    const failed = await pressSectionSaves(body);
    await refreshProgress();
    if (failed.length) throw new Error('A section could not be saved: ' + failed[0]);
    const r = draftId
      ? await api('POST', '/production/drafts/' + draftId + '/finalize', payload)
      : await api('POST', '/production', payload);
    toast(`Run ${r.processingLot}: ${fmt(r.inputKg, 0)} kg → ${fmt(r.outputLitres, 0)} L`);
    render();
  }
  // "Save & close" = the run header + feedstock, then every section's own Save, so entries not yet saved with their section's button are kept.
  // A section that cannot be saved keeps the window open with its message instead of silently dropping the entry.
  async function saveAndClose() {
    await saveDraft(true);
    const failed = await pressSectionSaves(body);
    if (failed.length) throw new Error('Progress was saved, but a section could not be saved: ' + failed[0]);
    toast('Progress saved — resume it anytime from “In progress”.'); render();
  }
  modal(draft ? 'Production run — ' + draft.processingLot : 'New production run', body, finalizeRun, 'Finalize run',
    { extraLabel: 'Save & close', onExtra: saveAndClose, wide: true, closeX: true, onClose: () => render() });
  if (opts && opts.section) jumpToSection(body, opts.section);
}

// Post-finalize view: feedstock characterization + process stages can still be
// filled in or corrected at any time (matching how the real paper logs are
// often completed days after the run), independent of the locked-in
// tote-consumption / packaging numbers (corrected via the separate Edit modal).
async function openProcessLog(run, section) {
  const stages = run.stages || {};
  const feedstockHost = el('div', {});
  (run.inputs || []).forEach(inp => {
    feedstockHost.append(buildFeedstockCard({
      markRequired: true,
      label: inp.toteLot + (inp.site ? '  ·  ' + inp.site : ''),
      initial: inp,
      mode: 'completed',
      onSave: async vals => { await api('PUT', '/production/' + run.id + '/inputs/' + inp.id, vals); },
      uploadPhoto: async (slot, file, b64) => {
        const r = await api('POST', '/production/' + run.id + '/inputs/' + inp.id + '/photo',
          { slot, filename: file.name, contentType: file.type || 'image/jpeg', dataB64: b64 });
        const updated = r.inputs.find(i => i.id === inp.id);
        return slot === 'surface' ? updated.surfacePhoto : updated.striationPhoto;
      },
      photoUrl: attId => attDownloadUrl(run.id, attId, false)
    }));
  });
  if (!run.inputs || !run.inputs.length) feedstockHost.append(el('div', { class: 'help' }, 'No feedstock characterization recorded.'));

  const getRunId = async () => run.id;
  const homogenizationSection =
    buildHomogenizationSection(getRunId, stages.homogenization, run.samplePoints || [], run.processingLot);
  // See openRun's identical comment: extractionSection's Save needs to poke
  // pasteurizationSection, which isn't built yet at this point.
  let pasteurizationSectionRef;
  const extractionSection = buildExtractionSection(getRunId, stages.extraction);
  const separationSection =
    buildSeparationSection(getRunId, stages.separation, run.samplePoints || [], run.processingLot,
      () => pasteurizationSectionRef?.refreshTds());
  // SKU is fixed once a run is finalized, so TDS/pH/Ksorbate targets need no
  // refresh wiring here.
  const pasteurizationSection = buildPasteurizationSection(
    getRunId, stages.pasteurization, run.samplePoints || [], run.processingLot,
    () => run.targetTds,
    () => stages.separation?.liquidTdsPct);
  pasteurizationSectionRef = pasteurizationSection;
  const dilutionSummary = buildDilutionAndPreservativesBox(
    getRunId, stages.dilution,
    () => State.ref.skus.find(s => s.code === run.sku)?.phTarget,
    () => State.ref.skus.find(s => s.code === run.sku)?.ksorbateTarget,
    () => pasteurizationSection.getPlan(),
    () => State.ref.skus.find(s => s.code === run.sku)?.nabenzoateTarget, run.id);
  pasteurizationSection.onPlanChange(() => dilutionSummary.refresh());
  dilutionSummary.refresh();
  const dilutionPasses = buildDilutionPassesSection(run.dilutionPasses || [], getRunId, {
    reagentWatch: dilutionSummary.reagentWatch,
    getTdsConc: () => stages.separation?.liquidTdsPct, getTdsTarget: () => run.targetTds,
    ensureSaved: async () => {
      const b = pasteurizationSection.box.querySelector('button.section-save');
      b.click(); for (let i = 0; i < 100 && b.disabled; i++) await new Promise(r => setTimeout(r, 50));
      const msg = b.nextElementSibling ? b.nextElementSibling.textContent.trim() : '';
      if (msg && msg !== 'Saved.') throw new Error(msg);
    },
    getStock: () => dilutionSummary.getStock(), getPass1Remaining: () => dilutionSummary.getRemaining(),
    getTargetPh: () => State.ref.skus.find(s => s.code === run.sku)?.phTarget });
  dilutionSummary.onChange(() => dilutionPasses.refresh());
  const dilutionsSection = buildDilutionsSection(run.dilutions || [], getRunId);
  const packagingEntriesSection = buildPackagingEntriesSection(run.packagingEntries || [], getRunId);
  const packagingPackagedInp = el('input', { type: 'datetime-local', value: stages.packaging?.packagedAt || '' });
  // QC Check + Sample Point ("LKE characterization") moved to the bottom of
  // Dilution & Preservation -- still packaging-stage columns/endpoints under
  // the hood (unchanged), just relocated in the UI, so they get their own
  // Save action separate from packagedAt's.
  const packagingQcCheck = buildQualityCheckBox('LKE characterization', stages.packaging || {}, { omitSlurrySolids: true, reqStage: 'packaging' });
  const packagingSampleCollectedInp = el('input', { type: 'datetime-local',
    value: stages.packaging?.sampleCollectedAt ? stages.packaging.sampleCollectedAt.replace('Z', '').slice(0, 16) : '' });
  const packagingSamplePointBox = el('div', { class: 'qc-check-box theme-sample' },
    el('div', { class: 'qc-check-title' }, 'Sample Point'),
    el('div', { class: 'qc-check-subtitle' }, 'LKE characterization'),
    rfield('packaging', 'sampleCollectedAt', 'Collection date and time', packagingSampleCollectedInp),
    buildSamplePointsSection(run.samplePoints || [], getRunId, run.processingLot,
      () => packagingSampleCollectedInp.value, 'packaging'));
  const packagingQcStatus = el('span', { class: 'help' });
  const packagingQcSaveBtn = el('button', {
    type: 'button', class: 'secondary section-save', onclick: async () => {
      packagingQcStatus.textContent = ''; packagingQcSaveBtn.disabled = true;
      try {
        await dilutionSummary.save();
        await api('PUT', '/production/' + run.id + '/stages/packaging', {
          ...packagingQcCheck.getPayload(),
          sampleCollectedAt: packagingSampleCollectedInp.value || null,
        });
        packagingQcStatus.textContent = 'Saved.';
      }
      catch (e) { packagingQcStatus.textContent = e.message; }
      packagingQcSaveBtn.disabled = false;
    }
  }, 'Save');
  const packagingStatus = el('span', { class: 'help' });
  const packagingSaveBtn = el('button', {
    type: 'button', class: 'secondary section-save', onclick: async () => {
      packagingStatus.textContent = ''; packagingSaveBtn.disabled = true;
      try {
        await api('PUT', '/production/' + run.id + '/stages/packaging', {
          packagedAt: packagingPackagedInp.value || null,
        });
        packagingStatus.textContent = 'Saved.';
      }
      catch (e) { packagingStatus.textContent = e.message; }
      packagingSaveBtn.disabled = false;
    }
  }, 'Save');

  // Initiation: the run-level details that used to live in the card's Edit window. Product is
  // fixed once finalized; the rest are editable under an amendment (like every other log entry).
  const plDate = el('input', { type: 'date', value: run.runDate || '' });
  const plLoc = productionLocationSelect('pl_loc', run.location);
  const plOps = buildOperatorsSelect(run.operators || '');
  const plNotes = el('textarea', { rows: '2', placeholder: 'Optional batch notes' }, run.notes || '');
  const plStatus = el('span', { class: 'help' });
  const plSave = el('button', { type: 'button', class: 'secondary', onclick: async () => {
    plStatus.textContent = ''; plSave.disabled = true;
    try {
      await api('PUT', '/production/' + run.id, { runDate: plDate.value, location: plLoc.value, operators: plOps.value, notes: plNotes.value });
      plStatus.textContent = 'Saved.';
    } catch (e) { plStatus.textContent = e.message; }
    plSave.disabled = false;
  } }, 'Save');
  const initiationSection = el('details', { class: 'accordion' }, el('summary', {}, 'Initiation'),
    el('div', { class: 'accordion-body' },
      el('div', { class: 'summary-line' }, sl('Product', skuName(run.sku)), el('span', { class: 'muted' }, 'fixed once the run is finalized')),
      el('div', { class: 'form-row' }, field(reqLabel('Run date'), plDate), field(reqLabel('Production Location'), plLoc)),
      field(reqLabel('Operators'), plOps.el),
      field('Notes', plNotes),
      el('div', { style: 'margin-top:6px' }, plSave, plStatus)));
  const logProgressHost = el('div', { class: 'allow-locked' });
  const drawLogProgress = prog => { logProgressHost.innerHTML = ''; const p = stageProgress({ progress: prog }, { onSelect: key => jumpToSection(body, key) }); if (p) logProgressHost.append(p); };
  drawLogProgress(run.progress);
  let logTimer = null;
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Run', run.processingLot), sl('SKU', skuName(run.sku)),
      el('span', { class: 'muted' }, 'Each section saves independently and can be filled in or corrected any time.')),
    logProgressHost, reqLegend(),
    logLockBanner(run),
    initiationSection,
    el('details', { class: 'accordion' }, el('summary', {}, 'Feedstock characterization'),
      el('div', { class: 'accordion-body' }, feedstockHost)),
    homogenizationSection, extractionSection, separationSection,
    pasteurizationSection.box,
    el('details', { class: 'accordion' }, el('summary', {}, 'Dilution & Preservation'),
      el('div', { class: 'accordion-body' },
        dilutionSummary.box, dilutionPasses.box, dilutionsSection,
        packagingQcCheck.box,
        packagingSamplePointBox,
        el('div', { style: 'margin-top:6px' }, packagingQcSaveBtn, packagingQcStatus))),
    el('details', { class: 'accordion' }, el('summary', {}, 'Packaging'),
      el('div', { class: 'accordion-body' },
        rfield('packaging', 'packagedAt', 'Packaging date and time', packagingPackagedInp),
        packagingEntriesSection.box,
        el('div', { style: 'margin-top:6px' }, packagingSaveBtn, packagingStatus))));

  ['click', 'change'].forEach(ev => body.addEventListener(ev, () => {
    clearTimeout(logTimer);
    logTimer = setTimeout(async () => {
      try { drawLogProgress((await api('GET', '/production/' + run.id + '/progress')).progress); } catch (e) { /* non-critical */ }
    }, 1200);
  }));
  const editable = !!run.amendment && canAmendLog();
  modal('Process log — ' + run.processingLot, body, async () => {
    if (editable) {          // under an amendment: save every section before closing so nothing typed is lost
      const failed = await pressSectionSaves(body);
      if (failed.length) throw new Error('A section could not be saved: ' + failed[0]);
    }
    render();
  }, editable ? 'Save & close' : 'Done', { wide: true, closeX: true, onClose: () => render() });
  if (!editable) lockLogBody(body);   // finalized: read-only unless an amendment is open and you may amend
  if (section) jumpToSection(body, section);
}

/* ---------------- Product release (review + Quality sign-off) ---------------- */
// Finalizing a run holds its finished goods in "Pending Release". A Production
// or Quality Manager reviews the production log and signs it off, then a Quality
// Manager signs to release for sale. Every signature re-asks for the password and
// is recorded server-side (who, when, what they attested to, hash of the log they
// saw) in an append-only, hash-chained trail; any later change to the log voids
// the sign-off.
const FG_STATUS = { pending_release: 'Pending Release', on_hand: 'On hand', hold: 'Hold', sold: 'Sold', disposed: 'Disposed' };
const fgStatusLabel = s => FG_STATUS[s] || s;
const fgStatusBadge = s => badge(s, fgStatusLabel(s));
const RELEASE_STATES = {
  pending_review: ['wip', 'Pending review'], pending_release: ['pending_release', 'Awaiting QA release'],
  released: ['on_hand', 'Released'], returned: ['low', 'Returned for correction'],
  rejected: ['low', 'Rejected — on hold'], legacy: ['sold', 'Released (pre-process)'],
  amending: ['hold', 'Under amendment'],
};
const releaseBadge = st => { const [c, t] = RELEASE_STATES[st] || ['sold', st || '—']; return badge(c, t); };
const RELEASE_EVENTS = {
  submitted: 'Run finalized — submitted', review_approved: 'Production log review APPROVED', review_returned: 'Returned for correction',
  resubmitted: 'Resubmitted for review', released: 'RELEASED for sale', release_rejected: 'Release REJECTED',
  reopened: 'Reopened', voided: 'Sign-off VOIDED (log changed)', legacy_release: 'Grandfathered as released',
  amendment_opened: 'Amendment OPENED (log unlocked)', amendment_submitted: 'Amendment submitted for review',
  amendment_cancelled: 'Amendment cancelled', integrity_repair: 'Integrity repair applied',
  rebaseline: 'Log hash re-baselined (snapshot format change)',
  lab_results_added: 'Lab results entered', lab_result_voided: 'Lab result VOIDED',
};
const shortHash = h => h ? h.slice(0, 10) + '…' : '—';
async function pageRelease(v) {
  const r = await api('GET', '/release');
  v.append(el('div', { class: 'page-head' }, el('h2', {}, 'Product Release'),
    el('div', { class: 'actions' },
      canIntegrity() ? el('button', { class: 'secondary', onclick: openIntegrityCheck }, 'Data integrity check') : null,
      el('button', { class: 'secondary', onclick: async () => {
        const c = await api('GET', '/release/verify');
        toast(c.ok ? 'Audit trail verified — ' + c.events + ' events, hash chain intact.'
          : 'AUDIT TRAIL BROKEN at event #' + c.brokenAtEventId + ' (run ' + c.runId + ')', !c.ok);
      } }, 'Verify audit trail'))));
  v.append(el('div', { class: 'help', style: 'margin-bottom:10px' },
    'Finished goods stay Pending Release (cannot be shipped) until the production log is reviewed and signed off by a Production or Quality Manager, '
    + 'and then released by a Quality Manager. Your permissions: '
    + (r.me.canReview ? (State.user.isQualityManager ? 'Quality Manager (review + release)' : 'Production Manager (review)') : 'none — view only')
    + (r.legacyCount ? '. ' + r.legacyCount + ' earlier run(s) pre-date this process and are treated as released.' : '.')));
  const groups = [
    ['Under amendment', ['amending']],
    ['Awaiting production-log review', ['pending_review']],
    ['Awaiting Quality release', ['pending_release']],
    ['Returned for correction / rejected', ['returned', 'rejected']],
    ['Released', ['released']],
  ];
  for (const [title, states] of groups) {
    const rows = r.runs.filter(x => states.includes(x.state));
    v.append(el('h3', { style: 'margin:16px 0 6px' }, title + ' (' + rows.length + ')'));
    if (!rows.length) { v.append(el('div', { class: 'help' }, 'Nothing here.')); continue; }
    v.append(table(['Run', 'Product', 'Finalized', 'Finished goods', 'Litres', 'Status', 'Lab results', 'Reviewed', 'Released'],
      rows.map(x => [mono(x.lot), skuName(x.sku), fmtWhen(x.finalizedAt) + (x.finalizedBy ? ' · ' + x.finalizedBy : ''),
        x.lots.map(l => fmt(l.qty) + ' × ' + l.packageSize).join(', ') || '—',
        num(fmt(x.lots.reduce((a, l) => a + (l.litres || 0), 0), 0)), releaseBadge(x.state), labBadge(x.lab),
        x.reviewedBy ? x.reviewedBy + ' · ' + fmtWhen(x.reviewedAt) : '—',
        x.releasedBy ? x.releasedBy + ' · ' + fmtWhen(x.releasedAt) : '—']),
      [false, false, false, false, true, false, false, false, false], i => openReleaseRun(rows[i].id)));
  }
  v.append(el('div', { class: 'help', style: 'margin-top:10px' }, 'Click a row to review the production log, sign, and see the full audit trail.'));
}
const humanKey = k => k.replace(/([A-Z])/g, ' $1').replace(/^./, c => c.toUpperCase());
function releaseLogSummary(run) {
  const kv = (obj) => Object.entries(obj).filter(([, x]) => x !== null && x !== undefined && x !== '' && typeof x !== 'object');
  const box = (title, rows) => el('details', { class: 'accordion' }, el('summary', {}, title),
    el('div', { class: 'accordion-body' }, rows));
  const kvTable = obj => { const e = kv(obj); return e.length ? table(['Field', 'Value'], e.map(([k, x]) => [humanKey(k), String(x === true ? 'Yes' : x === false ? 'No' : x)]), [false, false])
    : el('div', { class: 'help' }, 'Nothing recorded.'); };
  const out = el('div', {});
  out.append(box('Run summary', kvTable({ processingLot: run.processingLot, sku: skuName(run.sku), runDate: run.runDate, finalizedAt: run.finalizedAt,
    finalizedBy: run.release && run.release.finalizedBy, operators: run.operators, inputKg: run.inputKg, outputLitres: run.outputLitres,
    targetTds: run.targetTds, citricKg: run.citricKg, sorbateKg: run.sorbateKg, nabenzoateKg: run.nabenzoateKg, ibcUsed: run.ibcUsed,
    location: run.location, notes: run.notes })));
  out.append(box('Feedstock (' + (run.inputs || []).length + ' tote(s))', (run.inputs || []).length
    ? table(['Tote', 'Decision', 'Weight kg', 'pH', 'ORP', 'Odour', 'Notes'],
      run.inputs.map(i => [mono(i.toteLot), i.decision || '—', i.weightKg != null ? fmt(i.weightKg, 1) : '—', i.ph ?? '—', i.orp ?? '—', i.odour || '—', i.notes || '—']),
      [false, false, true, true, true, false, false]) : el('div', { class: 'help' }, 'No feedstock characterization recorded.')));
  for (const [key, label] of [['homogenization', 'Homogenization'], ['extraction', 'Extraction'], ['separation', 'Separation'], ['pasteurization', 'Pasteurization'], ['dilution', 'Dilution & Preservation (summary)'], ['packaging', 'Packaging / LKE characterization']]) {
    out.append(box(label, kvTable((run.stages || {})[key] || {})));
  }
  out.append(box('Dilution tanks (' + (run.dilutions || []).length + ')', (run.dilutions || []).length
    ? table(['Tank', 'Initial L', 'Water L', 'Final L', 'Sorbate kg', 'Benzoate kg', 'Citric kg', 'Preservatives added', 'Notes'],
      run.dilutions.map(d => [d.tank || '—', d.volumeInitialL ?? '—', d.waterRequiredL ?? '—', d.volumeFinalL ?? '—', d.sorbateRequiredKg ?? '—',
        d.benzoateRequiredKg ?? '—', d.citricKg ?? '—', d.preservativesAdded ? 'Yes' : 'No', d.notes || '—']), [false, true, true, true, true, true, true, false, false])
    : el('div', { class: 'help' }, 'None.')));
  out.append(box('Sample points (' + (run.samplePoints || []).length + ')', (run.samplePoints || []).length
    ? table(['Stage', 'Type', 'Description', 'Qty', 'Container'], run.samplePoints.map(s => [s.stage || '—', s.type || '—', s.description || '—', s.qty ?? '—', s.container || '—']), [false, false, false, true, false])
    : el('div', { class: 'help' }, 'None.')));
  out.append(box('Packaging entries (' + (run.packagingEntries || []).length + ')', (run.packagingEntries || []).length
    ? table(['Container', 'Qty'], run.packagingEntries.map(p => [p.containerUnit || '—', fmt(p.qty)]), [false, true]) : el('div', { class: 'help' }, 'None.')));
  out.append(box('Revision history (Rev ' + (run.revision || 1) + ')', revisionTracker(run)));
  out.append(box('Documents (' + (run.attachments || []).length + ')', (run.attachments || []).length
    ? table(['File', 'Type'], run.attachments.map(a => [a.filename || a.name || '—', a.contentType || a.type || '—']), [false, false]) : el('div', { class: 'help' }, 'None attached.')));
  return out;
}
function releaseAuditCsv(d) {
  const lines = [];
  const add = (...c) => lines.push(c.map(x => '"' + String(x == null ? '' : x).replace(/"/g, '""') + '"').join(','));
  add('Product release audit trail', d.lot, d.label);
  add('Event #', 'When (UTC)', 'Event', 'Signed by', 'Email', 'Capacity', 'Statement', 'Comment', 'Production-log hash (SHA-256)', 'Detail', 'Entry hash');
  d.events.forEach(e => add(e.id, e.at, RELEASE_EVENTS[e.type] || e.type, e.user, e.email, e.capacity, e.meaning, e.comment, e.logHash, e.detail ? JSON.stringify(e.detail) : '', e.entryHash));
  const a = el('a', { href: URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' })), download: 'release-audit-' + d.lot + '.csv' });
  document.body.append(a); a.click(); a.remove();
}
function printReleaseRecord(d) {
  const w = window.open('', '_blank');
  if (!w) return toast('Allow pop-ups to print.', true);
  const esc = x => String(x == null ? '' : x).replace(/&/g, '&amp;').replace(/</g, '&lt;');
  w.document.write('<html><head><title>Release record ' + esc(d.lot) + '</title><style>body{font-family:Segoe UI,Arial,sans-serif;margin:24px;font-size:12px}'
    + 'table{border-collapse:collapse;width:100%}td,th{border:1px solid #888;padding:4px 6px;text-align:left;vertical-align:top}.h{font-family:monospace;font-size:10px;word-break:break-all}</style></head><body>'
    + '<h2>Product release record — ' + esc(d.lot) + '</h2><p>Product: ' + esc(skuName(d.sku)) + ' · Status: <b>' + esc(d.label) + '</b> · Finalized: '
    + esc(d.finalizedAt) + ' by ' + esc(d.finalizedBy) + '</p><p>Finished goods: ' + esc(d.lots.map(l => l.lot + ' (' + l.qty + ' × ' + l.packageSize + ', ' + fgStatusLabel(l.status) + ')').join('; '))
    + '</p><p>Current production-log hash: <span class="h">' + esc(d.currentLogHash) + '</span><br>Hash at review: <span class="h">' + esc(d.reviewedLogHash || '—')
    + '</span><br>Audit chain: ' + (d.chain.ok ? 'verified (' + d.chain.events + ' events)' : 'BROKEN at event ' + d.chain.brokenAtEventId) + '</p>'
    + '<table><tr><th>#</th><th>When (UTC)</th><th>Event</th><th>Signed by</th><th>Statement / comment</th><th>Log hash</th></tr>'
    + d.events.map(e => '<tr><td>' + e.id + '</td><td>' + esc(e.at) + '</td><td>' + esc(RELEASE_EVENTS[e.type] || e.type) + '</td><td>' + esc(e.user) + (e.capacity ? '<br>' + esc(e.capacity) : '')
      + '</td><td>' + esc(e.meaning) + (e.comment ? '<br><i>' + esc(e.comment) + '</i>' : '') + '</td><td class="h">' + esc(e.logHash || '') + '</td></tr>').join('')
    + '</table><p>Generated ' + new Date().toISOString() + '</p></body></html>');
  w.document.close(); w.focus(); w.print();
}
async function openReleaseRun(id) {
  const d = await api('GET', '/release/runs/' + id);
  const me = d.me;
  const body = el('div', {});
  const refresh = async () => { body.closest('.modal-bg')?.remove(); await openReleaseRun(id); if (State.tab === 'release') { /* list refreshes on next visit */ } };
  body.append(el('div', { class: 'summary-line' }, sl('Run', d.lot), sl('Product', skuName(d.sku)), el('span', {}, 'Status: ', releaseBadge(d.state)),
    sl('Finalized', fmtWhen(d.finalizedAt) + (d.finalizedBy ? ' by ' + d.finalizedBy : ''))));
  body.append(table(['FG lot', 'Pack', 'Units', 'Litres', 'Status'], d.lots.map(l => [mono(l.lot), l.packageSize, fmt(l.qty), num(fmt(l.litres, 0)), fgStatusBadge(l.status)]), [false, false, true, true, false]));
  body.append(el('div', { class: 'sign-box' }, el('h4', {}, 'Laboratory results & Certificate of Analysis'),
    el('div', { style: 'display:flex;gap:10px;align-items:center;flex-wrap:wrap' }, labBadge(d.lab), el('span', { class: 'help' }, labSummaryText(d.lab)),
      el('button', { type: 'button', class: 'secondary', onclick: () => openLabResults(d.run) }, 'Lab results…'),
      el('button', { type: 'button', class: 'secondary', onclick: () => window.open(coaPdfUrl(id, false), '_blank') }, 'View CoA')),
    d.lab.missingRequired.length ? el('div', { class: 'help', style: 'color:var(--danger);font-weight:600;margin-top:6px' },
      'Release is blocked until these results are on file: ' + d.lab.missingRequired.join(', ') + '.') : null,
    d.lab.failed.length ? el('div', { class: 'help', style: 'color:var(--danger);font-weight:600;margin-top:6px' },
      'Outside specification: ' + d.lab.failed.join(', ') + '. A comment is required to release this lot.') : null));
  const integrity = [];
  if (d.logMatchesReview === true) integrity.push('Production log unchanged since review (hash ' + shortHash(d.reviewedLogHash) + ').');
  if (d.logMatchesReview === false) integrity.push('WARNING: the production log no longer matches the log that was reviewed.');
  integrity.push(d.chain.ok ? 'Audit trail hash chain verified (' + d.chain.events + ' events).' : 'WARNING: audit trail hash chain is BROKEN at event #' + d.chain.brokenAtEventId + '.');
  body.append(el('div', { class: 'help', style: 'margin:8px 0;' + (d.logMatchesReview === false || !d.chain.ok ? 'color:var(--danger);font-weight:600' : '') }, integrity.join(' ')));
  body.append(el('div', { style: 'margin:6px 0' },
    el('button', { type: 'button', class: 'secondary', onclick: () => openProcessLog(d.run) }, '📋 Open full process log'), ' ',
    el('span', { class: 'help' }, 'Review the log first, then sign below.')));
  body.append(releaseLogSummary(d.run));

  function signBox(title, attest, decisions, endpoint, opts = {}) {
    const decSel = decisions.length > 1 ? selectFrom('', decisions) : null;
    const cap = (!opts.quality && State.user.isProductionManager && State.user.isQualityManager) ? selectFrom('', [['Production Manager', 'Production Manager'], ['Quality Manager', 'Quality Manager']]) : null;
    const comment = el('textarea', { rows: '2', placeholder: opts.commentHint || 'Comment (required when returning/rejecting/reopening)' });
    const pw = el('input', { type: 'password', autocomplete: 'off', placeholder: 'Re-enter your password to sign' });
    const err = el('div', { class: 'error' });
    const btn = el('button', { type: 'button' }, opts.button || 'Sign');
    btn.addEventListener('click', async () => {
      err.textContent = ''; btn.disabled = true;
      try {
        await api('POST', '/release/runs/' + id + '/' + endpoint, {
          decision: decSel ? decSel.value : decisions[0][0], comment: comment.value || null, password: pw.value,
          capacity: cap ? cap.value : undefined });
        toast('Signature recorded'); await refresh();
      } catch (e) { err.textContent = e.message; btn.disabled = false; }
    });
    return el('div', { class: 'sign-box' }, el('h4', {}, title),
      el('div', { class: 'sign-attest' }, attest),
      decSel ? field('Decision', decSel) : null, cap ? field('Signing as', cap) : null,
      field('Comment', comment), field('Password', pw), err, btn);
  }
  if (d.state === 'pending_review') {
    body.append(me.canReview ? signBox('Production-log review sign-off',
      'By signing I confirm I have reviewed the production log for ' + d.lot + ' and that it is complete and accurate (or I am returning it for correction).',
      [['approve', 'Approve — log reviewed'], ['return', 'Return for correction']], 'review', { button: 'Sign review' })
      : el('div', { class: 'help sign-box' }, 'Awaiting review by a Production Manager or Quality Manager. You do not have review permission.'));
  } else if (d.state === 'returned') {
    const c = el('textarea', { rows: '2', placeholder: 'What was corrected?' }), e2 = el('div', { class: 'error' });
    const b = el('button', { type: 'button', onclick: async () => {
      e2.textContent = '';
      try { await api('POST', '/release/runs/' + id + '/resubmit', { comment: c.value }); toast('Resubmitted for review'); await refresh(); }
      catch (e) { e2.textContent = e.message; } } }, 'Resubmit for review');
    body.append(el('div', { class: 'sign-box' }, el('h4', {}, 'Returned for correction'),
      el('div', { class: 'help' }, 'The production log is locked: on the Production tab use “Amend run” (category: data entry error / late entry) to make the corrections and submit the amendment — that sends it back for review. If nothing needs changing, describe why here and resubmit.'), field('Corrections made', c), e2, b));
  } else if (d.state === 'pending_release') {
    body.append(me.canRelease ? signBox('Quality release sign-off',
      'By signing I confirm this product conforms to specification and is released for sale — or I am rejecting it and holding the lot(s).',
      [['release', 'Release for sale'], ['reject', 'Reject — hold']], 'release', { quality: true, button: 'Sign release',
        commentHint: d.lab.failed.length ? 'REQUIRED to release: why is this lot released with results outside specification (' + d.lab.failed.join(', ') + ')?' : undefined })
      : el('div', { class: 'help sign-box' }, 'Review is complete. Awaiting release by a Quality Manager. You do not have release permission.'));
  }
  if (['released', 'rejected', 'pending_release', 'returned'].includes(d.state) && me.canRelease) {
    body.append(el('details', { class: 'accordion' }, el('summary', {}, 'Reopen this run (Quality Manager)'),
      el('div', { class: 'accordion-body' }, el('div', { class: 'help' }, 'Voids the existing review/release and returns unsold lots to Pending Release. Units already shipped are recorded in the trail.'),
        signBox('Reopen', 'By signing I confirm the prior review/release no longer stands and a new review is required.', [['reopen', 'Reopen']], 'reopen', { quality: true, button: 'Sign & reopen', commentHint: 'Reason for reopening (required)' }))));
  }
  body.append(el('h3', { style: 'margin:14px 0 6px;font-size:14px' }, 'Audit trail'));
  body.append(el('div', { class: 'tablewrap' }, el('table', {},
    el('thead', {}, el('tr', {}, ...['#', 'When', 'Event', 'Signed by', 'Statement / comment', 'Log hash'].map(h => el('th', {}, h)))),
    el('tbody', {}, ...d.events.map(e => el('tr', {},
      el('td', { class: 'muted' }, e.id), el('td', { class: 'muted' }, fmtWhen(e.at)), el('td', {}, el('b', {}, RELEASE_EVENTS[e.type] || e.type)),
      el('td', {}, e.user || '—', e.capacity ? el('div', { class: 'muted' }, e.capacity) : null),
      el('td', {}, e.meaning || '', e.comment ? el('div', {}, '“' + e.comment + '”') : null,
        e.detail && e.detail.alreadyShippedLots && e.detail.alreadyShippedLots.length
          ? el('div', { class: 'muted' }, 'Already shipped: ' + e.detail.alreadyShippedLots.map(x => x.qty + ' × ' + x.lot + ' (' + x.shipment + ')').join('; ')) : null),
      el('td', { class: 'hashtxt', title: e.logHash || '' }, shortHash(e.logHash))))))));
  body.append(el('div', { style: 'margin-top:8px' },
    el('button', { type: 'button', class: 'secondary', onclick: () => releaseAuditCsv(d) }, '⬇ Audit trail CSV'), ' ',
    el('button', { type: 'button', class: 'secondary', onclick: () => printReleaseRecord(d) }, '🖨 Print release record')));
  modal('Product release — ' + d.lot, body, async () => { render(); }, 'Close', { wide: true, noCancel: true });
}

/* ---------------- Finished goods ---------------- */
async function pageFG(v) {
  v.append(el('div', { class: 'page-head' }, el('h2', {}, 'Finished Goods'),
    el('div', { class: 'actions' }, el('button', { class: 'secondary', onclick: () => printLabelsFromFG() }, 'Print all on-hand labels'))));
  const r = await api('GET', '/fg');
  window.__fg = r.fg;
  if (!r.fg.length) { v.append(el('div', { class: 'empty card' }, 'No finished goods yet.')); return; }
  const bulkBar = el('div', { class: 'bulkbar hidden' });
  const host = el('div', {});
  v.append(bulkBar, host);
  const selected = new Set();
  function updateBulk() {
    const n = selected.size;
    bulkBar.classList.toggle('hidden', n === 0);
    bulkBar.innerHTML = '';
    if (!n) return;
    bulkBar.append(
      el('span', {}, el('b', {}, n), ' lot' + (n === 1 ? '' : 's') + ' selected'),
      el('button', { onclick: () => bulkMoveFG([...selected]) }, 'Move selected'),
      el('button', { class: 'danger', onclick: () => disposeSimple('fg', [...selected], 'lot') }, 'Dispose / write off'),
      el('button', { class: 'secondary', onclick: () => { selected.clear(); draw(); } }, 'Clear'));
  }
  function draw() {
    const movable = r.fg.filter(f => f.status !== 'sold');
    [...selected].forEach(id => { if (!movable.some(f => f.id === id)) selected.delete(id); });
    host.innerHTML = '';
    const allCb = el('input', { type: 'checkbox', title: 'Select all', onchange: () => {
      movable.forEach(f => allCb.checked ? selected.add(f.id) : selected.delete(f.id)); draw();
    } });
    allCb.checked = movable.length > 0 && movable.every(f => selected.has(f.id));
    host.append(table(
      [allCb, 'FG lot', 'SKU', 'Pack', 'Units', 'Litres', 'TDS', 'Produced', 'Location', 'Status', ''],
      r.fg.map(f => [
        rowCheck(f, selected, updateBulk),
        mono(f.lot), skuName(f.sku), f.packageSize, fmt(f.qty), num(fmt(f.litres, 0)),
        f.tds != null ? f.tds + '%' : '—', f.producedDate || '—', f.location || '—',
        fgStatusBadge(f.status), rowActions([
          f.status !== 'sold' ? ['Move', () => moveFG(f)] : null,
          ['Label', () => printLabels([fgLabel(f)])],
          ['Edit', () => editFG(f)]
        ])
      ]), [false, false, false, false, true, true, false, false, false, false, false]));
    updateBulk();
  }
  draw();
}
function bulkMoveFG(ids) {
  const locs = State.ref.locations.map(l => [l, l]);
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Moving', ids.length + ' lot' + (ids.length === 1 ? '' : 's')),
      el('span', { class: 'muted' }, 'entire lots relocate; use a row’s Move for partial')),
    el('div', { class: 'form-row' },
      field('Move to', editableSelect(locs, 'mb_loc')),
      field('Date', el('input', { type: 'date', id: 'mb_date', value: todayStr() }))),
    field('Note (optional)', el('input', { id: 'mb_note' })));
  modal('Move ' + ids.length + ' FG lots', body, async () => {
    const to = body.querySelector('#mb_loc').value.trim();
    if (!to) throw new Error('Choose a destination location.');
    const res = await api('POST', '/fg/move-bulk', { ids, toLocation: to, date: body.querySelector('#mb_date').value, note: body.querySelector('#mb_note').value || null });
    State.ref = await api('GET', '/refdata');
    toast('Moved ' + res.moved + ' lot' + (res.moved === 1 ? '' : 's') + ' to ' + to); render();
  }, 'Move');
}
function printLabelsFromFG() {
  const fg = (window.__fg || []).filter(f => f.status === 'on_hand');
  if (!fg.length) return toast('No on-hand finished goods.', true);
  printLabels(fg.map(f => fgLabel(f)));
}
function editFG(f) {
  const body = el('div', {},
    el('div', { class: 'form-row' },
      field('Units on hand', el('input', { type: 'number', id: 'f_qty', value: f.qty, min: '0' })),
      f.status === 'pending_release'
        ? field('Status', el('div', {}, fgStatusBadge(f.status), el('div', { class: 'help' }, 'Set by the Product Release process (Product Release tab).')))
        : field('Status', selectFrom('', [['on_hand', 'On hand'], ['hold', 'Hold / QA'], ['sold', 'Sold / shipped']], null, 'f_status'))),
    el('div', { class: 'form-row' },
      field('TDS (%)', el('input', { type: 'number', step: '0.1', id: 'f_tds', value: f.tds ?? '' })),
      field('Location', el('input', { id: 'f_loc', value: f.location || '' }))));
  if (f.status !== 'pending_release') body.querySelector('#f_status').value = f.status;
  modal('Edit FG lot ' + f.lot, body, async () => {
    await api('PUT', '/fg/' + f.id, { qty: +body.querySelector('#f_qty').value, status: f.status === 'pending_release' ? f.status : body.querySelector('#f_status').value, tds: body.querySelector('#f_tds').value || null, location: body.querySelector('#f_loc').value });
    toast('Updated'); render();
  }, 'Save');
}

/* ---------------- Shipping ---------------- */
async function pageShipping(v) {
  v.append(el('div', { class: 'page-head' }, el('h2', {}, 'Shipping'),
    el('div', { class: 'actions' },
      el('button', { class: 'secondary', onclick: manageCustomers }, 'Customers'),
      el('button', { onclick: newShipment }, '+ New shipment'))));
  const custF = selectFrom('', [['', 'All customers'], ...State.ref.customers.map(c => [c.id, c.name])], reload, 'sh_cust');
  v.append(el('div', { class: 'toolbar' }, el('label', { style: 'margin:0 8px 0 0' }, 'Customer'), custF));
  const host = el('div', {});
  v.append(host);
  async function reload() {
    const q = custF.value ? '?customer=' + custF.value : '';
    const r = await api('GET', '/shipments' + q);
    host.innerHTML = '';
    if (!r.shipments.length) { host.append(el('div', { class: 'empty card' }, 'No shipments yet. Click “New shipment” to ship finished goods to a customer.')); return; }
    host.append(table(
      ['Shipment', 'Date', 'Customer', 'Lines', 'Litres', 'Carrier', 'Tracking', 'Status', ''],
      r.shipments.map(s => [
        mono(s.shipmentNo), s.shipDate, s.customer || '—', fmt(s.lineCount), num(fmt(s.litres, 0)),
        s.carrier || '—', s.trackingNo || '—', badge(shipBadge(s.status), s.status),
        rowActions([
          ['Packing slip', () => openShipment(s.id, 'slip')],
          ['Trace', () => openShipment(s.id, 'trace')],
          ['Edit', () => openShipment(s.id, 'edit')]
        ])
      ]), [false, false, false, true, true, false, false, false, false]));
  }
  reload();
}
function shipBadge(st) { return st === 'cancelled' ? 'low' : st === 'delivered' ? 'on_hand' : 'hold'; }

async function newShipment() {
  if (!State.ref.customers.length) { toast('Add a customer first.', true); return manageCustomers(); }
  const fg = (await api('GET', '/fg?status=on_hand')).fg.filter(f => f.qty > 0);
  if (!fg.length) { return toast('No finished goods on hand to ship.', true); }
  const custSel = selectFrom('', State.ref.customers.map(c => [c.id, c.name]), null, 'sh_c');
  const qtyInputs = {};
  const lineHost = el('div', { class: 'tote-pick' });
  const summary = el('div', { class: 'summary-line' });
  function recompute() {
    let units = 0, litres = 0, lines = 0;
    for (const f of fg) { const q = +qtyInputs[f.id].value || 0; if (q > 0) { lines++; units += q; litres += q * f.litresEach; } }
    summary.innerHTML = ''; summary.append(sl('Lines', lines), sl('Units', fmt(units)), sl('Litres', fmt(litres, 0) + ' L'));
  }
  const tbl = el('table', {}, el('thead', {}, el('tr', {},
    el('th', {}, 'FG lot'), el('th', {}, 'Product'), el('th', {}, 'Pack'), el('th', {}, 'Location'),
    el('th', { class: 'num' }, 'On hand'), el('th', { class: 'num' }, 'Ship qty'))));
  const tb = el('tbody', {});
  for (const f of fg) {
    const inp = el('input', { type: 'number', min: '0', max: f.qty, value: '0', style: 'width:80px', oninput: recompute });
    qtyInputs[f.id] = inp;
    tb.append(el('tr', {}, el('td', { class: 'mono' }, f.lot), el('td', {}, skuName(f.sku)),
      el('td', {}, f.packageSize), el('td', {}, f.location || '—'),
      el('td', { class: 'num' }, fmt(f.qty)), el('td', { class: 'num' }, inp)));
  }
  tbl.append(tb); lineHost.append(tbl);
  const locs = State.ref.customers.map(c => [c.id, c.name]);
  const body = el('div', {},
    el('div', { class: 'form-row' }, field('Customer', custSel),
      field('Ship date', el('input', { type: 'date', id: 'sh_date', value: todayStr() }))),
    el('div', { class: 'form-row' }, field('Carrier', el('input', { id: 'sh_carrier', placeholder: 'e.g. truck / courier' })),
      field('Tracking #', el('input', { id: 'sh_track' }))),
    field('Customer PO / reference', el('input', { id: 'sh_ref' })),
    el('h3', { style: 'margin:14px 0 6px;font-size:14px' }, 'Finished goods to ship'), lineHost, summary,
    field('Notes', el('textarea', { id: 'sh_notes', rows: '2' })));
  recompute();
  modal('New shipment', body, async () => {
    const lines = fg.map(f => ({ fgLotId: f.id, qty: +qtyInputs[f.id].value || 0 })).filter(l => l.qty > 0);
    if (!lines.length) throw new Error('Enter a ship quantity for at least one lot.');
    const r = await api('POST', '/shipments', {
      customerId: +custSel.value, shipDate: body.querySelector('#sh_date').value,
      carrier: body.querySelector('#sh_carrier').value || null, trackingNo: body.querySelector('#sh_track').value || null,
      reference: body.querySelector('#sh_ref').value || null, notes: body.querySelector('#sh_notes').value || null, lines
    });
    toast('Shipment ' + r.shipment.shipmentNo + ' created'); render();
  }, 'Create shipment');
}

async function openShipment(id, mode) {
  const s = (await api('GET', '/shipments/' + id)).shipment;
  if (mode === 'slip') return printPackingSlip(s);
  if (mode === 'edit') return editShipment(s);
  // trace view
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Shipment', s.shipmentNo), sl('Customer', s.customer || '—'),
      sl('Date', s.shipDate), sl('Status', s.status)),
    el('div', { class: 'help', style: 'margin:6px 0' }, 'Full provenance — each shipped lot traced back through its production run to the source stabilized totes/IBCs.'));
  for (const ln of s.lines) {
    body.append(el('div', { class: 'card', style: 'margin:8px 0;padding:12px' },
      el('div', {}, el('b', {}, fmt(ln.qty) + ' × ' + ln.packageSize), '  ', el('span', { class: 'mono' }, ln.lot),
        '  ', el('span', { class: 'muted' }, skuName(ln.sku) + ' · ' + fmt(ln.litres, 0) + ' L')),
      el('div', { class: 'muted', style: 'font-size:12px;margin-top:6px' },
        'Produced in run ', el('span', { class: 'mono' }, ln.processingLot || '—'),
        ln.runDate ? ' (' + ln.runDate + ')' : ''),
      el('div', { class: 'muted', style: 'font-size:12px;margin-top:4px' },
        'Source totes/IBCs: ', ln.inputTotes.length ? el('span', { class: 'mono' }, ln.inputTotes.join(', ')) : '—')));
  }
  modal('Traceability — ' + s.shipmentNo, body, async () => {}, 'Done');
}

function editShipment(s) {
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Shipment', s.shipmentNo), sl('Customer', s.customer || '—'),
      sl('Litres', fmt(s.litres, 0) + ' L')),
    el('div', { class: 'form-row' },
      field('Status', selectFrom('', [['shipped', 'Shipped'], ['delivered', 'Delivered'], ['cancelled', 'Cancelled (restock)']], null, 'se_status')),
      field('Carrier', el('input', { id: 'se_carrier', value: s.carrier || '' }))),
    el('div', { class: 'form-row' },
      field('Tracking #', el('input', { id: 'se_track', value: s.trackingNo || '' })),
      field('Customer PO / reference', el('input', { id: 'se_ref', value: s.reference || '' }))),
    field('Notes', el('textarea', { id: 'se_notes', rows: '2' }, s.notes || '')),
    el('div', { class: 'help' }, 'Cancelling a shipment returns its units to finished-goods stock.'));
  body.querySelector('#se_status').value = s.status;
  modal('Edit shipment — ' + s.shipmentNo, body, async () => {
    await api('PUT', '/shipments/' + s.id, {
      status: body.querySelector('#se_status').value, carrier: body.querySelector('#se_carrier').value,
      trackingNo: body.querySelector('#se_track').value, reference: body.querySelector('#se_ref').value,
      notes: body.querySelector('#se_notes').value
    });
    toast('Shipment updated'); render();
  }, 'Save');
}

function printPackingSlip(s) {
  const w = window.open('', '_blank');
  if (!w) return toast('Allow pop-ups to print the packing slip.', true);
  const cust = State.ref.customers.find(c => c.id === s.customerId) || {};
  const rows = s.lines.map(ln => `<tr><td class="mono">${ln.lot}</td><td>${skuName(ln.sku)}</td><td>${ln.packageSize}</td>
    <td style="text-align:right">${fmt(ln.qty)}</td><td style="text-align:right">${fmt(ln.litres, 0)} L</td>
    <td class="mono" style="font-size:10px">${(ln.processingLot || '—')}</td></tr>`).join('');
  const css = `body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:28px;color:#0c2b27;}
    .hd{display:flex;justify-content:space-between;border-bottom:3px solid #15564F;padding-bottom:10px;}
    .co{font-size:20px;font-weight:700;color:#15564F;} .sub{font-size:11px;color:#666;}
    h1{font-size:16px;margin:16px 0 2px;} .meta{display:flex;gap:40px;margin:10px 0;font-size:12px;}
    .meta b{display:block;color:#15564F;font-size:11px;text-transform:uppercase;}
    table{width:100%;border-collapse:collapse;margin-top:12px;font-size:12px;}
    th{background:#eef3f2;text-align:left;padding:7px 9px;border-bottom:2px solid #15564F;color:#15564F;}
    td{padding:7px 9px;border-bottom:1px solid #dde7e4;} .mono{font-family:ui-monospace,Consolas,monospace;}
    .tot{margin-top:10px;text-align:right;font-size:13px;font-weight:700;}
    .sign{margin-top:48px;display:flex;gap:40px;} .sign div{flex:1;border-top:1px solid #999;padding-top:5px;font-size:11px;color:#666;}
    .foot{margin-top:8px;font-size:10px;color:#999;}`;
  const addr = (s.shipTo || cust.address || '').replace(/\n/g, '<br>');
  const logo = location.origin + '/logo.png';
  w.document.write(`<!doctype html><html><head><title>Packing Slip ${s.shipmentNo}</title><style>${css}
    .hd .co{display:flex;align-items:center;gap:10px;} .hd img{height:46px;width:auto;}</style></head><body>
    <div class="hd"><div><div class="co"><img src="${logo}" alt="">CASCADIA SEAWEED</div><div class="sub">Kelp Processing &middot; Liquid Kelp Extract</div></div>
      <div style="text-align:right"><h1 style="margin:0">PACKING SLIP</h1><div class="mono">${s.shipmentNo}</div></div></div>
    <div class="meta">
      <div><b>Ship to</b>${cust.name || '—'}${addr ? '<br>' + addr : ''}${cust.contact ? '<br>Attn: ' + cust.contact : ''}</div>
      <div><b>Ship date</b>${s.shipDate}<br><b style="margin-top:8px">Status</b>${s.status}</div>
      <div><b>Carrier</b>${s.carrier || '—'}<br><b style="margin-top:8px">Tracking</b>${s.trackingNo || '—'}<br><b style="margin-top:8px">Customer PO</b>${s.reference || '—'}</div>
    </div>
    <table><thead><tr><th>FG lot</th><th>Product</th><th>Pack</th><th style="text-align:right">Qty</th><th style="text-align:right">Volume</th><th>Processing lot</th></tr></thead><tbody>${rows}</tbody></table>
    <div class="tot">Total: ${fmt(s.units)} units &middot; ${fmt(s.litres, 0)} L</div>
    ${s.notes ? '<div style="margin-top:10px;font-size:12px"><b>Notes:</b> ' + s.notes + '</div>' : ''}
    <div class="sign"><div>Picked / packed by</div><div>Received by (signature &amp; date)</div></div>
    <div class="foot">Lot numbers above provide full traceability to production run and source harvest totes. Generated by KelpWorks ERP.</div>
    <script>window.onload=()=>window.print()<\/script></body></html>`);
  w.document.close();
}

async function manageCustomers() {
  const listHost = el('div', {});
  async function refresh() {
    const cs = (await api('GET', '/customers')).customers;
    State.ref.customers = cs;
    listHost.innerHTML = '';
    if (!cs.length) { listHost.append(el('div', { class: 'help' }, 'No customers yet.')); }
    else listHost.append(table(['Customer', 'Contact', 'Email', 'Phone', ''], cs.map(c => [
      c.name, c.contact || '—', c.email || '—', c.phone || '—',
      rowActions([['Edit', () => custForm(c, refresh)]])
    ]), [false, false, false, false, false]));
  }
  const body = el('div', {},
    el('div', { class: 'page-head', style: 'margin:0 0 8px' }, el('h3', { style: 'margin:0' }, 'Customers'),
      el('div', { class: 'actions' }, el('button', { onclick: () => custForm(null, refresh) }, '+ Add customer'))),
    listHost);
  await refresh();
  modal('Customers', body, async () => {}, 'Done');
}
function custForm(c, after) {
  const body = el('div', {},
    field('Name', el('input', { id: 'cu_name', value: c ? c.name : '' })),
    el('div', { class: 'form-row' }, field('Contact', el('input', { id: 'cu_contact', value: c?.contact || '' })),
      field('Email', el('input', { id: 'cu_email', value: c?.email || '' }))),
    field('Phone', el('input', { id: 'cu_phone', value: c?.phone || '' })),
    field('Ship-to address', el('textarea', { id: 'cu_addr', rows: '3' }, c?.address || '')));
  modal(c ? 'Edit customer' : 'Add customer', body, async () => {
    const payload = { name: body.querySelector('#cu_name').value, contact: body.querySelector('#cu_contact').value,
      email: body.querySelector('#cu_email').value, phone: body.querySelector('#cu_phone').value, address: body.querySelector('#cu_addr').value };
    if (!payload.name.trim()) throw new Error('Name is required.');
    if (c) await api('PUT', '/customers/' + c.id, payload); else await api('POST', '/customers', payload);
    State.ref = await api('GET', '/refdata'); toast('Saved'); after();
  }, 'Save');
}

/* ---------------- CIP (Clean In Place) log ---------------- */
// One record per cleaning event; the chemical lines on it are what deduct the
// CIP agents' stock (backend _apply_cip_stock), so this log is the single
// source of truth for both the cleaning record and reagent consumption.
const CIP_EQUIPMENT = ['Homogenization / Tank 2A/B', 'Extraction', 'Separation', 'Pasteurization',
  'Tank 5A/B', 'Tank 6A/B', 'Packaging / filling'];
const CIP_PURPOSES = ['Post-run', 'Pre-run', 'Changeover', 'Scheduled', 'Other'];
function cipWhen(s) { return s ? s.replace('T', ' ').slice(0, 16) : '—'; }
// Duration (min) = End - Start (see CALCULATIONS); blank until both are set.
function cipDurationMin(e) {
  if (!e.startedAt || !e.endedAt) return null;
  const m = Math.round((new Date(e.endedAt) - new Date(e.startedAt)) / 60000);
  return Number.isNaN(m) || m < 0 ? null : m;
}
function cipDurationText(e) {
  const m = cipDurationMin(e);
  return m == null ? '—' : (m >= 60 ? Math.floor(m / 60) + ' h ' + (m % 60) + ' min' : m + ' min');
}
function cipChemicalsText(e) {
  return (e.chemicals || []).map(c => c.name.replace(/^CIP /, '') + ' ' + fmt(c.qty, 2) + ' ' + c.unit).join(' · ') || '—';
}
async function pageCIP(v) {
  const isAdmin = State.user.role === 'admin';
  let events = [];
  v.append(el('div', { class: 'page-head' }, el('h2', {}, 'CIP Log'),
    el('div', { class: 'actions' }, el('button', { onclick: () => openCipModal(null, events) }, '+ Log CIP'))));
  events = (await api('GET', '/cip')).events;
  v.append(el('div', { class: 'help', style: 'margin-bottom:10px' },
    'Every Clean In Place on the plant’s equipment. The chemicals logged here are deducted from the CIP agents in Inventory Items.'));
  const lastHost = el('div', {});
  const search = el('input', { placeholder: 'Filter by ref, equipment, chemical, operator or run…', style: 'max-width:340px' });
  const eqSel = el('select', {});
  const listHost = el('div', {});
  v.append(lastHost,
    el('div', { class: 'form-row', style: 'margin:14px 0 8px' }, field('Search', search), field('Equipment', eqSel)),
    listHost);

  // Last cleaned, one row per equipment (the standard areas even if never
  // cleaned yet, so a gap is visible at a glance).
  function drawLast() {
    const latest = new Map();   // events are newest-first, so first seen = last cleaned
    events.forEach(e => { if (!latest.has(e.equipment)) latest.set(e.equipment, e); });
    const names = [...CIP_EQUIPMENT, ...[...latest.keys()].filter(n => !CIP_EQUIPMENT.includes(n))];
    lastHost.innerHTML = '';
    lastHost.append(el('div', { class: 'card' }, el('h3', {}, 'Last cleaned'),
      table(['Equipment', 'Last cleaned', 'Days since', 'Result', 'By'],
        names.map(n => {
          const e = latest.get(n);
          if (!e) return [n, el('span', { class: 'muted' }, 'Never logged'), '—', '—', '—'];
          const days = Math.max(0, Math.floor((Date.now() - new Date(e.startedAt)) / 86400000));
          return [n, cipWhen(e.startedAt), String(days),
            e.result ? badge(e.result === 'pass' ? 'ok' : 'low', e.result.toUpperCase()) : '—',
            e.createdBy || '—'];
        }), [false, false, true, false, false])));
  }
  function drawEqOptions() {
    const names = [...new Set(events.map(e => e.equipment))].sort();
    const cur = eqSel.value;
    eqSel.innerHTML = '';
    eqSel.append(el('option', { value: '' }, 'All equipment'), ...names.map(n => el('option', { value: n }, n)));
    eqSel.value = names.includes(cur) ? cur : '';
  }
  function drawList() {
    const q = search.value.trim().toLowerCase();
    const rows = events.filter(e => (!eqSel.value || e.equipment === eqSel.value) && (!q ||
      [e.ref, e.equipment, e.purpose, e.operators, e.processingLot, cipChemicalsText(e)].join(' ').toLowerCase().includes(q)));
    listHost.innerHTML = '';
    if (!rows.length) {
      listHost.append(el('div', { class: 'empty card' }, events.length ? 'No CIP entries match that filter.'
        : 'No CIP cleanings logged yet. Click “+ Log CIP” to record one.'));
      return;
    }
    listHost.append(table(['Ref', 'Started', 'Duration', 'Equipment', 'Purpose', 'Chemicals used', 'Result', 'Operators', 'Run', ''],
      rows.map(e => [mono(e.ref), cipWhen(e.startedAt), cipDurationText(e), e.equipment, e.purpose || '—',
        cipChemicalsText(e),
        e.result ? badge(e.result === 'pass' ? 'ok' : 'low', e.result.toUpperCase()) : '—',
        e.operators || '—', e.processingLot ? mono(e.processingLot) : '—',
        rowActions([['Edit', () => openCipModal(e, events)],
          isAdmin ? ['Delete', async () => {
            if (!confirm('Delete ' + e.ref + '? Its chemicals are refunded to stock.')) return;
            await api('DELETE', '/cip/' + e.id);
            toast('CIP entry deleted'); render();
          }, 'danger'] : null])]),
      [false, false, false, false, false, false, false, false, false, false]));
  }
  search.addEventListener('input', drawList);
  eqSel.addEventListener('change', drawList);
  drawLast(); drawEqOptions(); drawList();
}
// Create (ev == null) or edit a CIP entry. Chemical lines are edited in the
// form and saved together with the rest in the one submit.
async function openCipModal(ev, events) {
  const agents = (await api('GET', '/consumables')).consumables.filter(c => c.isCipAgent);
  const runs = (await api('GET', '/production')).runs.slice(0, 40);
  const nowLocal = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  const startedInp = el('input', { type: 'datetime-local', value: ev ? (ev.startedAt || '').slice(0, 16) : nowLocal });
  const endedInp = el('input', { type: 'datetime-local', value: ev ? (ev.endedAt || '').slice(0, 16) : '' });
  const eqNames = [...new Set([...CIP_EQUIPMENT, ...(events || []).map(e => e.equipment)])];
  const eqField = editableSelect(eqNames.map(n => [n]), 'cip_eq');
  const purposeSel = selectFrom('', [['', '—'], ...CIP_PURPOSES.map(p => [p, p])]);
  purposeSel.value = ev?.purpose || '';
  const operatorsSelect = buildOperatorsSelect(ev?.operators || '');
  const runSel = selectFrom('', [['', 'None'], ...runs.map(r => [String(r.id), r.processingLot + ' — ' + skuName(r.sku) + ' (' + r.runDate + ')'])]);
  runSel.value = ev?.runId ? String(ev.runId) : '';
  const resultSel = selectFrom('', [['', '—'], ['pass', 'Pass'], ['fail', 'Fail']]);
  resultSel.value = ev?.result || '';
  const notesInp = el('textarea', { rows: '2', placeholder: 'Optional notes' }, ev?.notes || '');

  const rowsHost = el('div', {});
  const rowCtls = [];
  function numInp(ph, val, dec) {
    const i = el('input', { inputmode: 'decimal', placeholder: ph }); attachNumericMask(i, dec);
    if (val != null) i.value = formatQcValue(val, dec);
    return i;
  }
  function addRow(line) {
    const sel = selectFrom('', agents.map(a => [String(a.id), a.name + ' (' + fmt(a.onHand, 1) + ' ' + a.unit + ' on hand)']));
    if (line) sel.value = String(line.consumableId);
    const ctl = { sel, qty: numInp('L', line?.qty, 2), conc: numInp('%', line?.concentrationPct, 2),
      temp: numInp('°C', line?.tempC, 1), contact: numInp('min', line?.contactMin, 1) };
    // Agent gets its own full-width line (its label includes the on-hand
    // count, too long for the compact 108px fields); the readings sit below.
    const row = el('div', { class: 'qc-check-box', style: 'margin:6px 0' },
      field('CIP agent', sel),
      el('div', { class: 'form-row-compact' },
        field('Qty (L)', ctl.qty), field('Conc. (%)', ctl.conc),
        field('Temp (°C)', ctl.temp), field('Contact (min)', ctl.contact)),
      el('button', { type: 'button', class: 'icon-btn remove', title: 'Remove chemical', onclick: () => {
        row.remove(); rowCtls.splice(rowCtls.indexOf(ctl), 1);
      } }, '−'));
    rowCtls.push(ctl); rowsHost.append(row);
  }
  (ev?.chemicals || []).forEach(addRow);

  const body = el('div', {},
    el('div', { class: 'form-row' }, field('Started', startedInp), field('Ended', endedInp)),
    el('div', { class: 'form-row' }, field('Equipment cleaned', eqField), field('Purpose', purposeSel)),
    el('div', { class: 'form-row' }, field('Operators', operatorsSelect.el), field('Linked production run (optional)', runSel)),
    el('div', { class: 'qc-check-section-title' }, 'Chemicals used'),
    agents.length ? rowsHost : el('div', { class: 'help' }, 'No CIP agents are set up -- an admin can add one under Inventory Items.'),
    agents.length ? el('div', { style: 'margin:6px 0 10px' },
      el('button', { type: 'button', class: 'icon-btn add', title: 'Add chemical', onclick: () => addRow(null) }, '+')) : null,
    el('div', { class: 'qc-check-section-title' }, 'Final-rinse verification'),
    field('Result', resultSel),
    field('Notes', notesInp));
  body.querySelector('#cip_eq').value = ev?.equipment || '';
  modal(ev ? 'Edit CIP — ' + ev.ref : 'Log CIP', body, async () => {
    const chemicals = rowCtls.filter(c => c.qty.value.trim() !== '').map(c => ({
      consumableId: +c.sel.value, qty: qcParseValue(c.qty.value),
      concentrationPct: c.conc.value.trim() === '' ? null : qcParseValue(c.conc.value),
      tempC: c.temp.value.trim() === '' ? null : qcParseValue(c.temp.value),
      contactMin: c.contact.value.trim() === '' ? null : qcParseValue(c.contact.value) }));
    const payload = {
      startedAt: startedInp.value, endedAt: endedInp.value || null,
      equipment: body.querySelector('#cip_eq').value, purpose: purposeSel.value || null,
      operators: operatorsSelect.value, runId: runSel.value || null, chemicals,
      result: resultSel.value || null, notes: notesInp.value };
    const r = ev ? await api('PUT', '/cip/' + ev.id, payload) : await api('POST', '/cip', payload);
    if (r.warnings && r.warnings.length) toast(r.warnings.join(' · '), true);
    else toast(ev ? 'CIP entry updated' : 'CIP logged');
    render();
  }, ev ? 'Save changes' : 'Log CIP', { wide: true });
}

/* ---------------- Reagents, packaging & finished-good labels ---------------- */
// (Internally still "consumables" -- table, route, tab key -- only the
// user-facing wording changed.) Three groups share the one inventory table:
// reagents (neither a container nor a label), packaging (isContainer) and
// finished-good labels (labelSku set -- one item per SKU + package type).
async function pageConsumables(v) {
  const isAdmin = State.user.role === 'admin';
  v.append(el('div', { class: 'page-head' }, el('h2', {}, 'Inventory Items')));
  v.append(el('div', { class: 'help', style: 'margin:-8px 0 10px' },
    'Item # = category + sequence, assigned automatically and never reused: ',
    el('b', {}, 'RGT'), ' reagent · ', el('b', {}, 'CIP'), ' cleaning agent · ', el('b', {}, 'PKG'), ' packaging · ',
    el('b', {}, 'SMP'), ' sample container · ', el('b', {}, 'LBL'), ' finished-good label (e.g. RGT-001).'));
  const r = await api('GET', '/consumables');
  const bulkBar = el('div', { class: 'bulkbar hidden' });
  const host = el('div', {});
  v.append(bulkBar, host);
  const selected = new Set();
  function updateBulk() {
    const n = selected.size;
    bulkBar.classList.toggle('hidden', n === 0);
    bulkBar.innerHTML = '';
    if (!n) return;
    bulkBar.append(
      el('span', {}, el('b', {}, n), ' item' + (n === 1 ? '' : 's') + ' selected'),
      el('button', { class: 'danger', onclick: () => disposeConsumables(r.consumables.filter(c => selected.has(c.id))) }, 'Dispose / write off'),
      el('button', { class: 'secondary', onclick: () => { selected.clear(); draw(); } }, 'Clear'));
  }
  function itemsTable(items, opts) {
    opts = opts || {};
    const allCb = el('input', { type: 'checkbox', title: 'Select all', onchange: () => {
      items.forEach(c => allCb.checked ? selected.add(c.id) : selected.delete(c.id)); draw();
    } });
    allCb.checked = items.length > 0 && items.every(c => selected.has(c.id));
    // opts.labelGroup ('sku' | 'package' | 'none'): finished-good labels, grouped under a heading per SKU or per package type
    // (the grouped-by column is dropped from the rows); 'none' lists them flat with both columns.
    const lg = opts.labelGroup;
    const headers = [allCb, 'Item #'];
    if (lg === 'sku') headers.push('Package'); else if (lg === 'package') headers.push('SKU'); else if (lg === 'none') headers.push('SKU', 'Package'); else headers.push('Item');
    const bools = headers.map(() => false);
    if (opts.showType) { headers.push('Type'); bools.push(false); }
    if (opts.showVolume) { headers.push('Volume (L)'); bools.push(true); }
    headers.push('Location', 'On hand', 'Reorder at', 'Cost/unit', '', 'Actions');
    bools.push(false, true, true, true, false, false);
    const rowItems = [];                         // row index -> item (null for a group heading), for the row-click history
    const makeRow = c => {
      const row = [rowCheck(c, selected, updateBulk), c.itemNumber ? mono(c.itemNumber) : '—'];
      if (lg === 'sku') row.push(c.labelPackage || c.name);
      else if (lg === 'package') row.push(skuName(c.labelSku));
      else if (lg === 'none') row.push(skuName(c.labelSku), c.labelPackage || '—');
      else row.push(c.name);
      if (opts.showType) row.push(c.reagentType || '—');
      if (opts.showVolume) row.push(c.litresEach != null ? fmt(c.litresEach, c.litresEach % 1 ? 2 : 0) : '—');
      // Packaging items and labels are counted in whole units (totes,
      // bottles, labels ...), so their on-hand quantity displays with no
      // decimals; reagents (kg of Citric Acid, etc.) keep their fractional display.
      row.push(c.location || '—', fmt(c.onHand, opts.wholeUnits ? 0 : 1) + ' ' + c.unit, fmt(c.reorderLevel, 1),
        c.costPerUnit != null ? '$' + fmt(c.costPerUnit, 2) : '—',
        badge(c.low ? 'low' : 'ok', c.low ? 'LOW' : 'OK'),
        rowActions([['Receive', () => adjustC(c, 1)], ['Use', () => adjustC(c, -1)],
          ['Dispose', () => disposeConsumables([c]), 'danger'], ['Edit', () => editC(c)]]));
      return row;
    };
    let rows = [];
    const cmpSku = (a, b) => skuName(a.labelSku).localeCompare(skuName(b.labelSku)) || (a.labelPackage || '').localeCompare(b.labelPackage || '');
    if (lg === 'sku' || lg === 'package') {
      const groups = new Map();
      items.forEach(c => { const k = (lg === 'sku' ? c.labelSku : c.labelPackage) || ''; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(c); });
      const title = k => lg === 'sku' ? skuName(k) : (k || 'No package');
      [...groups.entries()].sort((a, b) => title(a[0]).localeCompare(title(b[0]))).forEach(([k, list]) => {
        list.sort(lg === 'sku' ? ((a, b) => (a.labelPackage || '').localeCompare(b.labelPackage || '')) : cmpSku);
        const onHand = list.reduce((a, c) => a + (c.onHand || 0), 0), low = list.filter(c => c.low).length;
        rows.push({ group: el('span', { class: 'group-title' }, el('b', {}, title(k)), el('span', { class: 'muted' },
          '  ·  ' + list.length + ' label type' + (list.length === 1 ? '' : 's') + '  ·  ' + fmt(onHand, 0) + ' on hand' + (low ? '  ·  ' + low + ' low' : ''))) });
        rowItems.push(null);
        list.forEach(c => { rows.push(makeRow(c)); rowItems.push(c); });
      });
    } else if (lg === 'none') {
      items.slice().sort(cmpSku).forEach(c => { rows.push(makeRow(c)); rowItems.push(c); });
    } else {
      items.forEach(c => { rows.push(makeRow(c)); rowItems.push(c); });
    }
    return table(headers, rows, bools, opts.history ? (ri => { if (rowItems[ri]) showConsumableHistory(rowItems[ri]); }) : null);
  }
  function draw() {
    host.innerHTML = '';
    const labels = r.consumables.filter(c => c.labelSku);
    const containers = r.consumables.filter(c => c.isContainer);
    const reagents = r.consumables.filter(c => !c.isContainer && !c.labelSku);
    host.append(el('div', { class: 'page-head' }, el('h2', {}, 'Reagents'),
      el('div', { class: 'actions' }, el('button', { onclick: addConsumable }, '+ Add reagent'))));
    host.append(itemsTable(reagents, { history: true, showType: true }));
    host.append(el('div', { class: 'help', style: 'margin-top:6px' },
      'Type groups items that are the same reagent (e.g. several Citric Acid grades or suppliers): the production log offers every item of a type and deducts from the one chosen. Set it with Edit.'));
    host.append(el('div', { class: 'page-head', style: 'margin-top:28px' }, el('h2', {}, 'Packaging'),
      el('div', { class: 'actions' },
        isAdmin ? el('button', { class: 'secondary', onclick: openContainerBulkImport }, '📤 Bulk import CSV') : null,
        isAdmin ? el('button', { onclick: addContainerType }, '+ Add container type') : null)));
    host.append(el('div', { class: 'help', style: 'margin-bottom:10px' },
      'Every container Production uses -- Packaging table output units and Sample Point vessels alike -- with its own on-hand inventory. '
      + (isAdmin ? 'Only an admin can bulk-upload counts or add a new container type; anyone can receive/use/edit an existing one.'
        : 'Ask an admin to bulk-upload counts or add a new container type.')));
    host.append(itemsTable(containers, { showVolume: true, wholeUnits: true, history: true }));
    // Finished-good labels are the physical labels stuck on each finished
    // package -- not the internal barcode labels printed from a row's Label button.
    const grpSel = selectFrom('', [['sku', 'Group by SKU'], ['package', 'Group by package'], ['none', 'No grouping']], () => {
      State.labelGroup = grpSel.value; try { localStorage.setItem('kelp.labelGroup', grpSel.value); } catch (e) { /* ignore */ } draw();
    });
    if (!State.labelGroup) { try { State.labelGroup = localStorage.getItem('kelp.labelGroup') || 'sku'; } catch (e) { State.labelGroup = 'sku'; } }
    grpSel.value = State.labelGroup;
    host.append(el('div', { class: 'page-head', style: 'margin-top:28px' }, el('h2', {}, 'Finished-good labels'),
      el('div', { class: 'actions' }, grpSel,
        isAdmin ? el('button', { onclick: addFgLabel }, '+ Add FG label') : null)));
    host.append(el('div', { class: 'help', style: 'margin-bottom:10px' },
      'One label item per product SKU + package type, with its own on-hand inventory. Saving a run’s Packaging section (or finalizing) deducts one label per container consumed '
      + 'from the matching item. ' + (isAdmin ? '' : 'Ask an admin to add a new FG label.')));
    host.append(itemsTable(labels, { labelGroup: State.labelGroup || 'sku', wholeUnits: true, history: true }));
    updateBulk();
  }
  draw();
}
function adjustC(c, sign) {
  const body = el('div', {},
    field((sign > 0 ? 'Quantity received' : 'Quantity used') + ' (' + c.unit + ')',
      el('input', { type: 'number', id: 'c_amt', min: '0', step: '0.1', value: '0' })),
    field('Reason / reference', el('input', { id: 'c_reason', placeholder: sign > 0 ? 'PO / supplier' : 'reason' })));
  modal((sign > 0 ? 'Receive ' : 'Use ') + c.name, body, async () => {
    const amt = +body.querySelector('#c_amt').value || 0;
    if (amt <= 0) throw new Error('Enter a quantity greater than 0.');
    await api('POST', '/consumables/' + c.id + '/adjust', { delta: sign * amt, reason: body.querySelector('#c_reason').value || (sign > 0 ? 'Received' : 'Used') });
    toast('Stock updated'); render();
  }, sign > 0 ? 'Receive' : 'Use');
}
function editC(c) {
  const locs = State.ref.locations.map(l => [l, l]);
  const litresInp = c.isContainer
    ? el('input', { type: 'number', min: '0', step: 'any', value: c.litresEach ?? '', placeholder: 'blank if not an FG package' })
    : null;
  const sampleCb = c.isContainer ? el('input', { type: 'checkbox' }) : null;
  if (sampleCb) sampleCb.checked = !!c.isSampleContainer;
  const isPlainReagent = !c.isContainer && !c.labelSku;
  const typeSel = isPlainReagent ? selectFrom('', [['', '— none —'], ...REAGENT_TYPES.map(t => [t, t])]) : null;
  if (typeSel) typeSel.value = c.reagentType || '';
  const body = el('div', {},
    field('Item #', el('input', { id: 'c_itemno', value: c.itemNumber ?? '', placeholder: 'Auto-assigned (e.g. RGT-008) — leave blank' })),
    typeSel ? field('Reagent type (items of one type are offered together in the production log)', typeSel) : null,
    el('div', { class: 'form-row' },
      field('Reorder level', el('input', { type: 'number', id: 'c_re', value: c.reorderLevel, step: '0.1' })),
      field('Cost per unit', el('input', { type: 'number', id: 'c_cost', value: c.costPerUnit ?? '', step: '0.01' }))),
    field('Warehouse location', editableSelect(locs, 'c_loc')),
    c.isContainer ? el('div', { class: 'form-row' },
      field('Volume (L, blank if not an FG package)', litresInp),
      field('Used for Sample Point', sampleCb)) : null);
  body.querySelector('#c_loc').value = c.location || '';
  modal('Edit ' + c.name, body, async () => {
    const payload = { reorderLevel: +body.querySelector('#c_re').value, costPerUnit: body.querySelector('#c_cost').value || null, location: body.querySelector('#c_loc').value,
      itemNumber: body.querySelector('#c_itemno').value };
    if (c.isContainer) {
      payload.litresEach = litresInp.value.trim() === '' ? null : +litresInp.value;
      payload.isSampleContainer = sampleCb.checked;
    }
    if (typeSel) payload.reagentType = typeSel.value || null;
    await api('PUT', '/consumables/' + c.id, payload);
    State.ref = await api('GET', '/refdata');
    toast('Updated'); render();
  }, 'Save');
}
// Every receive/use/bulk-import/live Packaging-Sample Point deduction against
// a container logs a consumable_txns row -- clicking its line in the
// Packaging table opens the full trail, timestamped and attributed to
// whoever made each change (blank for the rare change made with no signed-in
// user, e.g. a very old row from before user_name was tracked).
function consumableHistoryTable(log) {
  if (!log.length) return el('div', { class: 'help' }, 'No changes logged yet.');
  // Same "~10 rows then scroll" pattern as the Feedstock Stability log.
  return el('div', { class: 'tablewrap stability-log-scroll', style: 'margin-top:6px' },
    el('table', {},
      el('thead', {}, el('tr', {}, el('th', {}, 'When'), el('th', { class: 'num' }, 'Change'),
        el('th', {}, 'Reason'), el('th', {}, 'Reference'), el('th', {}, 'By'))),
      el('tbody', {}, ...log.map(r => el('tr', {},
        el('td', { class: 'muted' }, fmtWhen(r.createdAt)),
        el('td', { class: 'num' }, el('b', {}, (r.delta > 0 ? '+' : '') + fmt(r.delta, r.delta % 1 ? 2 : 0))),
        el('td', {}, r.reason || '—'),
        el('td', { class: 'muted' }, r.ref || '—'),
        el('td', {}, r.userName || '—'))))));
}
async function showConsumableHistory(c) {
  const data = await api('GET', '/consumables/' + c.id + '/history');
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Item', c.name), sl('On hand', fmt(c.onHand, (c.isContainer || c.labelSku) ? 0 : 1) + ' ' + c.unit)),
    consumableHistoryTable(data.history));
  modal('Transaction history — ' + c.name, body, async () => {}, 'Close', { noCancel: true });
}
function addConsumable() {
  const locs = State.ref.locations.map(l => [l, l]);
  // Admin-only: a CIP cleaning agent is offered on CIP Log chemical lines.
  const cipCb = State.user.role === 'admin' ? el('input', { type: 'checkbox' }) : null;
  const typeSel = selectFrom('', [['', '— none —'], ...REAGENT_TYPES.map(t => [t, t])]);
  const body = el('div', {},
    el('div', { class: 'form-row' }, field('Name', el('input', { id: 'n_name' })), field('Unit', el('input', { id: 'n_unit', value: 'kg' }))),
    field('Reagent type (items of one type are offered together in the production log)', typeSel),
    field('Item #', el('input', { id: 'n_itemno', placeholder: 'Auto-assigned (e.g. RGT-008) — leave blank' })),
    el('div', { class: 'form-row' }, field('On hand', el('input', { type: 'number', id: 'n_oh', value: '0' })),
      field('Reorder level', el('input', { type: 'number', id: 'n_re', value: '0' }))),
    el('div', { class: 'form-row' }, field('Cost per unit', el('input', { type: 'number', id: 'n_cost', step: '0.01' })),
      field('Warehouse location', editableSelect(locs, 'n_loc'))),
    cipCb ? field('CIP cleaning agent (offered on CIP Log lines)', cipCb) : null);
  modal('Add reagent', body, async () => {
    await api('POST', '/consumables', { name: body.querySelector('#n_name').value, unit: body.querySelector('#n_unit').value, onHand: +body.querySelector('#n_oh').value, reorderLevel: +body.querySelector('#n_re').value, costPerUnit: body.querySelector('#n_cost').value || null, location: body.querySelector('#n_loc').value,
      itemNumber: body.querySelector('#n_itemno').value, isCipAgent: cipCb ? cipCb.checked : false, reagentType: typeSel.value || null });
    State.ref = await api('GET', '/refdata');
    toast('Added'); render();
  }, 'Add');
}
// Admin-only: introduces a brand-new container type (Packaging output unit
// and/or Sample Point vessel) -- editing/receiving/using an existing one is
// open to everyone via editC/adjustC above.
function addContainerType() {
  const locs = State.ref.locations.map(l => [l, l]);
  const litresInp = el('input', { type: 'number', min: '0', step: 'any', placeholder: 'e.g. 1000 for IBC -- blank if not an FG package' });
  const sampleCb = el('input', { type: 'checkbox' });
  const body = el('div', {},
    el('div', { class: 'form-row' }, field('Name', el('input', { id: 'n_name', placeholder: 'e.g. 4 L' })),
      field('Unit', el('input', { id: 'n_unit', value: 'ea' }))),
    field('Item #', el('input', { id: 'n_itemno', placeholder: 'Auto-assigned (e.g. RGT-008) — leave blank' })),
    el('div', { class: 'form-row' }, field('On hand', el('input', { type: 'number', id: 'n_oh', value: '0' })),
      field('Reorder level', el('input', { type: 'number', id: 'n_re', value: '0' }))),
    el('div', { class: 'form-row' }, field('Cost per unit', el('input', { type: 'number', id: 'n_cost', step: '0.01' })),
      field('Warehouse location', editableSelect(locs, 'n_loc'))),
    el('div', { class: 'form-row' }, field('Volume (L, blank if not an FG package)', litresInp),
      field('Used for Sample Point', sampleCb)));
  modal('Add container type', body, async () => {
    await api('POST', '/consumables', {
      name: body.querySelector('#n_name').value, unit: body.querySelector('#n_unit').value,
      onHand: +body.querySelector('#n_oh').value, reorderLevel: +body.querySelector('#n_re').value,
      costPerUnit: body.querySelector('#n_cost').value || null, location: body.querySelector('#n_loc').value,
      isContainer: true, litresEach: litresInp.value.trim() === '' ? null : +litresInp.value,
      isSampleContainer: sampleCb.checked, itemNumber: body.querySelector('#n_itemno').value
    });
    State.ref = await api('GET', '/refdata');
    toast('Container type added'); render();
  }, 'Add');
}
// Admin-only: a finished-good label item, mapped to one product SKU + one
// package type (a packaging container with a volume). Named automatically
// ("FG Label - <SKU> - <package>"); deducted 1 per container consumed when a run's Packaging is saved
// or finalized. Unrelated to the internal barcode labels printed from a row's Label button.
function addFgLabel() {
  const locs = State.ref.locations.map(l => [l, l]);
  const skuSel = selectFrom('', (State.ref.skus || []).filter(s => s.active).map(s => [s.code, s.name]));
  const pkgSel = selectFrom('', (State.ref.containers || []).filter(c => c.litresEach != null).map(c => [c.name, c.name]));
  const body = el('div', {},
    el('div', { class: 'form-row' }, field('Product SKU', skuSel), field('Package type', pkgSel)),
    field('Item #', el('input', { id: 'n_itemno', placeholder: 'Auto-assigned (e.g. RGT-008) — leave blank' })),
    el('div', { class: 'form-row' }, field('On hand', el('input', { type: 'number', id: 'n_oh', value: '0' })),
      field('Reorder level', el('input', { type: 'number', id: 'n_re', value: '0' }))),
    el('div', { class: 'form-row' }, field('Cost per unit', el('input', { type: 'number', id: 'n_cost', step: '0.01' })),
      field('Warehouse location', editableSelect(locs, 'n_loc'))),
    el('div', { class: 'help' }, 'One label item per SKU + package type. Counted in labels (ea).'));
  modal('Add finished-good label', body, async () => {
    await api('POST', '/consumables', {
      labelSku: skuSel.value, labelPackage: pkgSel.value,
      onHand: +body.querySelector('#n_oh').value, reorderLevel: +body.querySelector('#n_re').value,
      costPerUnit: body.querySelector('#n_cost').value || null, location: body.querySelector('#n_loc').value,
      itemNumber: body.querySelector('#n_itemno').value
    });
    State.ref = await api('GET', '/refdata');
    toast('FG label added'); render();
  }, 'Add');
}
// Admin-only bulk stock-count import (e.g. after a physical stocktake) --
// same CSV-upload UX as openFeedstockImport, but sets an absolute on-hand
// count per row rather than creating new records.
function openContainerBulkImport() {
  let csvText = '';
  const fileInput = el('input', { type: 'file', accept: '.csv,text/csv' });
  const status = el('div', { class: 'help' });
  fileInput.addEventListener('change', () => {
    csvText = ''; status.textContent = '';
    const f = fileInput.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => {
      csvText = String(reader.result || '');
      const rows = csvText.split(/\r\n|\r|\n/).filter(l => l.trim() !== '').length - 1;
      status.textContent = f.name + ' — ' + Math.max(rows, 0) + ' row(s) ready to import.';
    };
    reader.onerror = () => { status.textContent = 'Could not read ' + f.name; };
    reader.readAsText(f);
  });
  const body = el('div', {},
    el('div', { class: 'help' },
      'Set an absolute on-hand count for many reagents/containers/labels at once, by name — e.g. after a physical stocktake. Download the template, fill it in, then upload it here.'),
    el('div', { style: 'margin:10px 0' },
      el('a', { href: 'templates/consumables_bulk_import_template.csv', download: 'consumables_bulk_import_template.csv' },
        '⬇ Download CSV template')),
    field('CSV file', fileInput),
    status,
    el('div', { class: 'help' },
      'Required columns: name (must exactly match an existing item — quote it if the name itself contains a comma), onHand. Optional: reason.'));
  modal('Bulk import inventory counts (CSV)', body, async () => {
    if (!csvText.trim()) throw new Error('Choose a CSV file to import.');
    const r = await api('POST', '/consumables/bulk', { csvText });
    State.ref = await api('GET', '/refdata');
    toast('Updated ' + r.updated + ' item(s).');
    render();
  }, 'Import');
}

/* ---------------- Reports ---------------- */
// disposals.entity_type is stored as 'tote' | 'fg' | 'consumable' (the table
// keeps its original name) -- show a readable label instead of the raw value.
function disposalTypeLabel(t) {
  return { tote: 'Tote', fg: 'Finished good', consumable: 'Reagent / packaging' }[t] || t;
}
function monthLabel(m) {
  const [y, mo] = m.split('-');
  return new Date(y, mo - 1, 1).toLocaleString(undefined, { month: 'long', year: 'numeric' });
}
async function pageReports(v) {
  const now = new Date();
  const iso = dt => `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
  const def = State.reportRange || { from: iso(new Date(now.getFullYear(), now.getMonth(), 1)), to: iso(now) };
  const fromInput = el('input', { type: 'date', id: 'rp_from', value: def.from, style: 'width:auto', onchange: gen });
  const toInput = el('input', { type: 'date', id: 'rp_to', value: def.to, style: 'width:auto', onchange: gen });
  const preset = (f, t) => { fromInput.value = f; toInput.value = t; gen(); };
  v.append(el('div', { class: 'page-head' }, el('h2', {}, 'Reports'),
    el('div', { class: 'actions' },
      el('button', { class: 'secondary', onclick: () => printReport(State.reportData) }, '🖨 Print / PDF'),
      el('button', { onclick: downloadReportXlsx }, '⬇ Export Excel'),
      el('button', { class: 'secondary', onclick: () => exportReportCsv(State.reportData) }, 'CSV'))));
  v.append(el('div', { class: 'toolbar' },
    el('label', { style: 'margin:0 4px 0 0' }, 'From'), fromInput,
    el('label', { style: 'margin:0 4px 0 10px' }, 'To'), toInput,
    el('span', { style: 'margin-left:10px' },
      el('button', { class: 'secondary', onclick: () => preset(iso(new Date(now.getFullYear(), now.getMonth(), 1)), iso(now)) }, 'This month'),
      el('button', { class: 'secondary', onclick: () => preset(iso(new Date(now.getFullYear(), now.getMonth() - 1, 1)), iso(new Date(now.getFullYear(), now.getMonth(), 0))) }, 'Last month'),
      el('button', { class: 'secondary', onclick: () => preset(iso(new Date(now.getFullYear(), 0, 1)), iso(now)) }, 'Year to date'))));
  const host = el('div', {});
  v.append(host);
  async function gen() {
    if (fromInput.value && toInput.value && fromInput.value > toInput.value) {
      const t = fromInput.value; fromInput.value = toInput.value; toInput.value = t;
    }
    State.reportRange = { from: fromInput.value, to: toInput.value };
    host.innerHTML = ''; host.append(el('div', { class: 'muted' }, 'Loading…'));
    const d = await api('GET', '/reports?from=' + encodeURIComponent(fromInput.value) + '&to=' + encodeURIComponent(toInput.value));
    State.reportData = d; host.innerHTML = ''; renderReport(host, d);
  }
  gen();
}
function renderReport(host, d) {
  host.append(el('div', { class: 'muted', style: 'margin-bottom:10px' },
    'Activity ', el('b', {}, d.period),
    ' · on-hand balances as of ', el('b', {}, d.asOf),
    ' · ', el('span', { class: 'help', style: 'display:inline' }, 'click any species / SKU / item row for its transaction ledger')));
  host.append(el('div', { class: 'tiles' },
    tile('Stabilized created', fmt(d.stabilized.created.kg, 0), 'kg · ' + fmt(d.stabilized.created.totes) + ' totes', true),
    tile('Stabilized consumed', fmt(d.stabilized.consumed.kg, 0), 'kg · ' + fmt(d.stabilized.consumed.totes) + ' totes'),
    tile('LKE produced', fmt(d.production.outputLitres, 0), 'L · ' + fmt(d.production.runs) + ' runs'),
    tile('LKE shipped', fmt(d.finishedGoods.shippedLitres, 0), 'L'),
    tile('Stabilized on hand', fmt(d.stabilized.onHand.kg, 0), 'kg at ' + d.asOf),
    tile('Finished goods on hand', fmt(d.finishedGoods.onHandLitres, 0), 'L at ' + d.asOf),
    tile('Written off', fmt(d.disposed ? d.disposed.fgLitres : 0, 0), 'L FG · ' + fmt(d.disposed ? d.disposed.toteKg : 0, 0) + ' kg totes')));

  const spTable = block => table(['Species', 'Totes', 'Kg'],
    block.bySpecies.map(r => [speciesName(r.species), fmt(r.totes), num(fmt(r.kg, 0))]),
    [false, true, true], i => openLedger('species', block.bySpecies[i].species));
  host.append(el('div', { class: 'grid2' },
    el('div', { class: 'card' }, el('h3', {}, 'Stabilized inventory — created (' + d.period + ')'), spTable(d.stabilized.created)),
    el('div', { class: 'card' }, el('h3', {}, 'Stabilized inventory — consumed into production'), spTable(d.stabilized.consumed))));
  host.append(el('div', { class: 'card' }, el('h3', {}, 'Stabilized inventory on hand — ' + d.asOf), spTable(d.stabilized.onHand)));

  const pr = d.production;
  host.append(el('div', { class: 'card' }, el('h3', {}, 'Production summary'),
    el('div', { class: 'summary-line' },
      sl('Runs', fmt(pr.runs)), sl('Input', fmt(pr.inputKg, 0) + ' kg'),
      sl('Output', fmt(pr.outputLitres, 0) + ' L'),
      sl('Yield', pr.yield != null ? pr.yield.toFixed(2) + ' L/kg' : '—'),
      sl('Citric', fmt(pr.citricKg, 1) + ' kg'), sl('Sorbate', fmt(pr.sorbateKg, 1) + ' kg'),
      sl('Na benzoate', fmt(pr.nabenzoateKg, 1) + ' kg')),
    pr.bySku.length ? table(['SKU', 'Runs', 'Litres produced'],
      pr.bySku.map(r => [skuName(r.sku), fmt(r.runs), num(fmt(r.litres, 0))]), [false, true, true],
      i => openLedger('sku', pr.bySku[i].sku)) : null));

  const fg = d.finishedGoods;
  host.append(el('div', { class: 'grid2' },
    el('div', { class: 'card' }, el('h3', {}, 'Finished goods shipped — by customer'),
      table(['Customer', 'Units', 'Litres'], fg.shippedByCustomer.map(r => [r.customer, fmt(r.units), num(fmt(r.litres, 0))]), [false, true, true])),
    el('div', { class: 'card' }, el('h3', {}, 'Finished goods on hand — ' + d.asOf),
      table(['SKU', 'Litres'], fg.onHand.map(r => [skuName(r.sku), num(fmt(r.litres, 0))]), [false, true],
        i => openLedger('sku', fg.onHand[i].sku)))));

  host.append(el('div', { class: 'grid2' },
    el('div', { class: 'card' }, el('h3', {}, 'Reagents — received / used (' + d.period + ')'),
      table(['Item', 'Received', 'Used'], d.consumables.inMonth.map(r => [r.name + ' (' + r.unit + ')', num(fmt(r.received, 1)), num(fmt(r.used, 1))]), [false, true, true],
        i => openLedger('consumable', d.consumables.inMonth[i].name))),
    el('div', { class: 'card' }, el('h3', {}, 'Reagents on hand — ' + d.asOf),
      table(['Item', 'On hand'], d.consumables.onHand.map(r => [r.name, fmt(r.onHand, 1) + ' ' + r.unit]), [false, true],
        i => openLedger('consumable', d.consumables.onHand[i].name)))));

  const bl = d.byLocation;
  if (bl) {
    host.append(el('div', { class: 'grid2' },
      el('div', { class: 'card' }, el('h3', {}, 'Stabilized inventory by location'),
        table(['Location', 'Totes', 'Kg'], bl.stabilized.map(r => [r.location, fmt(r.totes), num(fmt(r.kg, 0))]), [false, true, true])),
      el('div', { class: 'card' }, el('h3', {}, 'Finished goods by location'),
        table(['Location', 'Units', 'Litres'], bl.finishedGoods.map(r => [r.location, fmt(r.units), num(fmt(r.litres, 0))]), [false, true, true]))));
    host.append(el('div', { class: 'card' },
      el('h3', {}, 'Reagents / packaging by location'),
      el('div', { class: 'help', style: 'margin:-6px 0 8px' }, 'Current on-hand location of inventory.'),
      table(['Location', 'Item', 'On hand'], bl.consumables.map(r => [r.location, r.name, fmt(r.onHand, 1) + ' ' + r.unit]), [false, false, true],
        i => openLedger('consumable', bl.consumables[i].name))));
  }

  const dz = d.disposed;
  if (dz) {
    host.append(el('div', { class: 'card' }, el('h3', {}, 'Disposed / written off — ' + d.period),
      el('div', { class: 'summary-line' },
        sl('Totes', fmt(dz.totes) + ' (' + fmt(dz.toteKg, 0) + ' kg)'),
        sl('FG lots', fmt(dz.fgLots) + ' (' + fmt(dz.fgLitres, 0) + ' L)'),
        sl('Reagent / packaging write-offs', fmt(dz.consumableEvents))),
      (dz.lines && dz.lines.length)
        ? table(['Date', 'Type', 'Item', 'Qty', 'Reason', 'By'],
          dz.lines.map(l => [l.date, disposalTypeLabel(l.type), mono(l.ref), fmt(l.qty, 1) + ' ' + (l.unit || ''), l.reason, l.by || '—']),
          [false, false, false, true, false, false])
        : el('div', { class: 'help' }, 'No write-offs this month.')));
  }
}
function printReport(d) {
  if (!d) return toast('Nothing to print yet.', true);
  const w = window.open('', '_blank');
  if (!w) return toast('Allow pop-ups to print the report.', true);
  const sec = (title, headers, rows, nums) => `<h2>${title}</h2><table><thead><tr>${headers.map((h, i) => `<th${nums && nums[i] ? ' class=n' : ''}>${h}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${r.map((c, i) => `<td${nums && nums[i] ? ' class=n' : ''}>${c}</td>`).join('')}</tr>`).join('') || '<tr><td>—</td></tr>'}</tbody></table>`;
  const spRows = s => s.bySpecies.map(r => [speciesName(r.species), fmt(r.totes), fmt(r.kg, 0)]);
  const css = `body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:28px;color:#0c2b27;font-size:12px;}
    .hd{display:flex;justify-content:space-between;border-bottom:3px solid #15564F;padding-bottom:8px;margin-bottom:6px;}
    .co{font-size:18px;font-weight:700;color:#15564F;} h1{font-size:15px;margin:10px 0 0;} h2{font-size:13px;color:#15564F;margin:16px 0 4px;border-bottom:1px solid #dde7e4;padding-bottom:2px;}
    table{width:100%;border-collapse:collapse;margin-bottom:6px;} th{background:#eef3f2;text-align:left;padding:5px 8px;color:#15564F;} td{padding:5px 8px;border-bottom:1px solid #eee;} .n{text-align:right;}
    .tiles{display:flex;gap:18px;margin:10px 0;flex-wrap:wrap;} .ti{font-size:11px;color:#666;} .ti b{display:block;font-size:18px;color:#15564F;}
    .co{display:flex;align-items:center;gap:10px;} .hd img{height:46px;width:auto;}`;
  w.document.write(`<!doctype html><html><head><title>Manufacturing Report ${d.month}</title><style>${css}</style></head><body>
    <div class="hd"><div class="co"><img src="${location.origin}/logo.png" alt="">CASCADIA SEAWEED</div><div style="text-align:right"><h1 style="margin:0">MANUFACTURING REPORT</h1>${d.period} · on hand as of ${d.asOf}</div></div>
    <div class="tiles">
      <div class="ti">Stabilized created<b>${fmt(d.stabilized.created.kg, 0)} kg</b></div>
      <div class="ti">Stabilized consumed<b>${fmt(d.stabilized.consumed.kg, 0)} kg</b></div>
      <div class="ti">LKE produced<b>${fmt(d.production.outputLitres, 0)} L</b></div>
      <div class="ti">LKE shipped<b>${fmt(d.finishedGoods.shippedLitres, 0)} L</b></div>
      <div class="ti">Stabilized on hand<b>${fmt(d.stabilized.onHand.kg, 0)} kg</b></div>
      <div class="ti">FG on hand<b>${fmt(d.finishedGoods.onHandLitres, 0)} L</b></div>
    </div>
    ${sec('Stabilized inventory created', ['Species', 'Totes', 'Kg'], spRows(d.stabilized.created), [0, 1, 1])}
    ${sec('Stabilized inventory consumed', ['Species', 'Totes', 'Kg'], spRows(d.stabilized.consumed), [0, 1, 1])}
    ${sec('Stabilized inventory on hand (' + d.asOf + ')', ['Species', 'Totes', 'Kg'], spRows(d.stabilized.onHand), [0, 1, 1])}
    ${sec('Production by SKU', ['SKU', 'Runs', 'Litres'], d.production.bySku.map(r => [skuName(r.sku), fmt(r.runs), fmt(r.litres, 0)]), [0, 1, 1])}
    ${sec('Finished goods shipped by customer', ['Customer', 'Units', 'Litres'], d.finishedGoods.shippedByCustomer.map(r => [r.customer, fmt(r.units), fmt(r.litres, 0)]), [0, 1, 1])}
    ${sec('Finished goods on hand (' + d.asOf + ')', ['SKU', 'Litres'], d.finishedGoods.onHand.map(r => [skuName(r.sku), fmt(r.litres, 0)]), [0, 1])}
    ${sec('Reagents received / used',['Item', 'Received', 'Used'], d.consumables.inMonth.map(r => [r.name + ' (' + r.unit + ')', fmt(r.received, 1), fmt(r.used, 1)]), [0, 1, 1])}
    ${sec('Reagents on hand (' + d.asOf + ')', ['Item', 'On hand'], d.consumables.onHand.map(r => [r.name, fmt(r.onHand, 1) + ' ' + r.unit]), [0, 0])}
    ${sec('Stabilized by location (current)', ['Location', 'Totes', 'Kg'], (d.byLocation && d.byLocation.stabilized || []).map(r => [r.location, fmt(r.totes), fmt(r.kg, 0)]), [0, 1, 1])}
    ${sec('Finished goods by location (current)', ['Location', 'Units', 'Litres'], (d.byLocation && d.byLocation.finishedGoods || []).map(r => [r.location, fmt(r.units), fmt(r.litres, 0)]), [0, 1, 1])}
    ${sec('Reagents / packaging by location (current)', ['Location', 'Item', 'On hand'], (d.byLocation && d.byLocation.consumables || []).map(r => [r.location, r.name, fmt(r.onHand, 1) + ' ' + r.unit]), [0, 0, 0])}
    ${sec('Disposed / written off', ['Date', 'Type', 'Item', 'Qty', 'Reason', 'By'], (d.disposed && d.disposed.lines || []).map(l => [l.date, disposalTypeLabel(l.type), l.ref, fmt(l.qty, 1) + ' ' + (l.unit || ''), l.reason, l.by || '']), [0, 0, 0, 1, 0, 0])}
    <p style="margin-top:14px;font-size:10px;color:#999">Generated by KelpWorks ERP · ${d.month}</p>
    <script>window.onload=()=>window.print()<\/script></body></html>`);
  w.document.close();
}
function downloadReportXlsx() {
  const r = State.reportRange;
  if (!r || !r.from || !r.to) return toast('Pick a date range first.', true);
  const url = '/api/reports/xlsx?from=' + encodeURIComponent(r.from) + '&to=' + encodeURIComponent(r.to) + '&token=' + encodeURIComponent(State.token);
  const a = el('a', { href: url, download: 'kelpworks-report-' + r.from + '_' + r.to + '.xlsx' });
  document.body.append(a); a.click(); a.remove();
}
function signed(n) { if (n == null) return '—'; const s = Number(n); return (s > 0 ? '+' : '') + fmt(s, 1); }
async function openLedger(dim, key) {
  const r = State.reportData; if (!r) return;
  const data = await api('GET', '/ledger?dim=' + dim + '&key=' + encodeURIComponent(key) + '&from=' + r.from + '&to=' + r.to);
  const u = data.unit ? ' ' + data.unit : '';
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Item', data.title), sl('Period', r.period),
      sl('Opening', fmt(data.opening, 1) + u), sl('Closing', fmt(data.closing, 1) + u)),
    data.txns.length
      ? table(['Date', 'Transaction', 'Change', 'Balance'],
        data.txns.map(t => [t.date, t.description, signed(t.change), fmt(t.balance, 1) + u]), [false, false, true, true])
      : el('div', { class: 'help' }, 'No transactions in this period.'));
  modal('Transactions — ' + data.title, body, async () => {}, 'Done');
}
function exportReportCsv(d) {
  if (!d) return toast('Nothing to export yet.', true);
  const lines = [];
  const add = (...cols) => lines.push(cols.map(c => '"' + String(c == null ? '' : c).replace(/"/g, '""') + '"').join(','));
  add('Cascadia Seaweed — Manufacturing Report', d.period, 'On hand as of', d.asOf);
  add('');
  add('STABILIZED INVENTORY'); add('Metric', 'Species', 'Totes', 'Kg');
  const sp = (label, s) => s.bySpecies.forEach(r => add(label, speciesName(r.species), r.totes, r.kg));
  sp('Created', d.stabilized.created); sp('Consumed', d.stabilized.consumed); sp('On hand (' + d.asOf + ')', d.stabilized.onHand);
  add('');
  add('PRODUCTION'); add('Runs', d.production.runs, 'Input kg', d.production.inputKg, 'Output L', d.production.outputLitres, 'Yield L/kg', d.production.yield ?? '');
  add('SKU', 'Runs', 'Litres'); d.production.bySku.forEach(r => add(skuName(r.sku), r.runs, r.litres));
  add('');
  add('FINISHED GOODS SHIPPED'); add('Customer', 'Units', 'Litres'); d.finishedGoods.shippedByCustomer.forEach(r => add(r.customer, r.units, r.litres));
  add('FG ON HAND (' + d.asOf + ')'); add('SKU', 'Litres'); d.finishedGoods.onHand.forEach(r => add(skuName(r.sku), r.litres));
  add('');
  add('REAGENTS'); add('Item', 'Unit', 'Received', 'Used', 'On hand (' + d.asOf + ')');
  const oh = {}; d.consumables.onHand.forEach(r => oh[r.name] = r.onHand);
  d.consumables.inMonth.forEach(r => add(r.name, r.unit, r.received, r.used, oh[r.name] ?? ''));
  add('');
  add('INVENTORY BY LOCATION (current)');
  add('Stabilized', 'Location', 'Totes', 'Kg'); (d.byLocation && d.byLocation.stabilized || []).forEach(r => add('', r.location, r.totes, r.kg));
  add('Finished goods', 'Location', 'Units', 'Litres'); (d.byLocation && d.byLocation.finishedGoods || []).forEach(r => add('', r.location, r.units, r.litres));
  add('Reagents / packaging', 'Location', 'Item', 'On hand', 'Unit'); (d.byLocation && d.byLocation.consumables || []).forEach(r => add('', r.location, r.name, r.onHand, r.unit));
  add('');
  add('DISPOSED / WRITTEN OFF'); add('Date', 'Type', 'Item', 'Qty', 'Unit', 'Reason', 'By');
  (d.disposed && d.disposed.lines || []).forEach(l => add(l.date, disposalTypeLabel(l.type), l.ref, l.qty, l.unit, l.reason, l.by));
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
  const a = el('a', { href: URL.createObjectURL(blob), download: 'kelpworks-report-' + d.month + '.csv' });
  document.body.append(a); a.click(); a.remove();
}

/* ---------------- Yield & Usage ---------------- */
// Read-only view over completed runs: observed conversion rates (process =
// output L / measured input kg, harvest = output L / stored batch-average
// input kg) and what each run consumed, grouped by any ticked combination of product / species / farm / ... --
// the observation layer a BOM is later promoted from. Excluded runs never
// count toward the stats (Edit run -> "Exclude from yield & usage analysis").
const YU_CATEGORIES = { reagent: 'Reagent', packaging: 'Packaging', sample: 'Sample container', label: 'FG label' };
// Usage is grouped Reagents / Packaging (incl. sample containers) / Finished-goods labels.
// Reagents show median / min / max (per the chosen basis) and totals rounded to 0.1;
// packaging and labels are plain whole-unit totals (nearest 1) with no statistics.
const yuUsageGroup = u => u.category === 'sample' ? 'packaging' : u.category;
function yuUsageSections(g, basis, stat) {
  const out = [];
  const items = k => g.usage.filter(u => yuUsageGroup(u) === k);
  const reag = items('reagent');
  if (reag.length) out.push(el('div', { class: 'qc-check-section-title' }, 'Reagents — ' + YU_BASES[basis].label),
    table(['Item', 'Unit', 'Used in', 'Median', 'Min', 'Max', 'Total'],
      reag.map(u => [u.item, u.unit, u.usedIn + ' of ' + u.ofRuns + ' runs', ...stat(u[basis], 1), fmt(u.total, 1)]),
      [false, false, false, true, true, true, true]));
  [['packaging', 'Packaging — total used'], ['label', 'Finished goods labels — total used']].forEach(([k, title]) => {
    const rows = items(k);
    if (rows.length) out.push(el('div', { class: 'qc-check-section-title' }, title),
      table(['Item', 'Unit', 'Used in', 'Total'],
        rows.map(u => [u.item, u.unit, u.usedIn + ' of ' + u.ofRuns + ' runs', fmt(u.total, 0)]),
        [false, false, false, true]));
  });
  return out;
}
const YU_FLAGS = { mixed: 'Mixed source', missing_measured_weight: 'No measured weight', no_usage_data: 'No usage data',
  no_output: 'No output', no_source: 'No source totes' };
const YU_BASES = {
  perKLOutput: { label: 'per 1,000 L output' },
  perTonneProcess: { label: 'per 1,000 kg process input (measured)' },
  perTonneHarvest: { label: 'per 1,000 kg harvest input (batch-average)' },
};
const YU_DIMS = [['sku', 'Product'], ['species', 'Species'], ['farm', 'Farm'], ['stabilization', 'Stabilization method'],
  ['harvest_month', 'Harvest month'], ['processing_month', 'Processing month']];
const yuDimLabels = dims => (dims || []).map(d => (YU_DIMS.find(x => x[0] === d) || [d, d])[1]).join(', ') || 'None (all runs)';
const yuRange = (a, b) => !a ? '—' : (a === b ? a : a + ' → ' + b);
function yuQuery(s) {
  return '?groupBy=' + (s.dims.join(',') || 'none') + (s.from ? '&from=' + s.from : '') + (s.to ? '&to=' + s.to : '')
    + (s.showExcluded ? '&includeExcluded=1' : '');
}
async function pageYield(v) {
  const s = { from: '', to: '', dims: ['sku', 'species', 'farm'], showExcluded: false, basis: 'perKLOutput' };
  let data = null;
  const fromInp = el('input', { type: 'date' }), toInp = el('input', { type: 'date' });
  // Any combination of dimensions may be ticked; a run holding more than one
  // value of a ticked dimension lands in that dimension's "Mixed" bucket.
  const dimCbs = YU_DIMS.map(([k, label]) => {
    const cb = el('input', { type: 'checkbox' });
    cb.checked = s.dims.includes(k);
    cb.addEventListener('change', load);
    return { k, cb, el: el('label', { style: 'display:inline-flex;align-items:center;gap:4px;margin-right:14px;font-weight:normal;white-space:nowrap' }, cb, label) };
  });
  const groupBox = el('div', { style: 'display:flex;flex-wrap:wrap;padding:4px 0' }, ...dimCbs.map(d => d.el));
  const basisSel = selectFrom('', Object.entries(YU_BASES).map(([k, b]) => [k, b.label]));
  const exclCb = el('input', { type: 'checkbox' });
  const host = el('div', {});
  async function load() {
    s.from = fromInp.value; s.to = toInp.value; s.dims = dimCbs.filter(d => d.cb.checked).map(d => d.k); s.showExcluded = exclCb.checked;
    data = await api('GET', '/yield-usage' + yuQuery(s));
    draw();
  }
  function setRange(from, to) { fromInp.value = from; toInp.value = to; load(); }
  const today = new Date().toISOString().slice(0, 10);
  const daysAgo = n => new Date(Date.now() - n * 86400000).toISOString().slice(0, 10);
  v.append(el('div', { class: 'page-head' }, el('h2', {}, 'Yield & Usage'),
    el('div', { class: 'actions' },
      el('button', { class: 'secondary', onclick: () => exportYieldCsv(data) }, '⬇ CSV'),
      el('button', { class: 'secondary', onclick: () => {
        const a = el('a', { href: '/api/yield-usage/xlsx' + yuQuery(s) + '&token=' + encodeURIComponent(State.token), download: 'kelpworks-yield-usage.xlsx' });
        document.body.append(a); a.click(); a.remove();
      } }, '⬇ Excel'))));
  v.append(el('div', { class: 'help', style: 'margin-bottom:10px' },
    'Observed conversion rates and consumption from completed runs -- the basis for a BOM. Process rate = output L / measured input kg; '
    + 'harvest rate = output L / stored batch-average input kg. Usage comes from the consumable ledger; runs finalized before reagent deduction have no usage data.'),
    el('div', { class: 'form-row-3', style: 'margin-bottom:6px' }, field('From', fromInp), field('To', toInp), field('Group by (tick any combination)', groupBox)),
    el('div', { class: 'form-row-3', style: 'margin-bottom:10px' },
      field('Usage shown', basisSel),
      field('Show excluded runs', exclCb),
      el('div', { class: 'actions', style: 'align-self:end;display:flex;gap:6px' },
        el('button', { class: 'secondary', onclick: () => setRange('', '') }, 'All time'),
        el('button', { class: 'secondary', onclick: () => setRange(daysAgo(90), today) }, 'Last 90 days'),
        el('button', { class: 'secondary', onclick: () => setRange(today.slice(0, 4) + '-01-01', today) }, 'Year to date'))),
    host);
  [fromInp, toInp, exclCb].forEach(c => c.addEventListener('change', load));
  basisSel.addEventListener('change', () => { s.basis = basisSel.value; draw(); });

  const stat = (st, d) => st ? [fmt(st.median, d), fmt(st.min, d), fmt(st.max, d)] : ['—', '—', '—'];
  function draw() {
    host.innerHTML = '';
    if (!data.groups.length) {
      host.append(el('div', { class: 'empty card' }, 'No completed runs match this range' + (data.excludedCount ? ' (' + data.excludedCount + ' excluded).' : '.')));
    }
    data.groups.forEach(g => {
      const rate = (label, r) => [label, r ? fmt(r.n) : '0', ...stat(r, 3), r && r.pooled != null ? fmt(r.pooled, 3) : '—'];
      host.append(el('div', { class: 'card', style: 'margin-bottom:14px' },
        el('h3', {}, g.title, ' ', g.lowSample ? badge('low', 'LOW SAMPLE (n<' + data.minRuns + ')') : null),
        el('div', { class: 'summary-line' }, sl('Runs', fmt(g.runs)), sl('Harvest input', fmt(g.harvestKg, 0) + ' kg'),
          sl('Process input (' + (g.processRate ? g.processRate.n : 0) + ' runs measured)', fmt(g.processKg, 0) + ' kg'),
          sl('Output', fmt(g.outputL, 0) + ' L')),
        table(['Conversion rate (L / kg)', 'Runs', 'Median', 'Min', 'Max', 'Pooled'],
          [rate('Process (measured weight)', g.processRate), rate('Harvest (batch-average weight)', g.harvestRate)],
          [false, true, true, true, true, true]),
        ...(g.usage.length ? yuUsageSections(g, s.basis, stat)
          : [el('div', { class: 'qc-check-section-title' }, 'Usage'),
            el('div', { class: 'help' }, g.usageRuns ? 'No consumption recorded for these runs.'
              : 'No usage data yet -- runs finalized before reagent deduction have none.')])));
    });
    if (data.runs.length) {
      const body = table(['Lot', 'Processing date', 'Harvest date', 'Product', 'Farm', 'Species', 'Harvest kg', 'Process kg', 'Output L',
        'Harvest rate', 'Process rate', 'Extraction eff. (%)', 'Final pH', 'Final TDS (%)', 'Flags', ''],
        data.runs.map(r => [mono(r.lot),
          r.processingDate + (r.processingDateEstimated ? ' (est.)' : ''),
          yuRange(r.harvestDateFrom, r.harvestDateTo), r.skuName,
          r.farmNames.join(' · ') || '—', r.speciesNames.join(' · ') || '—',
          fmt(r.harvestKg, 1), r.processKg != null ? fmt(r.processKg, 1) : '—', fmt(r.outputL, 0),
          r.harvestRate != null ? fmt(r.harvestRate, 3) : '—', r.processRate != null ? fmt(r.processRate, 3) : '—',
          r.extractionEfficiency != null ? fmt(r.extractionEfficiency, 1) : '—',
          r.finalPh != null ? fmt(r.finalPh, 2) : '—', r.finalTds != null ? fmt(r.finalTds, 2) : '—',
          (r.excluded ? ['Excluded' + (r.excludeReason ? ': ' + r.excludeReason : '')] : []).concat(r.flags.map(f => YU_FLAGS[f] || f)).join(' · ') || '—',
          rowActions([['Edit', async () => {
            const run = (await api('GET', '/production')).runs.find(x => x.id === r.id);
            if (run) editRun(run);
          }]])]),
        [false, false, false, false, false, false, true, true, true, true, true, true, true, true, false, false]);
      [...body.querySelectorAll('tbody tr')].forEach((tr, i) => { if (data.runs[i].excluded) tr.style.opacity = '.55'; });
      host.append(el('details', { class: 'accordion', style: 'margin-top:6px' }, el('summary', {}, 'Runs (' + data.runs.length + ')'),
        el('div', { class: 'accordion-body' }, body)));
    }
  }
  await load();
}
function exportYieldCsv(d) {
  if (!d) return toast('Nothing to export yet.', true);
  const lines = [];
  const add = (...cols) => lines.push(cols.map(c => '"' + String(c == null ? '' : c).replace(/"/g, '""') + '"').join(','));
  add('Cascadia Seaweed — Yield & Usage', (d.from || 'start') + ' to ' + (d.to || 'today'), 'Grouped by', yuDimLabels(d.groupBy));
  add('');
  add('CONVERSION RATES (L output per kg input)');
  add('Group', 'Runs', 'Low sample', 'Process n', 'Process median', 'Process min', 'Process max', 'Process pooled',
    'Harvest n', 'Harvest median', 'Harvest min', 'Harvest max', 'Harvest pooled');
  d.groups.forEach(g => {
    const p = g.processRate || {}, h = g.harvestRate || {};
    add(g.title, g.runs, g.lowSample ? 'yes' : '', p.n ?? '', p.median ?? '', p.min ?? '', p.max ?? '', p.pooled ?? '',
      h.n ?? '', h.median ?? '', h.min ?? '', h.max ?? '', h.pooled ?? '');
  });
  add('');
  const r1 = v => v == null ? '' : Math.round(v * 10) / 10;
  add('REAGENTS (net consumed per run, from the ledger; rounded to 0.1)');
  add('Group', 'Item', 'Unit', 'Used in', 'Of runs', 'Total',
    'per 1000 L output median', 'min', 'max', 'per 1000 kg process median', 'min', 'max', 'per 1000 kg harvest median', 'min', 'max');
  d.groups.forEach(g => g.usage.filter(u => yuUsageGroup(u) === 'reagent').forEach(u => {
    const o = u.perKLOutput || {}, p = u.perTonneProcess || {}, h = u.perTonneHarvest || {};
    add(g.title, u.item, u.unit, u.usedIn, u.ofRuns, r1(u.total),
      r1(o.median), r1(o.min), r1(o.max), r1(p.median), r1(p.min), r1(p.max), r1(h.median), r1(h.min), r1(h.max));
  }));
  [['packaging', 'PACKAGING (total count used)'], ['label', 'FINISHED GOODS LABELS (total count used)']].forEach(([k, title]) => {
    add('');
    add(title);
    add('Group', 'Item', 'Unit', 'Used in', 'Of runs', 'Total');
    d.groups.forEach(g => g.usage.filter(u => yuUsageGroup(u) === k).forEach(u => add(g.title, u.item, u.unit, u.usedIn, u.ofRuns, Math.round(u.total))));
  });
  add('');
  add('RUNS');
  add('Lot', 'Processing date', 'Processing date estimated', 'Harvest date', 'Product', 'Farm', 'Species', 'Stabilization',
    'Harvest kg', 'Process kg', 'Output L', 'Harvest rate', 'Process rate', 'TDS before extraction (%)', 'TDS after extraction (%)', 'Extraction efficiency (%)', 'Final pH', 'Final TDS (%)', 'Excluded', 'Reason', 'Flags');
  d.runs.forEach(r => add(r.lot, r.processingDate, r.processingDateEstimated ? 'yes' : '', yuRange(r.harvestDateFrom, r.harvestDateTo),
    r.skuName, r.farmNames.join(' / '), r.speciesNames.join(' / '), r.stabilization.join(' / '),
    r.harvestKg, r.processKg ?? '', r.outputL, r.harvestRate ?? '', r.processRate ?? '', r.tdsBeforeExtraction ?? '', r.tdsAfterExtraction ?? '', r.extractionEfficiency ?? '', r.finalPh ?? '', r.finalTds ?? '',
    r.excluded ? 'yes' : '', r.excludeReason || '', r.flags.join(' ')));
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
  const a = el('a', { href: URL.createObjectURL(blob), download: 'kelpworks-yield-usage.csv' });
  document.body.append(a); a.click(); a.remove();
}

/* ---------------- Calculations ---------------- */
// Registry of every calculated field in the app -- displayed on the
// Calculations page. Keep this in sync going forward: add an entry when a
// new calculated field is built, remove its entry if that field is ever
// deleted. `settings` lists the admin-editable constant keys (if any) the
// formula depends on -- any new "magic number" a formula needs should
// become a new settings row (backend SETTINGS_DEFAULTS) rather than a bare
// literal in the code, so it shows up in the table below automatically.
const CALCULATIONS = [
  {
    title: 'Certificate of Analysis: heavy-metal loading (kg/ha)',
    formula: 'Loading (kg metal / ha) = Result (mg/kg) × Application rate (kg product / ha) × Application periods ÷ 1,000,000.  Result (mg/kg) = reported value × unit factor (ppm = 1, % = 10,000, ppb = 0.001).  Passes when Loading ≤ the kg/ha specification; a “<” result is compared at its detection limit.',
    description: 'The heavy-metal specifications are loading limits in kg metal per ha. Each lab concentration is converted at the product application rate and multiplied by the number of application periods, then compared with the limit; with no application rate set, the metals are listed but not judged. Both values are admin-only constants.',
    location: 'Production → 🧫 Lab results; Certificate of Analysis PDF',
    settings: ['coa_application_rate_kg_ha', 'coa_application_periods'],
  },
  {
    title: 'Measured %Solids Loading, (w/w)',
    formula: 'Measured %Solids Loading = Wet-solids-wt (g) / (Wet-solids-wt (g) + Liquid-wt (g))',
    description: 'The solids loading (w/w) of a homogenized tank sample, from a Process Check split of a weighed sample into its solid and liquid portions.',
    location: 'Production → Process log → Homogenization → Homogenization In → Process Check box',
    settings: [],
  },
  {
    title: 'Recommended Dilution Water (L)',
    formula: 'c₁V₁ = c₂V₂, so Recommended Dilution Water (L) = V₂ − V₁ = Pre-Dilution Tank Level (L) × (Measured %Solids Loading ÷ Target %Solids Loading − 1), or 0 when Measured ≤ Target',
    description: 'c₁ is the Measured %Solids Loading, V₁ the Pre-Dilution Tank Level (L), c₂ the Target %Solids Loading and V₂ the volume the tank reaches once diluted to the target. Rounded to the nearest 10 L; 0 when the measured loading is already at or below the target.',
    location: 'Production → Process log → Homogenization → Homogenization Out',
    settings: [],
  },
  {
    title: 'Lot %Solids Loading, (w/w) (calculated)',
    formula: 'Lot %Solids Loading = Measured %Solids Loading × (Post-Dilution Tank Level (L) − Dilution water added (L)) ÷ Post-Dilution Tank Level (L)',
    description: 'The solids loading of the lot after dilution, by the same c₁V₁ = c₂V₂ balance: V₂ is the final (Post-Dilution) tank volume, V₁ = V₂ − the dilution water added, c₁ the Measured %Solids Loading and c₂ the result. Blank until all three inputs are entered.',
    location: 'Production → Process log → Homogenization → Homogenization Out',
    settings: [],
  },
  {
    title: 'Maximum product to transfer, Tanks 5A/5B → 6A/6B (L)',
    formula: 'Available space = (Tank 6A/6B capacity × number of receiving tanks) − current level in the receiving tank(s)   ·   Maximum product to transfer = Available space × TDS target ÷ TDS concentrated (or = Available space if TDS concentrated ≤ target)',
    description: 'The most product that can be pushed through from 5A/5B before the receiving tank(s) reach capacity once it is diluted to the SKU’s target TDS (c₁V₁ = c₂V₂). TDS concentrated is the Separation filtrate TDS. Selecting 6A + 6B connected doubles the capacity; whatever is already in the selected tanks is deducted first.',
    location: 'Production → Process log → Pasteurization → Pasteurization In → Process Check (Dilution plan)',
    settings: ['dilution_tank_capacity_each_l'],
  },
  {
    title: 'Recommended product transfer and dilution water (L)',
    formula: 'Recommended product transfer = the smaller of (Tank 5A + 5B levels) and the Maximum product to transfer   ·   Recommended dilution water = transfer × (TDS concentrated ÷ TDS target − 1), or 0 when TDS concentrated ≤ target   ·   Expected final volume = current level in receiving tank(s) + transfer + water',
    description: 'What to move from 5A/5B and how much water to add to reach the target TDS without exceeding the receiving tank(s). When more product is available than fits, the remainder is shown as staying behind in 5A/5B. Volumes display rounded to the nearest 10 L.',
    location: 'Production → Process log → Pasteurization → Pasteurization In → Process Check (Dilution plan)',
    settings: ['dilution_tank_capacity_each_l'],
  },
  {
    title: 'Pre-processing: recommended dilution water and expected blend volume',
    formula: 'Estimated weight (kg) = Σ weight of the totes in the pick list.  Recommended water (L) = Estimated weight × (Starting % solids ÷ Target % solids − 1), 0 if the starting % is not above the target.  Expected blend volume (L) = Σ tote volume (L) + dilution water (the amount added, or the recommended amount until it is entered; 1 kg water = 1 L)',
    description: 'Solids loading of a shred-and-blend batch: the shredded kelp is diluted from its measured % solids to the target % solids. The target defaults from the setting and can be changed per batch. All Blend fields are optional.',
    location: 'Pre-Processing → batch → Blend — solids loading',
    settings: ['preproc_target_solids_pct'],
  },
  {
    title: 'Pre-processing: final % solids (calculated)',
    formula: 'Final % solids = Starting % solids × Estimated weight ÷ (Estimated weight + Dilution water added)',
    description: 'What the blend solids loading works out to given the water actually added; stored on each output IBC lot.',
    location: 'Pre-Processing → batch → Blend — solids loading; Feedstock Inventory → Trace',
    settings: [],
  },
  {
    title: 'Stabilization period (weeks)',
    formula: 'Stabilization period = round((End date − Start date) ÷ 7 days) to the nearest whole week, where Start date = Harvest date (for a fine-grind blend: its Pre-Processing batch date) and End date = today (in stock / hold / WIP), the processing date (consumed) or the disposal date (disposed)',
    description: 'How long a feedstock tote stabilized. A fine-grind blend counts from the date it was blended (its batch date) rather than the harvest dates of its source totes. While a tote is still in inventory the count runs to today; once it is consumed it is fixed at the date it was processed (its production run’s finalize date, or its Pre-Processing batch’s completion date); once disposed it is fixed at the disposal date. Blank when a needed date is missing.',
    location: 'Feedstock Inventory → Stabilization period (weeks)',
    settings: [],
  },
  {
    title: 'Retention sample discard-by date',
    formula: 'Discard-by = Collection date + Retention shelf life (months)',
    description: 'Each Retention sample in the Samples tab’s Retention inventory is flagged as expiring within 30 days, or expired, against this date. Only Retention samples have one.',
    location: 'Samples → Retention inventory',
    settings: ['sample_retention_months'],
  },
  {
    title: 'Pre-processing: final % solids (estimated from actuals)',
    formula: 'Product volume = Final blend volume − Dilution water added.  Product mass = Product volume × (Estimated weight ÷ Σ tote volume)  (1 kg/L if tote volumes are missing).  Final % solids = Starting % solids × Product mass ÷ (Product mass + Dilution water added)',
    description: 'A check on the blend using what was actually measured: the level-sensor final volume and the dilution water really added, rather than the estimated weights alone. Shown beside the calculated final % solids; blank until the starting %, water added and blend volume are entered.',
    location: 'Pre-Processing → batch → Blend — solids loading',
    settings: [],
  },
  {
    title: 'Pre-processing: output IBC weight',
    formula: 'Output IBC weight (kg) = (Estimated weight + Dilution water added) × (that IBC fill ÷ Total packed)',
    description: 'Each output IBC lot is stored in Feedstock Inventory with its share of the blend mass as its weight, so downstream yield figures use the blend mass.',
    location: 'Pre-Processing → batch → Pack-out; Feedstock Inventory → Avg kg',
    settings: [],
  },
  {
    title: 'Product remaining in Tank 5A/5B and additional dilution passes (L)',
    formula: 'Remaining product = (Tank 5A level + Tank 5B level) − Product transferred (the recommended transfer until the actual is entered)',
    description: 'When product is left in 5A/5B after a pass, the section offers another pass. Each extra pass has its own plan, actuals and per-tank preservative additions using the same maximum-transfer, recommended-water, variance and preservative-dose formulas as the first pass. The run’s Ksorbate / sodium benzoate totals (and so the reagent deduction) are the sum across all passes.',
    location: 'Production → Process log → Dilution & Preservation → Additional dilution passes',
    settings: ['dilution_tank_capacity_each_l', 'dilution_variance_flag_pct'],
  },
  {
    title: 'Dilution check: final volume variance and expected TDS',
    formula: 'Expected final volume = starting level(s) + Product transferred + Dilution water added   ·   Variance (%) = (Total final volume − Expected) ÷ Expected × 100, flagged when |Variance| exceeds the setting   ·   Expected TDS = TDS concentrated × Product transferred ÷ (Product transferred + Dilution water added)',
    description: 'Compares what the receiving tank level sensors read after dilution with what the transfer and water imply, and shows the TDS the measured amounts should produce against the target.',
    location: 'Production → Process log → Dilution & Preservation → Dilution',
    settings: ['dilution_variance_flag_pct'],
  },
  {
    title: 'Ksorbate, calculated (L)',
    formula: 'Ksorbate, calculated (L), per tank = Final level of that tank (L) × Ksorbate target (w/v) / Ksorbate stock concentration (w/v)',
    description: 'The estimated volume of stock Ksorbate solution needed to reach the product SKU’s target Ksorbate dose in the volume in each receiving tank (mass-conservation dilution math). The run total is the sum of what was added to each tank.',
    location: 'Production → Process log → Dilution & Preservation → Preservatives',
    settings: [],
  },
  {
    title: 'Ksorbate added (kg)',
    formula: 'Ksorbate added (kg) = Ksorbate added (L) × Ksorbate stock concentration (w/v) / 100',
    description: 'The mass of potassium sorbate added to the batch, from the volume of stock solution added and its %w/v concentration.',
    location: 'Production → Process log → Dilution & Preservation → Preservatives',
    settings: [],
  },
  {
    title: 'Nabenzoate, calculated (L)',
    formula: 'Nabenzoate, calculated (L), per tank = Final level of that tank (L) × Nabenzoate target (w/v) / Nabenzoate stock concentration (w/v)',
    description: 'The estimated volume of stock sodium benzoate solution needed to reach the product SKU’s target Nabenzoate dose in the volume in each receiving tank (same mass-conservation math as Ksorbate; the run total is the sum of the tanks).',
    location: 'Production → Process log → Dilution & Preservation → Preservatives',
    settings: ['nabenzoate_stock_concentration_default_pct'],
  },
  {
    title: 'Sodium benzoate added (kg)',
    formula: 'Sodium benzoate added (kg) = Sodium benzoate added (L) × Nabenzoate stock concentration (w/v) / 100',
    description: 'The mass of sodium benzoate added to the batch, from the volume of stock solution added and its %w/v concentration.',
    location: 'Production → Process log → Dilution & Preservation → Preservatives',
    settings: ['nabenzoate_stock_concentration_default_pct'],
  },
  {
    title: 'Reagent usage (inventory deduction)',
    formula: 'Citric Acid (kg) = Citric acid added (kg), plus the citric acid added in each additional dilution pass   ·   Potassium Sorbate (kg) = Ksorbate added (L) × stock (w/v) / 100   ·   Sodium Benzoate (kg) = Sodium benzoate added (L) × stock (w/v) / 100',
    description: 'What Saving Dilution & Preservation (or finalizing the run) deducts from Reagent stock, as one net-change ledger line per reagent: only the change since the last save is deducted, and discarding a draft refunds it. The same kg are added to the run’s Citric / Sorbate / Na benzoate totals.',
    location: 'Production → Process log → Dilution & Preservation (applied on Save and when a run is finalized)',
    settings: [],
  },
  {
    title: 'Density (calculated)',
    formula: 'Density (kg/L) = Weight (kg) / Volume (L), rounded to 3 decimals',
    description: 'A tote or feedstock sample’s density, from its weighed mass and measured volume.',
    location: 'Feedstock Inventory → Update → Details card · Production → Feedstock characterization card (new run and Process log)',
    settings: [],
  },
  {
    title: 'ORP meter range (calculated)',
    formula: 'ORP < B1 → "Spoiled"  ·  B1 ≤ ORP < B2 → "Spoilage underway"  ·  B2 ≤ ORP < B3 → "Watch closely"  ·  ORP ≥ B3 → "Stable / safe zone"',
    description: 'Classifies an ORP (mV) reading into an SOP-defined spoilage band (per REF_ORP_classification.pdf), using the 3 threshold values (B1/B2/B3) below.',
    location: 'Feedstock Inventory (table + Update modal) · Production → Feedstock characterization card',
    settings: ['orp_spoiled_below', 'orp_spoilage_underway_below', 'orp_watch_closely_below'],
  },
  {
    title: 'Conversion factor',
    formula: 'Conversion factor (L/kg) = Output (L) / Input (kg)',
    description: 'A finished run’s output-to-input yield ratio, shown on its own summary card.',
    location: 'Production Runs list → each run’s summary line',
    settings: [],
  },
  {
    title: 'Output (L) / New IBCs filled',
    formula: 'Output (L) = Σ (entry qty × container unit’s litres each)   ·   New IBCs filled = Σ qty where the container unit is IBC',
    description: 'A run’s total bottled output and IBC usage, computed from the Packaging table’s entries at finalization using each container’s litres-each value (Inventory Items → Packaging).',
    location: 'Production → Packaging section, applied when a run is finalized',
    settings: [],
  },
  {
    title: 'Process / Harvest conversion rate (Yield & Usage)',
    formula: 'Process rate (L/kg) = Output (L) / Σ measured tote weights (kg)   ·   Harvest rate (L/kg) = Output (L) / stored input (kg, batch-average tote weight)   ·   Pooled = Σ output / Σ input over the group’s runs',
    description: 'Per completed, non-excluded run, with median / min / max across the group. A run missing a measured weight on any accepted tote has no process rate (no fallback). Groups with fewer runs than the minimum are tagged "Low sample".',
    location: 'Yield & Usage tab',
    settings: ['yield_report_min_runs'],
  },
  {
    title: 'Extraction efficiency (Yield & Usage)',
    formula: 'Extraction efficiency (%) = (TDS after extraction − TDS before extraction) / TDS before extraction × 100',
    description: 'TDS before extraction is the Homogenization → Lot characterization TDS (%); TDS after extraction is the Extraction → Extraction Performance TDS (%). Blank when either value is missing or the before-TDS is zero.',
    location: 'Yield & Usage tab → Runs table',
    settings: [],
  },
  {
    title: 'Usage per 1,000 L / 1,000 kg (Yield & Usage)',
    formula: 'Usage = net units consumed on the run’s ledger lines (reagents, packaging, sample containers, FG labels; refunds and edits netted)   ·   per 1,000 L = usage / output (L) × 1000   ·   per 1,000 kg = usage / input (kg) × 1000',
    description: 'Statistics are over the runs that used the item; "used in a of b runs" counts b as the group’s runs that have any usage data (runs finalized before reagent deduction have none and are left out rather than counted as zero). The report groups items as Reagents (median / min / max, rounded to 0.1), Packaging and Finished goods labels (total count only, rounded to the nearest 1).',
    location: 'Yield & Usage tab',
    settings: ['yield_report_min_runs'],
  },
  {
    title: 'CIP duration',
    formula: 'Duration (min) = CIP end time − CIP start time',
    description: 'How long a Clean In Place took, shown as hours/minutes once both times are entered.',
    location: 'CIP Log → each entry’s Duration column',
    settings: [],
  },
  {
    title: 'CIP chemical usage (inventory deduction)',
    formula: 'Stock change per CIP agent = − Σ qty (L) on the entry’s chemical lines; edits adjust by the difference, deleting an entry refunds',
    description: 'Saving, editing or deleting a CIP Log entry adjusts the CIP Acid / Caustic / Sanitizer stock in Inventory Items, as one ledger line per agent referencing the CIP entry. A shortage never blocks logging a cleaning; it just drives stock below zero and the save says to receive stock.',
    location: 'CIP Log → Log CIP / Edit',
    settings: [],
  },
  {
    title: 'Finished-good label usage (inventory deduction)',
    formula: 'Labels consumed = Σ Packaging entry qty per container, for the label item mapped to (run SKU, container)',
    description: 'Whenever the Packaging section’s container usage is committed (its Save button, or finalize), one finished-good label is consumed for every container, from the label item mapped to the run’s product SKU and that container (Inventory Items → Finished-good labels; each SKU has one for the 1,000 L IBC and one for the 55 gal drum). Only the net change since the last save is applied, so editing quantities or discarding a draft adjusts or refunds the labels. No matching label item means nothing is deducted; a shortage never blocks.',
    location: 'Production → Packaging section (Save / finalize)',
    settings: [],
  },
  {
    title: 'Finished Goods Litres',
    formula: 'Litres = Qty × Litres each',
    description: 'A finished-goods lot’s total litres on hand, from its unit count and its package size’s litre value.',
    location: 'Finished Goods table · Shipping → New shipment summary',
    settings: [],
  },
  {
    title: 'Ksorbate / Nabenzoate (w/v)',
    formula: 'Percent (w/v) = target ratio × 100',
    description: 'Displays a product SKU’s target preservative ratio (stored as a decimal ratio) as a percentage.',
    location: 'Production → New production run / Edit run → Product Specification panel',
    settings: [],
  },
];
function settingLabel(key) {
  const s = State.ref.settings && State.ref.settings[key];
  return s ? s.label + ' = ' + fmt(s.value, Number.isInteger(s.value) ? 0 : 3) : key;
}
async function pageCalculations(v) {
  v.append(el('div', { class: 'page-head' }, el('h2', {}, 'Calculations'),
    el('div', { class: 'muted' }, 'Every calculated field in KelpWorks, and the constants behind them.')));

  const isAdmin = State.user.role === 'admin';
  const settingsHost = el('div', {});
  v.append(el('div', { class: 'card' },
    el('h3', {}, 'Variables and values'),
    el('div', { class: 'muted', style: 'font-size:12px;margin-bottom:10px' },
      isAdmin ? 'Edit a value and click Save — every calculation below using it picks up the change immediately.'
        : 'These constants feed the calculations below. Only an admin can edit them.'),
    settingsHost));
  function drawSettings() {
    settingsHost.innerHTML = '';
    const entries = Object.entries(State.ref.settings || {}).sort((a, b) => a[1].label.localeCompare(b[1].label));
    const rows = entries.map(([key, s]) => {
      const valInp = el('input', { type: 'number', step: 'any', value: s.value, style: 'width:110px' });
      if (!isAdmin) valInp.disabled = true;
      const rowStatus = el('span', { class: 'help' });
      const cell = isAdmin
        ? el('div', { style: 'display:flex;gap:8px;align-items:center' }, valInp,
            el('button', {
              type: 'button', class: 'secondary', onclick: async () => {
                rowStatus.textContent = '';
                try {
                  await api('PUT', '/settings/' + key, { value: valInp.value.trim() === '' ? null : +valInp.value });
                  State.ref = await api('GET', '/refdata');
                  rowStatus.textContent = 'Saved.';
                  render();
                } catch (e) { rowStatus.textContent = e.message; }
              }
            }, 'Save'), rowStatus)
        : valInp;
      return [s.label, cell, s.description || '—'];
    });
    settingsHost.append(table(['Variable', 'Value', 'Description'], rows, [false, false, false]));
  }
  drawSettings();

  CALCULATIONS.forEach(c => {
    v.append(el('div', { class: 'card' },
      el('h3', {}, c.title),
      el('div', { class: 'mono', style: 'background:#f6faf9;border-radius:8px;padding:10px 12px;margin-bottom:10px;white-space:pre-wrap;font-size:13px' }, c.formula),
      el('div', { style: 'margin-bottom:6px' }, c.description),
      el('div', { class: 'muted', style: 'font-size:12px' }, 'Found in: ' + c.location),
      c.settings.length ? el('div', { class: 'muted', style: 'font-size:12px;margin-top:4px' },
        'Uses: ' + c.settings.map(settingLabel).join('  ·  ')) : null));
  });
}

/* ---------------- Labels ---------------- */
/* ---------------- Samples: analysis catalogue, retention inventory, lab cart, requisitions ---------------- */
const SAMPLE_REMOVE_REASONS = ['Consumed in analysis', 'Disposed', 'Expired', 'Lost / damaged', 'Sent to lab (outside the app)', 'Other'];
const SAMPLE_STATUS_LABELS = { available: 'In inventory', in_cart: 'In cart', submitted: 'Sent to lab', removed: 'Removed' };
const SAMPLE_STATUS_BADGE = { available: 'on_hand', in_cart: 'wip', submitted: 'pending_release', removed: 'consumed' };
const sampleStatusBadge = s => badge(SAMPLE_STATUS_BADGE[s.status] || 'consumed', SAMPLE_STATUS_LABELS[s.status] || s.status);
const reqDocUrl = (runId, attId, dl) => attDownloadUrl(runId, attId, dl);
// A document link pair: download + a 👁 preview (Word / Excel are rendered as simple HTML in a window)
const reqDocLink = (runId, attId, filename, fallback) => el('span', { style: 'display:inline-flex;gap:6px;align-items:baseline' },
  el('a', { href: reqDocUrl(runId, attId, true), onclick: e => e.stopPropagation() }, '⬇ ' + (filename || fallback)),
  el('a', { href: '#', title: 'Preview', onclick: e => { e.preventDefault(); e.stopPropagation(); previewAttachment(runId, attId, filename || fallback); } }, '👁 Preview'));
const reqDocLinks = q => el('span', { style: 'display:flex;gap:14px;flex-wrap:wrap' },
  q.attachmentId ? reqDocLink(q.runId, q.attachmentId, q.filename, 'Requisition') : null,
  q.sheetAttachmentId ? reqDocLink(q.runId, q.sheetAttachmentId, q.sheetFilename, 'Sample spreadsheet') : null);
const PREVIEW_SHELL = h => '<!doctype html><meta charset="utf-8"><style>body{font:13px/1.4 Arial,Helvetica,sans-serif;margin:14px;color:#111}'
  + 'table{border-collapse:collapse;margin:6px 0;max-width:100%}td{border:1px solid #bbb;padding:3px 6px;vertical-align:top}table.x td{white-space:nowrap}'
  + 'p{margin:3px 0}p.e{min-height:6px}h4{margin:12px 0 4px}</style>' + h;
// parts: [{ label, html }] shown as tabs, each in a sandboxed frame (no scripts run, all text is escaped by the server)
function previewBody(parts, note) {
  const frame = el('iframe', { sandbox: '', style: 'width:100%;height:58vh;border:1px solid var(--line);border-radius:8px;background:#fff' });
  const tabs = el('div', { style: 'display:flex;gap:8px;margin:8px 0' });
  const show = i => { frame.srcdoc = PREVIEW_SHELL(parts[i].html); [...tabs.children].forEach((b, k) => { b.className = k === i ? '' : 'secondary'; }); };
  parts.forEach((p, i) => tabs.append(el('button', { type: 'button', onclick: () => show(i) }, p.label)));
  if (parts.length < 2) tabs.classList.add('hidden');
  show(0);
  return el('div', {}, tabs, frame, el('div', { class: 'help', style: 'margin-top:6px' }, note));
}
async function previewAttachment(runId, attId, filename) {
  try {
    const r = await api('GET', '/production/' + runId + '/attachments/' + attId + '/preview');
    modal('Preview — ' + (filename || r.filename), previewBody([{ label: r.kind === 'docx' ? 'Form' : 'Spreadsheet', html: r.html }],
      'A simplified preview of the stored document — logos and exact layout appear in the downloaded file.'),
    async () => { const l = el('a', { href: reqDocUrl(runId, attId, true), download: filename || r.filename }); document.body.append(l); l.click(); l.remove(); }, '⬇ Download', { wide: true });
  } catch (e) { toast(e.message, true); }
}
const sampleWhen = iso => iso ? String(iso).replace('T', ' ').replace('Z', '').slice(0, 16) : '—';

async function pageSamples(v) {
  const views = [['catalogue', 'Analysis catalogue'], ['retention', 'Retention inventory'], ['cart', 'Cart'], ['requisitions', 'Requisitions']];
  if (!views.some(x => x[0] === State.samplesView)) State.samplesView = 'catalogue';
  const cartCount = (await api('GET', '/cart')).items.length;
  v.append(el('div', { class: 'page-head' }, el('h2', {}, 'Samples'),
    el('div', { class: 'actions' }, ...views.map(([k, label]) => el('button', {
      class: State.samplesView === k ? '' : 'secondary', onclick: () => { State.samplesView = k; render(); }
    }, k === 'cart' ? 'Cart (' + cartCount + ')' : label)))));
  const host = el('div', {}); v.append(host);
  if (State.samplesView === 'catalogue') await drawSampleCatalogue(host);
  else if (State.samplesView === 'retention') await drawRetentionInventory(host);
  else if (State.samplesView === 'cart') await drawSampleCart(host);
  else await drawRequisitions(host);
}

// Bulk actions shared by the catalogue and retention views.
function sampleBulkBar(selected, byId) {
  const bar = el('div', { class: 'bulkbar hidden' });
  return {
    el: bar,
    update() {
      const ids = [...selected];
      bar.classList.toggle('hidden', !ids.length); bar.innerHTML = '';
      if (!ids.length) return;
      const cartable = ids.filter(i => byId(i) && byId(i).status === 'available');
      const removable = ids.filter(i => byId(i) && ['available', 'in_cart'].includes(byId(i).status));
      bar.append(el('span', {}, el('b', {}, ids.length), ' selected'),
        cartable.length ? el('button', { onclick: async () => { await api('POST', '/cart', { sampleIds: cartable }); toast(cartable.length + ' sample(s) added to the cart'); selected.clear(); render(); } }, 'Add to cart (' + cartable.length + ')') : null,
        el('button', { class: 'secondary', onclick: () => setSampleLocationModal(ids) }, 'Set location'),
        removable.length ? el('button', { class: 'danger', onclick: () => removeSamplesModal(removable) }, 'Remove from inventory (' + removable.length + ')') : null,
        el('button', { class: 'secondary', onclick: () => { selected.clear(); render(); } }, 'Clear'));
    }
  };
}

function setSampleLocationModal(ids) {
  const inp = el('input', { placeholder: 'e.g. Freezer 2, shelf B' });
  modal('Set storage location', el('div', {}, el('div', { class: 'help' }, ids.length + ' sample(s)'), field('Location', inp)), async () => {
    await api('POST', '/samples/location', { ids, location: inp.value });
    toast('Location updated'); render();
  }, 'Save');
}

function removeSamplesModal(ids) {
  const reason = el('select', {}, ...SAMPLE_REMOVE_REASONS.map(r => el('option', { value: r }, r)));
  const note = el('input', { placeholder: 'Optional note' });
  modal('Remove from inventory', el('div', {},
    el('div', { class: 'help', style: 'margin-bottom:8px' }, ids.length + ' sample(s) will no longer be available for analysis. The removal is logged with your name and the reason.'),
    field('Reason', reason), field('Note', note)), async () => {
    for (const id of ids) await api('POST', '/samples/' + id + '/remove', { reason: reason.value, note: note.value });
    toast(ids.length + ' sample(s) removed'); render();
  }, 'Remove');
}

async function openSampleDetails(id) {
  const s = await api('GET', '/samples/' + id);
  const loc = el('input', { value: s.location || '', placeholder: 'e.g. Freezer 2, shelf B' });
  const notes = el('textarea', { rows: '2', placeholder: 'Notes' }, s.notes || '');
  const rows = [['Unique ID', mono(s.code)], ['Sample ID Detailed', mono(s.idDetailed)], ['Sample ID Simplified', mono(s.idSimplified)],
    ['Label', (s.labelType === 'simplified' ? 'Simplified' : 'Detailed') + (s.idsLocked ? '  🔒 locked — on requisition ' + (s.reqNumber || '') : '')], ['Production run', mono(s.processingLot) ], ['Run date', s.runDate], ['Product', skuName(s.sku)],
    ['Process point', s.stageLabel], ['Type', s.type || '—'], ['Description', s.description || '—'], ['Container', s.container || '—'],
    ['Collected', sampleWhen(s.collectedAt)], ['Status', sampleStatusBadge(s)]];
  if (s.isRetention) rows.push(['Retention discard-by', s.discardBy ? s.discardBy + (s.expired ? '  (expired)' : '') : '—']);
  if (s.reqNumber) rows.push(['Requisition', s.reqAttachmentId
    ? el('a', { href: reqDocUrl(s.runId, s.reqAttachmentId, true) }, s.reqNumber + ' · ' + (s.reqLab || '')) : s.reqNumber]);
  if (s.status === 'removed') rows.push(['Removed', sampleWhen(s.removedAt) + ' by ' + (s.removedBy || '—') + ' — ' + (s.removedReason || '')]);
  const actions = el('div', { style: 'display:flex;gap:8px;flex-wrap:wrap;margin-top:12px' },
    s.status === 'available' ? el('button', { onclick: async () => { await api('POST', '/cart', { sampleIds: [s.id] }); toast('Added to the cart'); State.sampleSel?.delete(s.id); State.retentionSel?.delete(s.id); document.querySelector('.modal-bg')?.remove(); render(); } }, 'Add to cart') : null,
    ['available', 'in_cart'].includes(s.status) ? el('button', { class: 'danger', onclick: () => { document.querySelector('.modal-bg')?.remove(); removeSamplesModal([s.id]); } }, 'Remove from inventory') : null,
    s.status === 'removed' ? el('button', { class: 'secondary', onclick: async () => { await api('POST', '/samples/' + s.id + '/restore'); toast('Sample restored'); document.querySelector('.modal-bg')?.remove(); render(); } }, 'Undo removal') : null);
  const body = el('div', {}, table(['Field', 'Value'], rows),
    el('div', { class: 'form-row', style: 'margin-top:10px' }, field('Storage location', loc), field('Notes', notes)),
    actions, el('h4', { style: 'margin:14px 0 6px' }, 'History'),
    table(['When', 'Event', 'Detail', 'By'], s.events.map(e => [sampleWhen(e.at), e.type.replace('_', ' '), e.detail || '—', e.by || '—'])));
  modal('Sample — ' + s.code, body, async () => {
    await api('PUT', '/samples/' + s.id, { location: loc.value, notes: notes.value });
    toast('Sample updated'); render();
  }, 'Save changes', { wide: true });
}

function sampleFilterBar(f, samples, onChange, extra) {
  const uniq = k => [...new Set(samples.map(s => s[k]).filter(Boolean))].sort();
  const sel = (key, label, opts) => {
    const s = el('select', {}, el('option', { value: '' }, label), ...opts.map(o => el('option', { value: o[0] }, o[1])));
    s.value = f[key] || ''; s.addEventListener('change', () => { f[key] = s.value; onChange(); }); return s;
  };
  const q = el('input', { placeholder: 'Search sample ID, run, location…', value: f.q || '' });
  q.addEventListener('input', () => { f.q = q.value; onChange(); });
  return el('div', { class: 'toolbar' }, q,
    sel('run', 'All runs', uniq('processingLot').map(x => [x, x])),
    ...(extra || []).map(k => k === 'status' ? sel('status', 'All statuses', Object.entries(SAMPLE_STATUS_LABELS))
      : k === 'stage' ? sel('stage', 'All process points', [...new Set(samples.map(s => s.stageLabel))].sort().map(x => [x, x]))
      : sel('desc', 'All descriptions', uniq('description').map(x => [x, x]))));
}
function sampleMatches(s, f) {
  const q = (f.q || '').toLowerCase();
  return (!f.run || s.processingLot === f.run) && (!f.status || s.status === f.status) && (!f.stage || s.stageLabel === f.stage) &&
    (!f.desc || s.description === f.desc) &&
    (!q || (s.idDetailed + ' ' + s.idSimplified + ' ' + s.code + ' ' + s.processingLot + ' ' + (s.location || '') + ' ' + (s.description || '')).toLowerCase().includes(q));
}

// ---- shared by the Analysis catalogue and the Retention inventory: group-by boxes, sort by collected date, one grouped/sortable table ----
const SAMPLE_DIMS = {
  stage: ['Process point', s => s.stageLabel], type: ['Type', s => s.type || '—'], description: ['Description', s => s.description || '—'],
  container: ['Container', s => s.container || '—'], run: ['Run', s => s.processingLot],
};
// each view remembers its own grouping + sort while you move around the app
const sampleViewState = k => { State.sampleViews = State.sampleViews || {}; return State.sampleViews[k] = State.sampleViews[k] || { groupBy: [], sort: '' }; };
// "Group by" (any combination, in the order given) and "Sort" (collected date) in one tidy bar
function sampleViewBar(vs, dimKeys, onChange) {
  const opts = dimKeys.map(k => {
    const cb = el('input', { type: 'checkbox' }); cb.checked = vs.groupBy.includes(k);
    const lab = el('label', { class: 'vb-opt' + (cb.checked ? ' on' : '') }, cb, SAMPLE_DIMS[k][0]);
    cb.addEventListener('change', () => { lab.classList.toggle('on', cb.checked); vs.groupBy = dimKeys.filter(x => (x === k ? cb.checked : vs.groupBy.includes(x))); onChange(); });
    return lab;
  });
  vs.sortSel = selectFrom('', [['', 'Default order'], ['desc', 'Collected — newest first'], ['asc', 'Collected — oldest first']], () => { vs.sort = vs.sortSel.value; onChange(); });
  vs.sortSel.value = vs.sort;
  return el('div', { class: 'viewbar' }, el('span', { class: 'vb-label' }, 'Group by'), ...opts,
    el('span', { class: 'vb-sep' }), el('span', { class: 'vb-label' }, 'Sort'), vs.sortSel);
}
// cols: [{ label, cell: sample => <td>, dim?: a SAMPLE_DIMS key (column hidden while grouped by it), collected?: true (sortable header) }]
function sampleTable(rows, cols, vs, selected, bulk, redraw, emptyText) {
  const selectable = s => ['available', 'in_cart'].includes(s.status);
  const visible = cols.filter(c => !c.dim || !vs.groupBy.includes(c.dim));
  const cmp = (a, b) => String(a.collectedAt || '').localeCompare(String(b.collectedAt || '')) || a.code.localeCompare(b.code);
  if (vs.sort) rows = rows.slice().sort((a, b) => (vs.sort === 'asc' ? cmp(a, b) : cmp(b, a)));
  const all = el('input', { type: 'checkbox', onchange: () => { rows.filter(selectable).forEach(s => all.checked ? selected.add(s.id) : selected.delete(s.id)); redraw(); bulk.update(); } });
  all.checked = rows.some(selectable) && rows.filter(selectable).every(s => selected.has(s.id));
  const tb = el('tbody', {});
  if (!rows.length) tb.append(el('tr', {}, el('td', { colspan: visible.length + 1, class: 'empty' }, emptyText)));
  const sampleRow = s => {
    const cb = selectable(s) ? el('input', { type: 'checkbox', onclick: e => e.stopPropagation(), onchange: () => { cb.checked ? selected.add(s.id) : selected.delete(s.id); bulk.update(); } }) : null;
    if (cb) cb.checked = selected.has(s.id);
    return el('tr', { class: 'clickable', onclick: () => openSampleDetails(s.id) }, el('td', { class: 'checkcol' }, cb), ...visible.map(c => c.cell(s)));
  };
  if (!vs.groupBy.length) rows.forEach(s => tb.append(sampleRow(s)));
  else {
    const groups = new Map();
    rows.forEach(s => { const vals = vs.groupBy.map(k => SAMPLE_DIMS[k][1](s)); const key = vals.join('\u0000'); if (!groups.has(key)) groups.set(key, { vals, list: [] }); groups.get(key).list.push(s); });
    [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0])).forEach(([, g]) => {
      const sel = g.list.filter(selectable);
      const gcb = sel.length ? el('input', { type: 'checkbox', title: 'Select every sample in this group', onchange: () => { sel.forEach(s => gcb.checked ? selected.add(s.id) : selected.delete(s.id)); redraw(); bulk.update(); } }) : null;
      if (gcb) gcb.checked = sel.every(s => selected.has(s.id));
      tb.append(el('tr', { class: 'group-row' }, el('td', { class: 'checkcol' }, gcb),
        el('td', { colspan: visible.length }, el('b', {}, g.vals.join(' · ')), el('span', { class: 'muted' }, '  ·  ' + g.list.length + ' sample' + (g.list.length === 1 ? '' : 's')
          + (sel.length !== g.list.length ? '  ·  ' + sel.length + ' in inventory' : '')))));
      g.list.forEach(s => tb.append(sampleRow(s)));
    });
  }
  const th = c => c.collected
    ? el('th', { class: 'sortable', title: 'Click to sort by collected date', onclick: () => {
      vs.sort = vs.sort === '' ? 'desc' : vs.sort === 'desc' ? 'asc' : ''; if (vs.sortSel) vs.sortSel.value = vs.sort; redraw();
    } }, c.label + (vs.sort === 'desc' ? ' ▼' : vs.sort === 'asc' ? ' ▲' : ''))
    : el('th', {}, c.label);
  return el('div', { class: 'tablewrap' }, el('table', {}, el('thead', {}, el('tr', {}, el('th', { class: 'checkcol' }, all), ...visible.map(th))), tb));
}
const sdim = k => c => el('td', {}, SAMPLE_DIMS[k][1](c));

// Analysis catalogue: every sample logged in a finalized run's Sample Point boxes EXCEPT Retention samples (those live in the Retention
// inventory). Optional grouping by process point / type / description / container; a grouped column is dropped from the rows.
async function drawSampleCatalogue(host) {
  const { samples } = await api('GET', '/samples?retention=0');
  const f = State.sampleFilters = State.sampleFilters || {};
  const selected = State.sampleSel = State.sampleSel || new Set();
  const vs = sampleViewState('catalogue');
  const byId = id => samples.find(s => s.id === id);
  const bulk = sampleBulkBar(selected, byId);
  const tableHost = el('div', {}), count = el('span', { class: 'muted' });
  const cols = [
    { label: 'Unique ID', cell: s => el('td', { class: 'mono' }, s.code) }, { label: 'ID Detailed', cell: s => el('td', { class: 'mono' }, el('b', {}, s.idDetailed)) },
    { label: 'ID Simplified', cell: s => el('td', { class: 'mono' }, el('b', {}, s.idSimplified)) },
    { label: 'Run date', cell: s => el('td', {}, s.runDate) },
    { label: 'Process point', dim: 'stage', cell: sdim('stage') }, { label: 'Type', dim: 'type', cell: sdim('type') },
    { label: 'Description', dim: 'description', cell: sdim('description') }, { label: 'Container', dim: 'container', cell: sdim('container') },
    { label: 'Collected', collected: true, cell: s => el('td', {}, sampleWhen(s.collectedAt)) }, { label: 'Location', cell: s => el('td', {}, s.location || '—') },
    { label: 'Status', cell: s => el('td', {}, sampleStatusBadge(s)) }, { label: 'Requisition', cell: s => el('td', {}, s.reqNumber || '—') }];
  function draw() {
    const rows = samples.filter(s => sampleMatches(s, f));
    count.textContent = rows.length + ' of ' + samples.length + ' samples';
    tableHost.innerHTML = '';
    tableHost.append(sampleTable(rows, cols, vs, selected, bulk, draw, 'No samples match.'));
  }
  host.append(sampleFilterBar(f, samples, draw, ['status', 'stage', 'desc']), sampleViewBar(vs, ['stage', 'type', 'description', 'container'], draw),
    el('div', { style: 'margin:6px 0' }, count), bulk.el, tableHost,
    el('div', { class: 'help', style: 'margin-top:8px' }, 'Every tube / bag logged in a finalized run’s Sample Point boxes for analysis is catalogued here with its own ID — Retention samples are kept in the Retention inventory instead. Click a row for its full history; tick samples to add them to the lab cart or set their storage location. Group the rows with the “Group by” boxes, and sort by collected date from the bar or by clicking the Collected heading.'));
  draw(); bulk.update();
}

async function drawRetentionInventory(host) {
  const { samples, retentionMonths } = await api('GET', '/samples?retention=1');
  const f = State.retentionFilters = State.retentionFilters || {};
  const selected = State.retentionSel = State.retentionSel || new Set();
  const vs = sampleViewState('retention');
  const inInv = samples.filter(s => ['available', 'in_cart'].includes(s.status));
  const soon = inInv.filter(s => s.discardBy && !s.expired && s.discardBy <= new Date(Date.now() + 30 * 864e5).toISOString().slice(0, 10));
  const showAll = el('input', { type: 'checkbox' }); showAll.checked = !!f.showRemoved;
  showAll.addEventListener('change', () => { f.showRemoved = showAll.checked; draw(); });
  const byId = id => samples.find(s => s.id === id);
  const bulk = sampleBulkBar(selected, byId);
  const tableHost = el('div', {}), count = el('span', { class: 'muted' });
  const cols = [
    { label: 'Unique ID', cell: s => el('td', { class: 'mono' }, s.code) }, { label: 'ID Detailed', cell: s => el('td', { class: 'mono' }, el('b', {}, s.idDetailed)) },
    { label: 'ID Simplified', cell: s => el('td', { class: 'mono' }, el('b', {}, s.idSimplified)) },
    { label: 'Run', dim: 'run', cell: sdim('run') },
    { label: 'Process point', dim: 'stage', cell: sdim('stage') }, { label: 'Type', dim: 'type', cell: sdim('type') },
    { label: 'Collected', collected: true, cell: s => el('td', {}, sampleWhen(s.collectedAt)) },
    { label: 'Discard by', cell: s => el('td', {}, s.expired ? el('span', { class: 'var-flag' }, (s.discardBy || '') + ' · expired') : (s.discardBy || '—')) },
    { label: 'Container', dim: 'container', cell: sdim('container') }, { label: 'Location', cell: s => el('td', {}, s.location || '—') },
    { label: 'Status', cell: s => el('td', {}, sampleStatusBadge(s)) },
    { label: 'Removed / sent', cell: s => el('td', {}, s.status === 'removed' ? (s.removedReason || '') : (s.status === 'submitted' ? (s.reqNumber || '') : '')) }];
  function draw() {
    const rows = samples.filter(s => (f.showRemoved || ['available', 'in_cart'].includes(s.status)) && sampleMatches(s, f));
    count.textContent = rows.length + ' sample' + (rows.length === 1 ? '' : 's') + ' shown';
    tableHost.innerHTML = '';
    tableHost.append(sampleTable(rows, cols, vs, selected, bulk, draw, 'No retention samples.'));
  }
  host.append(el('div', { class: 'tiles' },
    tile('In inventory', inInv.length, 'retention samples', true), tile('Expiring in 30 days', soon.length, 'samples'),
    tile('Past discard-by', inInv.filter(s => s.expired).length, 'samples'),
    tile('No longer available', samples.length - inInv.length, 'removed or sent to a lab')),
    sampleFilterBar(f, samples, draw, ['status']),
    sampleViewBar(vs, ['stage', 'type', 'container', 'run'], draw),
    el('div', { style: 'display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin:6px 0' }, count,
      el('label', { style: 'display:flex;gap:6px;align-items:center;margin:0;font-size:13px;font-weight:normal' }, showAll, 'Show removed / sent samples')),
    bulk.el, tableHost,
    el('div', { class: 'help', style: 'margin-top:8px' }, 'A Retention sample leaves this inventory when it is removed (with a reason), or when it is put on a lab requisition. Discard-by = collection date + ' + retentionMonths + ' months (Admin → Settings: “Retention sample shelf life”).'));
  draw(); bulk.update();
}

// Installs the token-ready KelpWorks copy of a lab's requisition form (docs/requisition-templates) -- one click, admin only.
async function useReadyTemplate(lab, after) {
  if (!confirm((lab.hasTemplate ? 'Replace “' + lab.templateName + '” with the ready-made KelpWorks form for ' + lab.name + '?\n\nIts placeholders are filled in automatically. Your original file stays wherever you saved it.'
    : 'Use the ready-made KelpWorks form for ' + lab.name + '?'))) return;
  try {
    const r = await api('POST', '/labs/' + lab.id + '/template/builtin', {});
    toast('Template installed: ' + r.lab.templateName + (r.report && r.report.warnings && r.report.warnings.length ? ' — ' + r.report.warnings[0] : ''));
    await after();
  } catch (e) { toast(e.message, true); }
}
async function drawSampleCart(host) {
  const { items, labs } = await api('GET', '/cart');
  const contactDefaults = await api('GET', '/requisition-contact');
  if (!items.length) { host.append(el('div', { class: 'empty card' }, 'The cart is empty. Add samples from the Analysis catalogue or the Retention inventory, then choose a lab and analyses for each.')); return; }
  if (!labs.length) host.append(el('div', { class: 'card', style: 'margin-bottom:10px' }, 'No active labs yet — an administrator adds labs and their analyses under Admin → Labs & analyses.'));
  const selected = new Set();
  const redraw = async () => { host.innerHTML = ''; await drawSampleCart(host); };
  async function assign(sampleId, labId, analysisIds) {
    try { await api('PUT', '/cart/' + sampleId, { labId: labId || null, analysisIds }); }
    catch (e) { toast(e.message, true); }
  }
  // bulk assign
  const bulkLab = el('select', {}, el('option', { value: '' }, 'Choose lab…'), ...labs.map(l => el('option', { value: l.id }, l.name)));
  const bulkChecks = el('span', { style: 'display:flex;gap:10px;flex-wrap:wrap' });
  const bulkSet = new Set();
  bulkLab.addEventListener('change', () => {
    bulkSet.clear(); bulkChecks.innerHTML = '';
    const lab = labs.find(l => String(l.id) === bulkLab.value);
    if (lab) {
      const offered = lab.analyses.filter(a => a.active), boxes = [];
      // "Select all" ticks every analysis this lab offers (and unticks itself if one is cleared)
      const allBox = el('input', { type: 'checkbox' });
      allBox.addEventListener('change', () => { const on = allBox.checked; boxes.forEach(([cb, a]) => { cb.checked = on; on ? bulkSet.add(a.id) : bulkSet.delete(a.id); }); allBox.checked = on; });
      if (offered.length > 1) bulkChecks.append(el('label', { style: 'display:flex;gap:4px;align-items:center;font-size:13px;font-weight:700;padding-right:10px;border-right:1px solid var(--line)' }, allBox, 'Select all'));
      offered.forEach(a => {
        const cb = el('input', { type: 'checkbox', onchange: () => { cb.checked ? bulkSet.add(a.id) : bulkSet.delete(a.id); allBox.checked = boxes.every(([c]) => c.checked); } });
        boxes.push([cb, a]);
        bulkChecks.append(el('label', { style: 'display:flex;gap:4px;align-items:center;font-size:13px' }, cb, a.name));
      });
    }
  });
  const bulkBtn = el('button', { onclick: async () => {
    if (!selected.size) return toast('Tick the samples to assign.', true);
    if (!bulkLab.value) return toast('Choose a lab.', true);
    try { await api('POST', '/cart/assign', { sampleIds: [...selected], labId: +bulkLab.value, analysisIds: [...bulkSet] }); await redraw(); }
    catch (e) { toast(e.message, true); }
  } }, 'Apply to selected');
  host.append(el('div', { class: 'card', style: 'margin-bottom:10px' }, el('b', {}, 'Assign several samples at once'),
    el('div', { style: 'display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-top:8px' }, bulkLab, bulkChecks, bulkBtn)));
  // one row per sample
  const isAdmin = State.user.role === 'admin';
  const tb = el('tbody', {});
  const allCb = el('input', { type: 'checkbox', onchange: () => { tb.querySelectorAll('input.cartsel').forEach(c => { c.checked = allCb.checked; c.dispatchEvent(new Event('change')); }); } });
  items.forEach(it => {
    const sel = el('input', { type: 'checkbox', class: 'cartsel', onchange: () => { sel.checked ? selected.add(it.id) : selected.delete(it.id); } });
    const labSel = el('select', { style: 'min-width:190px' }, el('option', { value: '' }, 'Choose lab…'), ...labs.map(l => el('option', { value: l.id }, l.name)));
    labSel.value = it.cartLabId || '';
    const chosen = new Set(it.cartAnalyses);
    const checks = el('span', { style: 'display:flex;gap:12px;flex-wrap:wrap' });
    const statusCell = el('td', {});
    const setStatus = () => {
      statusCell.innerHTML = '';
      statusCell.append(!it.cartLabId ? badge('sold', 'Needs a lab') : !it.cartAnalyses.length ? badge('pending_release', 'Needs analyses') : badge('on_hand', 'Ready'));
    };
    const lab = labs.find(l => l.id === it.cartLabId);
    const offered = lab ? lab.analyses.filter(a => a.active) : [];
    if (lab && offered.length) offered.forEach(a => {
      const cb = el('input', { type: 'checkbox' }); cb.checked = chosen.has(a.id);
      cb.addEventListener('change', () => {
        cb.checked ? chosen.add(a.id) : chosen.delete(a.id);
        it.cartAnalyses = [...chosen]; setStatus();
        assign(it.id, lab.id, it.cartAnalyses).then(updateSummary);
      });
      checks.append(el('label', { style: 'display:flex;gap:4px;align-items:center;font-size:13px' }, cb, a.name));
    });
    else if (lab) checks.append(el('span', { class: 'muted' }, 'No analyses are set up for ' + lab.name + ' yet. ',
      isAdmin ? el('a', { href: '#', onclick: e => { e.preventDefault(); manageLabAnalyses(lab.id); } }, 'Set them up') : 'Ask an administrator to add them (Admin → Labs & analyses).'));
    else checks.append(el('span', { class: 'muted' }, 'Pick a lab to see its analyses'));
    labSel.addEventListener('change', async () => { await assign(it.id, labSel.value ? +labSel.value : null, []); await redraw(); });
    setStatus();
    tb.append(el('tr', {}, el('td', { class: 'checkcol' }, sel), el('td', { class: 'mono' }, it.idSimplified),
      el('td', {}, el('div', {}, it.description || '—'), el('div', { class: 'help' }, it.stageLabel + ' · ' + (it.container || ''))),
      el('td', {}, labSel), el('td', { style: 'white-space:normal' }, checks), statusCell,
      el('td', {}, el('button', { class: 'secondary', title: 'Take out of the cart', onclick: async () => { await api('DELETE', '/cart/' + it.id); render(); } }, '✕'))));
  });
  host.append(el('div', { class: 'tablewrap' }, el('table', {}, el('thead', {}, el('tr', {}, el('th', { class: 'checkcol' }, allCb),
    ...['Sample ID', 'Sample', 'Lab', 'Analyses', 'Status', ''].map(h => el('th', {}, h)))), tb)));

  // requisitions: one per lab and production run, each filled from THAT lab's own template, ready as soon as its samples are assigned
  const notes = el('input', { placeholder: 'Notes for the lab (optional)', style: 'max-width:420px' });
  // PO / reference # per requisition (one per lab + run): starts as the production run number; edit it on the card if the lab needs another
  const poByKey = {};
  const poPayload = () => Object.assign({}, poByKey);
  const readyHost = el('div', {});
  // the customer phone / emails printed on the forms: the admin defaults, editable for this requisition only
  const cPhone = el('input', { value: contactDefaults.phone, placeholder: 'Phone' });
  const cEmails = contactDefaults.emails.map((e, i) => el('input', { value: e, placeholder: 'Email ' + (i + 1) }));
  const contactBox = el('details', { style: 'margin:8px 0' }, el('summary', {}, 'Our contact details on the requisition (phone and up to 5 emails)'),
    el('div', { style: 'padding:8px 0' }, el('div', { class: 'form-row' }, field('Phone', cPhone), field('Email 1', cEmails[0])),
      el('div', { class: 'form-row' }, field('Email 2', cEmails[1]), field('Email 3', cEmails[2])), el('div', { class: 'form-row' }, field('Email 4', cEmails[3]), field('Email 5', cEmails[4])),
      el('div', { class: 'help' }, 'Starts from the defaults set by an administrator (Admin → Requisition contact details); changes here apply to the requisitions you create now.')));
  const contactPayload = () => ({ phone: cPhone.value, emails: cEmails.map(i => i.value) });
  async function createReqs(filter, btn) {
    if (btn) btn.disabled = true;
    try {
      const r = await api('POST', '/cart/requisitions', Object.assign({ notes: notes.value, poNumbers: poPayload(), contact: contactPayload() }, filter));
      // one requisition: hand over the filled form straight away
      if (r.requisitions.length === 1 && r.requisitions[0].attachmentId) {
        const q = r.requisitions[0];
        const l = el('a', { href: reqDocUrl(q.runId, q.attachmentId, true), download: q.filename || '' }); document.body.append(l); l.click(); l.remove();
      }
      const body = el('div', {}, el('div', { class: 'help', style: 'margin-bottom:8px' }, 'The requisition forms are filled in and ready to download below. They are also saved as documents on their production runs and listed under Samples → Requisitions; the samples have left the cart.'),
        table(['Requisition', 'Run', 'Lab', 'Samples', 'Documents'], r.requisitions.map(q => [mono(q.reqNumber), mono(q.processingLot), q.labName, q.samples.length,
          reqDocLinks(q)])));
      modal('Requisitions created', body, async () => {}, 'Close', { noCancel: true, wide: true });
      await redraw(); render();
    } catch (e) { toast(e.message, true); if (btn) btn.disabled = false; }
  }
  // the filled form + sample list exactly as they would be created, without creating anything
  async function previewRequisition(filter) {
    try {
      const r = await api('POST', '/cart/requisitions/preview', Object.assign({ notes: notes.value, poNumbers: poPayload(), contact: contactPayload() }, filter));
      const pv = r.previews[0];
      if (!pv) return toast('Nothing is ready to preview.', true);
      const dup = pv.conflicts || [];
      const parts = [{ label: 'Requisition form', html: pv.form }];
      if (pv.sheet) parts.push({ label: 'Sample list (Excel)', html: pv.sheet });
      modal('Preview requisition — ' + pv.lab + ' · ' + pv.lot, el('div', {},
        el('div', { class: 'summary-line' }, sl('Lab', pv.lab), sl('Run', pv.lot), sl('Samples', String(pv.nSamples)), sl('Form', pv.templateName || 'built-in layout')),
        el('div', { class: 'help' }, 'The requisition’s Sample ID is each sample’s ID Simplified. Samples with the same ID (and the same process point and tests) are one line on the sample list, with a Container Qty and total sample volume. '
          + pv.nSamples + ' sample' + (pv.nSamples === 1 ? '' : 's') + ' → ' + pv.lines + ' line' + (pv.lines === 1 ? '' : 's') + '.'),
        dup.length ? el('div', { class: 'help', style: 'color:var(--danger);font-weight:600' }, '⚠ These IDs are shared by samples from different process points or with different tests (' + dup.join(', ')
          + '). Number the labels (Print sample labels → Number) so each has its own ID.') : null,
        previewBody(parts, 'A simplified preview — the downloaded Word form keeps the lab’s own layout and logo. The requisition number is assigned when it is created. Nothing has been created or removed from the cart yet.')),
      () => createReqs(filter, null), '⬇ Create requisition & download', { wide: true });
    } catch (e) { toast(e.message, true); }
  }
  function updateSummary() {
    const groups = {};
    let notReady = 0;
    items.forEach(it => {
      if (!it.cartLabId || !it.cartAnalyses.length) { notReady++; return; }
      const k = it.runId + '|' + it.cartLabId;
      const g = groups[k] = groups[k] || { lot: it.processingLot, runId: it.runId, lab: labs.find(l => l.id === it.cartLabId), n: 0, names: new Set() };
      g.n++;
      it.cartAnalyses.forEach(id => { const a = g.lab.analyses.find(x => x.id === id); if (a) g.names.add(a.name); });
    });
    readyHost.innerHTML = '';
    const list = Object.values(groups);
    readyHost.append(el('h3', { style: 'margin:16px 0 6px' }, 'Requisitions ready (' + list.length + ')'));
    if (!list.length) readyHost.append(el('div', { class: 'help' }, 'Once a sample has a lab and at least one analysis, its requisition appears here — one per lab and production run, filled from that lab’s own template — ready to download.'));
    list.forEach(g => {
      const key = g.runId + ':' + g.lab.id;
      if (!(key in poByKey)) poByKey[key] = g.lot;              // default PO = the production run number
      const po = el('input', { value: poByKey[key], placeholder: 'PO / reference #', style: 'max-width:240px', title: 'Defaults to the production run number — change it if the lab needs another reference.' });
      po.addEventListener('input', () => { poByKey[key] = po.value; });
      const btn = el('button', { onclick: () => createReqs({ labId: g.lab.id, runId: g.runId }, btn) }, '⬇ Create requisition & download');
      readyHost.append(el('div', { class: 'card', style: 'margin-bottom:8px;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:center' },
        el('div', {}, el('b', {}, g.lab.name + ' · ' + g.lot), el('span', { class: 'muted' }, '  ' + g.n + ' sample' + (g.n === 1 ? '' : 's')),
          el('div', { class: 'help' }, 'Analyses: ' + [...g.names].join(', ')),
          el('div', { style: 'display:flex;gap:8px;align-items:center;margin:6px 0' }, el('label', { style: 'margin:0;font-size:12px' }, 'PO / reference #'), po),
          el('div', { class: 'help' }, g.lab.hasTemplate ? '📄 Form: ' + g.lab.templateName : 'No form uploaded for this lab — a plain built-in layout will be used.',
            g.lab.sampleSheet ? ' · plus a sample spreadsheet' : ''),
          // a form with no {{placeholders}} comes back exactly as it went in -- say so, and offer the fix
          (!g.lab.hasTemplate || g.lab.templateTokens === 0) ? el('div', { class: 'help', style: 'color:var(--danger)' },
            g.lab.hasTemplate ? '⚠ This form has no {{placeholders}}, so nothing can be filled in automatically. ' : '⚠ Uploading the lab’s own form lets it fill in automatically. ',
            g.lab.readyTemplate ? (isAdmin ? el('a', { href: '#', onclick: e => { e.preventDefault(); useReadyTemplate(g.lab, redraw); } }, 'Use the ready-made KelpWorks ' + g.lab.name + ' form')
              : 'Ask an administrator to install the ready-made form (Admin → Labs & analyses → Template).')
              : (isAdmin ? 'Upload a form with placeholders under Admin → Labs & analyses → Template.' : 'Ask an administrator to upload one.')) : null),
        el('div', { style: 'display:flex;gap:8px;flex-wrap:wrap' }, el('button', { class: 'secondary', onclick: () => previewRequisition({ labId: g.lab.id, runId: g.runId }) }, '👁 Preview'), btn)));
    });
    if (list.length > 1) {
      const all = el('button', { class: 'secondary', onclick: () => createReqs({}, all) }, 'Create all ' + list.length + ' requisitions');
      readyHost.append(el('div', {}, all));
    }
    if (notReady) readyHost.append(el('div', { class: 'help', style: 'margin-top:6px' }, notReady + ' sample(s) still need a lab and at least one analysis and will stay in the cart.'));
  }
  host.append(el('div', { style: 'display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:14px' }, notes,
    el('span', { class: 'help' }, 'Notes go on every requisition you create below. The PO / reference # is set on each requisition — it starts as the production run number.')), contactBox, readyHost,
    el('div', { class: 'help', style: 'margin-top:8px' }, 'A different lab means a different form and different analyses. Each requisition is saved as a document on its production run (Production → run → Documents). Samples leave the cart — and a Retention sample leaves the retention inventory — once its requisition is created.'));
  updateSummary();
}

async function drawRequisitions(host) {
  const { requisitions } = await api('GET', '/requisitions');
  host.append(table(['Requisition', 'Created', 'Run', 'Lab', 'PO #', 'Samples', 'By', 'Documents'],
    requisitions.map(q => [mono(q.reqNumber), fmtWhen(q.createdAt), mono(q.processingLot), q.labName, q.poNumber || '—', q.samples.length, q.createdBy || '—',
      reqDocLinks(q)]),
    [false, false, false, false, false, true, false, false],
    ri => {
      const q = requisitions[ri];
      modal('Requisition ' + q.reqNumber, el('div', {},
        el('div', { class: 'summary-line' }, sl('Lab', q.labName), sl('Run', q.processingLot), sl('PO #', q.poNumber || '—'), sl('Created', fmtWhen(q.createdAt) + ' by ' + (q.createdBy || '—'))),
        q.notes ? el('div', { class: 'help' }, 'Notes: ' + q.notes) : null,
        table(['Sample', 'Process point', 'Description', 'Analyses'], q.samples.map(s => [mono(s.idSimplified), s.stageLabel, s.description || '—', s.analyses.join(', ')]))),
        async () => {}, 'Close', { noCancel: true, wide: true });
    }));
  if (!requisitions.length) host.append(el('div', { class: 'help', style: 'margin-top:8px' }, 'Requisitions appear here once created from the cart.'));
}

/* ---- Admin: customer contact details printed on lab requisitions ---- */
async function drawAdminRequisitionContact(v) {
  const c = await api('GET', '/requisition-contact');
  const phone = el('input', { value: c.phone, placeholder: 'e.g. 204-963-5023' });
  const emails = c.emails.map((e, i) => el('input', { value: e, placeholder: 'Email ' + (i + 1) + (i ? ' (optional)' : '') }));
  const msg = el('span', { class: 'help' });
  const summaryNote = el('span', { class: 'muted', style: 'font-weight:normal;margin-left:8px' });
  const refresh = () => { const n = emails.filter(i => i.value.trim()).length; summaryNote.textContent = (phone.value.trim() || 'no phone') + ' · ' + n + ' email' + (n === 1 ? '' : 's'); };
  refresh();
  const save = el('button', { onclick: async () => {
    msg.style.color = ''; msg.textContent = '';
    try { await api('PUT', '/requisition-contact', { phone: phone.value, emails: emails.map(i => i.value) }); msg.textContent = 'Saved.'; refresh(); toast('Requisition contact details saved'); }
    catch (e) { msg.style.color = 'var(--danger)'; msg.textContent = e.message; }
  } }, 'Save');
  // collapsed by default and tucked under Labs & analyses: our phone + up to 5 emails printed on lab requisition forms
  v.append(el('details', { class: 'accordion', style: 'margin-top:12px' },
    el('summary', {}, 'Requisition contact details', summaryNote),
    el('div', { class: 'accordion-body' },
      el('div', { class: 'help', style: 'margin:0 0 8px' }, 'Our phone number and up to five emails, printed where a lab form has {{customer_phone}} and {{customer_email_1}} … {{customer_email_5}} (the FoodAssure form does). Blank emails are left off; a requisition can override them in the Samples cart.'),
      el('div', { class: 'form-row-3' }, field('Phone', phone), field('Email 1', emails[0]), field('Email 2', emails[1])),
      el('div', { class: 'form-row-3' }, field('Email 3', emails[2]), field('Email 4', emails[3]), field('Email 5', emails[4])),
      el('div', { style: 'margin-top:8px;display:flex;gap:10px;align-items:center' }, save, msg))));
}

/* ---- Admin: Certificate of Analysis specifications ---- */
async function drawAdminCoaSpecs(v) {
  const { specs, applicationRate, applicationPeriods } = await api('GET', '/coa-specs');
  v.append(el('div', { class: 'page-head', style: 'margin-top:28px' }, el('h2', {}, 'Certificate of Analysis specifications')));
  v.append(el('div', { class: 'help', style: 'margin:-6px 0 8px' }, 'Limits each run is judged against on its Certificate of Analysis. “Required” results must be on file before Quality can release a run. '
    + 'Heavy-metal limits are loadings in kg metal per ha; each lab result is converted to kg/ha at the product application rate (' + (applicationRate ? applicationRate + ' kg/ha' : 'not set') + ') × application periods ('
    + applicationPeriods + '), both set on the Calculations page. Changing a limit re-judges every certificate as it is next generated.'));
  const names = { physical: 'Physical & chemical', metals: 'Heavy metals', microbial: 'Microbiological' };
  const rows = [];
  for (const g of ['physical', 'metals', 'microbial']) {
    rows.push({ group: el('b', {}, names[g]) });
    specs.filter(x => x.group === g).forEach(x => rows.push([x.name, x.specText, x.required ? badge('on_hand', 'Required') : badge('sold', 'Optional'), x.method || '—', x.active ? badge('on_hand', 'On certificate') : badge('hold', 'Lab results only'),
      rowActions([['Edit', () => editCoaSpec(x)]])]));
  }
  v.append(table(['Test', 'Specification', 'Required for release', 'Default method', 'Status', ''], rows));
}
function editCoaSpec(x) {
  const groupName = { physical: 'Physical & chemical', metals: 'Heavy metals', microbial: 'Microbiological' }[x.group] || x.group;
  const absent = x.basis === 'absent';
  const unit = x.unit ? ' (' + x.unit + ')' : '';
  const mn = el('input', { value: x.minVal ?? '', placeholder: 'No minimum', inputmode: 'decimal' });
  const mx = el('input', { value: x.maxVal ?? '', placeholder: 'No maximum', inputmode: 'decimal' });
  const meth = el('input', { value: x.method || '', placeholder: 'e.g. MFHPB-18' });
  const excl = el('input', { type: 'checkbox' }); excl.checked = x.maxExclusive;
  const req = el('input', { type: 'checkbox' }); req.checked = x.required;
  const act = el('input', { type: 'checkbox' }); act.checked = x.active;
  const option = (cb, title, hint) => {
    const row = el('label', { class: 'perm-item' + (cb.checked ? ' on' : '') }, cb, el('span', { class: 'perm-text' }, el('b', {}, title), el('small', {}, hint)));
    cb.addEventListener('change', () => row.classList.toggle('on', cb.checked));
    return row;
  };
  const exclRow = el('div', { class: 'perm-list', style: 'margin-top:8px' },
    option(excl, 'Strict maximum', 'The result must be below the maximum (“< limit”) rather than at or below it (“≤ limit”).'));
  const num = i => (i.value.trim() === '' ? null : Number(i.value));
  // the same wording the certificate prints (coa_spec_text on the server)
  const f = v => String(Number(v.toPrecision(6)));
  const preview = el('b', {});
  function sync() {
    const a = num(mn), b = num(mx), u = x.unit ? ' ' + x.unit : '';
    let t = '—';
    if (absent) t = x.specText;
    else if (x.code === 'ph' && a != null && b != null) t = a.toFixed(1) + ' - ' + b.toFixed(1);
    else if (a != null && b != null) t = f(a) + ' - ' + f(b) + u;
    else if (a != null) t = 'min ' + f(a) + u;
    else if (b != null) t = (excl.checked ? '< ' : '≤ ') + f(b) + u;
    preview.textContent = t;
    exclRow.classList.toggle('hidden', absent || b == null || a != null);
  }
  [mn, mx].forEach(i => i.addEventListener('input', sync)); excl.addEventListener('change', sync);
  sync();
  const section = (title, ...kids) => el('div', { class: 'perm-box' }, el('div', { class: 'perm-title' }, title), ...kids);
  modal('Edit specification — ' + x.name, el('div', {},
    el('div', { class: 'summary-line' }, sl('Test', x.name), sl('Group', groupName), sl('Stated in', x.unit || 'the reported unit')),
    section('Limit',
      absent ? el('div', { class: 'help' }, 'Qualitative test: the result must be Negative. There is no numeric limit to edit.')
        : el('div', { class: 'form-row' }, field('Minimum' + unit, mn), field('Maximum' + unit, mx)),
      exclRow,
      el('div', { class: 'help', style: 'margin-top:8px' }, 'Printed on the certificate as: ', preview)),
    section('Release and certificate', el('div', { class: 'perm-list' },
      option(req, 'Required before release', 'Quality cannot release a run until a result for this test is on file.'),
      option(act, 'Listed on the certificate', 'Untick to keep this test in Lab results but leave it off the Certificate of Analysis.'))),
    section('Method', field('Default method', meth),
      el('div', { class: 'help' }, 'Filled in when a result for this test is entered; a lab report’s own method replaces it.'))),
    async () => {
      await api('PUT', '/coa-specs/' + x.code, { minVal: absent ? x.minVal : mn.value, maxVal: absent ? x.maxVal : mx.value,
        maxExclusive: excl.checked, required: req.checked, active: act.checked, method: meth.value });
      toast('Specification saved'); render();
    }, 'Save specification');
}

/* ---- Admin: labs, their analyses and requisition templates ---- */
// A lab's offered analyses as small wrapped chips under a count (instead of one long comma-separated line); long lists show the first few and "+N more".
function analysisChips(l) {
  const list = l.analyses.filter(a => a.active);
  if (!list.length) return el('span', { class: 'muted' }, 'none yet');
  const LIMIT = 6, wrap = el('div', { class: 'chips' });
  const draw = all => {
    wrap.innerHTML = '';
    list.slice(0, all ? list.length : LIMIT).forEach(a => wrap.append(el('span', { class: 'chip', title: a.method ? 'Method: ' + a.method : '' }, a.name)));
    if (list.length > LIMIT) wrap.append(el('span', { class: 'chip chip-more', role: 'button', tabindex: '0', onclick: e => { e.stopPropagation(); draw(!all); } },
      all ? 'Show fewer' : '+' + (list.length - LIMIT) + ' more'));
  };
  draw(false);
  return el('div', { class: 'chips-cell' }, el('div', { class: 'help', style: 'margin:0 0 3px' }, list.length + ' analys' + (list.length === 1 ? 'is' : 'es')), wrap);
}
async function drawAdminLabs(v) {
  v.append(el('div', { class: 'page-head', style: 'margin-top:28px' }, el('h2', {}, 'Labs & analyses'),
    el('div', { class: 'actions' }, el('button', { class: 'secondary', onclick: () => { window.location = '/api/labs/starter-template/download?token=' + encodeURIComponent(State.token); } }, '⬇ Starter requisition template'),
      el('button', { onclick: () => editLab(null) }, '+ Add lab'))));
  const { labs } = await api('GET', '/labs');
  if (!labs.length) { v.append(el('div', { class: 'empty card' }, 'No labs yet.')); }
  else v.append(table(['Lab', 'Contact', 'Analyses', 'Requisition template', 'Status', ''],
    labs.map(l => [el('b', {}, l.name), el('span', {}, l.contact || '—', l.email ? el('div', { class: 'help' }, l.email) : null),
      analysisChips(l),
      el('span', {}, l.hasTemplate ? el('span', {}, '📄 ' + l.templateName, l.templateTokens === 0 ? el('div', { class: 'help', style: 'color:var(--danger)' }, '⚠ no {{placeholders}} — won’t auto-fill') : null) : el('span', { class: 'muted' }, 'Built-in layout'), l.sampleSheet ? el('div', { class: 'help' }, '+ sample spreadsheet') : null),
      l.active ? badge('on_hand', 'Active') : badge('disposed', 'Inactive'),
      rowActions([['Edit', () => editLab(l)], ['Analyses', () => manageLabAnalyses(l.id)], ['Template', () => labTemplateModal(l)]])]),
    [false, false, false, false, false, false]));
  v.append(el('div', { class: 'help', style: 'margin-top:10px' }, 'Each lab lists the analyses it can perform; the Samples cart offers those as checkboxes. A lab’s requisition template is a Word (.docx) file with {{placeholders}} that the app fills in and saves on the production run.'));
}
function editLab(l) {
  const f = (k, placeholder) => el('input', { id: 'lb_' + k, value: (l && l[k]) || '', placeholder: placeholder || '' });
  const active = el('input', { type: 'checkbox' }); active.checked = l ? l.active : true;
  const sheetCb = el('input', { type: 'checkbox' }); sheetCb.checked = l ? l.sampleSheet : false;
  // the checklist look used elsewhere (Users, Certificate of Analysis specifications): one titled row per option, with its explanation
  const option = (cb, title, hint) => {
    const row = el('label', { class: 'perm-item' + (cb.checked ? ' on' : '') }, cb, el('span', { class: 'perm-text' }, el('b', {}, title), el('small', {}, hint)));
    cb.addEventListener('change', () => row.classList.toggle('on', cb.checked));
    return row;
  };
  const section = (title, ...kids) => el('div', { class: 'perm-box' }, el('div', { class: 'perm-title' }, title), ...kids);
  const body = el('div', {},
    section('Laboratory',
      el('div', { class: 'form-row' }, field('Lab name', f('name')), field('Contact person', f('contact')))),
    section('Contact details',
      el('div', { class: 'form-row' }, field('Email', f('email')), field('Phone', f('phone'))),
      field('Address', f('address')), field('Notes', f('notes', 'Optional'))),
    section('Options', el('div', { class: 'perm-list' },
      option(active, 'Active', 'Offered in the Samples cart. Untick to retire a lab without deleting it.'),
      option(sheetCb, 'Generate a sample spreadsheet', 'Also create an Excel sample list (sample ID, description, tests per sample) with each requisition — for labs whose form says “see attached spreadsheet”.'))));
  modal(l ? 'Edit lab — ' + l.name : 'Add lab', body, async () => {
    const p = {}; ['name', 'contact', 'email', 'phone', 'address', 'notes'].forEach(k => p[k] = body.querySelector('#lb_' + k).value);
    p.active = active.checked; p.sampleSheet = sheetCb.checked;
    if (l) await api('PUT', '/labs/' + l.id, p); else await api('POST', '/labs', p);
    toast('Lab saved'); render();
  }, l ? 'Save lab' : 'Add lab');
}
async function manageLabAnalyses(labId) {
  const { labs } = await api('GET', '/labs'); const l = labs.find(x => x.id === labId);
  const host = el('div', {});
  const name = el('input', { placeholder: 'Analysis name, e.g. Total Plate Count' }), code = el('input', { placeholder: 'Code (optional)', style: 'max-width:110px' }),
    method = el('input', { placeholder: 'Method / spec (optional)', style: 'max-width:170px' });
  async function draw() {
    const fresh = (await api('GET', '/labs')).labs.find(x => x.id === labId);
    host.innerHTML = '';
    host.append(table(['Analysis', 'Code', 'Method / spec', 'Status', ''], fresh.analyses.map(a => [a.name, a.code || '—', a.method || '—', a.active ? badge('on_hand', 'Offered') : badge('disposed', 'Hidden'),
      rowActions([[a.active ? 'Hide' : 'Show', async () => { await api('PUT', '/labs/' + labId + '/analyses/' + a.id, { active: !a.active }); draw(); }],
        ['Edit', () => {
          const n = el('input', { value: a.name }), c = el('input', { value: a.code || '' }), m = el('input', { value: a.method || '' });
          modal('Edit analysis', el('div', {}, field('Name', n), el('div', { class: 'form-row' }, field('Code', c), field('Method / specification', m))), async () => {
            await api('PUT', '/labs/' + labId + '/analyses/' + a.id, { name: n.value, code: c.value, method: m.value }); draw();
          }, 'Save');
        }],
        ['Delete', async () => { if (confirm('Delete “' + a.name + '”? Past requisitions keep their record of it.')) { await api('DELETE', '/labs/' + labId + '/analyses/' + a.id); draw(); } }, 'danger']])])));
  }
  const add = el('button', { onclick: async () => {
    if (!name.value.trim()) return;
    try { await api('POST', '/labs/' + labId + '/analyses', { name: name.value, code: code.value, method: method.value }); name.value = ''; code.value = ''; method.value = ''; draw(); }
    catch (e) { toast(e.message, true); }
  } }, '+ Add');
  const fromCoa = el('button', { class: 'secondary', onclick: async () => {
    const [{ specs }, fresh] = await Promise.all([api('GET', '/coa-specs'), api('GET', '/labs')]);
    const have = new Set(fresh.labs.find(x => x.id === labId).analyses.map(a => a.name.toLowerCase()));
    const groupName = { physical: 'Physical & chemical', metals: 'Heavy metals', microbial: 'Microbiological' };
    const avail = specs.filter(x => x.basis !== 'run' && !have.has(x.name.toLowerCase()));
    if (!avail.length) return toast('Every Certificate of Analysis test is already on this lab’s list.');
    // the checklist look used elsewhere (Users, specifications): a titled group per kind of test, one row per test with its method and limit,
    // and a "Select all" per group
    const boxes = [];
    const count = el('b', {});
    const syncCount = () => { const n = boxes.filter(([cb]) => cb.checked).length; count.textContent = n + ' selected'; };
    const body = el('div', {},
      el('div', { class: 'summary-line' }, sl('Lab', l.name), sl('Available', String(avail.length)), el('span', {}, count)),
      el('div', { class: 'help' }, 'Tick the tests this lab performs. Each is added with the method from the Certificate of Analysis specification; edit it afterwards if the lab uses another method.'));
    ['microbial', 'metals', 'physical'].forEach(g => {
      const grp = avail.filter(x => x.group === g);
      if (!grp.length) return;
      const mine = [];
      const all = el('input', { type: 'checkbox', title: 'Select every ' + groupName[g].toLowerCase() + ' test' });
      all.addEventListener('change', () => { const on = all.checked; mine.forEach(cb => { cb.checked = on; cb.dispatchEvent(new Event('change')); }); all.checked = on; });
      const list = el('div', { class: 'perm-list' });
      grp.forEach(x => {
        const cb = el('input', { type: 'checkbox' });
        const row = el('label', { class: 'perm-item' }, cb, el('span', { class: 'perm-text' }, el('b', {}, x.name),
          el('small', {}, [x.method ? 'Method: ' + x.method : '', x.specText && x.specText !== '-' ? 'Limit: ' + x.specText : ''].filter(Boolean).join('  ·  ') || 'No method set')));
        cb.addEventListener('change', () => { row.classList.toggle('on', cb.checked); all.checked = mine.every(c => c.checked); syncCount(); });
        mine.push(cb); boxes.push([cb, x]); list.append(row);
      });
      body.append(el('div', { class: 'perm-box' },
        el('div', { class: 'perm-title', style: 'display:flex;justify-content:space-between;align-items:center' }, el('span', {}, groupName[g]),
          el('label', { style: 'display:inline-flex;gap:6px;align-items:center;margin:0;font-size:11px;text-transform:none;letter-spacing:0;font-weight:600' }, all, 'Select all')), list));
    });
    syncCount();
    modal('Add Certificate of Analysis tests — ' + l.name, body, async () => {
      const chosen = boxes.filter(([cb]) => cb.checked);
      if (!chosen.length) throw new Error('Tick at least one test to add.');
      for (const [, x] of chosen) await api('POST', '/labs/' + labId + '/analyses', { name: x.name, code: '', method: x.method || '' });
      toast(chosen.length + ' test' + (chosen.length === 1 ? '' : 's') + ' added');
      await draw();
    }, 'Add selected tests');
  } }, '+ From CoA tests');
  modal('Analyses — ' + l.name, el('div', {}, host, el('div', { style: 'display:flex;gap:8px;margin-top:10px;flex-wrap:wrap' }, name, code, method, add, fromCoa),
    el('div', { class: 'help', style: 'margin-top:8px' }, 'A template checkbox column uses {{sample.check:Analysis name}} (or the code). Hiding an analysis removes it from the cart without deleting it.')),
    async () => { render(); }, 'Close', { noCancel: true, wide: true });
  draw();
}
function labTemplateModal(l) {
  const file = el('input', { type: 'file', accept: '.docx' }), out = el('div', { class: 'help', style: 'margin-top:8px' });
  const tokens = '{{req_number}} {{date}} {{date_long}} {{po_number}} {{po_check}} {{company}} {{lab_name}} {{lab_contact}} {{lab_email}} {{lab_phone}} {{lab_address}} {{customer_phone}} {{customer_email_1}} … {{customer_email_5}} {{processing_lot}} {{run_date}} {{product}} {{requested_by}} {{requested_by_email}} {{sample_count}} {{analyses}} {{notes}}';
  const body = el('div', {},
    el('div', { class: 'help' }, l.hasTemplate ? 'Current template: ' + l.templateName : 'No template uploaded — requisitions use the built-in layout.'),
    l.hasTemplate ? el('div', { style: 'margin:6px 0' }, el('a', { href: '/api/labs/' + l.id + '/template/download?token=' + encodeURIComponent(State.token) }, '⬇ Download current template'),
      '  ', el('button', { class: 'secondary', onclick: async () => { await api('DELETE', '/labs/' + l.id + '/template'); toast('Template removed'); document.querySelector('.modal-bg')?.remove(); render(); } }, 'Remove')) : null,
    l.readyTemplate ? el('div', { style: 'margin:8px 0;padding:10px 12px;background:#eef8f6;border-radius:10px' }, el('b', {}, 'Ready-made form available'),
      el('div', { class: 'help' }, l.readyTemplate + ' — the lab’s own form with the placeholders already in place.'),
      el('button', { type: 'button', style: 'margin-top:6px', onclick: () => useReadyTemplate(l, async () => { document.querySelector('.modal-bg')?.remove(); render(); }) }, 'Use this form for ' + l.name)) : null,
    field('Upload a Word template (.docx)', file), out,
    el('details', { style: 'margin-top:10px' }, el('summary', {}, 'Placeholders you can use in the template'),
      el('div', { class: 'help', style: 'margin-top:6px' }, 'Anywhere in the document: ' + tokens + '.'),
      el('div', { class: 'help', style: 'margin-top:6px' }, 'Put these in ONE table row — that row is repeated for every sample: {{sample.n}} {{sample.id}} {{sample.stage}} {{sample.type}} {{sample.description}} {{sample.container}} {{sample.collected}} {{sample.analyses}} {{sample.location}}. {{sample.report_description}} and {{sample.methods}} (the methods of that sample’s analyses) are also available. For a column of analysis checkboxes use {{sample.check:Analysis name}} (☒ / ☐).'),
      el('div', { class: 'help', style: 'margin-top:6px' }, 'A paragraph containing {{analysis.name}} (also {{analysis.code}}, {{analysis.count}}) is repeated once for each test requested on the requisition — use it for a “Tests requested” list. A value with several lines can be placed with {{analyses}} (comma separated).')));
  modal('Requisition template — ' + l.name, body, async () => {
    if (!file.files[0]) throw new Error('Choose a .docx file to upload.');
    const b64 = await readFileAsBase64(file.files[0]);
    const r = await api('POST', '/labs/' + l.id + '/template', { filename: file.files[0].name, dataB64: b64 });
    if (r.report.warnings.length) { alert('Template saved with notes:\n\n• ' + r.report.warnings.join('\n• ')); }
    else toast('Template saved');
    render();
  }, 'Upload template', { wide: true });
}

/* ---------------- Pre-Processing (shred + blend + pack back into feedstock) ---------------- */
// Recommended dilution water (L) and blend mass for a batch: bring the shredded mass from its measured
// % solids down to the target % solids (1 kg of water = 1 L). Documented on the Calculations page.
function preprocPlanCalc(shreddedKg, startPct, targetPct) {
  if (!(shreddedKg > 0) || !(startPct > 0) || !(targetPct > 0)) return null;
  const waterL = startPct > targetPct ? shreddedKg * (startPct / targetPct - 1) : 0;
  return { waterL, blendKg: shreddedKg + waterL };
}
function preNumInput(val, dec, ph) {
  const i = el('input', { inputmode: 'decimal', placeholder: ph || '' });
  attachNumericMask(i, dec);
  if (val != null) i.value = formatQcValue(val, dec);
  return i;
}
const preNumOf = inp => inp.value.trim() === '' ? null : qcParseValue(inp.value);
function preSection(title, bodyEls, saveFn) {
  const status = el('span', { class: 'help', style: 'margin-left:8px' });
  const btn = el('button', { type: 'button', class: 'secondary section-save', onclick: () => run() }, 'Save');
  async function run() {
    status.textContent = ''; btn.disabled = true;
    try { await saveFn(); status.textContent = 'Saved.'; return true; }
    catch (e) { status.textContent = e.message; return false; }
    finally { btn.disabled = false; }
  }
  const box = el('details', { class: 'accordion', open: 'open' }, el('summary', {}, title),
    el('div', { class: 'accordion-body' }, ...bodyEls, saveFn ? el('div', { style: 'margin-top:10px' }, btn, status) : null));
  return { box, save: run };
}
const preResult = (label, node) => el('div', { class: 'qc-check-result' }, el('span', { class: 'qc-check-result-label' }, label), node);
function preTile(span, text, warn) { span.className = text != null ? 'qc-check-result-value' + (warn ? ' var-flag' : '') : 'help'; span.textContent = text != null ? text : '—'; }

async function pagePreproc(v) {
  v.append(el('div', { class: 'page-head' }, el('h2', {}, 'Pre-Processing'),
    el('div', { class: 'actions' }, el('button', { onclick: async () => {
      const b = await api('POST', '/preproc'); await openPreprocBatch(b.id);
    } }, '+ New batch'))));
  const { batches } = await api('GET', '/preproc');
  const drafts = batches.filter(b => b.status === 'draft'), done = batches.filter(b => b.status === 'completed');
  if (!batches.length) {
    v.append(el('div', { class: 'empty card' }, 'No pre-processing batches yet. Click “New batch” to pull coarse-ground totes, shred and blend them, and pack the blend into IBCs that go back into Feedstock Inventory as fine-grind lots (traceable to the original totes).'));
    return;
  }
  if (drafts.length) {
    v.append(el('h3', { style: 'margin:0 0 8px' }, 'In progress'));
    drafts.forEach(b => v.append(preprocDraftCard(b)));
  }
  if (done.length) {
    if (drafts.length) v.append(el('h3', { style: 'margin:14px 0 8px' }, 'Completed'));
    done.forEach(b => v.append(preprocDoneCard(b)));
  }
}
function preprocDraftCard(b) {
  return el('div', { class: 'card' },
    el('div', { class: 'page-head', style: 'margin:0 0 8px' },
      el('h3', { style: 'margin:0' }, mono(b.batchLot), '  ', el('span', { class: 'badge hold' }, 'Not yet completed')),
      el('div', { class: 'actions' },
        el('button', { onclick: () => openPreprocBatch(b.id) }, 'Resume'),
        el('button', { class: 'danger', onclick: async () => {
          if (!confirm('Discard this draft batch? Every tote pulled into it goes back into stock.')) return;
          await api('DELETE', '/preproc/' + b.id); toast('Draft discarded'); render();
        } }, 'Discard'))),
    el('div', { class: 'summary-line' },
      sl('Batch date', b.batchDate || '—'), sl('Operators', b.operators || '—'),
      sl('Totes pulled', b.inputCount ? b.inputCount + ' · ' + fmt(b.inputKg, 1) + ' kg' : '—'),
      sl('Output location', b.location || '—')),
    stageProgress(b, { onSelect: key => openPreprocBatch(b.id, key), readyText: 'All required fields complete — ready to complete the batch' }),
    el('div', { class: 'muted', style: 'margin-top:6px;font-size:12px' }, 'Click a section above to open it. Completing the batch adds its fine-grind IBCs to Feedstock Inventory.'));
}
function preprocDoneCard(b) {
  const cell = (k, v, cls) => el('div', { class: 'rs' + (cls ? ' ' + cls : '') }, el('span', { class: 'rs-k' }, k), el('span', { class: 'rs-v' }, v));
  const dash = '—', opt = (x, d, u) => x != null ? fmt(x, d) + ' ' + u : dash;
  const lots = b.outputCount ? (b.outputCount === 1 ? b.batchLot + '-01' : b.batchLot + '-01 … -' + String(b.outputCount).padStart(2, '0')) : dash;
  return el('div', { class: 'card' },
    el('div', { class: 'page-head', style: 'margin:0 0 8px' },
      el('h3', { style: 'margin:0' }, mono(b.batchLot), '  ', el('span', { class: 'badge in_stock' }, 'Completed')),
      el('div', { class: 'actions' },
        el('button', { class: 'secondary', onclick: () => openPreprocBatch(b.id) }, '📋 Batch record'),
        el('button', { class: 'secondary', onclick: async () => {
          const f = await api('GET', '/preproc/' + b.id); const src = f.inputs.map(i => i.lot);
          printLabels(f.outputs.map(o => blendLabel(o, src)));
        } }, 'Print labels'))),
    el('div', { class: 'run-stats' },
      cell('Batch date', b.batchDate || dash), cell('Operators', b.operators || dash), cell('Completed', b.completedAt ? fmtWhen(b.completedAt) : dash),
      cell('Source totes', b.inputCount ? fmt(b.inputCount) : dash), cell('Feedstock weight', b.inputCount ? fmt(b.inputKg, 1) + ' kg' : dash),
      cell('Dilution water', opt(b.waterAddedL, 0, 'L')),
      cell('Final solids', opt(b.finalSolidsPct, 2, '%')), cell('pH', b.measuredPh != null ? fmt(b.measuredPh, 1) + (b.targetPh != null ? ' (target ' + fmt(b.targetPh, 1) + ')' : '') : dash),
      cell('Citric acid', opt(b.citricKg, 2, 'kg')),
      cell('Fine-grind IBCs', b.outputCount ? fmt(b.outputCount) : dash), cell('Volume packed', b.packedL != null ? fmt(b.packedL, 0) + ' L' : dash),
      cell('Stored at', b.location || dash),
      cell('Output lots', lots, 'rs-wide')));
}

async function openPreprocBatch(id, section) {
  let B = await api('GET', '/preproc/' + id);
  const v = el('div', {});            // the batch window's body (a wide window, like a production run's Process log)
  if (B.status === 'completed') {
    modal('Batch record — ' + B.batchLot, v, async () => { render(); }, 'Done', { wide: true, closeX: true, onClose: () => render() });
    return drawPreprocCompleted(v, B);
  }
  // Footer buttons call these once the sections below exist: "Save & close" saves every section; "Complete batch" saves then completes.
  let saveAllFn = async () => { throw new Error('Still loading — try again in a moment.'); }, completeFn = saveAllFn;
  modal('Pre-Processing batch — ' + B.batchLot, v, () => completeFn(), 'Complete batch',
    { wide: true, closeX: true, onClose: () => render(), extraLabel: 'Save & close', onExtra: async () => { await saveAllFn(); render(); } });

  const problemsHost = el('div', {});
  const drawProblems = () => {
    problemsHost.innerHTML = '';
    if (B.problems && B.problems.length)
      problemsHost.append(el('div', { class: 'help', style: 'margin:8px 0' }, el('b', {}, 'Still required to complete: '), B.problems.join(' · ')));
  };
  const apply = r => { B = r; drawProblems(); refreshAll(); };
  const put = async fields => apply(await api('PUT', '/preproc/' + id, fields));

  /* 1. Initiation */
  const dateInp = el('input', { type: 'date', value: B.batchDate || todayStr() });
  const ops = buildOperatorsSelect(B.operators || '');
  const notesInp = el('textarea', { rows: '2', placeholder: 'Notes' }, B.notes || '');
  const sInit = preSection('Initiation', [
    el('div', { class: 'form-row' }, field(reqLabel('Batch date'), dateInp), field(reqLabel('Operators'), ops.el)),
    field('Notes', notesInp)],
    () => put({ batchDate: dateInp.value, operators: ops.value || null, notes: notesInp.value }));

  /* 2. Feedstock pick list + characterization */
  const pickHost = el('div', {}), pickedHost = el('div', {}), pickSummary = el('div', { class: 'summary-line' });
  let pickTotes = [];
  const pf = { q: '', species: '', site: '' };
  const qInp = el('input', { placeholder: 'Search lot / location…' });
  const spSel = el('select', {}, el('option', { value: '' }, 'All species'),
    ...(State.ref.species || []).filter(s => s.code !== 'MIX').map(s => el('option', { value: s.code }, s.common || s.name)));
  const siteSel = el('select', {}, el('option', { value: '' }, 'All sites'),
    ...(State.ref.sites || []).filter(s => s.code !== 'MIX').map(s => el('option', { value: s.code }, s.name)));
  const picks = new Set();
  async function loadPickTotes() {
    const r = await api('GET', '/totes?status=in_stock');
    pickTotes = r.totes.filter(t => (t.grind || 'Coarse') === 'Coarse' && t.location !== 'QAQC Hold');
    drawPick();
  }
  function drawPick() {
    const q = qInp.value.trim().toLowerCase();
    const rows = pickTotes.filter(t => (!spSel.value || t.species === spSel.value) && (!siteSel.value || t.site === siteSel.value) &&
      (!q || (t.lot + ' ' + (t.location || '')).toLowerCase().includes(q)));
    [...picks].forEach(i => { if (!pickTotes.some(t => t.id === i)) picks.delete(i); });
    pickHost.innerHTML = '';
    const tb = el('tbody', {});
    if (!rows.length) tb.append(el('tr', {}, el('td', { colspan: 8, class: 'empty' }, 'No coarse-grind totes in stock match.')));
    rows.forEach(t => {
      const cb = el('input', { type: 'checkbox', onchange: () => { cb.checked ? picks.add(t.id) : picks.delete(t.id); addBtn.textContent = 'Add selected (' + picks.size + ')'; } });
      cb.checked = picks.has(t.id);
      tb.append(el('tr', {}, el('td', { class: 'checkcol' }, cb), el('td', { class: 'mono' }, t.lot), el('td', {}, siteName(t.site)),
        el('td', {}, speciesName(t.species)), el('td', {}, t.harvestDate || '—'), el('td', { class: 'num' }, fmt(t.avgWeightKg, 1)),
        el('td', { class: 'num' }, t.ph != null ? fmt(t.ph, 2) : '—'), el('td', {}, t.location || '—')));
    });
    pickHost.append(el('div', { class: 'tablewrap', style: 'max-height:260px;overflow:auto' }, el('table', {},
      el('thead', {}, el('tr', {}, el('th', { class: 'checkcol' }, ''), ...['Lot', 'Site', 'Species', 'Harvest date'].map(h => el('th', {}, h)),
        el('th', { class: 'num' }, 'Avg kg'), el('th', { class: 'num' }, 'pH'), el('th', {}, 'Location'))), tb)));
  }
  [qInp, spSel, siteSel].forEach(c => c.addEventListener(c === qInp ? 'input' : 'change', drawPick));
  const addBtn = el('button', { type: 'button', onclick: async () => {
    if (!picks.size) return toast('Select at least one tote.', true);
    try { apply(await api('POST', '/preproc/' + id + '/inputs', { toteIds: [...picks] })); picks.clear(); addBtn.textContent = 'Add selected (0)'; await loadPickTotes(); drawPicked(); }
    catch (e) { toast(e.message, true); }
  } }, 'Add selected (0)');
  const charCache = {}, pickInputs = {};
  async function drawPicked() {
    pickedHost.innerHTML = '';
    Object.keys(pickInputs).forEach(k => delete pickInputs[k]);
    const kg = B.inputs.reduce((a, i) => a + (i.weightKg || 0), 0);
    pickSummary.innerHTML = '';
    pickSummary.append(sl('Totes', B.inputs.length), sl('Input', fmt(kg, 1) + ' kg'), sl('Volume', fmt(B.inputs.reduce((a, i) => a + (i.volumeL || 0), 0), 0) + ' L'));
    if (!B.inputs.length) { pickedHost.append(el('div', { class: 'help' }, 'Add totes from the pick list above.')); return; }
    for (const i of B.inputs) {
      if (!(i.toteLotId in charCache)) {
        const r = await api('GET', '/totes/' + i.toteLotId + '/ph').catch(() => null);
        charCache[i.toteLotId] = r && r.latestCharacterization;
      }
      const wInp = preNumInput(i.weightKg, 1, 'kg');
      wInp.addEventListener('change', async () => { try { apply(await api('PUT', '/preproc/' + id + '/inputs/' + i.toteLotId, { weightKg: preNumOf(wInp) })); updatePickSummary(); } catch (e) { toast(e.message, true); } });
      const vInp = preNumInput(i.volumeL, 1, 'L');
      pickInputs[i.toteLotId] = { wInp, vInp };
      vInp.addEventListener('change', async () => { try { apply(await api('PUT', '/preproc/' + id + '/inputs/' + i.toteLotId, { volumeL: preNumOf(vInp) })); updatePickSummary(); } catch (e) { toast(e.message, true); } });
      const rm = el('button', { type: 'button', class: 'secondary', onclick: async () => {
        apply(await api('DELETE', '/preproc/' + id + '/inputs/' + i.toteLotId)); await loadPickTotes(); drawPicked();
      } }, 'Remove from batch');
      const card = buildFeedstockCard({
        label: 'Characterize — ' + i.lot, initial: charCache[i.toteLotId], mode: 'draft',
        omit: ['loadedAt', 'weightKg', 'volumeL', 'densityKgL'],
        onChange: vals => { charCache[i.toteLotId] = vals; },
        onSave: async vals => {
          charCache[i.toteLotId] = vals;
          const r = await api('POST', '/totes/' + i.toteLotId + '/characterize', vals);
          if (r.rejected) {
            apply(await api('DELETE', '/preproc/' + id + '/inputs/' + i.toteLotId));
            toast(i.lot + ' rejected — moved to QAQC Hold and removed from this batch.');
            await loadPickTotes(); drawPicked();
          }
        },
        uploadPhoto: async (slot, file, b64) => (await api('POST', '/totes/' + i.toteLotId + '/photo',
          { slot, filename: file.name, contentType: file.type || 'image/jpeg', dataB64: b64 })).attachmentId,
        photoUrl: attId => toteAttDownloadUrl(i.toteLotId, attId, false)
      });
      pickedHost.append(el('div', { class: 'card', style: 'margin:8px 0;padding:10px' },
        el('div', { style: 'display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap' },
          el('div', {}, el('b', { class: 'mono' }, i.lot), el('div', { class: 'help' }, siteName(i.site) + ' · ' + speciesName(i.species) + ' · avg ' + fmt(i.avgWeightKg, 1) + ' kg')),
          field(reqLabel('Weight tote (kg)'), wInp), field('Volume tote (L)', vInp), rm), card));
    }
  }
  const updatePickSummary = () => {
    const kg = B.inputs.reduce((a, i) => a + (i.weightKg || 0), 0), vol = B.inputs.reduce((a, i) => a + (i.volumeL || 0), 0);
    pickSummary.innerHTML = ''; pickSummary.append(sl('Totes', B.inputs.length), sl('Input', fmt(kg, 1) + ' kg'), sl('Volume', fmt(vol, 0) + ' L'));
  };
  const sPick = preSection('Feedstock pick list', [
    el('div', { class: 'help', style: 'margin-bottom:6px' }, 'Only coarse-grind totes in stock (not on QAQC Hold) are offered. Pulled totes are locked to this batch until it is completed or discarded.'),
    el('div', { class: 'form-row' }, field('Search', qInp), field('Species', spSel), field('Site', siteSel)),
    pickHost, el('div', { style: 'margin:8px 0' }, addBtn),
    pickSummary, pickedHost],
    async () => {
      // weight / volume save as you tab out of each box; this writes every pulled tote's current values in one go
      for (const [tid, c] of Object.entries(pickInputs))
        apply(await api('PUT', '/preproc/' + id + '/inputs/' + tid, { weightKg: preNumOf(c.wInp), volumeL: preNumOf(c.vInp) }));
      updatePickSummary();
    });

  /* 3. Blend: solids loading (all fields optional; estimated weight = the pulled totes' weights) */
  const startInp = preNumInput(B.startSolidsPct, 2, '%'), targetInp = preNumInput(B.targetSolidsPct, 2, '%');
  const waterInp = preNumInput(B.waterAddedL, 1, 'L'), volInp = preNumInput(B.blendVolumeL, 1, 'Measured from level sensor');
  const shredKgT = el('span', { class: 'help' }), recWaterT = el('span', { class: 'help' }), expVolT = el('span', { class: 'help' }), finalT = el('span', { class: 'help' }),
    finalEstT = el('span', { class: 'help' });
  // total volume of the pulled totes (needs every tote's volume); expected blend volume = that + the dilution water
  const toteVolume = () => (B.inputs.length && B.inputs.every(i => i.volumeL != null)) ? B.inputs.reduce((a, i) => a + i.volumeL, 0) : null;
  const shreddedKg = () => B.inputKg > 0 ? B.inputKg : null;
  const finalSolids = () => {
    const sh = shreddedKg(), st = preNumOf(startInp), w = preNumOf(waterInp);
    return (sh > 0 && st > 0 && w != null) ? st * sh / (sh + w) : null;
  };
  // Final % solids estimated from the ACTUALS: the measured final blend volume less the water actually added is the product
  // volume; its mass follows the totes' density (estimated weight / tote volume, 1 kg/L if volumes are missing); solids are the
  // starting % of that mass, spread over (product mass + water).
  const finalSolidsEst = () => {
    const sh = shreddedKg(), st = preNumOf(startInp), w = preNumOf(waterInp), V = preNumOf(volInp), tv = toteVolume();
    if (!(sh > 0 && st > 0 && w != null && V > 0) || V - w <= 0) return null;
    const mp = (V - w) * (tv > 0 ? sh / tv : 1);
    return st * mp / (mp + w);
  };
  function calcBlend() {
    const c = preprocPlanCalc(shreddedKg(), preNumOf(startInp), preNumOf(targetInp));
    preTile(shredKgT, shreddedKg() != null ? fmt(shreddedKg(), 1) + ' kg' : null);
    preTile(recWaterT, c ? fmt(c.waterL, 0) + ' L' : null);
    const water = preNumOf(waterInp) != null ? preNumOf(waterInp) : (c ? c.waterL : null), tv = toteVolume();
    preTile(expVolT, (tv != null && water != null) ? fmt(tv + water, 0) + ' L' : null);
    const f = finalSolids(); preTile(finalT, f != null ? fmt(f, 2) + ' %' : null);
    const fe = finalSolidsEst(); preTile(finalEstT, fe != null ? fmt(fe, 2) + ' %' : null);
    return c;
  }
  [startInp, targetInp, waterInp, volInp].forEach(i => i.addEventListener('input', calcBlend));
  const sBlend = preSection('Blend — solids loading', [
    el('div', { class: 'help', style: 'margin-bottom:6px' }, 'Estimated weight is the total weight of the totes in the pick list. Recommended water = estimated kg × (starting % ÷ target % − 1), taking 1 kg of water = 1 L. Expected blend volume = total tote volume + dilution water (the amount added, or the recommended amount until it is entered). Final % solids = starting % × estimated kg ÷ (estimated kg + water added).'),
    el('div', { class: 'form-row' }, field('Starting % solids', startInp), field('Target % solids', targetInp)),
    preResult('Estimated weight', shredKgT), preResult('Recommended dilution water', recWaterT), preResult('Expected blend volume', expVolT),
    el('div', { class: 'form-row', style: 'margin-top:8px' }, field('Dilution water added (L)', waterInp), field('Blend volume (L)', volInp)),
    preResult('Final % solids (calculated)', finalT),
    preResult('Final % solids (estimated from actuals)', finalEstT),
    el('div', { class: 'help' }, 'Estimated from actuals = starting % × product mass ÷ (product mass + water added), where product volume = final blend volume − water actually added, and its mass uses the totes’ weight ÷ volume.')],
    async () => {
      const c = calcBlend(), f = finalSolids();
      await put({ startSolidsPct: preNumOf(startInp), targetSolidsPct: preNumOf(targetInp), recommendedWaterL: c ? Math.round(c.waterL * 10) / 10 : null,
        waterAddedL: preNumOf(waterInp), blendVolumeL: preNumOf(volInp), finalSolidsPct: f != null ? Math.round(f * 100) / 100 : null });
    });

  /* 4. pH balancing */
  const phInp = preNumInput(B.measuredPh, 1, 'pH'), tphInp = preNumInput(B.targetPh, 1, 'pH'), citricInp = preNumInput(B.citricKg, 2, 'kg');
  const reagentWatch = buildReagentWatch(null, { 'Citric Acid': B.citricItemId });
  reagentWatch.load();
  citricInp.addEventListener('input', () => reagentWatch.setUsage('Citric Acid', 'batch', preNumOf(citricInp)));
  reagentWatch.setUsage('Citric Acid', 'batch', preNumOf(citricInp));
  const sPh = preSection('pH balancing', [
    el('div', { class: 'form-row' }, field(reqLabel('Measured pH (final)'), phInp), field(reqLabel('Target pH'), tphInp)),
    el('div', { class: 'form-row' }, field('Citric acid item (inventory)', reagentWatch.itemSelect('Citric Acid')),
      field(reqLabel('Citric acid added (kg)'), el('div', {}, citricInp, reagentWatch.noteEl('Citric Acid')))),
    el('div', { class: 'help' }, 'Citric acid is deducted from Inventory Items when the batch is completed.')],
    () => put({ measuredPh: preNumOf(phInp), targetPh: preNumOf(tphInp), citricKg: preNumOf(citricInp), citricItemId: reagentWatch.itemId('Citric Acid') }));

  /* 5. Pack-out into IBCs */
  const containers = (await api('GET', '/consumables')).consumables.filter(c => c.isContainer && !c.isSampleContainer);
  const locSel = el('select', {}, el('option', { value: '' }, 'Select…'), ...(State.ref.locations || []).map(l => el('option', { value: l }, l)));
  locSel.value = B.location || '';
  const packRows = el('tbody', {}), packTotals = el('div', {});
  const packedT = el('span', { class: 'help' }), ibcT = el('span', { class: 'help' });
  const rowCtl = [];
  function addPackRow(r) {
    r = r || { container: '', qty: null, litresEach: null };
    const sel = el('select', {}, el('option', { value: '' }, 'Select…'), ...containers.map(c => el('option', { value: c.name }, c.name + ' (' + fmt(c.onHand, 0) + ' on hand)')));
    sel.value = r.container || '';
    const qty = preNumInput(r.qty, 0, 'IBCs'), fill = preNumInput(r.litresEach, 1, 'L each');
    sel.addEventListener('change', () => { const c = containers.find(x => x.name === sel.value); if (c && c.litresEach && !fill.value) fill.value = formatQcValue(c.litresEach, 1); calcPack(); });
    [qty, fill].forEach(i => i.addEventListener('input', calcPack));
    const ctl = { sel, qty, fill };
    const tr = el('tr', {}, el('td', {}, sel), el('td', {}, qty), el('td', {}, fill),
      el('td', {}, el('button', { type: 'button', class: 'secondary', onclick: () => { rowCtl.splice(rowCtl.indexOf(ctl), 1); tr.remove(); calcPack(); } }, '✕')));
    rowCtl.push(ctl); packRows.append(tr);
  }
  function calcPack() {
    const n = rowCtl.reduce((a, c) => a + (preNumOf(c.qty) || 0), 0);
    const l = rowCtl.reduce((a, c) => a + (preNumOf(c.qty) || 0) * (preNumOf(c.fill) || 0), 0);
    preTile(ibcT, n ? String(n) : null); preTile(packedT, l ? fmt(l, 0) + ' L' : null);
  }
  (B.packaging.length ? B.packaging : [null]).forEach(addPackRow);
  const sPack = preSection('Pack-out into IBCs', [
    el('div', { class: 'help', style: 'margin-bottom:6px' }, 'Pulled coarse totes are shredded to a fine grind, and each output IBC becomes its own fine-grind lot (' + B.batchLot + '-01, -02 …) in Feedstock Inventory. Empty IBCs are deducted from Inventory Items when the batch is completed; the source totes’ emptied IBCs return to the Used IBC pool.'),
    field(reqLabel('Output location'), locSel),
    el('div', { class: 'tablewrap' }, el('table', {}, el('thead', {}, el('tr', {}, el('th', {}, 'Empty IBC used'), el('th', {}, 'Qty'), el('th', {}, 'Fill each (L)'), el('th', {}, ''))), packRows)),
    el('div', { style: 'margin:6px 0' }, el('button', { type: 'button', class: 'secondary', onclick: () => addPackRow() }, '+ Add row')),
    preResult('IBCs produced', ibcT), preResult('Total packed', packedT)],
    async () => {
      await api('PUT', '/preproc/' + id + '/packaging', { rows: rowCtl.map(c => ({ container: c.sel.value, qty: preNumOf(c.qty), litresEach: preNumOf(c.fill) })) });
      await put({ location: locSel.value || null });
    });

  const sections = [sInit, sPick, sBlend, sPh, sPack];
  saveAllFn = async () => {
    for (const sec of sections) if (sec.save && !(await sec.save())) throw new Error('A section could not be saved — see the message under its Save button.');
  };
  completeFn = async () => {
    await saveAllFn();
    const done = await api('POST', '/preproc/' + id + '/complete');
    State.ref = await api('GET', '/refdata');
    toast('Batch ' + done.batchLot + ' completed — ' + done.outputCount + ' fine-grind IBC lot(s) added to Feedstock Inventory.');
    render();
  };
  function refreshAll() { updatePickSummary(); calcBlend(); calcPack(); }
  ['initiation', 'pick', 'blend', 'ph', 'pack'].forEach((k, i) => { sections[i].box.dataset.sec = k; });
  v.append(...sections.map(s => s.box), problemsHost);
  drawProblems(); refreshAll(); await loadPickTotes(); await drawPicked();
  if (section) { const t = v.querySelector('[data-sec="' + section + '"]'); if (t) t.scrollIntoView({ block: 'start' }); }
}

function drawPreprocCompleted(v, B) {
  const sources = B.inputs.map(i => i.lot);
  v.append(el('div', { class: 'summary-line' }, sl('Status', 'Completed'), sl('Date', B.batchDate || '—'), sl('Operators', B.operators || '—'),
    sl('Completed', B.completedAt ? fmtWhen(B.completedAt) + (B.completedBy ? ' by ' + B.completedBy : '') : '—')));
  const opt = (x, d, u) => x != null ? fmt(x, d) + u : '—';
  v.append(el('div', { class: 'summary-line' }, sl('Estimated weight', opt(B.shreddedKg, 1, ' kg')), sl('Start solids', opt(B.startSolidsPct, 2, ' %')),
    sl('Target solids', opt(B.targetSolidsPct, 2, ' %')), sl('Water added', opt(B.waterAddedL, 0, ' L')), sl('Blend volume', opt(B.blendVolumeL, 0, ' L')),
    sl('Final solids (calc.)', opt(B.finalSolidsPct, 2, ' %')), sl('pH', fmt(B.measuredPh, 1) + ' (target ' + fmt(B.targetPh, 1) + ')'), sl('Citric acid', fmt(B.citricKg, 2) + ' kg')));
  v.append(el('h3', {}, 'Source totes (coarse grind)'),
    table(['Lot', 'Site', 'Species', 'Harvest date', 'Weight tote (kg)', 'Volume tote (L)'],
      B.inputs.map(i => [mono(i.lot), siteName(i.site), speciesName(i.species), i.harvestDate || '—', fmt(i.weightKg, 1), i.volumeL != null ? fmt(i.volumeL, 0) : '—']), [false, false, false, false, true, true]));
  v.append(el('div', { class: 'page-head', style: 'margin-top:14px' }, el('h3', {}, 'Fine-grind output IBCs'),
    el('div', { class: 'actions' }, el('button', { onclick: () => printLabels(B.outputs.map(o => blendLabel(o, sources))) }, '🖨 Print all labels'))));
  v.append(table(['Lot', 'Volume (L)', 'Weight (kg)', '% solids', 'pH', 'Location', 'Status', ''],
    B.outputs.map(o => [mono(o.lot), fmt(o.volumeL, 0), fmt(o.avgWeightKg, 1), o.solidsPct != null ? fmt(o.solidsPct, 2) : '—', fmt(o.ph, 1), o.location || '—',
      badge(o.status, statusLabel(o.status)), rowActions([['Label', () => printLabels([blendLabel(o, sources)])]])]),
    [false, true, true, true, true, false, false, false]));
}

async function showPreprocTrace(t) {
  const r = await api('GET', '/totes/' + t.id + '/trace');
  if (!r.role) { toast(t.lot + ' has no pre-processing history.'); return; }
  const b = r.batch;
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Lot', t.lot), sl('Batch', b.batchLot), sl('Batch date', b.batchDate || '—'), sl('Status', b.status)),
    el('div', { class: 'help', style: 'margin:6px 0' }, r.role === 'output'
      ? t.lot + ' is a fine-grind blend made in batch ' + b.batchLot + ' from these coarse-grind totes:'
      : t.lot + ' was shredded in batch ' + b.batchLot + ' and now lives in these fine-grind lots:'),
    el('div', {}, el('b', {}, 'Source totes')),
    table(['Lot', 'Site', 'Species', 'Harvest date', 'Weight (kg)'], b.inputs.map(i => [mono(i.lot), siteName(i.site), speciesName(i.species), i.harvestDate || '—', fmt(i.weightKg, 1)]), [false, false, false, false, true]),
    el('div', { style: 'margin-top:8px' }, el('b', {}, 'Fine-grind lots')),
    table(['Lot', 'Volume (L)', 'Location', 'Status'], b.outputs.map(o => [mono(o.lot), fmt(o.volumeL, 0), o.location || '—', badge(o.status, statusLabel(o.status))]), [false, true, false, false]));
  modal('Traceability — ' + t.lot, body, async () => {}, 'Close', { noCancel: true, wide: true });
}

/* label data builders */
// Fine-grind blend (output of a Pre-Processing batch): its own label, with the batch and the source lots.
function blendLabel(t, sources) {
  const src = sources || t.sourceLots || [];
  return { kind: 'Fine-Grind Blend', lot: t.lot, barcode: t.lot, meta: [
    ['Grind', 'Fine'], ['Batch', t.batchLot || (t.lot || '').replace(/-\d+$/, '')],
    ['Source', src.length ? (src.length <= 3 ? src.join(', ') : src.length + ' lots') : '—'],
    ['Solids', t.solidsPct != null ? fmt(t.solidsPct, 1) + ' %' : '—'], ['pH', t.ph ?? '—'],
    ['Volume', t.volumeL != null ? fmt(t.volumeL, 0) + ' L' : '—'], ['Date', t.receivedDate || '—'], ['Loc', t.location || '—']] };
}
function toteLabel(t) {
  if (t.grind === 'Fine') return blendLabel(t);
  return { kind: 'Stabilized Tote', lot: t.lot, barcode: t.lot, meta: [
    ['Species', speciesName(t.species)], ['Site', t.site], ['Avg wt', fmt(t.avgWeightKg, 1) + ' kg'],
    ['pH', t.ph ?? '—'], ['Harvest date', t.harvestDate || '—'], ['Loc', t.location || '—']] };
}
function fgLabel(f, run) {
  return { kind: 'Finished Good — LKE', lot: f.lot, barcode: f.lot, meta: [
    ['Product', skuName(f.sku)], ['Pack', f.packageSize], ['Units', fmt(f.qty)],
    ['TDS', f.tds != null ? f.tds + '%' : '—'], ['Produced', f.producedDate || (run && run.runDate) || '—']] };
}
/* ---------------- Sample labels (50 mL falcon tube) ---------------- */
// A deliberately plain label: lot number, "sample point - description", collection date. No barcode, no logo.
// Sized 63.5 x 25.4 mm (2.5 x 1 in): it wraps about two thirds of a 50 mL conical tube; three across fit a Letter or A4 page.
const SAMPLE_LABEL_CSS = `
  .sample-label{box-sizing:border-box;width:63.5mm;height:25.4mm;padding:2mm 3mm;display:flex;flex-direction:column;justify-content:center;gap:1.2mm;
    font-family:Arial,Helvetica,sans-serif;color:#000;overflow:hidden;break-inside:avoid;border:.2mm dashed #999;background:#fff}
  .sample-label .sl-lot{font-weight:700;font-size:11.5pt;line-height:1.1;white-space:nowrap}
  .sample-label .sl-point{font-weight:600;font-size:8.5pt;line-height:1.15;max-height:2.3em;overflow:hidden}
  .sample-label .sl-when{font-size:8.5pt;line-height:1.1;white-space:nowrap}`;
const escHtml = x => String(x == null ? '' : x).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
function sampleLabelHtml(lb) {
  return '<div class="sample-label"><div class="sl-lot">' + escHtml(lb.lot) + '</div><div class="sl-point">' + escHtml(lb.point) + '</div><div class="sl-when">' + escHtml(lb.when) + '</div></div>';
}
function printSampleLabels(labels) {
  const w = window.open('', '_blank');
  if (!w) return toast('Allow pop-ups to print labels.', true);
  w.document.write('<!doctype html><html><head><title>Sample labels</title><style>@page{size:auto;margin:8mm}body{margin:0}'
    + '.sheet{display:grid;grid-template-columns:repeat(3,63.5mm);justify-content:start}' + SAMPLE_LABEL_CSS + '</style></head><body><div class="sheet">'
    + labels.map(sampleLabelHtml).join('') + '</div><script>window.onload=()=>{window.print();}<\/script></body></html>');
  w.document.close();
}
async function openSampleLabels(run) {
  const { lot, lotSimplified, points } = await api('GET', '/production/' + run.id + '/sample-labels');
  if (!points.length) return toast('No sample points are logged for this run yet — add them in the Process log (Sample Point boxes).', true);
  if (!document.getElementById('sampleLabelCss')) document.head.append(el('style', { id: 'sampleLabelCss' }, SAMPLE_LABEL_CSS));
  const when = x => (x ? String(x).replace('T', ' ').replace('Z', '').slice(0, 16) : '—');
  // One table row per sample point. Per row: the name printed on line 2, the label type (Detailed / Simplified), whether its labels are numbered
  // from 1, and how many labels to print (default = the point's sample count; more repeats the numbers, for spares).
  //   Detailed   : line 1 = the production run (PR-…-053),  line 2 = name - description,  line 3 = date and time
  //   Simplified : line 1 = "Lot-" + the run's last 3 digits (Lot-053), line 2 unchanged, line 3 = date only
  // Printing (or Save labels) also stores these settings on the sample point, so each sample's Sample ID Detailed / Simplified follows in every
  // table (catalogues, cart, requisitions).
  const rows = points.map(pt => {
    const on = el('input', { type: 'checkbox' }); on.checked = true;
    const type = selectFrom('', [['detailed', 'Detailed'], ['simplified', 'Simplified']]); type.value = pt.labelType; type.style.width = 'auto';
    const numbered = el('input', { type: 'checkbox', title: 'Number this point’s labels 1, 2, 3 …' }); numbered.checked = pt.numbered;
    const copies = el('input', { type: 'number', min: '1', max: '50', value: String(pt.qty), style: 'width:70px' });
    const name = el('input', { value: pt.labelName, maxlength: '60', placeholder: pt.defaultLabelName,
      title: 'The name printed on the label. Clear it to use the default (' + pt.defaultLabelName + ').' });
    return { pt, on, type, numbered, copies, name };
  });
  const remember = el('input', { type: 'checkbox' });
  const nameOf = r => r.name.value.trim() || r.pt.defaultLabelName;
  const labelsOf = r => {
    const c = Math.max(0, parseInt(r.copies.value, 10) || 0), simple = r.type.value === 'simplified';
    return Array.from({ length: c }, (_x, i) => ({ lot: (simple ? lotSimplified : lot) + (r.numbered.checked ? '-' + ((i % r.pt.qty) + 1) : ''),
      point: nameOf(r) + ' - ' + r.pt.description, when: simple ? when(r.pt.collectedAt).slice(0, 10) : when(r.pt.collectedAt) }));
  };
  const buildLabels = () => rows.flatMap(r => (r.on.checked ? labelsOf(r) : []));
  const preview = el('div', { style: 'display:flex;gap:10px;flex-wrap:wrap;margin-top:8px' });
  const totalNote = el('b', {});
  function sync() {
    preview.innerHTML = '';
    rows.filter(r => r.on.checked && labelsOf(r).length).forEach(r => { const d = el('div', {}); d.innerHTML = sampleLabelHtml(labelsOf(r)[0]); preview.append(d.firstChild); });
    if (!preview.children.length) preview.append(el('div', { class: 'help' }, 'No sample points ticked.'));
    const n = buildLabels().length;
    totalNote.textContent = n + ' label' + (n === 1 ? '' : 's') + ' will print';
  }
  rows.forEach(r => ['change', 'input'].forEach(ev => [r.on, r.type, r.numbered, r.copies, r.name].forEach(c => c.addEventListener(ev, sync))));
  const allCb = el('input', { type: 'checkbox', onchange: () => { rows.forEach(r => { r.on.checked = allCb.checked; }); sync(); } }); allCb.checked = true;
  const tbl = el('div', { class: 'tablewrap' }, el('table', {}, el('thead', {}, el('tr', {}, el('th', { class: 'checkcol' }, allCb),
    ...['Sample point', 'Name on label', 'Label', 'Number', 'Description', 'Collected', 'Labels'].map(h => el('th', {}, h)))),
    el('tbody', {}, ...rows.map(r => el('tr', {}, el('td', { class: 'checkcol' }, r.on), el('td', {}, r.pt.stageLabel, r.pt.lockedCount ? el('div', { class: 'help', title: 'These samples are already on a lab requisition, so their IDs are locked: changes here only affect the labels you print and the samples not yet sent.' },
        '🔒 ' + r.pt.lockedCount + ' on a requisition') : null), el('td', {}, r.name), el('td', {}, r.type),
      el('td', { class: 'checkcol' }, r.numbered), el('td', {}, r.pt.description + (r.pt.type ? ' · ' + r.pt.type : '')), el('td', {}, when(r.pt.collectedAt)), el('td', {}, r.copies))))));
  sync();
  const option = (cb, title, hint) => {
    const row = el('label', { class: 'perm-item' + (cb.checked ? ' on' : '') }, cb, el('span', { class: 'perm-text' }, el('b', {}, title), el('small', {}, hint)));
    cb.addEventListener('change', () => row.classList.toggle('on', cb.checked));
    return row;
  };
  const section = (title, ...kids) => el('div', { class: 'perm-box' }, el('div', { class: 'perm-title' }, title), ...kids);
  async function saveSettings() {
    await api('PUT', '/production/' + run.id + '/sample-label-settings', { points: rows.map(r => ({ id: r.pt.id, labelType: r.type.value, numbered: r.numbered.checked, labelName: nameOf(r) })) });
    if (remember.checked) {
      const names = {};
      rows.forEach(r => { if (!(r.pt.stage in names)) names[r.pt.stage] = r.name.value.trim(); });
      await api('PUT', '/sample-label-names', { names });
    }
  }
  const body = el('div', {}, el('div', { class: 'summary-line' }, sl('Run', lot), sl('Sample points', String(points.length))),
    section('Sample points', tbl,
      el('div', { class: 'help', style: 'margin-top:6px' }, el('b', {}, 'Detailed'), ' = run number, name, date and time · ', el('b', {}, 'Simplified'), ' = “' + lotSimplified + '”, name, date only. ',
        'Tick “Number” to count a point’s labels from 1 (' + lot + '-1, ' + lotSimplified + '-1 …). Change “Labels” for spares. Saving the labels updates each sample’s Sample ID Detailed / Simplified in the catalogues — except samples already on a requisition (🔒), whose IDs are locked.')),
    section('Options', el('div', { class: 'perm-list' },
      option(remember, 'Save these names as the default', 'Use the names above on every run from now on (the Packaging point is “Finished Product” by default).'))),
    section('Preview', el('div', { class: 'help' }, 'The first label of each sample point · ', totalNote, ' · 63.5 × 25.4 mm (2.5 × 1 in), sized to wrap a 50 mL falcon tube.'), preview));
  modal('Print sample labels — ' + lot, body, async () => {
    const out = buildLabels();
    if (!out.length) throw new Error('Tick at least one sample point with one or more labels.');
    await saveSettings();
    printSampleLabels(out);
  }, 'Print labels', { wide: true, extraLabel: 'Save labels', onExtra: async () => { await saveSettings(); toast('Sample label settings saved'); render(); } });
}

function printLabels(labels) {
  const w = window.open('', '_blank');
  if (!w) return toast('Allow pop-ups to print labels.', true);
  const css = `body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:12px;}
  .labels-sheet{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;}
  .kelp-label{border:1px solid #000;border-radius:6px;padding:10px 12px;display:flex;flex-direction:column;gap:4px;break-inside:avoid;}
  .ll-top{display:flex;justify-content:space-between;align-items:baseline;}
  .ll-co{font-weight:700;font-size:12px;letter-spacing:.5px;}
  .ll-kind{font-size:10px;color:#333;text-transform:uppercase;letter-spacing:1px;}
  .ll-lot{font-family:ui-monospace,Menlo,Consolas,monospace;font-weight:700;font-size:15px;}
  .ll-meta{font-size:11px;display:flex;gap:12px;flex-wrap:wrap;}
  .kelp-label svg{width:100%;height:46px;} .ll-human{text-align:center;font-family:ui-monospace,monospace;font-size:11px;letter-spacing:1px;}`;
  const logo = location.origin + '/logo.png';
  const html = labels.map(lb => `<div class="kelp-label">
    <div class="ll-top"><span class="ll-co"><img src="${logo}" alt="" style="height:16px;width:auto;margin-right:5px;vertical-align:middle">CASCADIA SEAWEED</span><span class="ll-kind">${lb.kind}</span></div>
    <div class="ll-lot">${lb.lot}</div>
    <div class="ll-meta">${lb.meta.map(([k, v]) => `<span><b>${k}:</b> ${v}</span>`).join('')}</div>
    ${code128SVG(lb.barcode)}<div class="ll-human">${lb.barcode}</div></div>`).join('');
  w.document.write(`<!doctype html><html><head><title>KelpWorks labels</title><style>${css}</style></head><body><div class="labels-sheet">${html}</div><script>window.onload=()=>{window.print();}<\/script></body></html>`);
  w.document.close();
}

/* ---------------- shared UI bits ---------------- */
function table(headers, rows, numCols = [], rowClick = null) {
  const colClass = i => numCols[i] === 'center' ? 'ctr' : numCols[i] ? 'num' : '';
  const thead = el('thead', {}, el('tr', {}, ...headers.map((h, i) => el('th', { class: colClass(i) }, h))));
  const tb = el('tbody', {});
  if (!rows.length) tb.append(el('tr', {}, el('td', { colspan: headers.length, class: 'empty' }, 'Nothing here yet.')));
  rows.forEach((r, ri) => {
    // a row of the form { group: node } is a full-width group heading (not a data row; it isn't clickable)
    // (or { groupCells: [...] }: a group heading that also carries column titles, one cell per column)
    if (r && !Array.isArray(r) && r.group != null) { tb.append(el('tr', { class: 'group-row' }, el('td', { colspan: headers.length }, r.group))); return; }
    if (r && !Array.isArray(r) && r.groupCells) { tb.append(el('tr', { class: 'group-row' }, ...r.groupCells.map((c, i) => el('td', { class: colClass(i) }, c || '')))); return; }
    const cells = r.map((c, i) => el('td', { class: colClass(i) }, c == null ? '—' : (c.nodeType ? c : String(c))));
    tb.append(el('tr', rowClick ? { class: 'clickable', onclick: () => rowClick(ri) } : {}, ...cells));
  });
  return el('div', { class: 'tablewrap' }, el('table', {}, thead, tb));
}
function mono(s) { return el('span', { class: 'mono' }, s); }
function badge(cls, txt) { return el('span', { class: 'badge ' + cls }, txt); }
function num(s) { return el('span', { class: 'num' }, s); }
function rowActions(items) {
  return el('span', { class: 'row-actions' }, ...items.filter(Boolean).map(([label, fn, cls]) => el('button', {
    class: (cls || 'secondary') + ' ',
    // Stops a row-action button from also firing the row's own onclick (e.g.
    // a table's rowClick, used elsewhere to open a row's history) when both
    // are present on the same row.
    onclick: e => { e.stopPropagation(); return fn(e); }
  }, label)));
}
function field(label, control) { return el('div', { class: 'field' }, label ? el('label', {}, label) : null, control); }
function selectFrom(label, opts, onchange, id) {
  const s = el('select', id ? { id } : {}, ...opts.map(([v, t]) => el('option', { value: v }, t)));
  if (onchange) s.addEventListener('change', onchange);
  return s;
}
function editableSelect(opts, id) {
  // free-text input with known options offered via a datalist
  const list = 'dl_' + id;
  const inp = el('input', { id, list, placeholder: 'type or pick…' });
  const dl = el('datalist', { id: list }, ...opts.map(([v]) => el('option', { value: v })));
  return el('span', { style: 'display:block' }, inp, dl);
}

/* ---------------- Modal ---------------- */
function modal(title, body, onSubmit, submitLabel = 'Save', opts = {}) {
  const errBox = el('div', { class: 'error' });
  const submitBtn = el('button', {}, submitLabel);
  const actions = el('div', { class: 'modal-actions' });
  if (!opts.noCancel) actions.append(el('button', { class: 'secondary', onclick: close }, 'Cancel'));
  // Optional secondary action (e.g. "Save & close") that runs its own handler
  // and closes the modal on success, same as submit but without finalizing.
  let extraBtn = null;
  if (opts.extraLabel && opts.onExtra) {
    extraBtn = el('button', { class: 'secondary', onclick: runExtra }, opts.extraLabel);
    actions.append(extraBtn);
  }
  actions.append(submitBtn);
  const card = el('div', { class: 'modal' + (opts.wide ? ' wide' : '') },
    opts.closeX ? el('button', { type: 'button', class: 'modal-close-x', 'aria-label': 'Close', title: 'Close', onclick: () => { close(); if (opts.onClose) opts.onClose(); } }, '×') : null,
    el('h3', {}, title), body, errBox, actions);
  // Backdrop clicks do NOT close the dialog — only Cancel or completing the
  // action does, so a stray click off the popup can't discard your input.
  const bg = el('div', { class: 'modal-bg' }, card);
  function close() { bg.remove(); }
  async function runExtra() {
    errBox.textContent = ''; submitBtn.disabled = true; extraBtn.disabled = true;
    try { await opts.onExtra(); close(); }
    catch (e) { errBox.textContent = e.message; submitBtn.disabled = false; extraBtn.disabled = false; }
  }
  submitBtn.addEventListener('click', async () => {
    errBox.textContent = ''; submitBtn.disabled = true; if (extraBtn) extraBtn.disabled = true;
    try { await onSubmit(); close(); }
    catch (e) { errBox.textContent = e.message; submitBtn.disabled = false; if (extraBtn) extraBtn.disabled = false; }
  });
  $('#modalRoot').append(bg);
}
function changePasswordModal(forced) {
  const body = el('div', {},
    forced ? el('div', { class: 'summary-line' }, 'For security, please set a new password before continuing.') : null,
    field('Current password', el('input', { type: 'password', id: 'pw_cur', autocomplete: 'current-password' })),
    el('div', { class: 'form-row' },
      field('New password', el('input', { type: 'password', id: 'pw_new', autocomplete: 'new-password' })),
      field('Confirm new password', el('input', { type: 'password', id: 'pw_conf', autocomplete: 'new-password' }))),
    el('div', { class: 'help' }, 'At least 8 characters.'));
  modal('Change password', body, async () => {
    const cur = body.querySelector('#pw_cur').value, nw = body.querySelector('#pw_new').value, cf = body.querySelector('#pw_conf').value;
    if (nw.length < 8) throw new Error('New password must be at least 8 characters.');
    if (nw !== cf) throw new Error('New passwords do not match.');
    await api('POST', '/me/password', { currentPassword: cur, newPassword: nw });
    State.user.mustChange = false;
    toast('Password updated');
  }, forced ? 'Set password' : 'Update', forced ? { noCancel: true, noBackdropClose: true } : {});
}

/* ---------------- Admin ---------------- */
async function pageAdmin(v) {
  v.append(el('div', { class: 'page-head' }, el('h2', {}, 'Admin — Users'),
    el('div', { class: 'actions' },
      el('button', { class: 'secondary', onclick: openIntegrityCheck }, 'Data integrity check'),
      el('button', { class: 'secondary', onclick: downloadDbBackup }, '⬇ Download database backup'),
      el('button', { onclick: addUser }, '+ Add user'))));
  const r = await api('GET', '/users');
  v.append(table(
    ['Name', 'Email', 'Role', 'Permissions', 'Status', 'Actions'],
    r.users.map(u => [
      u.name, mono(u.email),
      badge(u.role === 'admin' ? 'hold' : 'on_hand', u.role === 'admin' ? 'Admin' : 'User'),
      el('span', {}, u.isProductionManager ? badge('wip', 'Production Mgr') : null, ' ',
        u.isQualityManager ? badge('on_hand', 'Quality Mgr') : null, ' ',
        u.canAmendLog ? badge('hold', 'Log Amender') : null,
        !u.isProductionManager && !u.isQualityManager && !u.canAmendLog ? el('span', { class: 'muted' }, '—') : null),
      u.active ? badge('on_hand', u.mustChange ? 'Must reset' : 'Active') : badge('disposed', 'Inactive'),
      rowActions([
        ['Reset password', () => resetUserPassword(u)],
        ['Edit', () => editUser(u)],
        u.active ? ['Deactivate', () => setUserActive(u, false), 'danger'] : ['Activate', () => setUserActive(u, true)]
      ])
    ]), [false, false, false, false, false, false]));
  v.append(el('div', { class: 'help', style: 'margin-top:10px' },
    'New users and password resets require the person to set a new password on next sign-in. '
    + 'Permissions: only users flagged Production Manager / Quality Manager can sign product-release steps, and only users flagged Production Log Amender can amend a finalized production log (being an administrator does not grant any of them); changes to these flags are logged.'));

  // SOP documents: controlled documents a production-log QC Check links to
  // by a stable reference key (never the display name, so a rename here is
  // reflected everywhere that link appears without breaking it) -- admin-only
  // to add/edit/delete, but the list (and download) is readable by anyone
  // signed in. Clicking a row (not its action buttons) opens its edit history.
  v.append(el('div', { class: 'page-head', style: 'margin-top:28px' }, el('h2', {}, 'SOP Documents'),
    el('div', { class: 'actions' }, el('button', { onclick: addSop }, '+ Add SOP document'))));
  const sr = await api('GET', '/sop-documents');
  if (!sr.sops.length) v.append(el('div', { class: 'empty card' }, 'No SOP documents yet.'));
  else v.append(table(
    ['Name', 'File', 'Uploaded', ''],
    sr.sops.map(s => [
      s.name,
      s.hasFile ? el('a', { href: sopDownloadUrl(s.id, false), target: '_blank', rel: 'noopener' }, s.filename || 'Download')
        : el('span', { class: 'muted' }, 'Not yet uploaded'),
      s.uploadedAt ? fmtWhen(s.uploadedAt) + (s.uploadedBy ? ' · ' + s.uploadedBy : '') : '—',
      rowActions([
        ['Edit', () => editSop(s)],
        ['Delete', () => deleteSop(s), 'danger'],
      ])
    ]), [false, false, false, false], ri => showSopHistory(sr.sops[ri])));
  v.append(el('div', { class: 'help', style: 'margin-top:10px' },
    'A QC Check in the production log links to an SOP by its reference key, not its name — renaming a document here is picked up everywhere it’s linked. Click a row to see its change history.'));
  await drawAdminLabs(v);
  await drawAdminRequisitionContact(v);
  await drawAdminCoaSpecs(v);
}
/* ---------------- Certificate of Analysis: lab results ---------------- */
const COA_BADGE = { pass: ['on_hand', 'PASS'], fail: ['low', 'FAIL'], review: ['pending_release', 'REVIEW'], not_evaluated: ['pending_release', 'N/E'] };
function coaStatusBadge(row) {
  return COA_BADGE[row.status] ? badge(...COA_BADGE[row.status]) : badge('sold', row.required ? 'PENDING' : 'Not tested');
}
function labBadge(l) {
  if (!l) return '—';
  if (l.missingRequired.length) return badge('low', 'Micro results missing');
  if (l.failed.length) return badge('low', 'Outside spec');
  if (l.review.length) return badge('pending_release', 'Review');
  return badge('on_hand', 'Complete');
}
function labSummaryText(l) {
  return 'Required (microbial) ' + l.requiredReceived + '/' + l.requiredTotal + ' · Heavy metals ' + l.metalsReceived + '/' + l.metalsTotal
    + (l.additionalCount ? ' · ' + l.additionalCount + ' additional' : '') + (l.review.length ? ' · ' + l.review.length + ' to review' : '');
}
async function openLabResults(run) {
  const body = el('div', {});
  let changed = false;
  async function draw() {
    const d = await api('GET', '/production/' + run.id + '/lab-results');
    const s = d.coa.summary;
    body.innerHTML = '';
    // the action bar is part of every redraw (a save or void redraws the window, so it must come back each time)
    body.append(el('div', { style: 'margin-bottom:8px;display:flex;gap:8px;flex-wrap:wrap' },
      el('button', { type: 'button', onclick: () => addLabReport(run, d, async () => { changed = true; await draw(); }) }, '+ Add lab report'),
      el('button', { type: 'button', class: 'secondary', onclick: () => window.open(coaPdfUrl(run.id, false), '_blank') }, 'View Certificate of Analysis'),
      el('button', { type: 'button', class: 'secondary', onclick: () => openAttachments(run) }, '📎 Documents')));
    body.append(el('div', { class: 'summary-line' }, sl('Run', run.processingLot), sl('Required', s.requiredReceived + ' of ' + s.requiredTotal),
      sl('Heavy metals', s.metalsReceived + ' of ' + s.metalsTotal), sl('Outside spec', s.failed.length ? s.failed.join(', ') : 'none')));
    if (s.missingRequired.length) body.append(el('div', { class: 'help', style: 'color:var(--danger);font-weight:600' },
      'Quality cannot release this run until these results are on file: ' + s.missingRequired.join(', ') + '.'));
    if (d.coa.rows.some(x => x.group === 'metals' && x.status === 'not_evaluated')) body.append(el('div', { class: 'help' },
      'Heavy-metal results are listed but not judged: the product application rate has not been set. An administrator sets it (with the application periods) on the Calculations page.'));
    else if (s.applicationRate && s.metalsReceived) body.append(el('div', { class: 'help' },
      'Metal loading (kg/ha) = result (mg/kg) × application rate (' + s.applicationRate + ' kg/ha) × application periods (' + s.applicationPeriods + ') ÷ 1,000,000.'));
    const rows = [];
    for (const [g, gname] of [['physical', 'Physical & chemical'], ['metals', 'Heavy metals'], ['microbial', 'Microbiological']]) {
      const grp = d.coa.rows.filter(x => x.group === g);
      if (!grp.length) continue;
      // the Loading (kg/ha) title sits in the Heavy metals heading row: only the metals have a loading
      rows.push(g === 'metals' ? { groupCells: [el('b', {}, gname), '', '', el('b', {}, 'Loading (kg/ha)'), '', ''] } : { group: el('b', {}, gname) });
      grp.forEach(x => rows.push([el('span', {}, x.name + (x.required ? ' *' : ''), x.listed ? null : el('span', { class: 'muted', style: 'font-size:11px', title: 'Shown here only; not printed on the Certificate of Analysis' }, '  · not on certificate')), x.specText, x.resultText || '—', g === 'metals' ? (x.loadingText || '—') : '',
        x.result ? [x.result.labName, x.result.reportNumber].filter(Boolean).join(' · ') : (x.source || '—'), coaStatusBadge(x)]));
    }
    if (d.coa.additional.length) {
      rows.push({ group: el('b', {}, 'Additional analyses') });
      d.coa.additional.forEach(x => rows.push([x.analyte, '—', x.resultText + (x.unit ? ' ' + x.unit : ''), '', [x.labName, x.reportNumber].filter(Boolean).join(' · '), '—']));
    }
    body.append(table(['Test', 'Specification', 'Result', '', 'Laboratory / report', 'Status'], rows, [false, false, false, 'center', false, false]));
    body.append(el('div', { class: 'help' }, '* required before Quality can release the lot. TDS and pH come from the Packaging QC check in the production log.'));
    const hist = d.results;
    body.append(el('details', { class: 'accordion', style: 'margin-top:10px' }, el('summary', {}, 'Entry history (' + hist.length + ')'),
      el('div', { class: 'accordion-body' }, hist.length ? table(['Entered', 'Test', 'Result', 'Report', 'By', ''],
        hist.map(x => [fmtWhen(x.enteredAt), x.analyte, x.voidedAt ? el('s', {}, x.resultText) : x.resultText,
          [x.labName, x.reportNumber, x.reportDate].filter(Boolean).join(' · '),
          x.enteredBy || '—', x.voidedAt ? el('span', { class: 'help' }, 'Voided by ' + (x.voidedBy || '—') + ': ' + (x.voidReason || ''))
            : rowActions([['Void', () => voidLabResult(x)]])]), [false, false, false, false, false, false])
        : el('div', { class: 'help' }, 'Nothing entered yet.'))));
  }
  function voidLabResult(x) {
    const reason = el('input', { placeholder: 'Why is this result being voided?' });
    modal('Void ' + x.analyte + ' result', el('div', {}, el('div', { class: 'help' }, x.resultText + ' · ' + (x.reportNumber || '')), field('Reason', reason),
      el('div', { class: 'help' }, 'A voided result stays in the history and the audit trail; enter the corrected result as a new entry.')),
      async () => { await api('POST', '/production/' + run.id + '/lab-results/' + x.id + '/void', { reason: reason.value }); changed = true; await draw(); toast('Result voided'); }, 'Void result');
  }
  await draw();
  modal('Lab results — ' + run.processingLot, body, async () => { if (changed && State.tab === 'release') render(); }, 'Done', { noCancel: true, wide: true });
}
async function addLabReport(run, d, done) {
  const [{ labs }, { attachments }] = await Promise.all([api('GET', '/labs'), api('GET', '/production/' + run.id + '/attachments')]);
  const labSel = selectFrom('', [['', '— pick the laboratory —'], ...labs.filter(l => l.active).map(l => [String(l.id), l.name]), ['other', 'Other…']]);
  const labOther = el('input', { placeholder: 'Laboratory name', style: 'display:none;margin-top:4px' });
  labSel.addEventListener('change', () => { labOther.style.display = labSel.value === 'other' ? '' : 'none'; });
  const rep = el('input', { placeholder: 'e.g. 26-AU-223.18A or VR26-05008.007' });
  const rdate = el('input', { type: 'date' });
  const sref = el('input', { placeholder: 'Lab’s own sample ID (optional)' });
  const attSel = selectFrom('', [['', '— none —'], ...attachments.map(a => [String(a.id), a.filename])]);
  const scanMsg = el('div', { class: 'help' });
  const pdfIn = el('input', { type: 'file', accept: '.pdf,application/pdf' });
  const specs = d.specs;
  const tbody = el('tbody', {});
  const lines = [];
  function addLine(code, value, extra) {
    const testSel = selectFrom('', [['', '— test —'], ...['microbial', 'metals', 'physical'].flatMap(g => specs.filter(x => x.group === g).map(x => [x.code, x.name])), ['__other', 'Other (additional analysis)…']]);
    const nameInp = el('input', { placeholder: 'Test name', style: 'display:none;margin-top:4px' });
    const valInp = el('input', { placeholder: '<20, 100, Negative', style: 'width:120px' });
    const unitSel = el('select', { style: 'display:none;width:80px' }, ...d.metalUnits.map(u => el('option', { value: u }, u)));
    const unitTxt = el('span', { class: 'help' });
    const unitFree = el('input', { placeholder: 'unit', style: 'display:none;width:80px' });
    const methInp = el('input', { placeholder: 'method', style: 'width:110px' });
    const line = { testSel, nameInp, valInp, unitSel, unitFree, methInp };
    function sync() {
      const sp = specs.find(x => x.code === testSel.value);
      nameInp.style.display = testSel.value === '__other' ? '' : 'none';
      unitSel.style.display = sp && sp.basis === 'metal' ? '' : 'none';
      unitFree.style.display = testSel.value === '__other' ? '' : 'none';
      unitTxt.textContent = sp && sp.basis !== 'metal' ? sp.unit : '';
      if (sp && !methInp.value) methInp.value = sp.method || '';
      if (sp) methInp.placeholder = sp.method || 'method';
    }
    testSel.addEventListener('change', () => { methInp.value = ''; sync(); });
    if (code) testSel.value = code;
    if (value) valInp.value = value;
    if (extra && extra.other) { testSel.value = '__other'; nameInp.value = extra.other; unitFree.value = extra.unit || ''; }
    sync();
    if (extra && extra.method) methInp.value = extra.method;
    if (extra && extra.unit && [...unitSel.options].some(o => o.value.toLowerCase() === extra.unit.toLowerCase())) {
      unitSel.value = [...unitSel.options].find(o => o.value.toLowerCase() === extra.unit.toLowerCase()).value;
    }
    lines.push(line);
    const tr = el('tr', {}, el('td', {}, testSel, nameInp), el('td', {}, valInp), el('td', {}, unitSel, unitTxt, unitFree), el('td', {}, methInp),
      el('td', {}, el('button', { type: 'button', class: 'secondary', onclick: () => { lines.splice(lines.indexOf(line), 1); tr.remove(); } }, '×')));
    tbody.append(tr);
  }
  // Read the chosen PDF and fill the form from it (header fields only where still empty; the result rows are replaced).
  async function scanReport(attId) {
    scanMsg.style.color = ''; scanMsg.textContent = 'Reading the report…';
    try {
      const r = await api('POST', '/production/' + run.id + '/lab-results/scan', { attachmentId: Number(attId) });
      const lab = labs.find(l => r.lab && l.name.toLowerCase().replace(/\s/g, '').includes(r.lab.toLowerCase().replace(/\s/g, '')));
      if (!labSel.value) {
        if (lab) labSel.value = String(lab.id);
        else if (r.lab) { labSel.value = 'other'; labOther.value = r.lab; }
        labSel.dispatchEvent(new Event('change'));
      }
      if (!rep.value && r.reportNumber) rep.value = r.reportNumber;
      if (!rdate.value && r.reportDate) rdate.value = r.reportDate;
      if (!sref.value && r.sampleRef) sref.value = r.sampleRef;
      lines.length = 0; tbody.innerHTML = '';
      r.rows.forEach(x => x.specCode && specs.some(sp => sp.code === x.specCode)
        ? addLine(x.specCode, x.value, { unit: x.unit, method: x.method })
        : addLine(null, x.value, { other: x.analyte || x.specCode, unit: x.unit, method: x.method }));
      scanMsg.textContent = 'Read ' + r.rows.length + ' result' + (r.rows.length === 1 ? '' : 's') + ' from the report'
        + (r.warnings.length ? ' — ' + r.warnings.join(' ') : '') + '. Check every value against the report before saving.';
    } catch (e) { scanMsg.style.color = 'var(--danger)'; scanMsg.textContent = e.message; }
  }
  attSel.addEventListener('change', () => { if (attSel.value) scanReport(attSel.value); });
  pdfIn.addEventListener('change', async () => {
    const f = pdfIn.files[0]; if (!f) return;
    scanMsg.style.color = ''; scanMsg.textContent = 'Uploading ' + f.name + '…';
    try {
      const resp = await uploadAtt(run.id, f);
      const a = resp.attachments.reduce((m, x) => (x.id > m.id ? x : m), resp.attachments[0]);
      attSel.append(el('option', { value: String(a.id) }, a.filename)); attSel.value = String(a.id);
      pdfIn.value = '';
      await scanReport(a.id);
    } catch (e) { scanMsg.style.color = 'var(--danger)'; scanMsg.textContent = e.message; }
  });
  function addPanel(codes) { codes.forEach(c => { if (specs.some(x => x.code === c) && !lines.some(l => l.testSel.value === c)) addLine(c); }); }
  const panelBar = el('div', { style: 'display:flex;gap:8px;flex-wrap:wrap;margin:8px 0' },
    el('button', { type: 'button', class: 'secondary', onclick: () => addPanel(['apc', 'yeast', 'mold']) }, '+ APC / Yeast / Mold'),
    el('button', { type: 'button', class: 'secondary', onclick: () => addPanel(['fecal', 'salm']) }, '+ Fecal coliforms / Salmonella'),
    el('button', { type: 'button', class: 'secondary', onclick: () => addPanel(specs.filter(x => x.group === 'metals').map(x => x.code)) }, '+ Heavy-metals panel'),
    el('button', { type: 'button', class: 'secondary', onclick: () => addLine() }, '+ Other test'));
  const body = el('div', {},
    el('div', { class: 'sign-box' }, el('h4', {}, 'Lab report PDF'),
      el('div', { class: 'form-row' }, field('Upload the report', pdfIn), field('…or pick one already attached', attSel)),
      scanMsg, el('div', { class: 'help' }, 'The report is read automatically and the fields below are filled in — you review and correct them before saving.')),
    el('div', { class: 'form-row' }, field('Laboratory', el('div', {}, labSel, labOther)), field('Report number', rep), field('Report date', rdate)),
    el('div', { class: 'form-row' }, field('Lab sample ID', sref)),
    panelBar,
    el('div', { class: 'tablewrap' }, el('table', {}, el('thead', {}, el('tr', {}, ...['Test', 'Result', 'Unit', 'Method', ''].map(h => el('th', {}, h)))), tbody)),
    el('div', { class: 'help' }, 'Enter results exactly as the lab reports them: a number, a “<” value for below detection (e.g. <20), or Negative / Positive. Microbial results are per gram (Salmonella per 25 g). '
      + 'Other tests on the same form are additional analyses and appear on the certificate without a specification.'));
  modal('Add lab report — ' + run.processingLot, body, async () => {
    const labName = labSel.value === 'other' ? labOther.value : '';
    const results = lines.filter(l => l.testSel.value).map(l => {
      const other = l.testSel.value === '__other';
      return { specCode: other ? null : l.testSel.value, analyte: other ? l.nameInp.value : null, value: l.valInp.value,
        unit: other ? l.unitFree.value : (l.unitSel.style.display === 'none' ? null : l.unitSel.value), method: l.methInp.value };
    });
    await api('POST', '/production/' + run.id + '/lab-results', { labId: labSel.value && labSel.value !== 'other' ? Number(labSel.value) : null, labName,
      reportNumber: rep.value, reportDate: rdate.value, sampleRef: sref.value, attachmentId: attSel.value ? Number(attSel.value) : null, results });
    toast('Lab results saved');
    await done();
  }, 'Save results', { wide: true });
  addPanel(['apc', 'yeast', 'mold']);
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    if (file.size > 25 * 1024 * 1024) return reject(new Error(file.name + ' exceeds the 25 MB limit'));
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.onerror = () => reject(new Error('Could not read ' + file.name));
    reader.readAsDataURL(file);
  });
}
function addSop() {
  const nameInp = el('input', { id: 's_name', placeholder: 'e.g. Determining % Wet Solids SOP' });
  const keyInp = el('input', { id: 's_key', placeholder: 'e.g. wet_solids_sop' });
  const fileInp = el('input', { type: 'file', id: 's_file' });
  const body = el('div', {},
    field('Document name', nameInp),
    field('Reference key (optional)', keyInp),
    el('div', { class: 'help', style: 'margin-top:-8px' },
      'Only needed if a spot in the production log will link to this document — set once and can’t be changed later, so the link keeps working even if the name above is edited afterward.'),
    field('File (optional — can be added later)', fileInp));
  modal('Add SOP document', body, async () => {
    const name = nameInp.value.trim();
    if (!name) throw new Error('Enter a document name.');
    const file = fileInp.files[0];
    const payload = { name, key: keyInp.value.trim() || null };
    if (file) {
      payload.filename = file.name; payload.contentType = file.type || 'application/octet-stream';
      payload.dataB64 = await readFileAsBase64(file);
    }
    await api('POST', '/sop-documents', payload);
    State.ref = await api('GET', '/refdata');
    toast('SOP document added'); render();
  }, 'Add');
}
function editSop(s) {
  const nameInp = el('input', { id: 'es_name', value: s.name });
  const fileInp = el('input', { type: 'file', id: 'es_file' });
  const body = el('div', {},
    field('Document name', nameInp),
    s.key ? el('div', { class: 'help', style: 'margin-top:-8px' }, 'Reference key: ' + s.key + ' (fixed)') : null,
    field(s.hasFile ? 'Replace file (optional)' : 'Upload file', fileInp),
    s.hasFile ? el('div', { class: 'help' }, 'Current file: ' + (s.filename || '—')) : null);
  modal('Edit SOP document', body, async () => {
    const name = nameInp.value.trim();
    if (!name) throw new Error('Enter a document name.');
    const file = fileInp.files[0];
    const payload = { name };
    if (file) {
      payload.filename = file.name; payload.contentType = file.type || 'application/octet-stream';
      payload.dataB64 = await readFileAsBase64(file);
    }
    await api('PUT', '/sop-documents/' + s.id, payload);
    State.ref = await api('GET', '/refdata');
    toast('SOP document updated'); render();
  }, 'Save');
}
async function deleteSop(s) {
  if (!confirm('Delete "' + s.name + '"? This cannot be undone, and any QC Check linking to it will show it as not configured.')) return;
  await api('DELETE', '/sop-documents/' + s.id);
  State.ref = await api('GET', '/refdata');
  toast('SOP document deleted'); render();
}
async function showSopHistory(s) {
  const r = await api('GET', '/sop-documents/' + s.id + '/edits');
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Document', s.name)),
    !r.edits.length ? el('div', { class: 'help' }, 'No changes logged yet.') :
      el('div', { class: 'tablewrap', style: 'margin-top:6px' }, el('table', {},
        el('thead', {}, el('tr', {}, el('th', {}, 'When'), el('th', {}, 'Field'), el('th', {}, 'From'), el('th', {}, 'To'), el('th', {}, 'By'))),
        el('tbody', {}, ...r.edits.map(e => el('tr', {},
          el('td', { class: 'muted' }, fmtWhen(e.at)), el('td', {}, e.field),
          el('td', { class: 'muted' }, e.oldValue ?? '—'), el('td', {}, el('b', {}, e.newValue ?? '—')),
          el('td', {}, e.by || '—')))))));
  modal('Change history — ' + s.name, body, async () => {}, 'Close', { noCancel: true });
}
// Downloads a full, consistent snapshot of the live database (sqlite3's
// backup API server-side, not a raw file copy) straight to the browser —
// an off-server copy, since a backup sitting on the same disk as the
// original doesn't help if that disk is lost.
function downloadDbBackup() {
  const ts = new Date().toISOString().replace(/[:T]/g, '-').slice(0, 19);
  const url = '/api/admin/backup?token=' + encodeURIComponent(State.token);
  const a = el('a', { href: url, download: 'kelpworks-backup-' + ts + '.db' });
  document.body.append(a); a.click(); a.remove();
  toast('Backup downloading…');
}
// Permissions checklist for Add / Edit user: one tidy row per permission (checkbox, name,
// plain-language description). Admin role alone grants none of these.
const USER_PERMISSIONS = [
  ['pm', 'isProductionManager', 'Production Manager', 'Reviews and signs off finalized production logs (Product Release).'],
  ['qm', 'isQualityManager', 'Quality Manager', 'Reviews logs, releases or rejects finished goods for sale, and can run the data integrity check.'],
  ['am', 'canAmendLog', 'Production Log Amender', 'Can amend a finalized run’s production log (open an amendment, edit it, submit it for re-review).'],
];
function permissionChecklist(prefix, u) {
  const list = el('div', { class: 'perm-list' }, ...USER_PERMISSIONS.map(([id, key, name, desc]) => {
    const cb = el('input', { type: 'checkbox', id: prefix + '_' + id });
    cb.checked = !!(u && u[key]);
    const row = el('label', { class: 'perm-item' + (cb.checked ? ' on' : '') }, cb,
      el('span', { class: 'perm-text' }, el('b', {}, name), el('small', {}, desc)));
    cb.addEventListener('change', () => row.classList.toggle('on', cb.checked));
    return row;
  }));
  return el('div', { class: 'perm-box' }, el('div', { class: 'perm-title' }, 'Permissions'), list,
    el('div', { class: 'perm-note' }, 'Being an administrator does not grant any of these. Every change is logged, and signatures re-ask for the signer’s password.'));
}
const permissionValues = (body, prefix) => Object.fromEntries(USER_PERMISSIONS.map(([id, key]) => [key, body.querySelector('#' + prefix + '_' + id).checked]));
function addUser() {
  const body = el('div', {},
    el('div', { class: 'form-row' }, field('Name', el('input', { id: 'u_name' })),
      field('Email', el('input', { id: 'u_email', type: 'email' }))),
    el('div', { class: 'form-row' }, field('Temporary password', el('input', { id: 'u_pw', value: 'Cascadia123!' })),
      field('Role', selectFrom('', [['user', 'User'], ['admin', 'Administrator']], null, 'u_role'))),
    permissionChecklist('u'),
    el('div', { class: 'help' }, 'They’ll be required to change this password on first sign-in.'));
  modal('Add user', body, async () => {
    await api('POST', '/users', { name: body.querySelector('#u_name').value, email: body.querySelector('#u_email').value, password: body.querySelector('#u_pw').value, role: body.querySelector('#u_role').value,
      ...permissionValues(body, 'u') });
    toast('User created'); render();
  }, 'Create');
}
function editUser(u) {
  const body = el('div', {},
    el('div', { class: 'form-row' },
      field('Name', el('input', { id: 'ue_name', value: u.name })),
      field('Role', selectFrom('', [['user', 'User'], ['admin', 'Administrator']], null, 'ue_role'))),
    permissionChecklist('ue', u));
  body.querySelector('#ue_role').value = u.role;
  modal('Edit ' + u.email, body, async () => {
    await api('PUT', '/users/' + u.id, { name: body.querySelector('#ue_name').value, role: body.querySelector('#ue_role').value,
      ...permissionValues(body, 'ue') });
    toast('Updated'); render();
  }, 'Save');
}
function resetUserPassword(u) {
  const body = el('div', {},
    field('New password for ' + u.email, el('input', { id: 'rp_pw', value: 'Cascadia123!' })),
    el('div', { class: 'help' }, 'They’ll be required to change it on next sign-in.'));
  modal('Reset password', body, async () => {
    const pw = body.querySelector('#rp_pw').value;
    if (pw.length < 8) throw new Error('Password must be at least 8 characters.');
    await api('POST', '/users/' + u.id + '/password', { password: pw });
    toast('Password reset');
  }, 'Reset');
}
async function setUserActive(u, active) {
  if (!active && !confirm('Deactivate ' + u.email + '? They will no longer be able to sign in.')) return;
  try { await api('PUT', '/users/' + u.id, { active }); toast(active ? 'Activated' : 'Deactivated'); render(); }
  catch (e) { toast(e.message, true); }
}

/* ---------------- start ---------------- */
if (State.token) boot(); else show('login');
