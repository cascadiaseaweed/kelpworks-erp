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
  if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
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
  [...$('#tabs').children].forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  render();
}

async function boot() {
  try {
    State.user = (await api('GET', '/me'));
    State.ref = await api('GET', '/refdata');
    $('#whoName').textContent = State.user.name;
    $('#tabs [data-tab=admin]').classList.toggle('hidden', State.user.role !== 'admin');
    if (State.tab === 'admin' && State.user.role !== 'admin') State.tab = 'dashboard';
    show('app'); selectTab(State.tab);
    if (State.user.mustChange) changePasswordModal(true);
  } catch (err) { logout(); }
}

/* ---------------- Router ---------------- */
function render() {
  const v = $('#view'); v.innerHTML = '';
  ({ dashboard: pageDashboard, stabilized: pageStabilized, production: pageProduction,
     qc: pageQC, fg: pageFG, shipping: pageShipping, consumables: pageConsumables, reports: pageReports,
     calculations: pageCalculations, labels: pageLabels, admin: pageAdmin }[State.tab])(v);
}

/* ---------------- Dashboard ---------------- */
async function pageDashboard(v) {
  v.append(el('div', { class: 'page-head' }, el('h2', {}, 'Dashboard')));
  const d = await api('GET', '/dashboard');
  v.append(el('div', { class: 'tiles' },
    tile('Stabilized totes', fmt(d.stabilized.totes), 'in stock', true),
    tile('Stabilized kelp', fmt(d.stabilized.kg, 0), 'kg on hand'),
    tile('Finished goods', fmt(d.finishedGoods.litres, 0), 'litres on hand'),
    tile('Low stock alerts', fmt(d.lowStock.length), 'consumables')
  ));
  const left = el('div', { class: 'card' }, el('h3', {}, 'Stabilized inventory by species'),
    table(['Species', 'Totes', 'Kg'], d.stabilized.bySpecies.map(r => [speciesName(r.species), fmt(r.totes), num(fmt(r.kg, 0))]), [false, true, true]));
  const fgRows = d.finishedGoods.lines.map(r => [skuName(r.sku), r.packageSize, fmt(r.qty), num(fmt(r.litres, 0))]);
  const right = el('div', { class: 'card' }, el('h3', {}, 'Finished goods on hand'),
    fgRows.length ? table(['SKU', 'Pack', 'Units', 'Litres'], fgRows, [false, false, true, true]) : el('div', { class: 'empty' }, 'No finished goods yet — run a production batch.'));
  v.append(el('div', { class: 'grid2' }, left, right));

  const consRows = d.consumables.map(c => [c.name, fmt(c.onHand, 1) + ' ' + c.unit, badge(c.low ? 'low' : 'ok', c.low ? 'LOW' : 'OK')]);
  const cons = el('div', { class: 'card' }, el('h3', {}, 'Consumables'), table(['Item', 'On hand', ''], consRows, [false, true, false]));
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
const STAB_COLUMNS = [
  { key: 'lot', label: 'Lot number', value: t => t.lot },
  { key: 'site', label: 'Site', value: t => siteName(t.site), options: () => (State.ref.sites || []).map(s => s.name) },
  { key: 'species', label: 'Species', value: t => speciesName(t.species),
    options: () => [...new Set((State.ref.species || []).map(s => s.common || s.name))] },
  { key: 'stabMethod', label: 'Stabilization method', value: t => t.stabilizationMethod || '',
    options: () => ['Citric acid', 'Fresh'] },
  { key: 'harvestDate', label: 'Harvest date', value: t => t.harvestDate || '' },
  { key: 'receivedDate', label: 'Received date', value: t => t.receivedDate || '' },
  { key: 'avgKg', label: 'Avg kg', value: t => t.avgWeightKg, numeric: true },
  { key: 'ph', label: 'pH', value: t => t.ph, numeric: true },
  { key: 'orp', label: 'ORP (mV)', value: t => t.orp, numeric: true },
  { key: 'lastUpdated', label: 'Last updated', value: t => t.lastUpdated ? fmtWhen(t.lastUpdated) : '' },
  { key: 'location', label: 'Location', value: t => t.location || '' },
  { key: 'status', label: 'Status', value: t => statusLabel(t.status), options: () => Object.values(STATUS_LABELS) },
];
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
  const filters = {};
  let sortKey = null, sortDir = 1;
  let visibleSelectable = [];

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
  function drawStab() {
    let rows = stabCache.filter(t => STAB_COLUMNS.every(c => {
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
      ...STAB_COLUMNS.map(c => {
        const arrow = sortKey === c.key ? (sortDir === 1 ? ' ▲' : ' ▼') : '';
        return el('th', {
          class: (c.numeric ? 'num ' : '') + 'sortable', title: 'Click to sort',
          onclick: () => { sortKey === c.key ? (sortDir = -sortDir) : (sortKey = c.key, sortDir = 1); drawStab(); }
        }, c.label + arrow);
      }), el('th', {}, ''));

    const filterRow = el('tr', { class: 'filter-row' }, el('th', {}, ''),
      ...STAB_COLUMNS.map(c => {
        const cell = el('th', {});
        if (c.options) {
          const sel = el('select', {}, el('option', { value: '' }, 'All'), ...c.options().map(o => el('option', { value: o }, o)));
          sel.value = filters[c.key] || '';
          sel.addEventListener('change', () => { filters[c.key] = sel.value; drawStab(); });
          cell.append(sel);
        } else {
          const inp = el('input', { placeholder: 'Filter…', value: filters[c.key] || '' });
          inp.addEventListener('input', () => { filters[c.key] = inp.value; drawStab(); });
          cell.append(inp);
        }
        return cell;
      }), el('th', {}));

    const tbody = el('tbody', {});
    if (!rows.length) tbody.append(el('tr', {}, el('td', { colspan: STAB_COLUMNS.length + 2, class: 'empty' }, 'No totes match.')));
    rows.forEach(t => {
      const movable = t.status === 'in_stock' || t.status === 'hold';
      tbody.append(el('tr', {
        class: 'clickable',
        title: 'Click for the full Feedstock Stability log',
        onclick: e => { if (!e.target.closest('.checkcol, .row-actions')) showHistory(t); }
      },
        el('td', { class: 'checkcol' }, rowCheck(t, selected, updateBulk)),
        el('td', { class: 'mono' }, t.lot), el('td', {}, siteName(t.site)), el('td', {}, speciesName(t.species)),
        el('td', {}, t.stabilizationMethod || '—'),
        el('td', {}, t.harvestDate || '—'), el('td', {}, t.receivedDate || '—'),
        el('td', { class: 'num' }, fmt(t.avgWeightKg, 1)),
        el('td', { class: 'num' }, phCell(t)), el('td', { class: 'num' }, orpCell(t)),
        el('td', {}, t.lastUpdated ? fmtWhen(t.lastUpdated) : '—'),
        el('td', {}, t.location || '—'), el('td', {}, badge(t.status, statusLabel(t.status))),
        el('td', {}, rowActions([
          movable ? ['Move', () => moveTote(t)] : null,
          movable ? ['Update', () => updateCondition(t)] : null,
          ['Label', () => printLabels([toteLabel(t)])],
          movable ? ['Delete', () => delTote(t), 'danger'] : null
        ]))));
    });

    host.append(el('div', { class: 'tablewrap sticky-actions' },
      el('table', {}, el('thead', {}, headRow, filterRow), tbody)));
    updateBulk();
  }
  drawStab();
}
function rowCheck(item, selected, onChange) {
  if (item.status === 'consumed' || item.status === 'sold' || item.status === 'disposed') return '';
  const cb = el('input', { type: 'checkbox', class: 'rowcheck', onchange: () => {
    cb.checked ? selected.add(item.id) : selected.delete(item.id); onChange();
  } });
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
  modal('Dispose / write off consumables', body, async () => {
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
  const sites = State.ref.sites.map(s => [s.code, s.code + ' — ' + s.name]);
  const species = State.ref.species.map(s => [s.code, s.common || s.name]);
  const locs = State.ref.locations.map(l => [l, l]);
  const sourceConsumables = (await api('GET', '/consumables')).consumables.filter(c => c.unit === 'tote');
  // Burlap sacks aren't inventory-tracked as a consumable, so it's always
  // offered as a literal extra option alongside whatever IBC-tote stock exists.
  const sourceOpts = [...sourceConsumables.map(c => [String(c.id), c.name + ' (' + fmt(c.onHand, 0) + ' on hand)']),
    ['BURLAP', 'Burlap sack']];
  // Stabilization method drives sensible defaults for the two fields below it:
  // Citric acid → Tote; Fresh → Bag + Burlap sack (fresh kelp is commonly
  // bagged and delivered loose rather than in a tracked IBC tote).
  const onStabChange = () => {
    const fresh = body.querySelector('#h_stab').value === 'Fresh';
    body.querySelector('#h_unit').value = fresh ? 'Bag' : 'Tote';
    body.querySelector('#h_source').value = fresh ? 'BURLAP' : (sourceOpts[0] ? sourceOpts[0][0] : 'BURLAP');
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
      field('Storage unit source', selectFrom('', sourceOpts, null, 'h_source')),
      field('pH', el('input', { type: 'number', id: 'h_ph', step: '0.1', placeholder: 'e.g. 3.7' }))),
    field('ORP (mV) — optional', el('input', { type: 'number', id: 'h_orp', step: '1', placeholder: 'e.g. -150' })),
    field('Notes', el('textarea', { id: 'h_notes', rows: '2', placeholder: 'Optional' })),
    el('div', { class: 'help' }, 'The selected storage unit source stock is reduced by the number of storage units checked in (Burlap sack is not inventory-tracked).'),
    el('div', { class: 'help', id: 'h_preview' }));
  const c_count = body.querySelector('#h_count'), c_kg = body.querySelector('#h_kg');
  const upd = () => {
    const n = +c_count.value || 0, kg = +c_kg.value || 0;
    body.querySelector('#h_preview').textContent = n > 0 ? `Creates ${n} storage unit(s); average weight ${n ? (kg / n).toFixed(2) : 0} kg each.` : '';
  };
  c_count.addEventListener('input', upd); c_kg.addEventListener('input', upd); upd();
  onStabChange();
  modal('Check in a harvest batch', body, async () => {
    const sourceSel = body.querySelector('#h_source');
    const isBurlap = sourceSel.value === 'BURLAP';
    const payload = {
      site: body.querySelector('#h_site').value, species: body.querySelector('#h_species').value,
      harvestDate: body.querySelector('#h_date').value, receivedDate: body.querySelector('#h_received').value,
      location: body.querySelector('#h_loc').value,
      toteCount: +body.querySelector('#h_count').value, totalKg: +body.querySelector('#h_kg').value,
      stabilizationMethod: body.querySelector('#h_stab').value, storageUnit: body.querySelector('#h_unit').value,
      ibcConsumableId: isBurlap ? null : (sourceSel.value ? +sourceSel.value : null),
      storageSourceLabel: isBurlap ? 'Burlap sack' : null,
      ph: body.querySelector('#h_ph').value || null, orp: body.querySelector('#h_orp').value || null,
      notes: body.querySelector('#h_notes').value || null
    };
    const r = await api('POST', '/harvest', payload);
    State.ref = await api('GET', '/refdata');
    toast(`Created ${r.count} storage unit(s) · ${r.avgWeightKg} kg each` + (r.storageSource ? ` · ${r.count} from ${r.storageSource}` : ''));
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
      'Optional: receivedDate, stabilizationMethod, storageUnit, location, ph, orp, storageSource, notes. ' +
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
    const fgList = run.fgLots.map(f => `${fmt(f.qty)} × ${f.packageSize}`).join(', ') || '—';
    const card = el('div', { class: 'card' },
      el('div', { class: 'page-head', style: 'margin:0 0 8px' },
        el('h3', { style: 'margin:0' }, mono(run.processingLot) , '  ', el('span', { class: 'pill' }, skuName(run.sku))),
        el('div', { class: 'actions' },
          el('button', { class: 'secondary', onclick: () => editRun(run) }, 'Edit'),
          el('button', { class: 'secondary', onclick: () => openProcessLog(run) }, '📋 Process log'),
          el('button', { class: 'secondary', onclick: () => openQcForRun(run) },
            '🧪 QC' + (run.qc && run.qc.length ? ' (' + run.qc.length + ')' : '')),
          el('button', { class: 'secondary', onclick: () => openAttachments(run) },
            '📎 Documents' + (run.attachments && run.attachments.length ? ' (' + run.attachments.length + ')' : '')),
          el('button', { class: 'secondary', onclick: () => printLabels(run.fgLots.map(f => fgLabel(f, run))) }, 'Print FG labels'))),
      el('div', { class: 'summary-line' },
        sl('Run date', run.runDate), sl('Input', fmt(run.inputKg, 1) + ' kg'),
        sl('Output', fmt(run.outputLitres, 0) + ' L'),
        sl('Conversion factor', run.inputKg ? (run.outputLitres / run.inputKg).toFixed(2) + ' L/kg' : '—'),
        sl('Target TDS', run.targetTds != null ? run.targetTds + '%' : '—'),
        sl('Citric', fmt(run.citricKg, 1) + ' kg'), sl('Sorbate', fmt(run.sorbateKg, 1) + ' kg'),
        sl('New IBCs filled', fmt(run.ibcUsed)), sl('Used IBCs freed', fmt(run.inputTotes.length)),
        sl('Packaged', fgList), run.operators ? sl('Operators', run.operators) : null),
      stageProgress(run),
      el('div', { class: 'muted', style: 'margin-top:8px;font-size:12px' },
        `Consumed ${run.inputTotes.length} tote(s): `, el('span', { class: 'mono' }, run.inputTotes.join(', '))),
      run.notes ? el('div', { class: 'muted', style: 'margin-top:4px;font-size:12px' }, '“' + run.notes + '”') : null,
      run.edits && run.edits.length ? editHistoryBlock(run.edits) : null);
    v.append(card);
  }
}
// Small at-a-glance progress dots for the 7 process-log sections a run can carry.
function stageProgress(run) {
  const stages = run.stages || {};
  const items = [
    ['Feedstock', (run.inputs || []).some(i => i.ph != null || i.surfacePhoto || i.striationPhoto || i.decision === 'rejected')],
    ['Homogenization', !!(stages.homogenization && stages.homogenization.startedAt)],
    ['Extraction', !!(stages.extraction && stages.extraction.startedAt)],
    ['Separation', !!(stages.separation && stages.separation.startedAt)],
    ['Pasteurization', !!(stages.pasteurization && stages.pasteurization.startedAt)],
    ['Dilution & Preservation', (run.dilutions || []).length > 0],
    ['Packaging', !!(stages.packaging && stages.packaging.startedAt)],
  ];
  return el('div', { class: 'stage-progress' }, ...items.map(([label, done]) =>
    el('span', { class: 'stage-dot' + (done ? ' done' : ''), title: label + (done ? ' — logged' : ' — not yet logged') }, done ? '●' : '○')));
}
function draftCard(d) {
  const pkgSummary = (d.packages || []).filter(p => p.qty > 0).map(p => `${fmt(p.qty)} × ${p.size}`).join(', ') || '—';
  return el('div', { class: 'card' },
    el('div', { class: 'page-head', style: 'margin:0 0 8px' },
      el('h3', { style: 'margin:0' }, mono(d.processingLot), '  ', el('span', { class: 'badge hold' }, 'Not yet submitted')),
      el('div', { class: 'actions' },
        el('button', { onclick: () => openRun(d) }, 'Resume'),
        el('button', { class: 'danger', onclick: () => discardDraft(d) }, 'Discard'))),
    el('div', { class: 'summary-line' },
      sl('Run date', d.runDate), sl('SKU', d.sku ? skuName(d.sku) : '—'),
      sl('Totes selected', d.toteLots.length ? d.toteLots.join(', ') : '—'),
      sl('Packaging', pkgSummary), d.operators ? sl('Operators', d.operators) : null),
    stageProgress(d),
    d.notes ? el('div', { class: 'muted', style: 'margin-top:4px;font-size:12px' }, '“' + d.notes + '”') : null);
}
async function discardDraft(d) {
  if (!confirm('Discard this in-progress run? This cannot be undone.')) return;
  await api('DELETE', '/production/drafts/' + d.id);
  toast('Draft discarded');
  render();
}
function editHistoryBlock(edits) {
  const wrap = el('details', { class: 'edit-history' },
    el('summary', {}, `Edit history (${edits.length} change${edits.length === 1 ? '' : 's'})`));
  wrap.append(el('div', { class: 'tablewrap', style: 'margin-top:8px' }, el('table', {},
    el('thead', {}, el('tr', {}, el('th', {}, 'When'), el('th', {}, 'User'), el('th', {}, 'Field'), el('th', {}, 'From'), el('th', {}, 'To'))),
    el('tbody', {}, ...edits.map(e => el('tr', {},
      el('td', { class: 'muted' }, fmtWhen(e.at)), el('td', {}, e.user || '—'),
      el('td', {}, e.field), el('td', { class: 'muted' }, e.old || '—'), el('td', {}, el('b', {}, e.new || '—'))))))));
  return wrap;
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
async function editRun(run) {
  const operatorsSelect = buildOperatorsSelect(run.operators || '');
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Processing lot', run.processingLot), sl('SKU', skuName(run.sku)),
      sl('Target TDS', run.targetTds != null ? run.targetTds + '%' : '—'),
      el('span', { class: 'muted' }, 'Totes consumed & packaged output are fixed; correct the run details below.')),
    el('div', { class: 'form-row' },
      field('Run date', el('input', { type: 'date', id: 'e_date', value: run.runDate || todayStr() })),
      field('Citric acid (kg)', el('input', { type: 'number', step: '0.1', id: 'e_citric', value: run.citricKg ?? 0 }))),
    el('div', { class: 'form-row' },
      field('Potassium sorbate (kg)', el('input', { type: 'number', step: '0.1', id: 'e_sorbate', value: run.sorbateKg ?? 0 })),
      field('Production Location', productionLocationSelect('e_loc', run.location))),
    field('Operators', operatorsSelect.el),
    field('Notes', el('textarea', { id: 'e_notes', rows: '2' }, run.notes || '')),
    el('div', { class: 'help' }, 'Changing citric / sorbate adjusts consumable stock by the difference. Every change is logged with your name.'));
  modal('Edit run — ' + run.processingLot, body, async () => {
    const r = await api('PUT', '/production/' + run.id, {
      runDate: body.querySelector('#e_date').value,
      citricKg: body.querySelector('#e_citric').value || 0,
      sorbateKg: body.querySelector('#e_sorbate').value || 0,
      location: body.querySelector('#e_loc').value,
      operators: operatorsSelect.value,
      notes: body.querySelector('#e_notes').value
    });
    State.ref = await api('GET', '/refdata');
    toast(r.changed ? r.changed + ' change' + (r.changed === 1 ? '' : 's') + ' logged' : 'No changes');
    render();
  }, 'Save changes');
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
        await api('POST', '/production/' + rid + '/attachments',
          { filename: file.name, contentType: file.type || 'application/octet-stream', dataB64: b64 });
        resolve();
      } catch (e) { reject(e); }
    };
    reader.onerror = () => reject(new Error('Could not read ' + file.name));
    reader.readAsDataURL(file);
  });
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
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Run', run.processingLot),
      el('span', { class: 'muted' }, 'lab results, paper logs, images — PDF, images, Office docs (max 25 MB each)')),
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
// [Sample Location, Sample Type, Measurement, Unit] — transcribed from the QAQC
// sample plan (Claude_sample_fields.xlsx). Sample Type isn't a user-facing field
// (removed per feedback) but is kept here to color-code line items by it.
const QC_TABLE = [
  ['Homogenization (Feedstock Characterization)', 'Process', 'Total IBC weight', 'kg'],
  ['Homogenization (Feedstock Characterization)', 'Process', 'Total IBC volume', 'L'],
  ['Homogenization (Feedstock Characterization)', 'Process', 'Total rinse water', 'L'],
  ['Homogenization (Feedstock Characterization)', 'Product', 'Fraction wet solid', '%'],
  ['Homogenization (Feedstock Characterization)', 'Product', 'TSslurry', '%'],
  ['Homogenization (Feedstock Characterization)', 'Product', 'TSliquid', '%'],
  ['Homogenization (Feedstock Characterization)', 'Product', 'TSsolids', '%'],
  ['Homogenization (Feedstock Characterization)', 'Product', 'ρliquid', 'g/mL'],
  ['Homogenization (Feedstock Characterization)', 'Product', 'ρslurry', 'g/mL'],

  ['Pre-Extraction (Lot Characterization)', 'Process', 'Total dilution water', 'L'],
  ['Pre-Extraction (Lot Characterization)', 'Process', 'Total slurry level (2A/B)', 'L'],
  ['Pre-Extraction (Lot Characterization)', 'Quality', 'pH', ''],
  ['Pre-Extraction (Lot Characterization)', 'Quality', 'TDS', '%'],
  ['Pre-Extraction (Lot Characterization)', 'Quality', 'Brix', '%'],
  ['Pre-Extraction (Lot Characterization)', 'Quality', 'Mannitol', '%'],
  ['Pre-Extraction (Lot Characterization)', 'Product', 'Fraction wet solid', '%'],
  ['Pre-Extraction (Lot Characterization)', 'Product', 'TSslurry', '%'],
  ['Pre-Extraction (Lot Characterization)', 'Product', 'TSliquid', '%'],
  ['Pre-Extraction (Lot Characterization)', 'Product', 'TSsolids', '%'],
  ['Pre-Extraction (Lot Characterization)', 'Product', 'ρliquid', 'g/mL'],
  ['Pre-Extraction (Lot Characterization)', 'Product', 'ρslurry', 'g/mL'],

  ['Post-Extraction (Extraction Performance)', 'Process', 'Total slurry level (3)', 'L'],
  ['Post-Extraction (Extraction Performance)', 'Quality', 'pH', ''],
  ['Post-Extraction (Extraction Performance)', 'Quality', 'TDS', '%'],
  ['Post-Extraction (Extraction Performance)', 'Quality', 'Brix', '%'],
  ['Post-Extraction (Extraction Performance)', 'Quality', 'Mannitol', '%'],
  ['Post-Extraction (Extraction Performance)', 'Product', 'Fraction wet solid', '%'],
  ['Post-Extraction (Extraction Performance)', 'Product', 'TSslurry', '%'],
  ['Post-Extraction (Extraction Performance)', 'Product', 'TSliquid', '%'],
  ['Post-Extraction (Extraction Performance)', 'Product', 'TSsolids', '%'],
  ['Post-Extraction (Extraction Performance)', 'Product', 'ρliquid', 'g/mL'],
  ['Post-Extraction (Extraction Performance)', 'Product', 'ρslurry', 'g/mL'],

  ['Solids Characterization', 'Process', 'Total solids weight', 'kg'],
  ['Solids Characterization', 'Product', 'Moisture', '%'],

  ['Post-Pasteurization (Reagent & Dilution Requirements)', 'Process', 'Total tank level (5A/B)', 'L'],
  ['Post-Pasteurization (Reagent & Dilution Requirements)', 'Quality', 'pH', ''],
  ['Post-Pasteurization (Reagent & Dilution Requirements)', 'Quality', 'TDS', '%'],
  ['Post-Pasteurization (Reagent & Dilution Requirements)', 'Product', 'TSliquid', '%'],
  ['Post-Pasteurization (Reagent & Dilution Requirements)', 'Product', 'ρliquid', 'g/mL'],

  ['Final Product (LKE Characterization)', 'Process', 'Total citric', 'kg'],
  ['Final Product (LKE Characterization)', 'Process', 'Total ksorbate', 'kg'],
  ['Final Product (LKE Characterization)', 'Process', 'Total dilution water', 'L'],
  ['Final Product (LKE Characterization)', 'Process', 'Total tank level (6A/B)', 'L'],
  ['Final Product (LKE Characterization)', 'Process', 'IBC count', ''],
  ['Final Product (LKE Characterization)', 'Quality', 'pH', ''],
  ['Final Product (LKE Characterization)', 'Quality', 'TDS', '%'],
  ['Final Product (LKE Characterization)', 'Quality', 'Brix', '%'],
  ['Final Product (LKE Characterization)', 'Quality', 'Mannitol', '%'],
  ['Final Product (LKE Characterization)', 'Product', 'TSliquid', '%'],
  ['Final Product (LKE Characterization)', 'Product', 'ρliquid', 'g/mL'],
  ['Final Product (LKE Characterization)', 'Product', 'Settling rate', 'mL/h'],
  ['Final Product (LKE Characterization)', 'Product', 'TSSliquid', '%']
];
const QC_LOCATIONS = [...new Set(QC_TABLE.map(r => r[0])), 'Other'];
const QC_MEASUREMENTS = [...new Set(QC_TABLE.map(r => r[2]))].sort();
function qcRow(r) { return { location: r[0], type: r[1], measurement: r[2], unit: r[3] }; }
function measurementsForLocation(loc) { return QC_TABLE.filter(r => r[0] === loc).map(qcRow); }
function locationsForMeasurement(name) { return QC_TABLE.filter(r => r[2] === name).map(qcRow); }
function qcTypeClass(type) {
  return type === 'Process' ? 'qc-type-process' : type === 'Quality' ? 'qc-type-quality' : 'qc-type-product';
}
// Most location names carry a parenthetical sub-category, e.g.
// "Homogenization (Feedstock Characterization)" — split that out so it can be
// shown as secondary detail instead of cluttering the main name with brackets.
function qcLocationParts(loc) {
  const m = /^(.*?)\s*\(([^)]+)\)\s*$/.exec(loc);
  return m ? { name: m[1], note: m[2] } : { name: loc, note: null };
}
function qcLocationLabel(loc, variant) {
  const { name, note } = qcLocationParts(loc);
  return el('span', { class: 'qc-loc qc-loc-' + (variant || 'secondary') },
    el('span', { class: 'qc-loc-name' }, name),
    note ? el('span', { class: 'qc-loc-note' }, note) : null);
}
// Custom dropdown (native <select> can't render multi-line option text) that
// shows every location as its stacked two-line label, both for the closed
// button and the open list of choices.
function buildQcLocationSelect(initialValue, onChange) {
  let value = QC_LOCATIONS.includes(initialValue) ? initialValue : QC_LOCATIONS[0];
  const btn = el('button', { type: 'button', class: 'qc-loc-select-btn' });
  const panel = el('div', { class: 'qc-loc-panel hidden' });
  const wrap = el('div', { class: 'qc-loc-select' }, btn, panel);
  function onDocClick(e) { if (!wrap.contains(e.target)) close(); }
  function open() {
    panel.innerHTML = '';
    QC_LOCATIONS.forEach(l => panel.append(el('div', {
      class: 'qc-loc-option' + (l === value ? ' selected' : ''),
      onclick: () => { value = l; renderBtn(); close(); onChange(value); }
    }, qcLocationLabel(l, 'primary'))));
    panel.classList.remove('hidden');
    document.addEventListener('click', onDocClick, true);
  }
  function close() {
    panel.classList.add('hidden');
    document.removeEventListener('click', onDocClick, true);
  }
  function renderBtn() {
    btn.innerHTML = '';
    btn.append(qcLocationLabel(value, 'primary'), el('span', { class: 'qc-loc-caret' }, '▾'));
  }
  btn.addEventListener('click', () => { panel.classList.contains('hidden') ? open() : close(); });
  renderBtn();
  return { el: wrap, get value() { return value; } };
}
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

