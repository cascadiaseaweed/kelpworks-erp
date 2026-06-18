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
    show('app'); selectTab(State.tab);
  } catch (err) { logout(); }
}

/* ---------------- Router ---------------- */
function render() {
  const v = $('#view'); v.innerHTML = '';
  ({ dashboard: pageDashboard, stabilized: pageStabilized, production: pageProduction,
     fg: pageFG, shipping: pageShipping, consumables: pageConsumables, reports: pageReports,
     labels: pageLabels }[State.tab])(v);
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

/* ---------------- Stabilized inventory ---------------- */
let stabCache = [];
async function pageStabilized(v) {
  v.append(el('div', { class: 'page-head' },
    el('h2', {}, 'Stabilized Inventory'),
    el('div', { class: 'actions' }, el('button', { onclick: openHarvest }, '+ Check in harvest'))));
  const r = await api('GET', '/totes');
  stabCache = r.totes;
  const search = el('input', { placeholder: 'Search lot / location…', oninput: drawStab });
  const spcF = selectFrom('Species', [['', 'All species'], ...(State.ref.species.map(s => [s.code, s.common || s.name]))], drawStab);
  const stF = selectFrom('Status', [['', 'All'], ['in_stock', 'In stock'], ['consumed', 'Consumed'], ['disposed', 'Disposed']], drawStab);
  const bar = el('div', { class: 'toolbar' }, search, spcF, stF, el('span', { class: 'muted', id: 'stabCount' }));
  const bulkBar = el('div', { class: 'bulkbar hidden' });
  const host = el('div', {});
  v.append(bar, bulkBar, host);
  const selected = new Set();
  let visibleInStock = [];

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
    const q = search.value.toLowerCase(), sp = spcF.value, st = stF.value;
    const rows = stabCache.filter(t =>
      (!sp || t.species === sp) && (!st || t.status === st) &&
      (!q || (t.lot + ' ' + (t.location || '')).toLowerCase().includes(q)));
    visibleInStock = rows.filter(t => t.status === 'in_stock');
    // drop selections no longer visible/in-stock
    [...selected].forEach(id => { if (!visibleInStock.some(t => t.id === id)) selected.delete(id); });
    $('#stabCount').textContent = rows.length + ' totes · ' + fmt(rows.filter(x => x.status === 'in_stock').reduce((a, b) => a + (b.avgWeightKg || 0), 0), 0) + ' kg shown';
    host.innerHTML = '';
    const allCb = el('input', { type: 'checkbox', title: 'Select all in stock', onchange: () => {
      visibleInStock.forEach(t => allCb.checked ? selected.add(t.id) : selected.delete(t.id));
      drawStab();
    } });
    allCb.checked = visibleInStock.length > 0 && visibleInStock.every(t => selected.has(t.id));
    const t = table(
      [allCb, 'Lot number', 'Site', 'Species', 'Checked in', 'Avg kg', 'pH', 'Location', 'Status', ''],
      rows.map(t => [
        rowCheck(t, selected, updateBulk),
        mono(t.lot), t.site, speciesName(t.species), t.checkinDate || '—',
        num(fmt(t.avgWeightKg, 1)), phCell(t), t.location || '—',
        badge(t.status, t.status === 'in_stock' ? 'In stock' : t.status === 'disposed' ? 'Disposed' : 'Consumed'),
        rowActions([
          t.status === 'in_stock' ? ['Move', () => moveTote(t)] : null,
          t.status === 'in_stock' ? ['Update pH', () => updatePh(t)] : null,
          ['Label', () => printLabels([toteLabel(t)])],
          t.status === 'in_stock' ? ['Delete', () => delTote(t), 'danger'] : null
        ])
      ]), [false, false, false, false, false, true, true, false, false, false]);
    host.append(t);
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
  return el('span', {}, String(t.ph),
    t.phUpdated ? el('span', { class: 'help', style: 'margin-top:0' }, 'updated ' + t.phUpdated) : null);
}
async function updatePh(t) {
  const data = await api('GET', '/totes/' + t.id + '/ph');
  const history = el('div', {});
  function drawHistory(log) {
    history.innerHTML = '';
    if (!log.length) { history.append(el('div', { class: 'help' }, 'No pH readings logged yet.')); return; }
    history.append(el('div', { class: 'tablewrap', style: 'margin-top:6px' },
      el('table', {}, el('thead', {}, el('tr', {}, el('th', {}, 'Date'), el('th', { class: 'num' }, 'pH'), el('th', {}, 'Note'))),
        el('tbody', {}, ...log.map(r => el('tr', {},
          el('td', {}, r.date), el('td', { class: 'num' }, r.ph), el('td', { class: 'muted' }, r.note || '—')))))));
  }
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Tote', t.lot),
      sl('Current pH', data.ph == null ? '—' : data.ph),
      sl('Last updated', data.phUpdated || 'never')),
    el('div', { class: 'form-row' },
      field('New pH reading', el('input', { type: 'number', step: '0.1', id: 'p_ph', placeholder: 'e.g. 3.7' })),
      field('Reading date', el('input', { type: 'date', id: 'p_date', value: new Date().toISOString().slice(0, 10) }))),
    field('Note (optional)', el('input', { id: 'p_note', placeholder: 'who / instrument / observation' })),
    el('label', {}, 'Reading history'), history);
  drawHistory(data.phLog);
  modal('Update pH — ' + t.lot, body, async () => {
    const ph = body.querySelector('#p_ph').value;
    if (ph === '') throw new Error('Enter a pH value.');
    const r = await api('POST', '/totes/' + t.id + '/ph', {
      ph: +ph, date: body.querySelector('#p_date').value, note: body.querySelector('#p_note').value || null
    });
    toast('pH ' + r.tote.ph + ' logged on ' + r.tote.phUpdated);
    render();
  }, 'Log reading');
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
function openHarvest() {
  const sites = State.ref.sites.map(s => [s.code, s.code + ' — ' + s.name]);
  const species = State.ref.species.map(s => [s.code, s.common || s.name]);
  const locs = State.ref.locations.map(l => [l, l]);
  const body = el('div', {},
    el('div', { class: 'form-row' },
      field('Farm site', selectFrom('', sites, null, 'h_site')),
      field('Species', selectFrom('', species, null, 'h_species'))),
    el('div', { class: 'form-row' },
      field('Check-in date', el('input', { type: 'date', id: 'h_date', value: new Date().toISOString().slice(0, 10) })),
      field('Storage location', editableSelect(locs, 'h_loc'))),
    el('div', { class: 'form-row' },
      field('Number of totes', el('input', { type: 'number', id: 'h_count', min: '1', value: '1' })),
      field('Total harvest (kg)', el('input', { type: 'number', id: 'h_kg', min: '0', step: '0.01', placeholder: 'averaged across totes' }))),
    el('div', { class: 'form-row' },
      field('pH', el('input', { type: 'number', id: 'h_ph', step: '0.1', placeholder: 'e.g. 3.7' })), el('div', {})),
    el('div', { class: 'help', id: 'h_preview' }));
  const c_count = body.querySelector('#h_count'), c_kg = body.querySelector('#h_kg');
  const upd = () => {
    const n = +c_count.value || 0, kg = +c_kg.value || 0;
    body.querySelector('#h_preview').textContent = n > 0 ? `Creates ${n} tote lot(s); average weight ${n ? (kg / n).toFixed(2) : 0} kg each.` : '';
  };
  c_count.addEventListener('input', upd); c_kg.addEventListener('input', upd); upd();
  modal('Check in a harvest batch', body, async () => {
    const payload = {
      site: body.querySelector('#h_site').value, species: body.querySelector('#h_species').value,
      checkinDate: body.querySelector('#h_date').value, location: body.querySelector('#h_loc').value,
      toteCount: +body.querySelector('#h_count').value, totalKg: +body.querySelector('#h_kg').value,
      ph: body.querySelector('#h_ph').value || null
    };
    const r = await api('POST', '/harvest', payload);
    State.ref = await api('GET', '/refdata');
    toast(`Created ${r.count} totes · ${r.avgWeightKg} kg each`);
    render();
  }, 'Check in');
}

