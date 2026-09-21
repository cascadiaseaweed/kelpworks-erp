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
     labels: pageLabels, admin: pageAdmin }[State.tab])(v);
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
async function openHarvest() {
  const sites = State.ref.sites.map(s => [s.code, s.code + ' — ' + s.name]);
  const species = State.ref.species.map(s => [s.code, s.common || s.name]);
  const locs = State.ref.locations.map(l => [l, l]);
  const ibcSources = (await api('GET', '/consumables')).consumables.filter(c => c.unit === 'tote');
  const ibcOpts = ibcSources.map(c => [String(c.id), c.name + ' (' + fmt(c.onHand, 0) + ' on hand)']);
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
      field('IBC tote source', ibcOpts.length ? selectFrom('', ibcOpts, null, 'h_ibc') : el('input', { id: 'h_ibc', disabled: 'disabled', placeholder: 'no IBC stock' })),
      field('pH', el('input', { type: 'number', id: 'h_ph', step: '0.1', placeholder: 'e.g. 3.7' }))),
    el('div', { class: 'help' }, 'The selected empty-IBC stock is reduced by the number of totes checked in.'),
    el('div', { class: 'help', id: 'h_preview' }));
  const c_count = body.querySelector('#h_count'), c_kg = body.querySelector('#h_kg');
  const upd = () => {
    const n = +c_count.value || 0, kg = +c_kg.value || 0;
    body.querySelector('#h_preview').textContent = n > 0 ? `Creates ${n} tote lot(s); average weight ${n ? (kg / n).toFixed(2) : 0} kg each.` : '';
  };
  c_count.addEventListener('input', upd); c_kg.addEventListener('input', upd); upd();
  modal('Check in a harvest batch', body, async () => {
    const ibcSel = body.querySelector('#h_ibc');
    const ibcId = ibcSel && ibcSel.value && !ibcSel.disabled ? +ibcSel.value : null;
    const payload = {
      site: body.querySelector('#h_site').value, species: body.querySelector('#h_species').value,
      checkinDate: body.querySelector('#h_date').value, location: body.querySelector('#h_loc').value,
      toteCount: +body.querySelector('#h_count').value, totalKg: +body.querySelector('#h_kg').value,
      ph: body.querySelector('#h_ph').value || null, ibcConsumableId: ibcId
    };
    const r = await api('POST', '/harvest', payload);
    State.ref = await api('GET', '/refdata');
    toast(`Created ${r.count} totes · ${r.avgWeightKg} kg each` + (r.ibcSource ? ` · ${r.count} from ${r.ibcSource}` : ''));
    render();
  }, 'Check in');
}