// Renders the three ways to work through a QC log: by Sample location (all
// measurements at one location), by Measurement (that measurement across every
// location it applies to), or Not yet logged (everything with no value yet for
// this run). Each line item pre-fills with the run's most recent value for that
// (location, measurement) pair, if any, marked with a green check, and is
// color-coded by its original Process/Quality/Product grouping. "Other" (only
// reachable via Sample location) falls back to a single free-text measurement +
// value. `existingEntries` is the run's QC log, newest first. `state` is a
// plain object {mode, location, measurement} the caller owns and this mutates
// in place, so the caller's next render can pick up where the user left off.
function renderQcBulkEntry(host, run, existingEntries, state, onSaved) {
  host.innerHTML = '';
  const modeSel = el('select', {},
    el('option', { value: 'location' }, 'Sample location'),
    el('option', { value: 'measurement' }, 'Measurement'),
    el('option', { value: 'unlogged' }, 'Not yet logged'));
  modeSel.value = state.mode;
  const pickerHost = el('div', {});
  const legend = el('div', { class: 'qc-legend' },
    el('span', { class: 'qc-legend-item qc-type-process' }, 'Process'),
    el('span', { class: 'qc-legend-item qc-type-quality' }, 'Quality'),
    el('span', { class: 'qc-legend-item qc-type-product' }, 'Product'));
  const rowsHost = el('div', { class: 'qc-rows-scroll' });
  const errBox = el('div', { class: 'help' });
  const saveBtn = el('button', { type: 'button', onclick: save }, 'Save values');
  let rowCtls = [];

  function existingFor(loc, metric) {
    return existingEntries.find(e => e.sampleLocation === loc && e.metric === metric);
  }
  function buildPicker() {
    pickerHost.innerHTML = '';
    if (state.mode === 'location') {
      const picker = buildQcLocationSelect(state.location, v => { state.location = v; rebuildRows(); });
      state.location = picker.value;
      pickerHost.append(field('Sample location', picker.el));
    } else if (state.mode === 'measurement') {
      const sel = el('select', {}, ...QC_MEASUREMENTS.map(m => el('option', { value: m }, m)));
      sel.value = QC_MEASUREMENTS.includes(state.measurement) ? state.measurement : QC_MEASUREMENTS[0];
      state.measurement = sel.value;
      sel.addEventListener('change', () => { state.measurement = sel.value; rebuildRows(); });
      pickerHost.append(field('Measurement', sel));
    }
    rebuildRows();
  }
  function buildRow(r, showLocation) {
    const existing = existingFor(r.location, r.measurement);
    const maxDecimals = qcMaxDecimals(r.unit);
    const valueInput = el('input', {
      inputmode: 'decimal', placeholder: r.unit || '',
      value: existing ? formatQcValue(existing.value, maxDecimals) : ''
    });
    attachNumericMask(valueInput, maxDecimals);
    const check = el('span', { class: 'qc-check' + (existing ? '' : ' hidden'), title: 'Already logged' }, '✓ Logged');
    valueInput.addEventListener('input', () => {
      check.classList.toggle('hidden', !(existing && qcParseValue(valueInput.value) === existing.value));
    });
    rowsHost.append(el('div', { class: 'qc-row ' + qcTypeClass(r.type) },
      el('span', { class: 'qc-row-label' },
        el('b', {}, r.measurement + (r.unit ? ' (' + r.unit + ')' : '')),
        showLocation ? qcLocationLabel(r.location) : null),
      valueInput, check));
    rowCtls.push({ location: r.location, measurement: r.measurement, unit: r.unit, valueInput, existing });
  }
  function rebuildRows() {
    rowsHost.innerHTML = '';
    rowCtls = [];
    if (state.mode === 'location' && state.location === 'Other') {
      const measureInput = el('input', { placeholder: 'Measurement name' });
      const valueInput = el('input', { inputmode: 'decimal' });
      attachNumericMask(valueInput, 2);
      rowsHost.append(el('div', { class: 'form-row' },
        field('Measurement', measureInput), field('Value', valueInput)));
      rowCtls.push({ other: true, measureInput, valueInput });
      saveBtn.textContent = 'Add entry';
      return;
    }
    saveBtn.textContent = 'Save values';
    let list;
    if (state.mode === 'location') list = measurementsForLocation(state.location);
    else if (state.mode === 'measurement') list = locationsForMeasurement(state.measurement);
    else {
      // Alphabetical by measurement, then by location in its natural (not
      // alphabetical) order — i.e. the order locations are listed elsewhere.
      const locOrder = new Map(QC_LOCATIONS.map((l, i) => [l, i]));
      list = QC_TABLE.map(qcRow).filter(r => !existingFor(r.location, r.measurement))
        .sort((a, b) => a.measurement.localeCompare(b.measurement) || locOrder.get(a.location) - locOrder.get(b.location));
    }
    if (!list.length) {
      rowsHost.append(el('div', { class: 'help' },
        state.mode === 'unlogged' ? 'Everything has a logged value.' : 'No standard measurements here.'));
      return;
    }
    const showLocation = state.mode !== 'location';
    list.forEach(r => buildRow(r, showLocation));
  }
  modeSel.addEventListener('change', () => { state.mode = modeSel.value; buildPicker(); });
  buildPicker();

  async function save() {
    errBox.textContent = '';
    saveBtn.disabled = true;
    try {
      if (state.mode === 'location' && state.location === 'Other') {
        const { measureInput, valueInput } = rowCtls[0];
        const metric = measureInput.value.trim();
        const value = qcParseValue(valueInput.value);
        if (!metric) throw new Error('Enter a measurement name.');
        if (!valueInput.value.trim() || Number.isNaN(value)) throw new Error('Enter a numeric value.');
        await api('POST', '/production/' + run.id + '/qc', { sampleLocation: 'Other', metric, value, unit: '' });
      } else {
        let saved = 0;
        for (const r of rowCtls) {
          const raw = r.valueInput.value.trim();
          if (!raw) continue;
          const value = qcParseValue(raw);
          if (Number.isNaN(value)) throw new Error('Enter a numeric value for ' + r.measurement + '.');
          if (r.existing && r.existing.value === value) continue;   // unchanged
          await api('POST', '/production/' + run.id + '/qc',
            { sampleLocation: r.location, metric: r.measurement, value, unit: r.unit || '' });
          saved++;
        }
        if (!saved) { errBox.textContent = 'No new or changed values to save.'; saveBtn.disabled = false; return; }
      }
      await onSaved();
    } catch (e) {
      errBox.textContent = e.message;
      saveBtn.disabled = false;
    }
  }
  host.append(field('View by', modeSel), pickerHost, legend, rowsHost, saveBtn, errBox);
}