/* ---------------- Production ---------------- */
async function pageProduction(v) {
  v.append(el('div', { class: 'page-head' },
    el('h2', {}, 'Production Runs'),
    el('div', { class: 'actions' }, el('button', { onclick: openRun }, '+ New production run'))));
  const r = await api('GET', '/production');
  if (!r.runs.length) { v.append(el('div', { class: 'empty card' }, 'No production runs yet. Click “New production run” to process stabilized totes into finished goods.')); return; }
  for (const run of r.runs) {
    const fgList = run.fgLots.map(f => `${fmt(f.qty)} × ${f.packageSize}`).join(', ') || '—';
    const card = el('div', { class: 'card' },
      el('div', { class: 'page-head', style: 'margin:0 0 8px' },
        el('h3', { style: 'margin:0' }, mono(run.processingLot) , '  ', el('span', { class: 'pill' }, skuName(run.sku))),
        el('div', { class: 'actions' },
          el('button', { class: 'secondary', onclick: () => editRun(run) }, 'Edit'),
          el('button', { class: 'secondary', onclick: () => openAttachments(run) },
            '📎 Documents' + (run.attachments && run.attachments.length ? ' (' + run.attachments.length + ')' : '')),
          el('button', { class: 'secondary', onclick: () => printLabels(run.fgLots.map(f => fgLabel(f, run))) }, 'Print FG labels'))),
      el('div', { class: 'summary-line' },
        sl('Run date', run.runDate), sl('Input', fmt(run.inputKg, 1) + ' kg'),
        sl('Output', fmt(run.outputLitres, 0) + ' L'),
        sl('Dilution', run.inputKg ? (run.outputLitres / run.inputKg).toFixed(2) + ' L/kg' : '—'),
        sl('Target TDS', run.targetTds != null ? run.targetTds + '%' : '—'),
        sl('Citric', fmt(run.citricKg, 1) + ' kg'), sl('Sorbate', fmt(run.sorbateKg, 1) + ' kg'),
        sl('Packaged', fgList)),
      el('div', { class: 'muted', style: 'margin-top:8px;font-size:12px' },
        `Consumed ${run.inputTotes.length} tote(s): `, el('span', { class: 'mono' }, run.inputTotes.join(', '))),
      run.notes ? el('div', { class: 'muted', style: 'margin-top:4px;font-size:12px' }, '“' + run.notes + '”') : null,
      run.edits && run.edits.length ? editHistoryBlock(run.edits) : null);
    v.append(card);
  }
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
function fmtWhen(iso) { if (!iso) return '—'; return iso.replace('T', ' ').replace('Z', '').slice(0, 16); }
async function editRun(run) {
  const locs = State.ref.locations.map(l => [l, l]);
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Processing lot', run.processingLot), sl('SKU', skuName(run.sku)),
      el('span', { class: 'muted' }, 'Totes consumed & packaged output are fixed; correct the run details below.')),
    el('div', { class: 'form-row' },
      field('Run date', el('input', { type: 'date', id: 'e_date', value: run.runDate || todayStr() })),
      field('Target TDS (%)', el('input', { type: 'number', step: '0.1', id: 'e_tds', value: run.targetTds ?? '' }))),
    el('div', { class: 'form-row' },
      field('Citric acid (kg)', el('input', { type: 'number', step: '0.1', id: 'e_citric', value: run.citricKg ?? 0 })),
      field('Potassium sorbate (kg)', el('input', { type: 'number', step: '0.1', id: 'e_sorbate', value: run.sorbateKg ?? 0 }))),
    field('Location', editableSelect(locs, 'e_loc')),
    field('Notes', el('textarea', { id: 'e_notes', rows: '2' }, run.notes || '')),
    el('div', { class: 'help' }, 'Changing citric / sorbate adjusts consumable stock by the difference. Every change is logged with your name.'));
  body.querySelector('#e_loc').value = run.location || '';
  modal('Edit run — ' + run.processingLot, body, async () => {
    const r = await api('PUT', '/production/' + run.id, {
      runDate: body.querySelector('#e_date').value,
      targetTds: body.querySelector('#e_tds').value || null,
      citricKg: body.querySelector('#e_citric').value || 0,
      sorbateKg: body.querySelector('#e_sorbate').value || 0,
      location: body.querySelector('#e_loc').value,
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
  fileInput.addEventListener('change', async () => {
    const files = [...fileInput.files]; if (!files.length) return;
    status.textContent = 'Uploading ' + files.length + ' file(s)…';
    try {
      for (const f of files) await uploadAtt(run.id, f);
      fileInput.value = ''; status.textContent = ''; await refresh(); toast('Uploaded'); State.attachmentsChanged = true;
    } catch (e) { status.textContent = ''; toast(e.message, true); await refresh(); }
  });
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Run', run.processingLot),
      el('span', { class: 'muted' }, 'lab results, paper logs, images — PDF, images, Office docs (max 25 MB each)')),
    el('label', {}, 'Attached documents'), listHost,
    el('div', { style: 'margin-top:16px' }, el('label', {}, 'Add documents'), fileInput, status));
  await refresh();
  modal('Documents — ' + run.processingLot, body, async () => { if (State.attachmentsChanged) { State.attachmentsChanged = false; render(); } }, 'Done');
}
function sl(k, val) { return el('span', {}, k + ': ', el('b', {}, val)); }

async function openRun() {
  const totes = (await api('GET', '/totes?status=in_stock')).totes;
  const skus = State.ref.skus;
  const locs = State.ref.locations.map(l => [l, l]);
  const skuSel = selectFrom('', skus.map(s => [s.code, s.name]), () => filterTotes(), 'r_sku');
  const search = el('input', { placeholder: 'Filter totes…', oninput: () => filterTotes() });
  const pickHost = el('div', { class: 'tote-pick' });
  const summary = el('div', { class: 'summary-line' });
  const pkgInputs = {};
  const pkgGrid = el('div', { class: 'pkg-grid' }, ...Object.keys(State.ref.packageSizes).map(sz => {
    const inp = el('input', { type: 'number', min: '0', value: '0', oninput: recompute });
    pkgInputs[sz] = inp;
    return field(sz + ' units', inp);
  }));
  let selected = new Set();

  function speciesOfSku() { const s = skus.find(x => x.code === skuSel.value); return s ? s.species : null; }
  function filterTotes() {
    const sp = speciesOfSku(), q = search.value.toLowerCase();
    const rows = totes.filter(t => (!sp || t.species === sp) && (!q || (t.lot + ' ' + (t.location || '')).toLowerCase().includes(q)));
    pickHost.innerHTML = '';
    const tbl = el('table', {},
      el('thead', {}, el('tr', {}, el('th', { class: 'checkcol' }, ''), el('th', {}, 'Lot'), el('th', {}, 'Site'), el('th', { class: 'num' }, 'Avg kg'), el('th', {}, 'pH'), el('th', {}, 'Location'))));
    const tb = el('tbody', {});
    for (const t of rows) {
      const cb = el('input', { type: 'checkbox', onchange: () => { cb.checked ? selected.add(t.id) : selected.delete(t.id); recompute(); } });
      cb.checked = selected.has(t.id);
      tb.append(el('tr', {}, el('td', { class: 'checkcol' }, cb), el('td', { class: 'mono' }, t.lot), el('td', {}, t.site), el('td', { class: 'num' }, fmt(t.avgWeightKg, 1)), el('td', {}, t.ph ?? '—'), el('td', {}, t.location || '—')));
    }
    if (!rows.length) tb.append(el('tr', {}, el('td', { colspan: 6, class: 'empty' }, 'No in-stock totes for this species.')));
    tbl.append(tb); pickHost.append(tbl); recompute();
  }
  function recompute() {
    const chosen = totes.filter(t => selected.has(t.id));
    const inputKg = chosen.reduce((a, b) => a + (b.avgWeightKg || 0), 0);
    let outL = 0; for (const sz in pkgInputs) outL += (State.ref.packageSizes[sz] || 0) * (+pkgInputs[sz].value || 0);
    summary.innerHTML = '';
    summary.append(sl('Totes', chosen.length), sl('Input', fmt(inputKg, 1) + ' kg'),
      sl('Output', fmt(outL, 0) + ' L'), sl('Dilution', inputKg ? (outL / inputKg).toFixed(2) + ' L/kg' : '—'));
  }

  const body = el('div', {},
    el('div', { class: 'form-row' }, field('Finished-good SKU', skuSel),
      field('Target TDS (%)', el('input', { type: 'number', step: '0.1', id: 'r_tds', placeholder: 'e.g. 4.0' }))),
    field('Select stabilized totes to process', search), pickHost, summary,
    el('h3', { style: 'margin:16px 0 8px;font-size:14px' }, 'Bottling / packaging output'), pkgGrid,
    el('div', { class: 'form-row' },
      field('Citric acid (kg)', el('input', { type: 'number', step: '0.1', min: '0', id: 'r_citric', value: '0' })),
      field('Potassium sorbate (kg)', el('input', { type: 'number', step: '0.1', min: '0', id: 'r_sorbate', value: '0' }))),
    el('div', { class: 'form-row' },
      field('Run date', el('input', { type: 'date', id: 'r_date', value: new Date().toISOString().slice(0, 10) })),
      field('Location', editableSelect(locs, 'r_loc'))),
    field('Notes', el('textarea', { id: 'r_notes', rows: '2', placeholder: 'Optional batch notes' })));
  filterTotes();
  modal('New production run', body, async () => {
    const packages = Object.keys(pkgInputs).map(sz => ({ size: sz, qty: +pkgInputs[sz].value || 0 })).filter(p => p.qty > 0);
    if (!selected.size) throw new Error('Select at least one tote.');
    if (!packages.length) throw new Error('Enter at least one packaged output quantity.');
    const r = await api('POST', '/production', {
      sku: skuSel.value, toteIds: [...selected], targetTds: body.querySelector('#r_tds').value || null,
      citricKg: +body.querySelector('#r_citric').value || 0, sorbateKg: +body.querySelector('#r_sorbate').value || 0,
      runDate: body.querySelector('#r_date').value, location: body.querySelector('#r_loc').value,
      notes: body.querySelector('#r_notes').value, packages
    });
    toast(`Run ${r.processingLot}: ${fmt(r.inputKg, 0)} kg → ${fmt(r.outputLitres, 0)} L`);
    render();
  }, 'Create run');
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
  const defMonth = State.reportMonth || (now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0'));
  const monthInput = el('input', { type: 'month', id: 'rp_month', value: defMonth, style: 'width:auto', onchange: gen });
  v.append(el('div', { class: 'page-head' }, el('h2', {}, 'Reports'),
    el('div', { class: 'actions' },
      el('label', { style: 'margin:0 6px 0 0' }, 'Month'), monthInput,
      el('button', { class: 'secondary', onclick: () => printReport(State.reportData) }, '🖨 Print / PDF'),
      el('button', { onclick: downloadReportXlsx }, '⬇ Export Excel'),
      el('button', { class: 'secondary', onclick: () => exportReportCsv(State.reportData) }, 'CSV'))));
  const host = el('div', {});
  v.append(host);
  async function gen() {
    State.reportMonth = monthInput.value;
    host.innerHTML = '';
    host.append(el('div', { class: 'muted' }, 'Loading…'));
    const d = await api('GET', '/reports?month=' + encodeURIComponent(monthInput.value));
    State.reportData = d;
    host.innerHTML = '';
    renderReport(host, d);
  }
  gen();
}
function renderReport(host, d) {
  host.append(el('div', { class: 'muted', style: 'margin-bottom:10px' },
    'Activity during ', el('b', {}, monthLabel(d.month)),
    ' · on-hand balances as of ', el('b', {}, d.asOf)));
  host.append(el('div', { class: 'tiles' },
    tile('Stabilized created', fmt(d.stabilized.created.kg, 0), 'kg · ' + fmt(d.stabilized.created.totes) + ' totes', true),
    tile('Stabilized consumed', fmt(d.stabilized.consumed.kg, 0), 'kg · ' + fmt(d.stabilized.consumed.totes) + ' totes'),
    tile('LKE produced', fmt(d.production.outputLitres, 0), 'L · ' + fmt(d.production.runs) + ' runs'),
    tile('LKE shipped', fmt(d.finishedGoods.shippedLitres, 0), 'L'),
    tile('Stabilized on hand', fmt(d.stabilized.onHand.kg, 0), 'kg at ' + d.asOf),
    tile('Finished goods on hand', fmt(d.finishedGoods.onHandLitres, 0), 'L at ' + d.asOf),
    tile('Written off', fmt(d.disposed ? d.disposed.fgLitres : 0, 0), 'L FG · ' + fmt(d.disposed ? d.disposed.toteKg : 0, 0) + ' kg totes')));

  const spRows = src => src.bySpecies.map(r => [speciesName(r.species), fmt(r.totes), num(fmt(r.kg, 0))]);
  host.append(el('div', { class: 'grid2' },
    el('div', { class: 'card' }, el('h3', {}, 'Stabilized inventory — created (' + monthLabel(d.month) + ')'),
      table(['Species', 'Totes', 'Kg'], spRows(d.stabilized.created), [false, true, true])),
    el('div', { class: 'card' }, el('h3', {}, 'Stabilized inventory — consumed into production'),
      table(['Species', 'Totes', 'Kg'], spRows(d.stabilized.consumed), [false, true, true]))));
  host.append(el('div', { class: 'card' }, el('h3', {}, 'Stabilized inventory on hand — ' + d.asOf),
    table(['Species', 'Totes', 'Kg'], spRows(d.stabilized.onHand), [false, true, true])));

  const pr = d.production;
  host.append(el('div', { class: 'card' }, el('h3', {}, 'Production summary'),
    el('div', { class: 'summary-line' },
      sl('Runs', fmt(pr.runs)), sl('Input', fmt(pr.inputKg, 0) + ' kg'),
      sl('Output', fmt(pr.outputLitres, 0) + ' L'),
      sl('Yield', pr.yield != null ? pr.yield.toFixed(2) + ' L/kg' : '—'),
      sl('Citric', fmt(pr.citricKg, 1) + ' kg'), sl('Sorbate', fmt(pr.sorbateKg, 1) + ' kg')),
    pr.bySku.length ? table(['SKU', 'Runs', 'Litres produced'],
      pr.bySku.map(r => [skuName(r.sku), fmt(r.runs), num(fmt(r.litres, 0))]), [false, true, true]) : null));

  const fg = d.finishedGoods;
  host.append(el('div', { class: 'grid2' },
    el('div', { class: 'card' }, el('h3', {}, 'Finished goods shipped — by customer'),
      table(['Customer', 'Units', 'Litres'], fg.shippedByCustomer.map(r => [r.customer, fmt(r.units), num(fmt(r.litres, 0))]), [false, true, true])),
    el('div', { class: 'card' }, el('h3', {}, 'Finished goods on hand — ' + d.asOf),
      table(['SKU', 'Litres'], fg.onHand.map(r => [skuName(r.sku), num(fmt(r.litres, 0))]), [false, true]))));

  host.append(el('div', { class: 'grid2' },
    el('div', { class: 'card' }, el('h3', {}, 'Consumables — received / used (' + monthLabel(d.month) + ')'),
      table(['Item', 'Received', 'Used'], d.consumables.inMonth.map(r => [r.name + ' (' + r.unit + ')', num(fmt(r.received, 1)), num(fmt(r.used, 1))]), [false, true, true])),
    el('div', { class: 'card' }, el('h3', {}, 'Consumables on hand — ' + d.asOf),
      table(['Item', 'On hand'], d.consumables.onHand.map(r => [r.name, fmt(r.onHand, 1) + ' ' + r.unit]), [false, true]))));

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
      table(['Location', 'Item', 'On hand'], bl.consumables.map(r => [r.location, r.name, fmt(r.onHand, 1) + ' ' + r.unit]), [false, false, true])));
  }

  const dz = d.disposed;
  if (dz) {
    host.append(el('div', { class: 'card' }, el('h3', {}, 'Disposed / written off — ' + monthLabel(d.month)),
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
    <div class="hd"><div class="co"><img src="${location.origin}/logo.png" alt="">CASCADIA SEAWEED</div><div style="text-align:right"><h1 style="margin:0">MANUFACTURING REPORT</h1>${monthLabel(d.month)} · on hand as of ${d.asOf}</div></div>
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
  const m = (document.querySelector('#rp_month') || {}).value || State.reportMonth;
  if (!m) return toast('Pick a month first.', true);
  const url = '/api/reports/xlsx?month=' + encodeURIComponent(m) + '&token=' + encodeURIComponent(State.token);
  const a = el('a', { href: url, download: 'kelpworks-report-' + m + '.xlsx' });
  document.body.append(a); a.click(); a.remove();
}
function exportReportCsv(d) {
  if (!d) return toast('Nothing to export yet.', true);
  const lines = [];
  const add = (...cols) => lines.push(cols.map(c => '"' + String(c == null ? '' : c).replace(/"/g, '""') + '"').join(','));
  add('Cascadia Seaweed — Manufacturing Report', monthLabel(d.month), 'On hand as of', d.asOf);
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
    ['pH', t.ph ?? '—'], ['Checked in', t.checkinDate || '—'], ['Loc', t.location || '—']] };
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
function table(headers, rows, numCols = []) {
  const thead = el('thead', {}, el('tr', {}, ...headers.map((h, i) => el('th', { class: numCols[i] ? 'num' : '' }, h))));
  const tb = el('tbody', {});
  if (!rows.length) tb.append(el('tr', {}, el('td', { colspan: headers.length, class: 'empty' }, 'Nothing here yet.')));
  for (const r of rows) tb.append(el('tr', {}, ...r.map((c, i) => el('td', { class: numCols[i] ? 'num' : '' }, c == null ? '—' : (c.nodeType ? c : String(c))))));
  return el('div', { class: 'tablewrap' }, el('table', {}, thead, tb));
}
function mono(s) { return el('span', { class: 'mono' }, s); }
function badge(cls, txt) { return el('span', { class: 'badge ' + cls }, txt); }
function num(s) { return el('span', { class: 'num' }, s); }
function rowActions(items) { return el('span', { class: 'row-actions' }, ...items.filter(Boolean).map(([label, fn, cls]) => el('button', { class: (cls || 'secondary') + ' ', onclick: fn }, label))); }
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
function modal(title, body, onSubmit, submitLabel = 'Save') {
  const errBox = el('div', { class: 'error' });
  const submitBtn = el('button', {}, submitLabel);
  const card = el('div', { class: 'modal' }, el('h3', {}, title), body, errBox,
    el('div', { class: 'modal-actions' },
      el('button', { class: 'secondary', onclick: close }, 'Cancel'), submitBtn));
  const bg = el('div', { class: 'modal-bg', onclick: e => { if (e.target === bg) close(); } }, card);
  function close() { bg.remove(); }
  submitBtn.addEventListener('click', async () => {
    errBox.textContent = ''; submitBtn.disabled = true;
    try { await onSubmit(); close(); }
    catch (e) { errBox.textContent = e.message; submitBtn.disabled = false; }
  });
  $('#modalRoot').append(bg);
}

/* ---------------- start ---------------- */
if (State.token) boot(); else show('login');