/* ---------------- Production ---------------- */
async function pageProduction(v) {
  v.append(el('div', { class: 'page-head' },
    el('h2', {}, 'Production Runs'),
    el('div', { class: 'actions' }, el('button', { onclick: () => openRun() }, '+ New production run'))));
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
    ['Separation', !!(stages.separation && stages.separation.startedAt) || (run.separationSolids || []).length > 0],
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
      el('h3', { style: 'margin:0' }, 'In-progress run', '  ', el('span', { class: 'badge hold' }, 'Not yet submitted')),
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
function fmtWhen(iso) { if (!iso) return '—'; return iso.replace('T', ' ').replace('Z', '').slice(0, 16); }
async function editRun(run) {
  const operatorsSelect = buildOperatorsSelect(run.operators || '');
  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Processing lot', run.processingLot), sl('SKU', skuName(run.sku)),
      el('span', { class: 'muted' }, 'Totes consumed & packaged output are fixed; correct the run details below.')),
    el('div', { class: 'form-row' },
      field('Run date', el('input', { type: 'date', id: 'e_date', value: run.runDate || todayStr() })),
      field('Target TDS (%)', el('input', { type: 'number', step: '0.1', id: 'e_tds', value: run.targetTds ?? '' }))),
    el('div', { class: 'form-row' },
      field('Citric acid (kg)', el('input', { type: 'number', step: '0.1', id: 'e_citric', value: run.citricKg ?? 0 })),
      field('Potassium sorbate (kg)', el('input', { type: 'number', step: '0.1', id: 'e_sorbate', value: run.sorbateKg ?? 0 }))),
    el('div', { class: 'form-row' },
      field('Production Location', productionLocationSelect('e_loc', run.location)),
      field('Operators', operatorsSelect.el)),
    field('Notes', el('textarea', { id: 'e_notes', rows: '2' }, run.notes || '')),
    el('div', { class: 'help' }, 'Changing citric / sorbate adjusts consumable stock by the difference. Every change is logged with your name.'));
  modal('Edit run — ' + run.processingLot, body, async () => {
    const r = await api('PUT', '/production/' + run.id, {
      runDate: body.querySelector('#e_date').value,
      targetTds: body.querySelector('#e_tds').value || null,
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
const STAGE_DEFS = {
  homogenization: { key: 'homogenization', title: 'Homogenization', fields: [
    ['startedAt', 'Started at', 'dt'], ['rinsingWaterL', 'Rinsing water (L)', 'num'],
    ['slurryL', 'Slurry (L)', 'num'], ['dilutionWaterL', 'Dilution water (L)', 'num'],
    ['citricKg', 'Citric acid added (kg)', 'num'], ['outputL', 'Output (L)', 'num']] },
  extraction: { key: 'extraction', title: 'Extraction', fields: [
    ['startedAt', 'Started at', 'dt'], ['amplitudePct', 'Amplitude (%)', 'num'],
    ['flowrateLpm', 'Flow rate (L/min)', 'num'], ['pressurePsi', 'Pressure (psi)', 'num'],
    ['startingPowerW', 'Starting power (W)', 'num']] },
  separation: { key: 'separation', title: 'Separation parameters', fields: [
    ['startedAt', 'Started at', 'dt'], ['flowrateLpm', 'Flow rate (L/min)', 'num'],
    ['meshMicron', 'Mesh size (micron)', 'num'], ['waterAdditionL', 'Water addition (L)', 'num']] },
  pasteurization: { key: 'pasteurization', title: 'Pasteurization', fields: [
    ['startedAt', 'Started at', 'dt'], ['productSetpointC', 'Product set-point (°C)', 'num'],
    ['boilerSetpointC', 'Boiler set-point (°C)', 'num'], ['totalVolumeL', 'Total volume (L)', 'num']] },
};
// A stage section is self-saving (its own small "Save" button, nothing required)
// so it never blocks finalizing a run and can be revisited at any time.
// `bare` skips the outer <details> wrapper, for stages folded into a bigger section.
function buildStageSection(getRunId, def, values, bare) {
  const inputs = {};
  const rows = def.fields.map(([key, label, kind]) => {
    let inp;
    if (kind === 'dt') { inp = el('input', { type: 'datetime-local', value: values?.[key] || '' }); }
    else {
      inp = el('input', { inputmode: 'decimal', placeholder: label });
      attachNumericMask(inp, 2);
      if (values && values[key] != null) inp.value = formatQcValue(values[key], 2);
    }
    inputs[key] = { inp, kind };
    return field(label, inp);
  });
  const status = el('span', { class: 'help' });
  const saveBtn = el('button', { type: 'button', class: 'secondary', onclick: save }, 'Save');
  async function save() {
    status.textContent = ''; saveBtn.disabled = true;
    try {
      const rid = await getRunId();
      const payload = {};
      for (const key in inputs) {
        const { inp, kind } = inputs[key];
        payload[key] = kind === 'num' ? (inp.value.trim() === '' ? null : qcParseValue(inp.value)) : (inp.value || null);
      }
      await api('PUT', '/production/' + rid + '/stages/' + def.key, payload);
      status.textContent = 'Saved.';
    } catch (e) { status.textContent = e.message; }
    saveBtn.disabled = false;
  }
  const content = el('div', {}, el('div', { class: 'form-row' }, ...rows),
    el('div', { style: 'margin-top:6px' }, saveBtn, status));
  if (bare) return content;
  return el('details', { class: 'accordion' }, el('summary', {}, def.title),
    el('div', { class: 'accordion-body' }, content));
}

const ODOUR_INTENSITIES = ['', 'Mild', 'Medium', 'Strong'];
// REF_odour.pdf — the standard odour vocabulary offered in the Feedstock
// dropdown; "Other" reveals a free-text field for anything not on this list.
const REF_ODOURS = ['Marine', 'Sweet (Apple Juice)', 'Sulfuric (Rotten Eggs)', 'Butyric (Sour Milk, Parmesan Cheese)', 'Other'];
// REF_ORP_classification.pdf — ORP (mV) is classified into one of these bands;
// the "ORP meter range" field is calculated from this, never typed by hand.
const REF_ORP_RANGES = [
  { min: -400, max: -201, label: 'Spoiled' },
  { min: -200, max: -51, label: 'Spoilage underway' },
  { min: -50, max: -1, label: 'Watch closely' },
  { min: 0, max: 400, label: 'Stable / safe zone' },
];
function classifyOrp(mv) {
  if (mv == null || Number.isNaN(mv)) return null;
  const band = REF_ORP_RANGES.find(r => mv >= r.min && mv <= r.max);
  return band ? band.label : 'Out of range';
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
// Multi-select operator picker (a run usually has more than one) built on the
// same floating-panel pattern as the QC sample-location dropdown. Stores/reads
// a comma-separated string of initials (+ any free-text "Other" names) so it
// round-trips through the existing `operators` text column unchanged.
function buildOperatorsSelect(initialValue) {
  const known = new Set();
  let otherText = '';
  (initialValue || '').split(',').map(s => s.trim()).filter(Boolean).forEach(tok => {
    const match = REF_OPERATORS.find(o => o.initials.toLowerCase() === tok.toLowerCase()
      || (o.first + ' ' + o.last).toLowerCase() === tok.toLowerCase() || o.last.toLowerCase() === tok.toLowerCase());
    if (match) known.add(match.initials); else otherText = otherText ? otherText + ', ' + tok : tok;
  });
  const btn = el('button', { type: 'button', class: 'qc-loc-select-btn' });
  const panel = el('div', { class: 'qc-loc-panel hidden' });
  const wrap = el('div', { class: 'qc-loc-select' }, btn, panel);
  const otherInput = el('input', { placeholder: 'Other operator name(s)', value: otherText });
  function currentValue() {
    const parts = REF_OPERATORS.filter(o => known.has(o.initials)).map(o => o.initials);
    if (otherInput.value.trim()) parts.push(otherInput.value.trim());
    return parts.join(', ');
  }
  function renderBtn() {
    btn.innerHTML = '';
    btn.append(el('span', {}, currentValue() || 'Select operators…'), el('span', { class: 'qc-loc-caret' }, '▾'));
  }
  function onDocClick(e) { if (!wrap.contains(e.target)) close(); }
  function open() {
    panel.innerHTML = '';
    REF_OPERATORS.forEach(o => {
      const cb = el('input', { type: 'checkbox', style: 'width:auto;flex:none' });
      cb.checked = known.has(o.initials);
      cb.addEventListener('change', () => { cb.checked ? known.add(o.initials) : known.delete(o.initials); renderBtn(); });
      panel.append(el('label', { class: 'qc-loc-option', style: 'display:flex;align-items:center;gap:8px;cursor:pointer' },
        cb, o.first + ' ' + o.last + ' (' + o.initials + ')'));
    });
    panel.append(el('div', { class: 'qc-loc-option' }, field('Other', otherInput)));
    panel.classList.remove('hidden');
    document.addEventListener('click', onDocClick, true);
  }
  function close() { panel.classList.add('hidden'); document.removeEventListener('click', onDocClick, true); }
  btn.addEventListener('click', () => { panel.classList.contains('hidden') ? open() : close(); });
  otherInput.addEventListener('input', renderBtn);
  renderBtn();
  return { el: wrap, get value() { return currentValue(); } };
}

// One tote's receiving-inspection card: photos, pH/ORP/odour readings and an
// accept/reject decision. `mode: 'draft'` keeps edits in memory (bundled into
// the outer save/finalize payload); `mode: 'completed'` saves immediately via
// its own Save button, since the run may already be finalized.
function buildFeedstockCard(opts) {
  const v = Object.assign({ loadedAt: '', ph: null, phMeasuredAt: null, orp: null, orpRange: '', odour: '', odourOther: '',
    odourIntensity: '', decision: 'accepted', rejectionReason: '', notes: '',
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
  const odourSel = el('select', {}, ...REF_ODOURS.map(o => el('option', { value: o }, o)));
  odourSel.value = REF_ODOURS.includes(v.odour) ? v.odour : (v.odour ? 'Other' : REF_ODOURS[0]);
  const odourOtherInp = el('input', { placeholder: 'Odour (other)', value: v.odourOther || (odourSel.value === 'Other' && v.odour && !REF_ODOURS.includes(v.odour) ? v.odour : '') });
  const odourOtherField = field('Odour (other)', odourOtherInp);
  odourOtherField.classList.toggle('hidden', odourSel.value !== 'Other');
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
      odour: odourSel.value,
      odourOther: odourSel.value === 'Other' ? (odourOtherInp.value.trim() || null) : null,
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
  odourSel.addEventListener('change', () => { odourOtherField.classList.toggle('hidden', odourSel.value !== 'Other'); notifyChange(); });
  decisionSel.addEventListener('change', () => { reasonField.classList.toggle('hidden', decisionSel.value !== 'rejected'); notifyChange(); });
  [loadedAt, odourOtherInp, intensitySel, notesInp]
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

  const fieldsRow = el('div', { class: 'form-row' },
    field('Loaded at', loadedAt), field('pH', el('div', {}, phInp, phMeasuredNote)),
    field('ORP (mV)', orpInp), field('ORP meter range (calculated)', orpRangeNote),
    field('Odour', odourSel), odourOtherField,
    field('Odour intensity', intensitySel), field('Decision', decisionSel));
  const bodyEls = [fieldsRow, reasonField, field('Notes', notesInp),
    el('div', { style: 'display:flex;gap:14px;flex-wrap:wrap;margin-top:8px' },
      photoSlot('surfacePhotoId', 'Surface photo'), photoSlot('striationPhotoId', 'Settling / striation photo'))];

  if (opts.mode === 'completed') {
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
  return el('details', { class: 'accordion feedstock-tote' },
    el('summary', {}, opts.label + (v.decision === 'rejected' ? '  ⚠ Rejected' : '')),
    el('div', { class: 'accordion-body' }, ...bodyEls));
}

// Totes inspected but never selected for the run (e.g. "rejected and discarded
// due to smell") — a small audit list, independent of tote_lots inventory.
function buildRejectedFeedstockSection(initialItems, getRunId) {
  let items = (initialItems || []).slice();
  const listHost = el('div', {});
  const toteInp = el('input', { placeholder: 'Tote lot #' });
  const reasonInp = el('input', { placeholder: 'Reason (e.g. smell)' });
  const status = el('div', { class: 'help' });
  function draw() {
    listHost.innerHTML = '';
    if (!items.length) { listHost.append(el('div', { class: 'help' }, 'None recorded.')); return; }
    listHost.append(table(['Tote lot', 'Reason', 'When', ''], items.map((it, i) => [
      el('span', { class: 'mono' }, it.toteLot), it.reason, fmtWhen(it.at),
      rowActions([['Remove', () => remove(i), 'danger']])
    ]), [false, false, false, false]));
  }
  async function persist() {
    status.textContent = 'Saving…';
    try {
      const rid = await getRunId();
      const r = await api('PUT', '/production/' + rid + '/rejected-feedstock', { items });
      items = r.run.rejectedFeedstock || [];
      status.textContent = '';
    } catch (e) { status.textContent = e.message; }
    draw();
  }
  async function add() {
    const toteLot = toteInp.value.trim(), reason = reasonInp.value.trim();
    if (!toteLot || !reason) { status.textContent = 'Enter both a tote lot and a reason.'; return; }
    items.push({ toteLot, reason, at: new Date().toISOString() });
    toteInp.value = ''; reasonInp.value = '';
    await persist();
  }
  async function remove(i) { items.splice(i, 1); await persist(); }
  draw();
  return el('div', {}, listHost,
    el('div', { class: 'form-row', style: 'margin-top:8px' }, field('Tote lot #', toteInp), field('Reason', reasonInp)),
    el('button', { type: 'button', class: 'secondary', onclick: add }, '+ Add'), status);
}

// Separation solids: a run may have several collections (e.g. multiple passes).
function buildSepSolidsSection(initial, getRunId, photoUrlFn) {
  let items = (initial || []).slice();
  const listHost = el('div', {});
  const status = el('div', { class: 'help' });
  function draw() {
    listHost.innerHTML = '';
    if (!items.length) { listHost.append(el('div', { class: 'help' }, 'No collections logged yet.')); return; }
    items.forEach(it => {
      const weightInp = el('input', { inputmode: 'decimal', placeholder: 'kg' }); attachNumericMask(weightInp, 2);
      if (it.weightKg != null) weightInp.value = formatQcValue(it.weightKg, 2);
      const whenInp = el('input', { type: 'datetime-local', value: it.loggedAt ? it.loggedAt.replace('Z', '').slice(0, 16) : '' });
      const notesInp = el('input', { placeholder: 'Notes', value: it.notes || '' });
      const img = el('img', { style: 'width:60px;height:60px;object-fit:cover;border-radius:6px' });
      if (it.photo && photoUrlFn) img.src = photoUrlFn(it.photo); else img.style.display = 'none';
      const fileInput = el('input', { type: 'file', accept: 'image/*', style: 'display:none' });
      const rowStatus = el('span', { class: 'help' });
      fileInput.addEventListener('change', async () => {
        const f = fileInput.files[0]; fileInput.value = ''; if (!f) return;
        const reader = new FileReader();
        reader.onload = async () => {
          img.src = String(reader.result); img.style.display = '';
          try {
            const b64 = String(reader.result).split(',')[1];
            const rid = await getRunId();
            const r = await api('POST', '/production/' + rid + '/separation-solids/' + it.id + '/photo',
              { filename: f.name, contentType: f.type || 'image/jpeg', dataB64: b64 });
            items = r.separationSolids;
          } catch (e) { rowStatus.textContent = e.message; }
        };
        reader.readAsDataURL(f);
      });
      const saveBtn = el('button', {
        type: 'button', class: 'secondary', onclick: async () => {
          try {
            const rid = await getRunId();
            const r = await api('PUT', '/production/' + rid + '/separation-solids/' + it.id, {
              weightKg: weightInp.value.trim() === '' ? null : qcParseValue(weightInp.value),
              loggedAt: whenInp.value || null, notes: notesInp.value.trim() || null
            });
            items = r.separationSolids; rowStatus.textContent = 'Saved.';
          } catch (e) { rowStatus.textContent = e.message; }
        }
      }, 'Save');
      const delBtn = el('button', {
        type: 'button', class: 'danger', onclick: async () => {
          if (!confirm('Remove this collection?')) return;
          const rid = await getRunId();
          const r = await api('DELETE', '/production/' + rid + '/separation-solids/' + it.id);
          items = r.separationSolids; draw();
        }
      }, 'Remove');
      listHost.append(el('div', { class: 'repeat-item' },
        el('div', { class: 'form-row' }, field('Weight (kg)', weightInp), field('Logged at', whenInp)),
        field('Notes', notesInp),
        el('div', { style: 'display:flex;gap:10px;align-items:center;margin-top:6px' },
          img, el('button', { type: 'button', class: 'secondary', onclick: () => fileInput.click() }, '📷 Photo'),
          fileInput, saveBtn, delBtn, rowStatus)));
    });
  }
  draw();
  const addBtn = el('button', {
    type: 'button', class: 'secondary', onclick: async () => {
      try { const rid = await getRunId(); const r = await api('POST', '/production/' + rid + '/separation-solids', {}); items = r.separationSolids; draw(); }
      catch (e) { status.textContent = e.message; }
    }
  }, '+ Add collection');
  return el('div', {}, listHost, addBtn, status);
}

// Dilution & Preservation: a run may split its output across several tanks.
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
  const addBtn = el('button', {
    type: 'button', class: 'secondary', onclick: async () => {
      try { const rid = await getRunId(); const r = await api('POST', '/production/' + rid + '/dilutions', {}); items = r.dilutions; draw(); }
      catch (e) { status.textContent = e.message; }
    }
  }, '+ Add tank');
  return el('div', {}, listHost, addBtn, status);
}

async function openRun(draftSummary) {
  // Re-fetch a resumed draft in full (list snapshots omit stage/solids/dilution detail).
  const draft = (draftSummary && draftSummary.id) ? (await api('GET', '/production/drafts/' + draftSummary.id)).run : draftSummary;
  const totes = (await api('GET', '/totes?status=in_stock')).totes;
  const skus = State.ref.skus;
  const skuSel = selectFrom('', skus.map(s => [s.code, s.name]), () => filterTotes(), 'r_sku');
  if (draft && draft.sku) skuSel.value = draft.sku;
  const search = el('input', { placeholder: 'Filter totes…', oninput: () => filterTotes() });
  const pickHost = el('div', { class: 'tote-pick' });
  const summary = el('div', { class: 'summary-line' });
  const feedstockHost = el('div', {});
  const pkgInputs = {};
  const draftQty = {};
  (draft?.packages || []).forEach(p => { draftQty[p.size] = p.qty; });
  const pkgGrid = el('div', { class: 'pkg-grid' }, ...Object.keys(State.ref.packageSizes).map(sz => {
    const inp = el('input', { type: 'number', min: '0', value: String(draftQty[sz] || 0), oninput: recompute });
    pkgInputs[sz] = inp;
    return field(sz + ' units', inp);
  }));
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

  function speciesOfSku() { const s = skus.find(x => x.code === skuSel.value); return s ? s.species : null; }
  function filterTotes() {
    const sp = speciesOfSku(), q = search.value.toLowerCase();
    const rows = totes.filter(t => (!sp || t.species === sp) && (!q || (t.lot + ' ' + (t.location || '')).toLowerCase().includes(q)));
    pickHost.innerHTML = '';
    const tbl = el('table', {},
      el('thead', {}, el('tr', {}, el('th', { class: 'checkcol' }, ''), el('th', {}, 'Lot'), el('th', {}, 'Site'), el('th', { class: 'num' }, 'Avg kg'), el('th', {}, 'pH'), el('th', {}, 'Location'))));
    const tb = el('tbody', {});
    for (const t of rows) {
      const cb = el('input', { type: 'checkbox', onchange: () => { cb.checked ? selected.add(t.id) : selected.delete(t.id); recompute(); renderFeedstockCards(); } });
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
      sl('Output', fmt(outL, 0) + ' L'), sl('Conversion factor', inputKg ? (outL / inputKg).toFixed(2) + ' L/kg' : '—'));
  }
  // Rebuilt only when tote selection changes (not on every keystroke elsewhere
  // in the modal) so in-progress typing inside a tote's card is never wiped.
  function renderFeedstockCards() {
    feedstockHost.innerHTML = '';
    const chosen = totes.filter(t => selected.has(t.id));
    if (!chosen.length) { feedstockHost.append(el('div', { class: 'help' }, 'Select totes above to characterize the feedstock.')); return; }
    for (const t of chosen) {
      feedstockHost.append(buildFeedstockCard({
        label: t.lot + (t.site ? '  ·  ' + t.site : ''),
        initial: feedstockState[t.id],
        mode: 'draft',
        onChange: vals => { feedstockState[t.id] = vals; },
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

  const rejectedHost = buildRejectedFeedstockSection(draft?.rejectedFeedstock || [], ensureRunId);
  const stages = draft?.stages || {};
  const homogSection = buildStageSection(ensureRunId, STAGE_DEFS.homogenization, stages.homogenization);
  const extractionSection = buildStageSection(ensureRunId, STAGE_DEFS.extraction, stages.extraction);
  const separationParams = buildStageSection(ensureRunId, STAGE_DEFS.separation, stages.separation, true);
  const sepSolidsSection = buildSepSolidsSection(draft?.separationSolids || [], ensureRunId, attId => attDownloadUrl(draftId, attId, false));
  const pasteurizationSection = buildStageSection(ensureRunId, STAGE_DEFS.pasteurization, stages.pasteurization);
  const dilutionsSection = buildDilutionsSection(draft?.dilutions || [], ensureRunId);
  const packagingStartedInp = el('input', { type: 'datetime-local', value: stages.packaging?.startedAt || '' });
  const packagingStatus = el('span', { class: 'help' });
  const packagingSaveBtn = el('button', {
    type: 'button', class: 'secondary', onclick: async () => {
      packagingStatus.textContent = ''; packagingSaveBtn.disabled = true;
      try {
        const rid = await ensureRunId();
        await api('PUT', '/production/' + rid + '/stages/packaging', { startedAt: packagingStartedInp.value || null });
        packagingStatus.textContent = 'Saved.';
      } catch (e) { packagingStatus.textContent = e.message; }
      packagingSaveBtn.disabled = false;
    }
  }, 'Save timestamp');

  const body = el('div', {},
    el('details', { class: 'accordion', open: '' }, el('summary', {}, 'Initiation'),
      el('div', { class: 'accordion-body' },
        el('div', { class: 'form-row' }, field('Finished-good SKU', skuSel),
          field('Target TDS (%)', el('input', { type: 'number', step: '0.1', id: 'r_tds', placeholder: 'e.g. 4.0', value: draft?.targetTds ?? '' }))),
        el('div', { class: 'form-row' },
          field('Run date', el('input', { type: 'date', id: 'r_date', value: draft?.runDate || new Date().toISOString().slice(0, 10) })),
          field('Production Location', productionLocationSelect('r_loc', draft?.location))),
        field('Operators', operatorsSelect.el),
        field('Notes', el('textarea', { id: 'r_notes', rows: '2', placeholder: 'Optional batch notes' }, draft?.notes || '')))),
    el('details', { class: 'accordion', open: '' }, el('summary', {}, 'Feedstock'),
      el('div', { class: 'accordion-body' },
        field('Select stabilized totes to process', search), pickHost, summary,
        el('h4', { style: 'margin:14px 0 4px;font-size:13px' }, 'Feedstock characterization'), feedstockHost,
        el('h4', { style: 'margin:14px 0 4px;font-size:13px' }, 'Rejected feedstock (inspected, not used)'), rejectedHost)),
    homogSection, extractionSection,
    el('details', { class: 'accordion' }, el('summary', {}, 'Separation'),
      el('div', { class: 'accordion-body' }, separationParams,
        el('h4', { style: 'margin:14px 0 4px;font-size:13px' }, 'Solids collections'), sepSolidsSection)),
    pasteurizationSection,
    el('details', { class: 'accordion' }, el('summary', {}, 'Dilution & Preservation'),
      el('div', { class: 'accordion-body' }, dilutionsSection)),
    el('details', { class: 'accordion' }, el('summary', {}, 'Packaging'),
      el('div', { class: 'accordion-body' },
        el('h4', { style: 'margin:0 0 8px;font-size:13px' }, 'Bottling / packaging output'), pkgGrid,
        el('div', { class: 'form-row' },
          field('Citric acid (kg)', el('input', { type: 'number', step: '0.1', min: '0', id: 'r_citric', value: draft?.citricKg ?? 0 })),
          field('Potassium sorbate (kg)', el('input', { type: 'number', step: '0.1', min: '0', id: 'r_sorbate', value: draft?.sorbateKg ?? 0 }))),
        field('Packaging started at', packagingStartedInp),
        el('div', { style: 'margin-top:6px' }, packagingSaveBtn, packagingStatus))));
  filterTotes();
  renderFeedstockCards();

  function buildPayload() {
    const packages = Object.keys(pkgInputs).map(sz => ({ size: sz, qty: +pkgInputs[sz].value || 0 })).filter(p => p.qty > 0);
    return {
      sku: skuSel.value, toteIds: [...selected], targetTds: body.querySelector('#r_tds').value || null,
      citricKg: +body.querySelector('#r_citric').value || 0, sorbateKg: +body.querySelector('#r_sorbate').value || 0,
      runDate: body.querySelector('#r_date').value, location: body.querySelector('#r_loc').value,
      operators: operatorsSelect.value,
      notes: body.querySelector('#r_notes').value, packages,
      feedstockDetails: feedstockState
    };
  }
  async function saveDraft(silent) {
    const payload = buildPayload();
    const r = draftId
      ? await api('PUT', '/production/drafts/' + draftId, payload)
      : await api('POST', '/production/drafts', payload);
    draftId = r.run.id;
    if (!silent) { toast('Progress saved — resume it anytime from “In progress”.'); render(); }
  }
  async function finalizeRun() {
    const payload = buildPayload();
    if (!payload.toteIds.length) throw new Error('Select at least one tote.');
    if (!payload.packages.length) throw new Error('Enter at least one packaged output quantity.');
    const r = draftId
      ? await api('POST', '/production/drafts/' + draftId + '/finalize', payload)
      : await api('POST', '/production', payload);
    toast(`Run ${r.processingLot}: ${fmt(r.inputKg, 0)} kg → ${fmt(r.outputLitres, 0)} L`);
    render();
  }
  modal(draft ? 'Resume production run' : 'New production run', body, finalizeRun, 'Create run',
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
  const rejectedHost = buildRejectedFeedstockSection(run.rejectedFeedstock || [], getRunId);
  const homogSection = buildStageSection(getRunId, STAGE_DEFS.homogenization, stages.homogenization);
  const extractionSection = buildStageSection(getRunId, STAGE_DEFS.extraction, stages.extraction);
  const separationParams = buildStageSection(getRunId, STAGE_DEFS.separation, stages.separation, true);
  const sepSolidsSection = buildSepSolidsSection(run.separationSolids || [], getRunId, attId => attDownloadUrl(run.id, attId, false));
  const pasteurizationSection = buildStageSection(getRunId, STAGE_DEFS.pasteurization, stages.pasteurization);
  const dilutionsSection = buildDilutionsSection(run.dilutions || [], getRunId);
  const packagingStartedInp = el('input', { type: 'datetime-local', value: stages.packaging?.startedAt || '' });
  const packagingStatus = el('span', { class: 'help' });
  const packagingSaveBtn = el('button', {
    type: 'button', class: 'secondary', onclick: async () => {
      packagingStatus.textContent = ''; packagingSaveBtn.disabled = true;
      try { await api('PUT', '/production/' + run.id + '/stages/packaging', { startedAt: packagingStartedInp.value || null }); packagingStatus.textContent = 'Saved.'; }
      catch (e) { packagingStatus.textContent = e.message; }
      packagingSaveBtn.disabled = false;
    }
  }, 'Save timestamp');

  const body = el('div', {},
    el('div', { class: 'summary-line' }, sl('Run', run.processingLot), sl('SKU', skuName(run.sku)),
      el('span', { class: 'muted' }, 'Each section saves independently and can be filled in or corrected any time.')),
    el('details', { class: 'accordion', open: '' }, el('summary', {}, 'Feedstock characterization'),
      el('div', { class: 'accordion-body' }, feedstockHost,
        el('h4', { style: 'margin:14px 0 4px;font-size:13px' }, 'Rejected feedstock (inspected, not used)'), rejectedHost)),
    homogSection, extractionSection,
    el('details', { class: 'accordion' }, el('summary', {}, 'Separation'),
      el('div', { class: 'accordion-body' }, separationParams,
        el('h4', { style: 'margin:14px 0 4px;font-size:13px' }, 'Solids collections'), sepSolidsSection)),
    pasteurizationSection,
    el('details', { class: 'accordion' }, el('summary', {}, 'Dilution & Preservation'),
      el('div', { class: 'accordion-body' }, dilutionsSection)),
    el('details', { class: 'accordion' }, el('summary', {}, 'Packaging'),
      el('div', { class: 'accordion-body' }, field('Packaging started at', packagingStartedInp),
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
    el('div', { class: 'actions' }, el('button', { onclick: addUser }, '+ Add user'))));
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