async function pageQC(v) {
  v.append(el('div', { class: 'page-head' },
    el('h2', {}, 'Quality Control Log'),
    el('div', { class: 'actions' }, el('button', { onclick: () => openQcModal() }, '+ Add QC entry'))));
  const search = el('input', { placeholder: 'Filter by lot, location or measurement…', style: 'max-width:320px;margin-bottom:14px' });
  const listHost = el('div', {});
  v.append(search, listHost);
  const r = await api('GET', '/production/qc');
  function draw() {
    const q = search.value.trim().toLowerCase();
    const rows = r.qc.filter(e => !q ||
      (e.processingLot + ' ' + e.metric + ' ' + (e.sampleLocation || '')).toLowerCase().includes(q));
    listHost.innerHTML = '';
    if (!rows.length) {
      listHost.append(el('div', { class: 'empty card' },
        r.qc.length ? 'No QC entries match that filter.'
          : 'No QC results logged yet. Click “+ Add QC entry” to log a lot-specific measurement, traceable back to its production run.'));
      return;
    }
    listHost.append(table(['Lot', 'Date', 'SKU', 'Sample location', 'Measurement', 'Value', 'Notes', 'Recorded by', 'When', ''],
      rows.map(e => [
        el('span', { class: 'mono', style: 'cursor:pointer;text-decoration:underline', title: 'View production run',
          onclick: () => selectTab('production') }, e.processingLot),
        e.runDate, skuName(e.sku), e.sampleLocation || '—', e.metric,
        formatQcValue(e.value, qcMaxDecimals(e.unit)) + (e.unit ? ' ' + e.unit : ''),
        e.notes || '—', e.recordedBy || '—', fmtWhen(e.recordedAt),
        rowActions([
          State.user.role === 'admin' ? ['Delete', async () => {
            if (!confirm('Remove this QC entry?')) return;
            await api('DELETE', '/production/' + e.runId + '/qc/' + e.id);
            toast('Removed'); render();
          }, 'danger'] : null
        ])
      ]), [false, false, false, false, false, false, false, false, false, false]));
  }
  search.addEventListener('input', draw);
  draw();
}

async function openQcModal() {
  const runs = (await api('GET', '/production')).runs;
  if (!runs.length) { toast('No finalized production runs to log QC against yet.', true); return; }
  const runSel = selectFrom('', runs.map(r => [String(r.id), r.processingLot + ' — ' + skuName(r.sku) + ' (' + r.runDate + ')']),
    () => loadForRun(), 'qc_run');
  const bulkHost = el('div', { style: 'margin-top:10px' });
  const state = { mode: 'location', location: QC_LOCATIONS[0], measurement: QC_MEASUREMENTS[0] };
  async function loadForRun() {
    const entries = (await api('GET', '/production/' + runSel.value + '/qc')).qc;
    renderQcBulkEntry(bulkHost, { id: runSel.value }, entries, state,
      async () => { toast('Saved'); await loadForRun(); render(); });
  }
  const body = el('div', {}, field('Production lot', runSel), bulkHost);
  await loadForRun();
  modal('Add QC entry', body, async () => { render(); }, 'Done');
}

async function openQcForRun(run) {
  const listHost = el('div', { class: 'qc-results-scroll' });
  const bulkHost = el('div', {});
  const state = { mode: 'location', location: QC_LOCATIONS[0], measurement: QC_MEASUREMENTS[0] };
  async function refresh() {
    const entries = (await api('GET', '/production/' + run.id + '/qc')).qc;
    drawList(entries);
    renderQcBulkEntry(bulkHost, run, entries, state,
      async () => { toast('Saved'); State.qcChanged = true; await refresh(); });
  }
  function drawList(entries) {
    listHost.innerHTML = '';
    if (!entries.length) { listHost.append(el('div', { class: 'help' }, 'No QC results logged yet.')); return; }
    listHost.append(table(['Sample location', 'Measurement', 'Value', 'Notes', 'Recorded by', 'When', ''], entries.map(q => [
      q.sampleLocation || '—', q.metric, formatQcValue(q.value, qcMaxDecimals(q.unit)) + (q.unit ? ' ' + q.unit : ''),
      q.notes || '—', q.recordedBy || '—', fmtWhen(q.recordedAt),
      rowActions([
        State.user.role === 'admin' ? ['Delete', async () => {
          if (!confirm('Remove this QC entry?')) return;
          await api('DELETE', '/production/' + run.id + '/qc/' + q.id);
          toast('Removed'); refresh(); State.qcChanged = true;
        }, 'danger'] : null
      ])
    ]), [false, false, false, false, false, false, false]));
  }
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Run', run.processingLot), sl('SKU', skuName(run.sku))),
    el('div', {}, el('label', {}, 'Add / update results'), bulkHost),
    el('label', { style: 'margin-top:16px' }, 'Logged results'), listHost);
  await refresh();
  modal('Quality Control Log — ' + run.processingLot, body, async () => { if (State.qcChanged) { State.qcChanged = false; render(); } }, 'Done');
}

/* ---- Process-stage building blocks, shared by openRun (pre-finalize) and
   openProcessLog (post-finalize) ---- */
// Every stage (Homogenization, Extraction, Separation, Pasteurization) is
// bespoke -- each needs a QC Check/Sample Point card and/or custom per-field
// placeholders/defaults a generic field-list renderer can't do.
// Pasteurization: "Start Conditions" groups the stage's process parameters
// (Total volume (L) was removed), a Sample Point box subtitled
// "Pre-pasteurization microbial check" follows, then "Pasteurization Out"
// holds a second, independent Sample Point box subtitled "Post-
// pasteurization microbial check". Same one-accordion/section-title/
// single-Save formatting as Extraction/Separation. Pasteurization In's
// Process Check ("Dilution requirements") also holds the run's TDS target
// and Tank 6A/B max level (both display-only) and the calculated Target
// fill level, Tank 6A/B (L) -- same mass-conservation math as
// Homogenization's Target fill level, Tank 2A/B (L) -- so `getTdsTarget` is
// a getter (not a plain value): a caller with a live-changing SKU (the
// picker in a draft still being edited) can call the returned `refresh()`
// again after that changes.
function buildPasteurizationSection(getRunId, values, samplePoints, processingLot, getTdsTarget) {
  values = values || {};
  const startedAt = el('input', { type: 'datetime-local', value: values.startedAt || '' });
  const productSetpointInp = el('input', { inputmode: 'decimal', placeholder: 'Product set-point (°C)' }); attachNumericMask(productSetpointInp, 2);
  productSetpointInp.value = values.productSetpointC != null ? formatQcValue(values.productSetpointC, 2)
    : String(settingValue('pasteurization_default_product_setpoint_c', 80));
  const boilerSetpointInp = el('input', { inputmode: 'decimal', placeholder: 'Boiler set-point (°C)' }); attachNumericMask(boilerSetpointInp, 2);
  boilerSetpointInp.value = values.boilerSetpointC != null ? formatQcValue(values.boilerSetpointC, 2)
    : String(settingValue('pasteurization_default_boiler_setpoint_c', 90));

  // Pasteurization In: a Process Check for the dilution TDS reading used to
  // work out the fill level in Tanks 6A/B, followed by the pre-
  // pasteurization microbial Sample Point.
  const dilutionTdsInp = el('input', { inputmode: 'decimal', placeholder: 'Used to calculate fill level in Tanks 6A/B' });
  attachNumericMask(dilutionTdsInp, 1);
  if (values.tdsPct != null) dilutionTdsInp.value = formatQcValue(values.tdsPct, 1);
  const dilutionReqContent = el('div', {});
  function refreshDilutionReq() {
    dilutionReqContent.innerHTML = '';
    const tdsIn = dilutionTdsInp.value.trim() === '' ? null : qcParseValue(dilutionTdsInp.value);
    const tdsTarget = getTdsTarget();
    const tankMax = settingValue('dilution_tank_6ab_max_level_l', 5000);
    const fillLevelRaw = (tdsIn != null && tdsIn > 0 && tdsTarget != null) ? tankMax * tdsTarget / tdsIn : null;
    lastFillLevel = fillLevelRaw != null ? Math.round(fillLevelRaw / 10) * 10 : null;
    dilutionReqContent.append(
      el('div', { class: 'summary-line' },
        sl('TDS target', tdsTarget != null ? formatQcValue(tdsTarget, 1) + '%' : '—'),
        sl('Tank 6A/B max level (L)', fmt(tankMax, 0) + ' L')),
      field('Target fill level, Tank 6A/B (L)',
        el('span', { class: lastFillLevel != null ? 'qc-check-result-value' : 'help' },
          lastFillLevel != null ? fmt(lastFillLevel, 0) + ' L'
            : 'Enter the TDS concentrated (%) and select a SKU with a target TDS to calculate')));
  }
  let lastFillLevel = null;
  dilutionTdsInp.addEventListener('input', refreshDilutionReq);
  const dilutionProcessCheckBox = el('div', { class: 'qc-check-box' },
    el('div', { class: 'qc-check-title' }, 'Process Check'),
    el('div', { class: 'qc-check-subtitle' }, 'Dilution requirements'),
    field('TDS concentrated (%)', dilutionTdsInp),
    dilutionReqContent);

  const preCollectedInp = el('input', { type: 'datetime-local',
    value: values.preSampleCollectedAt ? values.preSampleCollectedAt.replace('Z', '').slice(0, 16) : '' });
  const preSamplePointBox = el('div', { class: 'qc-check-box theme-sample' },
    el('div', { class: 'qc-check-title' }, 'Sample Point'),
    el('div', { class: 'qc-check-subtitle' }, 'Pre-pasteurization microbial check'),
    field('Collection date and time', preCollectedInp),
    buildSamplePointsSection(samplePoints, getRunId, processingLot, () => preCollectedInp.value, 'pasteurization_pre'));

  const postCollectedInp = el('input', { type: 'datetime-local',
    value: values.postSampleCollectedAt ? values.postSampleCollectedAt.replace('Z', '').slice(0, 16) : '' });
  const postSamplePointBox = el('div', { class: 'qc-check-box theme-sample' },
    el('div', { class: 'qc-check-title' }, 'Sample Point'),
    el('div', { class: 'qc-check-subtitle' }, 'Post-pasteurization microbial check'),
    field('Collection date and time', postCollectedInp),
    buildSamplePointsSection(samplePoints, getRunId, processingLot, () => postCollectedInp.value, 'pasteurization_post'));

  const status = el('span', { class: 'help' });
  const saveBtn = el('button', { type: 'button', class: 'secondary', onclick: save }, 'Save');
  async function save() {
    status.textContent = ''; saveBtn.disabled = true;
    try {
      const rid = await getRunId();
      await api('PUT', '/production/' + rid + '/stages/pasteurization', {
        startedAt: startedAt.value || null,
        productSetpointC: productSetpointInp.value.trim() === '' ? null : qcParseValue(productSetpointInp.value),
        boilerSetpointC: boilerSetpointInp.value.trim() === '' ? null : qcParseValue(boilerSetpointInp.value),
        preSampleCollectedAt: preCollectedInp.value || null,
        postSampleCollectedAt: postCollectedInp.value || null,
        tdsPct: dilutionTdsInp.value.trim() === '' ? null : qcParseValue(dilutionTdsInp.value),
      });
      status.textContent = 'Saved.';
    } catch (e) { status.textContent = e.message; }
    saveBtn.disabled = false;
  }
  refreshDilutionReq();
  return {
    box: el('details', { class: 'accordion' }, el('summary', {}, 'Pasteurization'),
      el('div', { class: 'accordion-body' },
        el('div', { class: 'qc-check-section-title', style: 'margin-top:0' }, 'Start Conditions'),
        el('div', { class: 'form-row' }, field('Started at', startedAt)),
        // Standard 2-column form-row (not the compact 108px fields) so both
        // labels fit on one line and the two input boxes stay level.
        el('div', { class: 'form-row' },
          field('Product set-point (°C)', productSetpointInp), field('Boiler set-point (°C)', boilerSetpointInp)),
        el('div', { class: 'qc-check-section-title' }, 'Pasteurization In'),
        dilutionProcessCheckBox,
        preSamplePointBox,
        el('div', { class: 'qc-check-section-title' }, 'Pasteurization Out'),
        postSamplePointBox,
        el('div', { style: 'margin-top:6px' }, saveBtn, status))),
    refresh: refreshDilutionReq,
    getTargetFillLevel: () => lastFillLevel
  };
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
  const boxChildren = [
    el('div', { class: 'qc-check-title' }, 'QC Check'),
    el('div', { class: 'qc-check-subtitle' }, subtitle),
    el('div', { class: 'qc-check-section-title' }, 'Liquid'),
    el('div', { class: 'form-row-compact' },
      field('pH', qcPhInp), field('TDS (%)', tdsInp), field('Brix (%)', brixInp), field('Mannitol (%)', mannitolInp),
      field('TSliquid (%)', tsLiquidInp), field('ρliquid (g/mL)', rhoLiquidInp)),
  ];
  let tsSlurryInp, rhoSlurryInp, tsSolidsInp;
  if (!opts.omitSlurrySolids) {
    tsSlurryInp = pctInput(); if (values.tsSlurryPct != null) tsSlurryInp.value = formatQcValue(values.tsSlurryPct, 1);
    rhoSlurryInp = densityInput(); if (values.rhoSlurryGMl != null) rhoSlurryInp.value = formatQcValue(values.rhoSlurryGMl, 3);
    tsSolidsInp = pctInput(); if (values.tsSolidsPct != null) tsSolidsInp.value = formatQcValue(values.tsSolidsPct, 1);
    boxChildren.push(
      el('div', { class: 'qc-check-section-title' }, 'Slurry / Solids'),
      el('div', { class: 'form-row-compact' },
        field('TSslurry (%)', tsSlurryInp), field('ρslurry (g/mL)', rhoSlurryInp), field('TSsolids (%)', tsSolidsInp)));
  }
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
function buildExtractionSection(getRunId, values) {
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

  const qcCheck = buildQualityCheckBox('Extraction Performance', values);

  const status = el('span', { class: 'help' });
  const saveBtn = el('button', { type: 'button', class: 'secondary', onclick: save }, 'Save');
  async function save() {
    status.textContent = ''; saveBtn.disabled = true;
    try {
      const rid = await getRunId();
      await api('PUT', '/production/' + rid + '/stages/extraction', {
        startedAt: startedAt.value || null,
        amplitudePct: amplitudeInp.value.trim() === '' ? null : qcParseValue(amplitudeInp.value),
        flowrateLpm: flowrateInp.value.trim() === '' ? null : qcParseValue(flowrateInp.value),
        pressurePsi: pressureInp.value.trim() === '' ? null : qcParseValue(pressureInp.value),
        startingPowerW: startingPowerInp.value.trim() === '' ? null : qcParseValue(startingPowerInp.value),
        ...qcCheck.getPayload(),
      });
      status.textContent = 'Saved.';
    } catch (e) { status.textContent = e.message; }
    saveBtn.disabled = false;
  }
  return el('details', { class: 'accordion' }, el('summary', {}, 'Extraction'),
    el('div', { class: 'accordion-body' },
      el('div', { class: 'qc-check-section-title', style: 'margin-top:0' }, 'Start Conditions'),
      el('div', { class: 'form-row' }, field('Started at', startedAt)),
      el('div', { class: 'form-row-compact' },
        field('Amplitude (%)', amplitudeInp), field('Flow rate (L/min)', flowrateInp)),
      // Pressure/Starting power each get the full row width (not shared
      // 2-up, not the compact 108px fields above) so their long SOP-target
      // placeholder text is never clipped.
      field('Pressure (psi)', pressureInp),
      field('Starting power (W)', startingPowerInp),
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
function buildSeparationSection(getRunId, values, samplePoints, processingLot) {
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
  const solidsQcBox = el('div', { class: 'qc-check-box theme-quality' },
    el('div', { class: 'qc-check-title' }, 'QC Check'),
    el('div', { class: 'qc-check-subtitle' }, 'Solids characterization'),
    field('%Moisture', moistureInp));
  const solidsCollectedInp = el('input', { type: 'datetime-local',
    value: values.solidsSampleCollectedAt ? values.solidsSampleCollectedAt.replace('Z', '').slice(0, 16) : '' });
  const solidsSamplePointBox = el('div', { class: 'qc-check-box theme-sample' },
    el('div', { class: 'qc-check-title' }, 'Sample Point'),
    el('div', { class: 'qc-check-subtitle' }, 'Solids Characterization'),
    field('Collection date and time', solidsCollectedInp),
    buildSamplePointsSection(samplePoints, getRunId, processingLot, () => solidsCollectedInp.value, 'separation_solids'));

  // Liquid Out: Liquid fields only, no Slurry/Solids section.
  const liquidQc = buildQualityCheckBox('Filtrate characterization', {
    qcPh: values.liquidQcPh, tdsPct: values.liquidTdsPct, brixPct: values.liquidBrixPct,
    mannitolPct: values.liquidMannitolPct, tsLiquidPct: values.liquidTsLiquidPct,
    rhoLiquidGMl: values.liquidRhoLiquidGMl,
  }, { omitSlurrySolids: true });

  const status = el('span', { class: 'help' });
  const saveBtn = el('button', { type: 'button', class: 'secondary', onclick: save }, 'Save');
  async function save() {
    status.textContent = ''; saveBtn.disabled = true;
    try {
      const rid = await getRunId();
      const liquidPayload = liquidQc.getPayload();
      await api('PUT', '/production/' + rid + '/stages/separation', {
        startedAt: startedAt.value || null,
        flowrateLpm: flowrateInp.value.trim() === '' ? null : qcParseValue(flowrateInp.value),
        meshMicron: meshInp.value.trim() === '' ? null : qcParseValue(meshInp.value),
        wetSolidsWtKg: wetSolidsWtInp.value.trim() === '' ? null : qcParseValue(wetSolidsWtInp.value),
        pctMoisture: moistureInp.value.trim() === '' ? null : qcParseValue(moistureInp.value),
        liquidQcPh: liquidPayload.qcPh, liquidTdsPct: liquidPayload.tdsPct,
        liquidBrixPct: liquidPayload.brixPct, liquidMannitolPct: liquidPayload.mannitolPct,
        liquidTsLiquidPct: liquidPayload.tsLiquidPct, liquidRhoLiquidGMl: liquidPayload.rhoLiquidGMl,
        solidsSampleCollectedAt: solidsCollectedInp.value || null,
      });
      status.textContent = 'Saved.';
    } catch (e) { status.textContent = e.message; }
    saveBtn.disabled = false;
  }
  return el('details', { class: 'accordion' }, el('summary', {}, 'Separation'),
    el('div', { class: 'accordion-body' },
      el('div', { class: 'qc-check-section-title', style: 'margin-top:0' }, 'Start Conditions'),
      el('div', { class: 'form-row' }, field('Started at', startedAt)),
      el('div', { class: 'form-row-compact' },
        field('Flow rate (L/min)', flowrateInp), field('Mesh size (micron)', meshInp)),
      el('div', { class: 'qc-check-section-title' }, 'Solids Out'),
      field('Total wet-solids weight (kg)', wetSolidsWtInp),
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
  // Tank level (L) is also the starting volume the dilution-water-target
  // calc (in the Output section, below) scales up from.
  const tankInp = el('input', { inputmode: 'decimal', placeholder: 'Measured using level sensor' }); attachNumericMask(tankInp, 2);
  if (values.slurryL != null) tankInp.value = formatQcValue(values.slurryL, 2);
  // Process Check (Solids loading): %Wet-Solids, (g/g) = Wet-solids-wt /
  // (Wet-solids-wt + Liquid-wt) -- shown as a percentage, but kept internally
  // as the raw 0-1 ratio so the Output section's dilution-target calc (which
  // reuses this function) can use it directly.
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
    el('div', { class: 'qc-check-result' },
      el('span', { class: 'qc-check-result-label' }, '%Wet-Solids, (g/g)'), resultValue),
    el('div', { class: 'form-row' },
      field('Wet-solids-wt (g)', wetInp), field('Liquid-wt (g)', liquidInp)),
    sopLinkEl('wet_solids_sop'));

  // Output: a target %Wet-Solids to dilute the tank down to, and the
  // initial fill volume ("Target fill level") the tank should be loaded to
  // so that topping it up to the target %Wet-Solids with dilution water
  // lands it exactly at Tank 2A/B's max level (admin-editable, see the
  // Calculations page) -- standard mass-conservation dilution math:
  // fill level = tank max level x target %Wet-Solids / actual %Wet-Solids.
  const targetPctInp = el('input', { inputmode: 'decimal', placeholder: '%' }); attachNumericMask(targetPctInp, 1);
  targetPctInp.value = values.targetPctWetSolids != null ? formatQcValue(values.targetPctWetSolids, 1)
    : formatQcValue(settingValue('homog_default_target_pct_wet_solids', 50.0), 1);
  // Shown with an explicit "%" suffix beside the input, not just implied by
  // the label, so the value's units are unambiguous at a glance.
  const targetPctField = el('div', { style: 'display:flex;align-items:center;gap:6px' },
    targetPctInp, el('span', { class: 'help' }, '%'));
  // Shown large (matches the %Wet-Solids result style) once calculable, and
  // rounded UP to the nearest 10 L with no decimals -- an operator fills to
  // a round number, not a precise fraction of a litre.
  const dilutionTargetValue = el('span', { class: 'help' });
  function calcDilutionTarget() {
    const initialPct = calcPctWetSolids();
    const targetPctRaw = targetPctInp.value.trim() === '' ? null : qcParseValue(targetPctInp.value);
    if (initialPct == null || !initialPct || !targetPctRaw) return null;
    const targetPct = targetPctRaw / 100;
    const maxLevel = settingValue('homog_tank_2ab_max_level_l', 5000);
    return maxLevel * targetPct / initialPct;
  }
  function refreshDilutionTarget() {
    const v = calcDilutionTarget();
    dilutionTargetValue.className = v != null ? 'qc-check-result-value' : 'help';
    dilutionTargetValue.textContent = v != null ? fmt(Math.ceil(v / 10) * 10, 0) + ' L' : 'Enter the QC Check and Target %Wet-Solids to calculate';
  }
  refreshDilutionTarget();
  targetPctInp.addEventListener('input', refreshDilutionTarget);

  const dilutionInp = el('input', { inputmode: 'decimal', placeholder: 'Measured using dilution totalizer' }); attachNumericMask(dilutionInp, 2);
  if (values.dilutionWaterL != null) dilutionInp.value = formatQcValue(values.dilutionWaterL, 2);

  // QC Check (lot characterization): liquid-phase and slurry/solids-phase
  // readings, each its own compact wrapping row -- shared with Extraction
  // Out, which uses the same card under its own subtitle.
  const qcCheck = buildQualityCheckBox('Lot characterization', values);
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
  const saveBtn = el('button', { type: 'button', class: 'secondary', onclick: save }, 'Save');
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
      el('div', { class: 'form-row' }, field('Started at', startedAt)),
      el('div', { class: 'form-row' },
        field('Rinse water (L)', rinsingInp), field('Tank level (L)', tankInp)),
      processCheck1Box,
      el('div', { class: 'qc-check-section-title' }, 'Homogenization Out'),
      el('div', { class: 'form-row' },
        field('Target %Wet-Solids', targetPctField), field('Target fill level, Tank 2A/B (L)', dilutionTargetValue)),
      el('div', { class: 'form-row' }, field('Dilution water added (L)', dilutionInp)),
      qcCheckBox,
      samplePointBox,
      el('div', { style: 'margin-top:6px' }, saveBtn, status)));
}

const SAMPLE_TYPES = ['Slurry', 'Liquid', 'Solid'];
const SAMPLE_DESCRIPTIONS = ['Microbial', 'Retention', 'Metals & Nutrients', 'Proximate Analysis', 'R&D', 'Other'];
const SAMPLE_CONTAINERS = ['50 mL falcon tube', '100 g sample bag', '1 L bottle', '2 L bottle'];
// One printable label per physical container -- a sample row with Qty > 1
// prints that many copies, each with its own barcode (copy N of Qty) so
// every physical container is still uniquely identifiable.
function samplePointLabel(processingLot, row, copyIndex, totalCopies, collectedAt) {
  const barcode = (processingLot || 'RUN') + '-SP' + row.id + (totalCopies > 1 ? '-' + copyIndex : '');
  return { kind: 'Sample Point', lot: barcode, barcode, meta: [
    ['Type', row.type || '—'], ['Description', row.description || '—'], ['Container', row.container || '—'],
    ['Qty', totalCopies > 1 ? copyIndex + ' of ' + totalCopies : String(row.qty || 1)],
    ['Collected', collectedAt ? fmtWhen(collectedAt) : '—']] };
}
// The Sample Point table itself: a repeatable list of samples (Type/
// Description/Qty/Container), each row saved immediately on add/edit/remove
// via its own run_sample_points row (mirrors buildDilutionsSection), plus
// one print-labels button per row that prints Qty copies using the box's
// single shared "Collection date and time". A run can have more than one
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
      const containerSel = selectCell(SAMPLE_CONTAINERS, it.container, () => patch(it.id, { container: containerSel.value }));
      const printBtn = el('button', {
        type: 'button', class: 'secondary', title: 'Print label(s) for this sample', onclick: () => {
          const qty = Math.max(1, Math.min(10, +qtyInp.value || 1));
          const labels = [];
          for (let i = 1; i <= qty; i++) labels.push(samplePointLabel(processingLot, it, i, qty, getCollectedAt()));
          printLabels(labels);
        }
      }, '🖨');
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
        el('td', { style: 'display:flex;gap:6px;justify-content:flex-end' }, printBtn, removeBtn)));
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
    el('div', { style: 'margin-top:8px' }, addBtn), status);
}

// Packaging: a run may package output across several container units and
// quantities -- same add/remove-row format as Sample Point, but with just a
// "Container unit"/Qty pair per row (no type/description, no print). The
// box's single "Packaging date and time" applies to the whole table and
// lives on production_runs (packaging_packaged_at) instead, saved alongside
// it by the Packaging accordion's own Save button. "Container unit" options
// are admin-editable (Admin -- Container units), each mapped to a fixed
// litres-per-unit conversion (e.g. IBC = 1000 L) -- this table's entries are
// also what finalize reads to create this run's Finished Goods lots (see
// `hasEntries`, used for the "enter at least one packaged output quantity"
// check at finalize).
function buildPackagingEntriesSection(initial, getRunId) {
  let items = (initial || []).slice();
  const tbody = el('tbody', {});
  const status = el('div', { class: 'help' });
  function unitOptions() {
    const codes = (State.ref.containerUnits || []).map(u => u.code);
    return codes.length ? codes : [''];
  }
  async function patch(id, payload) {
    try {
      const rid = await getRunId();
      const r = await api('PUT', '/production/' + rid + '/packaging-entries/' + id, payload);
      items = r.packagingEntries;
      status.textContent = '';
    } catch (e) { status.textContent = e.message; }
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
    decision: 'accepted', rejectionReason: '', notes: '',
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
  const decisionSel = el('select', {}, el('option', { value: 'accepted' }, 'Accepted'), el('option', { value: 'rejected' }, 'Rejected'));
  decisionSel.value = v.decision || 'accepted';
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
      decision: decisionSel.value,
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
    return el('div', { class: 'photo-slot', style: 'flex:1 1 160px' },
      el('div', { class: 'help' }, label), img, fileInput, cameraInput,
      el('div', { style: 'display:flex;gap:6px;margin-top:4px' },
        el('button', { type: 'button', class: 'secondary', onclick: () => fileInput.click() }, 'Upload'),
        el('button', { type: 'button', class: 'secondary', onclick: () => cameraInput.click() }, '📷 Photo')),
      status);
  }

  // Feedstock Inventory's own Details card omits pH/ORP entirely -- those
  // live in the "Current pH/ORP" summary and quick-update fields just above
  // it in that modal instead, so the same reading isn't captured twice.
  const omit = opts.omit || [];
  const allFields = [
    ['loadedAt', field('Loaded at', loadedAt)],
    ['ph', field('pH', el('div', {}, phInp, phMeasuredNote))],
    ['orp', field('ORP (mV)', orpInp)],
    ['orpRange', field('ORP meter range (calculated)', orpRangeNote)],
    ['weightKg', field('Weight (kg)', weightInp)],
    ['volumeL', field('Volume (L)', volumeInp)],
    ['densityKgL', field('Density (calculated)', densityNote)],
    ['odour', field('Odour', odourMultiSelect.el)],
    ['odourIntensity', field('Odour intensity', intensitySel)],
    ['decision', field('Decision', decisionSel)],
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

// Dilution & Preservation -> "Dilution" and "Preservatives". The Dilution
// requirements Process Check (TDS concentrated/target, Tank 6A/B max level,
// Target fill level) now lives entirely in Pasteurization In (see
// buildPasteurizationSection) -- this box only holds the run's actual
// measured fill level/pH and the citric acid added to correct it, plain
// (unboxed) fields, against the SKU's fixed Target pH; and "Preservatives",
// the potassium sorbate stock-solution dosing fields, including a
// calculated estimate of how much stock solution to add (never persisted
// itself, same convention as every other calculated field in KelpWorks).
// Everything here saves together through the "dilution" stage endpoint; the
// repeatable tank list below (buildDilutionsSection) is unrelated and saves
// independently per row. `getTargetPh`/`getKsorbateTarget`/`getTargetFillLevel`
// are getters (not plain values) so a caller with a live-changing SKU (e.g.
// the picker in a draft still being edited) can call `refresh()` again after
// that changes; `getTargetFillLevel` also changes live as the operator types
// into Pasteurization In's TDS concentrated (%) field.
function buildDilutionAndPreservativesBox(getRunId, values, getTargetPh, getKsorbateTarget, getTargetFillLevel) {
  values = values || {};

  const fillLevelInp = el('input', { inputmode: 'decimal', placeholder: 'Measured using level sensor' });
  attachNumericMask(fillLevelInp, 2);
  if (values.fillLevelTank6abL != null) fillLevelInp.value = formatQcValue(values.fillLevelTank6abL, 2);
  const measuredPhInp = el('input', { inputmode: 'decimal', placeholder: 'pH' }); attachNumericMask(measuredPhInp, 1);
  if (values.measuredPh != null) measuredPhInp.value = formatQcValue(values.measuredPh, 1);
  const citricInp = el('input', { inputmode: 'decimal', placeholder: 'kg' }); attachNumericMask(citricInp, 2);
  if (values.citricKg != null) citricInp.value = formatQcValue(values.citricKg, 2);
  const targetPhValue = el('span', { class: 'help' });
  function refreshTargetPh() {
    const targetPh = getTargetPh();
    targetPhValue.textContent = targetPh != null ? formatQcValue(targetPh, 1) : '—';
  }

  // Dilution water added, TDS: how much dilution water was actually added,
  // found by comparing the tank's measured Fill level against the planned
  // (pre-dilution) Target fill level, Tank 6A/B (L) from Pasteurization In.
  const dilutionWaterAddedValue = el('span', { class: 'help' });
  function calcDilutionWaterAddedTds() {
    const fillLevel = fillLevelInp.value.trim() === '' ? null : qcParseValue(fillLevelInp.value);
    const targetFillLevel = getTargetFillLevel();
    if (fillLevel == null || targetFillLevel == null) return null;
    return Math.round((fillLevel - targetFillLevel) / 10) * 10;
  }
  function refreshDilutionWaterAdded() {
    const v = calcDilutionWaterAddedTds();
    dilutionWaterAddedValue.className = v != null ? 'qc-check-result-value' : 'help';
    dilutionWaterAddedValue.textContent = v != null ? fmt(v, 0) + ' L'
      : 'Enter the Fill level, Tank 6A/B to calculate';
  }
  fillLevelInp.addEventListener('input', refreshDilutionWaterAdded);

  const ksorbateStockInp = el('input', { inputmode: 'decimal', placeholder: '%' }); attachNumericMask(ksorbateStockInp, 1);
  ksorbateStockInp.value = values.ksorbateStockPct != null ? formatQcValue(values.ksorbateStockPct, 1)
    : formatQcValue(settingValue('ksorbate_stock_concentration_default_pct', 25), 1);
  const ksorbateStockField = el('div', { style: 'display:flex;align-items:center;gap:6px' },
    ksorbateStockInp, el('span', { class: 'help' }, '%'));

  // Ksorbate, calculated (L): the volume of stock solution needed to reach
  // the SKU's target Ksorbate dose in the tank's current (actual) fill
  // volume -- same mass-conservation shape as Target fill level, Tank 6A/B
  // (L), solved for volume instead of level.
  const ksorbateCalculatedLValue = el('span', { class: 'help' });
  function calcKsorbateCalculatedL() {
    const fillLevel = fillLevelInp.value.trim() === '' ? null : qcParseValue(fillLevelInp.value);
    const stockPct = ksorbateStockInp.value.trim() === '' ? null : qcParseValue(ksorbateStockInp.value);
    const ksorbateTarget = getKsorbateTarget();
    if (fillLevel == null || !stockPct || ksorbateTarget == null) return null;
    return fillLevel * ksorbateTarget * 100 / stockPct;
  }
  function refreshKsorbateCalculatedL() {
    const v = calcKsorbateCalculatedL();
    ksorbateCalculatedLValue.className = v != null ? 'qc-check-result-value' : 'help';
    ksorbateCalculatedLValue.textContent = v != null ? formatQcValue(v, 2) + ' L'
      : 'Enter the Fill level, Tank 6A/B and Ksorbate stock concentration, and select a SKU with a Ksorbate target to calculate';
  }

  const ksorbateAddedLInp = el('input', { inputmode: 'decimal', placeholder: 'L' }); attachNumericMask(ksorbateAddedLInp, 2);
  if (values.ksorbateAddedL != null) ksorbateAddedLInp.value = formatQcValue(values.ksorbateAddedL, 2);
  const ksorbateAddedKgValue = el('span', { class: 'help' });
  function calcKsorbateAddedKg() {
    const stockPct = ksorbateStockInp.value.trim() === '' ? null : qcParseValue(ksorbateStockInp.value);
    const addedL = ksorbateAddedLInp.value.trim() === '' ? null : qcParseValue(ksorbateAddedLInp.value);
    if (stockPct == null || addedL == null) return null;
    return addedL * stockPct / 100;
  }
  function refreshKsorbateAddedKg() {
    const v = calcKsorbateAddedKg();
    ksorbateAddedKgValue.className = v != null ? 'qc-check-result-value' : 'help';
    ksorbateAddedKgValue.textContent = v != null ? formatQcValue(v, 2) + ' kg' : 'Enter the stock concentration and volume added to calculate';
  }
  fillLevelInp.addEventListener('input', refreshKsorbateCalculatedL);
  ksorbateStockInp.addEventListener('input', () => { refreshKsorbateCalculatedL(); refreshKsorbateAddedKg(); });
  ksorbateAddedLInp.addEventListener('input', refreshKsorbateAddedKg);

  const status = el('span', { class: 'help' });
  const saveBtn = el('button', { type: 'button', class: 'secondary', onclick: save }, 'Save');
  async function save() {
    status.textContent = ''; saveBtn.disabled = true;
    try {
      const rid = await getRunId();
      await api('PUT', '/production/' + rid + '/stages/dilution', {
        fillLevelTank6abL: fillLevelInp.value.trim() === '' ? null : qcParseValue(fillLevelInp.value),
        measuredPh: measuredPhInp.value.trim() === '' ? null : qcParseValue(measuredPhInp.value),
        citricKg: citricInp.value.trim() === '' ? null : qcParseValue(citricInp.value),
        ksorbateStockPct: ksorbateStockInp.value.trim() === '' ? null : qcParseValue(ksorbateStockInp.value),
        ksorbateAddedL: ksorbateAddedLInp.value.trim() === '' ? null : qcParseValue(ksorbateAddedLInp.value),
      });
      status.textContent = 'Saved.';
    } catch (e) { status.textContent = e.message; }
    saveBtn.disabled = false;
  }

  refreshTargetPh();
  refreshDilutionWaterAdded();
  refreshKsorbateCalculatedL();
  refreshKsorbateAddedKg();
  return {
    box: el('div', {},
      el('div', { class: 'qc-check-section-title', style: 'margin-top:0' }, 'Dilution'),
      el('div', { class: 'form-row' },
        field('Fill level, Tank 6A/B (L)', fillLevelInp),
        field('Dilution water added, TDS', dilutionWaterAddedValue)),
      el('div', { class: 'form-row-3' },
        field('Measured pH', measuredPhInp), field('Target pH', targetPhValue),
        field('Citric acid added (kg)', citricInp)),
      el('div', { class: 'qc-check-section-title' }, 'Preservatives'),
      el('div', { class: 'form-row' },
        field('Ksorbate stock concentration (w/v)', ksorbateStockField),
        field('Ksorbate, calculated (L)', ksorbateCalculatedLValue)),
      field('Ksorbate added (L)', ksorbateAddedLInp),
      field('Ksorbate added (kg)', ksorbateAddedKgValue),
      el('div', { style: 'margin-top:6px' }, saveBtn, status)),
    refresh: () => { refreshTargetPh(); refreshDilutionWaterAdded(); refreshKsorbateCalculatedL(); }
  };
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
      field('Product SKU (required)', skuSel),
      field('Run date (required)', dateInp)),
    specPanel,
    el('div', { class: 'form-row', style: 'margin-top:8px' },
      field('Production Location (required)', locSel),
      field('Operators (required)', operatorsSelect.el)),
    field('Notes', el('textarea', { id: 'nr_notes', rows: '2', placeholder: 'Optional batch notes' })),
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
  const extractionSection = buildExtractionSection(ensureRunId, stages.extraction);
  const separationSection =
    buildSeparationSection(ensureRunId, stages.separation, draft?.samplePoints || [], draft?.processingLot);
  // TDS/pH/Ksorbate targets follow the currently-selected SKU (which can
  // still change in this draft), so they're refreshed alongside the spec
  // panel below.
  const pasteurizationSection = buildPasteurizationSection(
    ensureRunId, stages.pasteurization, draft?.samplePoints || [], draft?.processingLot,
    () => skus.find(x => x.code === skuSel.value)?.tdsTarget);
  const dilutionSummary = buildDilutionAndPreservativesBox(
    ensureRunId, stages.dilution,
    () => skus.find(x => x.code === skuSel.value)?.phTarget,
    () => skus.find(x => x.code === skuSel.value)?.ksorbateTarget,
    () => pasteurizationSection.getTargetFillLevel());
  const dilutionsSection = buildDilutionsSection(draft?.dilutions || [], ensureRunId);
  const packagingEntriesSection = buildPackagingEntriesSection(draft?.packagingEntries || [], ensureRunId);
  const packagingPackagedInp = el('input', { type: 'datetime-local', value: stages.packaging?.packagedAt || '' });
  const packagingQcCheck = buildQualityCheckBox('LKE characterization', stages.packaging || {}, { omitSlurrySolids: true });
  const packagingSampleCollectedInp = el('input', { type: 'datetime-local',
    value: stages.packaging?.sampleCollectedAt ? stages.packaging.sampleCollectedAt.replace('Z', '').slice(0, 16) : '' });
  const packagingSamplePointBox = el('div', { class: 'qc-check-box theme-sample' },
    el('div', { class: 'qc-check-title' }, 'Sample Point'),
    el('div', { class: 'qc-check-subtitle' }, 'LKE characterization'),
    field('Collection date and time', packagingSampleCollectedInp),
    buildSamplePointsSection(draft?.samplePoints || [], ensureRunId, draft?.processingLot,
      () => packagingSampleCollectedInp.value, 'packaging'));
  const packagingStatus = el('span', { class: 'help' });
  const packagingSaveBtn = el('button', {
    type: 'button', class: 'secondary', onclick: async () => {
      packagingStatus.textContent = ''; packagingSaveBtn.disabled = true;
      try {
        const rid = await ensureRunId();
        await api('PUT', '/production/' + rid + '/stages/packaging', {
          packagedAt: packagingPackagedInp.value || null,
          ...packagingQcCheck.getPayload(),
          sampleCollectedAt: packagingSampleCollectedInp.value || null,
        });
        packagingStatus.textContent = 'Saved.';
      } catch (e) { packagingStatus.textContent = e.message; }
      packagingSaveBtn.disabled = false;
    }
  }, 'Save');

  const body = el('div', {},
    draft ? el('div', { class: 'summary-line', style: 'margin-bottom:10px' },
      sl('Run code', mono(draft.processingLot)), sl('SKU', skuName(draft.sku))) : null,
    el('details', { class: 'accordion' }, el('summary', {}, 'Initiation'),
      el('div', { class: 'accordion-body' },
        el('div', { class: 'form-row' },
          field('Product SKU', skuSel),
          field('Run date', el('input', { type: 'date', id: 'r_date', value: draft?.runDate || new Date().toISOString().slice(0, 10) }))),
        specPanel,
        el('div', { class: 'form-row', style: 'margin-top:8px' },
          field('Production Location', productionLocationSelect('r_loc', draft?.location)),
          field('Operators', operatorsSelect.el)),
        field('Notes', el('textarea', { id: 'r_notes', rows: '2', placeholder: 'Optional batch notes' }, draft?.notes || '')))),
    el('details', { class: 'accordion', ...(fresh ? { open: '' } : {}) }, el('summary', {}, 'Feedstock'),
      el('div', { class: 'accordion-body' },
        field('Filter totes', generalFilterInp),
        field('Stabilization method', stabFilterSel), pickHost, summary,
        el('h4', { style: 'margin:14px 0 4px;font-size:13px' }, 'Feedstock characterization'), feedstockHost)),
    homogenizationSection, extractionSection, separationSection,
    pasteurizationSection.box,
    el('details', { class: 'accordion' }, el('summary', {}, 'Dilution & Preservation'),
      el('div', { class: 'accordion-body' }, dilutionSummary.box, dilutionsSection)),
    el('details', { class: 'accordion' }, el('summary', {}, 'Packaging'),
      el('div', { class: 'accordion-body' },
        field('Packaging date and time', packagingPackagedInp),
        packagingEntriesSection.box,
        packagingQcCheck.box,
        packagingSamplePointBox,
        el('div', { style: 'margin-top:6px' }, packagingSaveBtn, packagingStatus))));
  filterTotes();
  renderFeedstockCards();
  renderSpecPanel();

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
    const r = draftId
      ? await api('POST', '/production/drafts/' + draftId + '/finalize', payload)
      : await api('POST', '/production', payload);
    toast(`Run ${r.processingLot}: ${fmt(r.inputKg, 0)} kg → ${fmt(r.outputLitres, 0)} L`);
    render();
  }
  modal(draft ? 'Production run — ' + draft.processingLot : 'New production run', body, finalizeRun, 'Finalize run',
    { extraLabel: 'Save & close', onExtra: saveDraft, wide: true });
}

// Post-finalize view: feedstock characterization + process stages can still be
// filled in or corrected at any time (matching how the real paper logs are
// often completed days after the run), independent of the locked-in
// tote-consumption / packaging numbers (corrected via the separate Edit modal).
async function openProcessLog(run) {
  const stages = run.stages || {};
  const feedstockHost = el('div', {});
  (run.inputs || []).forEach(inp => {
    feedstockHost.append(buildFeedstockCard({
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
  const extractionSection = buildExtractionSection(getRunId, stages.extraction);
  const separationSection =
    buildSeparationSection(getRunId, stages.separation, run.samplePoints || [], run.processingLot);
  // SKU is fixed once a run is finalized, so TDS/pH/Ksorbate targets need no
  // refresh wiring here.
  const pasteurizationSection = buildPasteurizationSection(
    getRunId, stages.pasteurization, run.samplePoints || [], run.processingLot,
    () => run.targetTds);
  const dilutionSummary = buildDilutionAndPreservativesBox(
    getRunId, stages.dilution,
    () => State.ref.skus.find(s => s.code === run.sku)?.phTarget,
    () => State.ref.skus.find(s => s.code === run.sku)?.ksorbateTarget,
    () => pasteurizationSection.getTargetFillLevel());
  const dilutionsSection = buildDilutionsSection(run.dilutions || [], getRunId);
  const packagingEntriesSection = buildPackagingEntriesSection(run.packagingEntries || [], getRunId);
  const packagingPackagedInp = el('input', { type: 'datetime-local', value: stages.packaging?.packagedAt || '' });
  const packagingQcCheck = buildQualityCheckBox('LKE characterization', stages.packaging || {}, { omitSlurrySolids: true });
  const packagingSampleCollectedInp = el('input', { type: 'datetime-local',
    value: stages.packaging?.sampleCollectedAt ? stages.packaging.sampleCollectedAt.replace('Z', '').slice(0, 16) : '' });
  const packagingSamplePointBox = el('div', { class: 'qc-check-box theme-sample' },
    el('div', { class: 'qc-check-title' }, 'Sample Point'),
    el('div', { class: 'qc-check-subtitle' }, 'LKE characterization'),
    field('Collection date and time', packagingSampleCollectedInp),
    buildSamplePointsSection(run.samplePoints || [], getRunId, run.processingLot,
      () => packagingSampleCollectedInp.value, 'packaging'));
  const packagingStatus = el('span', { class: 'help' });
  const packagingSaveBtn = el('button', {
    type: 'button', class: 'secondary', onclick: async () => {
      packagingStatus.textContent = ''; packagingSaveBtn.disabled = true;
      try {
        await api('PUT', '/production/' + run.id + '/stages/packaging', {
          packagedAt: packagingPackagedInp.value || null,
          ...packagingQcCheck.getPayload(),
          sampleCollectedAt: packagingSampleCollectedInp.value || null,
        });
        packagingStatus.textContent = 'Saved.';
      }
      catch (e) { packagingStatus.textContent = e.message; }
      packagingSaveBtn.disabled = false;
    }
  }, 'Save');

  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Run', run.processingLot), sl('SKU', skuName(run.sku)),
      el('span', { class: 'muted' }, 'Each section saves independently and can be filled in or corrected any time.')),
    el('details', { class: 'accordion' }, el('summary', {}, 'Feedstock characterization'),
      el('div', { class: 'accordion-body' }, feedstockHost)),
    homogenizationSection, extractionSection, separationSection,
    pasteurizationSection.box,
    el('details', { class: 'accordion' }, el('summary', {}, 'Dilution & Preservation'),
      el('div', { class: 'accordion-body' }, dilutionSummary.box, dilutionsSection)),
    el('details', { class: 'accordion' }, el('summary', {}, 'Packaging'),
      el('div', { class: 'accordion-body' },
        field('Packaging date and time', packagingPackagedInp),
        packagingEntriesSection.box,
        packagingQcCheck.box,
        packagingSamplePointBox,
        el('div', { style: 'margin-top:6px' }, packagingSaveBtn, packagingStatus))));

  modal('Process log — ' + run.processingLot, body, async () => { render(); }, 'Done', { wide: true });
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
        badge(f.status, f.status), rowActions([
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
      field('Status', selectFrom('', [['on_hand', 'On hand'], ['hold', 'Hold / QA'], ['sold', 'Sold / shipped']], null, 'f_status'))),
    el('div', { class: 'form-row' },
      field('TDS (%)', el('input', { type: 'number', step: '0.1', id: 'f_tds', value: f.tds ?? '' })),
      field('Location', el('input', { id: 'f_loc', value: f.location || '' }))));
  body.querySelector('#f_status').value = f.status;
  modal('Edit FG lot ' + f.lot, body, async () => {
    await api('PUT', '/fg/' + f.id, { qty: +body.querySelector('#f_qty').value, status: body.querySelector('#f_status').value, tds: body.querySelector('#f_tds').value || null, location: body.querySelector('#f_loc').value });
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

/* ---------------- Consumables ---------------- */
async function pageConsumables(v) {
  v.append(el('div', { class: 'page-head' }, el('h2', {}, 'Consumables & Packaging'),
    el('div', { class: 'actions' }, el('button', { onclick: addConsumable }, '+ Add item'))));
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
  function draw() {
    host.innerHTML = '';
    const allCb = el('input', { type: 'checkbox', title: 'Select all', onchange: () => {
      r.consumables.forEach(c => allCb.checked ? selected.add(c.id) : selected.delete(c.id)); draw();
    } });
    allCb.checked = r.consumables.length > 0 && r.consumables.every(c => selected.has(c.id));
    host.append(table(
      [allCb, 'Item', 'Location', 'On hand', 'Reorder at', 'Cost/unit', '', 'Actions'],
      r.consumables.map(c => [
        rowCheck(c, selected, updateBulk),
        c.name, c.location || '—', fmt(c.onHand, 1) + ' ' + c.unit, fmt(c.reorderLevel, 1), c.costPerUnit != null ? '$' + fmt(c.costPerUnit, 2) : '—',
        badge(c.low ? 'low' : 'ok', c.low ? 'LOW' : 'OK'),
        rowActions([['Receive', () => adjustC(c, 1)], ['Use', () => adjustC(c, -1)],
          ['Dispose', () => disposeConsumables([c]), 'danger'], ['Edit', () => editC(c)]])
      ]), [false, false, false, true, true, true, false, false]));
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
  const body = el('div', {},
    el('div', { class: 'form-row' },
      field('Reorder level', el('input', { type: 'number', id: 'c_re', value: c.reorderLevel, step: '0.1' })),
      field('Cost per unit', el('input', { type: 'number', id: 'c_cost', value: c.costPerUnit ?? '', step: '0.01' }))),
    field('Warehouse location', editableSelect(locs, 'c_loc')));
  body.querySelector('#c_loc').value = c.location || '';
  modal('Edit ' + c.name, body, async () => {
    await api('PUT', '/consumables/' + c.id, { reorderLevel: +body.querySelector('#c_re').value, costPerUnit: body.querySelector('#c_cost').value || null, location: body.querySelector('#c_loc').value });
    State.ref = await api('GET', '/refdata');
    toast('Updated'); render();
  }, 'Save');
}
function addConsumable() {
  const locs = State.ref.locations.map(l => [l, l]);
  const body = el('div', {},
    el('div', { class: 'form-row' }, field('Name', el('input', { id: 'n_name' })), field('Unit', el('input', { id: 'n_unit', value: 'kg' }))),
    el('div', { class: 'form-row' }, field('On hand', el('input', { type: 'number', id: 'n_oh', value: '0' })),
      field('Reorder level', el('input', { type: 'number', id: 'n_re', value: '0' }))),
    el('div', { class: 'form-row' }, field('Cost per unit', el('input', { type: 'number', id: 'n_cost', step: '0.01' })),
      field('Warehouse location', editableSelect(locs, 'n_loc'))));
  modal('Add consumable / packaging', body, async () => {
    await api('POST', '/consumables', { name: body.querySelector('#n_name').value, unit: body.querySelector('#n_unit').value, onHand: +body.querySelector('#n_oh').value, reorderLevel: +body.querySelector('#n_re').value, costPerUnit: body.querySelector('#n_cost').value || null, location: body.querySelector('#n_loc').value });
    State.ref = await api('GET', '/refdata');
    toast('Added'); render();
  }, 'Add');
}

/* ---------------- Reports ---------------- */
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
      sl('Citric', fmt(pr.citricKg, 1) + ' kg'), sl('Sorbate', fmt(pr.sorbateKg, 1) + ' kg')),
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
    el('div', { class: 'card' }, el('h3', {}, 'Consumables — received / used (' + d.period + ')'),
      table(['Item', 'Received', 'Used'], d.consumables.inMonth.map(r => [r.name + ' (' + r.unit + ')', num(fmt(r.received, 1)), num(fmt(r.used, 1))]), [false, true, true],
        i => openLedger('consumable', d.consumables.inMonth[i].name))),
    el('div', { class: 'card' }, el('h3', {}, 'Consumables on hand — ' + d.asOf),
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
      el('h3', {}, 'Consumables / packaging by location'),
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
        sl('Consumable write-offs', fmt(dz.consumableEvents))),
      (dz.lines && dz.lines.length)
        ? table(['Date', 'Type', 'Item', 'Qty', 'Reason', 'By'],
          dz.lines.map(l => [l.date, l.type, mono(l.ref), fmt(l.qty, 1) + ' ' + (l.unit || ''), l.reason, l.by || '—']),
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
    ${sec('Consumables received / used', ['Item', 'Received', 'Used'], d.consumables.inMonth.map(r => [r.name + ' (' + r.unit + ')', fmt(r.received, 1), fmt(r.used, 1)]), [0, 1, 1])}
    ${sec('Consumables on hand (' + d.asOf + ')', ['Item', 'On hand'], d.consumables.onHand.map(r => [r.name, fmt(r.onHand, 1) + ' ' + r.unit]), [0, 0])}
    ${sec('Stabilized by location (current)', ['Location', 'Totes', 'Kg'], (d.byLocation && d.byLocation.stabilized || []).map(r => [r.location, fmt(r.totes), fmt(r.kg, 0)]), [0, 1, 1])}
    ${sec('Finished goods by location (current)', ['Location', 'Units', 'Litres'], (d.byLocation && d.byLocation.finishedGoods || []).map(r => [r.location, fmt(r.units), fmt(r.litres, 0)]), [0, 1, 1])}
    ${sec('Consumables / packaging by location (current)', ['Location', 'Item', 'On hand'], (d.byLocation && d.byLocation.consumables || []).map(r => [r.location, r.name, fmt(r.onHand, 1) + ' ' + r.unit]), [0, 0, 0])}
    ${sec('Disposed / written off', ['Date', 'Type', 'Item', 'Qty', 'Reason', 'By'], (d.disposed && d.disposed.lines || []).map(l => [l.date, l.type, l.ref, fmt(l.qty, 1) + ' ' + (l.unit || ''), l.reason, l.by || '']), [0, 0, 0, 1, 0, 0])}
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
  add('CONSUMABLES'); add('Item', 'Unit', 'Received', 'Used', 'On hand (' + d.asOf + ')');
  const oh = {}; d.consumables.onHand.forEach(r => oh[r.name] = r.onHand);
  d.consumables.inMonth.forEach(r => add(r.name, r.unit, r.received, r.used, oh[r.name] ?? ''));
  add('');
  add('INVENTORY BY LOCATION (current)');
  add('Stabilized', 'Location', 'Totes', 'Kg'); (d.byLocation && d.byLocation.stabilized || []).forEach(r => add('', r.location, r.totes, r.kg));
  add('Finished goods', 'Location', 'Units', 'Litres'); (d.byLocation && d.byLocation.finishedGoods || []).forEach(r => add('', r.location, r.units, r.litres));
  add('Consumables', 'Location', 'Item', 'On hand', 'Unit'); (d.byLocation && d.byLocation.consumables || []).forEach(r => add('', r.location, r.name, r.onHand, r.unit));
  add('');
  add('DISPOSED / WRITTEN OFF'); add('Date', 'Type', 'Item', 'Qty', 'Unit', 'Reason', 'By');
  (d.disposed && d.disposed.lines || []).forEach(l => add(l.date, l.type, l.ref, l.qty, l.unit, l.reason, l.by));
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
  const a = el('a', { href: URL.createObjectURL(blob), download: 'kelpworks-report-' + d.month + '.csv' });
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
    title: '%Wet-Solids, (g/g)',
    formula: '%Wet-Solids = Wet-solids-wt (g) / (Wet-solids-wt (g) + Liquid-wt (g))',
    description: 'The wet-solids fraction of a homogenized tank sample, from a Process Check split of a weighed sample into its solid and liquid portions.',
    location: 'Production → Process log → Homogenization → Homogenization In → Process Check box',
    settings: [],
  },
  {
    title: 'Target fill level, Tank 2A/B (L)',
    formula: 'Target fill level, Tank 2A/B (L) = Tank 2A/B max level (L) × Target %Wet-Solids / %Wet-Solids',
    description: 'The initial fill volume the tank should be loaded to so that topping it up to the target %Wet-Solids with dilution water lands it exactly at Tank 2A/B’s max level (mass-conservation dilution math).',
    location: 'Production → Process log → Homogenization → Homogenization Out',
    settings: ['homog_tank_2ab_max_level_l'],
  },
  {
    title: 'Target fill level, Tank 6A/B (L)',
    formula: 'Target fill level, Tank 6A/B (L) = Tank 6A/B max level (L) × TDS target / TDS concentrated (%)',
    description: 'The largest initial volume that, once diluted from the measured TDS concentrated (%) down to the SKU’s TDS target, still fits Tank 6A/B’s max level (mass-conservation dilution math).',
    location: 'Production → Process log → Pasteurization → Pasteurization In → Process Check (Dilution requirements)',
    settings: ['dilution_tank_6ab_max_level_l'],
  },
  {
    title: 'Ksorbate, calculated (L)',
    formula: 'Ksorbate, calculated (L) = Fill level, Tank 6A/B (L) × Ksorbate target (w/v) / Ksorbate stock concentration (w/v)',
    description: 'The estimated volume of stock Ksorbate solution needed to reach the product SKU’s target Ksorbate dose in the tank’s current fill volume (mass-conservation dilution math).',
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
    description: 'A run’s total bottled output and IBC usage, computed from the Packaging table’s entries at finalization using each container unit’s litres-each value (Admin → Container Units).',
    location: 'Production → Packaging section, applied when a run is finalized',
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
async function pageLabels(v) {
  v.append(el('div', { class: 'page-head' }, el('h2', {}, 'Print Labels')));
  const src = selectFrom('', [['totes', 'Stabilized totes (in stock)'], ['fg', 'Finished goods (on hand)']], load, 'lbl_src');
  const host = el('div', {});
  v.append(el('div', { class: 'label-controls no-print' },
    field('Label source', src),
    el('button', { onclick: () => selectAll(host, true) }, 'Select all'),
    el('button', { class: 'secondary', onclick: () => selectAll(host, false) }, 'Clear'),
    el('button', { onclick: () => doPrint(host) }, '🖨 Print selected')), host);
  async function load() {
    host.innerHTML = '';
    let labels = [];
    if (src.value === 'totes') labels = (await api('GET', '/totes?status=in_stock')).totes.map(toteLabel);
    else labels = (await api('GET', '/fg?status=on_hand')).fg.map(f => fgLabel(f));
    if (!labels.length) { host.append(el('div', { class: 'empty card' }, 'Nothing to label here yet.')); return; }
    const sheet = el('div', { class: 'labels-sheet' });
    labels.forEach((lb, i) => {
      const wrapEl = el('label', { class: 'kelp-label', style: 'cursor:pointer' });
      const cb = el('input', { type: 'checkbox', class: 'no-print lbl-cb', checked: 'checked', style: 'position:absolute;margin:-6px 0 0 -6px' });
      wrapEl.dataset.idx = i; wrapEl._label = lb;
      wrapEl.append(cb, ...labelInner(lb));
      sheet.append(wrapEl);
    });
    host.append(sheet);
  }
  load();
}
function selectAll(host, on) { host.querySelectorAll('.lbl-cb').forEach(cb => cb.checked = on); }
function doPrint(host) {
  const chosen = [...host.querySelectorAll('.kelp-label')].filter(l => l.querySelector('.lbl-cb').checked).map(l => l._label);
  if (!chosen.length) return toast('Select at least one label.', true);
  printLabels(chosen);
}

/* label data builders */
function toteLabel(t) {
  return { kind: 'Stabilized Tote', lot: t.lot, barcode: t.lot, meta: [
    ['Species', speciesName(t.species)], ['Site', t.site], ['Avg wt', fmt(t.avgWeightKg, 1) + ' kg'],
    ['pH', t.ph ?? '—'], ['Harvest date', t.harvestDate || '—'], ['Loc', t.location || '—']] };
}
function fgLabel(f, run) {
  return { kind: 'Finished Good — LKE', lot: f.lot, barcode: f.lot, meta: [
    ['Product', skuName(f.sku)], ['Pack', f.packageSize], ['Units', fmt(f.qty)],
    ['TDS', f.tds != null ? f.tds + '%' : '—'], ['Produced', f.producedDate || (run && run.runDate) || '—']] };
}
function labelInner(lb) {
  return [
    el('div', { class: 'll-top' },
      el('span', { class: 'll-co' }, el('img', { src: 'logo.png', alt: '', style: 'height:16px;width:auto;margin-right:5px;vertical-align:middle' }), 'CASCADIA SEAWEED'),
      el('span', { class: 'll-kind' }, lb.kind)),
    el('div', { class: 'll-lot' }, lb.lot),
    el('div', { class: 'll-meta' }, ...lb.meta.map(([k, val]) => el('span', {}, el('b', {}, k + ': '), String(val)))),
    el('div', { class: 'svg-host', html: code128SVG(lb.barcode) }),
    el('div', { class: 'll-human' }, lb.barcode)
  ];
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
  const thead = el('thead', {}, el('tr', {}, ...headers.map((h, i) => el('th', { class: numCols[i] ? 'num' : '' }, h))));
  const tb = el('tbody', {});
  if (!rows.length) tb.append(el('tr', {}, el('td', { colspan: headers.length, class: 'empty' }, 'Nothing here yet.')));
  rows.forEach((r, ri) => {
    const cells = r.map((c, i) => el('td', { class: numCols[i] ? 'num' : '' }, c == null ? '—' : (c.nodeType ? c : String(c))));
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
  const card = el('div', { class: 'modal' + (opts.wide ? ' wide' : '') }, el('h3', {}, title), body, errBox, actions);
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
      el('button', { class: 'secondary', onclick: downloadDbBackup }, '⬇ Download database backup'),
      el('button', { onclick: addUser }, '+ Add user'))));
  const r = await api('GET', '/users');
  v.append(table(
    ['Name', 'Email', 'Role', 'Status', 'Actions'],
    r.users.map(u => [
      u.name, mono(u.email),
      badge(u.role === 'admin' ? 'hold' : 'on_hand', u.role === 'admin' ? 'Admin' : 'User'),
      u.active ? badge('on_hand', u.mustChange ? 'Must reset' : 'Active') : badge('disposed', 'Inactive'),
      rowActions([
        ['Reset password', () => resetUserPassword(u)],
        ['Edit', () => editUser(u)],
        u.active ? ['Deactivate', () => setUserActive(u, false), 'danger'] : ['Activate', () => setUserActive(u, true)]
      ])
    ]), [false, false, false, false, false]));
  v.append(el('div', { class: 'help', style: 'margin-top:10px' },
    'New users and password resets require the person to set a new password on next sign-in.'));

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

  // Container units: the Packaging table's "Container unit" dropdown
  // options, each mapped to a fixed litres-per-unit conversion (e.g.
  // IBC = 1000 L) -- distinct from the fixed IBC/4L/1L/250ml package sizes
  // (Calculations page) used by the Bottling/packaging output grid.
  v.append(el('div', { class: 'page-head', style: 'margin-top:28px' }, el('h2', {}, 'Container Units'),
    el('div', { class: 'actions' }, el('button', { onclick: addContainerUnit }, '+ Add container unit'))));
  const cur = await api('GET', '/container-units');
  v.append(table(
    ['Container unit', 'Litres each', 'Status', ''],
    cur.containerUnits.map(u => [
      u.code, fmt(u.litresEach, u.litresEach % 1 ? 2 : 0) + ' L',
      badge(u.active ? 'on_hand' : 'disposed', u.active ? 'Active' : 'Inactive'),
      rowActions([
        ['Edit', () => editContainerUnit(u)],
        u.active ? ['Deactivate', () => setContainerUnitActive(u, false), 'danger']
          : ['Activate', () => setContainerUnitActive(u, true)]
      ])
    ]), [false, false, false, false]));
  v.append(el('div', { class: 'help', style: 'margin-top:10px' },
    'Deactivating a container unit removes it from the Packaging table\'s picker without deleting past entries that used it.'));
}
function addContainerUnit() {
  const codeInp = el('input', { placeholder: 'e.g. 4 L' });
  const litresInp = el('input', { type: 'number', step: 'any', min: '0', placeholder: 'Litres per unit' });
  const body = el('div', {}, field('Container unit name', codeInp), field('Litres per unit', litresInp));
  modal('Add container unit', body, async () => {
    const code = codeInp.value.trim();
    if (!code) throw new Error('Enter a container unit name.');
    const litres = +litresInp.value;
    if (!litres || litres <= 0) throw new Error('Enter a positive litres-per-unit value.');
    await api('POST', '/container-units', { code, litresEach: litres });
    State.ref = await api('GET', '/refdata');
    toast('Container unit added'); render();
  }, 'Add');
}
function editContainerUnit(u) {
  const codeInp = el('input', { value: u.code });
  const litresInp = el('input', { type: 'number', step: 'any', min: '0', value: u.litresEach });
  const body = el('div', {}, field('Container unit name', codeInp), field('Litres per unit', litresInp),
    el('div', { class: 'help' }, 'Renaming updates every in-progress Packaging table entry and past Finished Goods lot that used it.'));
  modal('Edit ' + u.code, body, async () => {
    const code = codeInp.value.trim();
    if (!code) throw new Error('Enter a container unit name.');
    const litres = +litresInp.value;
    if (!litres || litres <= 0) throw new Error('Enter a positive litres-per-unit value.');
    await api('PUT', '/container-units/' + encodeURIComponent(u.code), { code, litresEach: litres });
    State.ref = await api('GET', '/refdata');
    toast('Container unit updated'); render();
  }, 'Save');
}
async function setContainerUnitActive(u, active) {
  if (!confirm((active ? 'Reactivate ' : 'Deactivate ') + u.code + '?')) return;
  await api('PUT', '/container-units/' + encodeURIComponent(u.code), { active });
  State.ref = await api('GET', '/refdata');
  toast('Container unit ' + (active ? 'reactivated' : 'deactivated'));
  render();
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
function addUser() {
  const body = el('div', {},
    el('div', { class: 'form-row' }, field('Name', el('input', { id: 'u_name' })),
      field('Email', el('input', { id: 'u_email', type: 'email' }))),
    el('div', { class: 'form-row' }, field('Temporary password', el('input', { id: 'u_pw', value: 'Cascadia123!' })),
      field('Role', selectFrom('', [['user', 'User'], ['admin', 'Administrator']], null, 'u_role'))),
    el('div', { class: 'help' }, 'They’ll be required to change this password on first sign-in.'));
  modal('Add user', body, async () => {
    await api('POST', '/users', { name: body.querySelector('#u_name').value, email: body.querySelector('#u_email').value, password: body.querySelector('#u_pw').value, role: body.querySelector('#u_role').value });
    toast('User created'); render();
  }, 'Create');
}
function editUser(u) {
  const body = el('div', { class: 'form-row' },
    field('Name', el('input', { id: 'ue_name', value: u.name })),
    field('Role', selectFrom('', [['user', 'User'], ['admin', 'Administrator']], null, 'ue_role')));
  body.querySelector('#ue_role').value = u.role;
  modal('Edit ' + u.email, body, async () => {
    await api('PUT', '/users/' + u.id, { name: body.querySelector('#ue_name').value, role: body.querySelector('#ue_role').value });
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
