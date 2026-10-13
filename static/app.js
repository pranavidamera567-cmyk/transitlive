const $ = (s) => document.querySelector(s);
const esc = (t) => String(t ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const api = (path) => fetch(path).then(async (r) => { const b = await r.json(); if (!r.ok) throw new Error(b.error || r.statusText); return b; });

const state = { net: null, routes: {}, visible: new Set(), stops: {}, selectedStop: null, ws: null, retry: 0 };
const map = L.map('map');
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);
const lineLayer = L.layerGroup().addTo(map), stopLayer = L.layerGroup().addTo(map);
const planLayer = L.layerGroup().addTo(map), vehicleLayer = L.layerGroup().addTo(map);
const vehicleMarkers = {}, routeLines = {};

const fmtEta = (sec) => (sec < 45 ? 'Due' : sec < 3600 ? `${Math.round(sec / 60)} min` : `${Math.floor(sec / 3600)}h ${Math.round((sec % 3600) / 60)}m`);
const delayClass = (sec) => (sec < 60 ? 'ontime' : sec < 300 ? 'late' : 'very-late');
const delayText = (sec) => (sec < 60 ? 'on time' : `${Math.round(sec / 60)} min late`);
const swatch = (color) => `<span class="swatch" style="background:${color}"></span>`;

// ---- static network ---------------------------------------------------------------------------
async function init() {
  state.net = await api('/api/network');
  map.setView(state.net.center, 13);
  state.net.routes.forEach((r) => { state.routes[r.id] = r; state.visible.add(r.id); });
  state.net.stops.forEach((s) => { state.stops[s.id] = s; });
  drawRoutes();
  drawStops();
  renderRouteToggles();
  const options = state.net.stops.slice().sort((a, b) => a.name.localeCompare(b.name))
    .map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
  $('#from').innerHTML = options;
  $('#to').innerHTML = options;
  $('#to').selectedIndex = Math.min(5, state.net.stops.length - 1);
  connect();
}

function drawRoutes() {
  lineLayer.clearLayers();
  state.net.routes.filter((r) => state.visible.has(r.id)).forEach((r) => {
    routeLines[r.id] = L.polyline(r.geometry, { color: r.color, weight: 5, opacity: 0.75 })
      .bindTooltip(r.name, { sticky: true }).addTo(lineLayer);
  });
}

function drawStops() {
  stopLayer.clearLayers();
  state.net.stops.filter((s) => s.routes.some((r) => state.visible.has(r))).forEach((s) => {
    L.circleMarker([s.lat, s.lon], { radius: 6, color: '#14213d', weight: 2, fillColor: '#fff', fillOpacity: 1 })
      .bindTooltip(s.name).on('click', () => selectStop(s.id)).addTo(stopLayer);
  });
}

function renderRouteToggles() {
  $('#routes').innerHTML = state.net.routes.map((r) => `
    <label class="route-toggle">
      <input type="checkbox" data-route="${r.id}" ${state.visible.has(r.id) ? 'checked' : ''}>
      ${swatch(r.color)}<span>${esc(r.name)}</span><span class="muted" style="margin-left:auto">every ${r.headway_min} min</span>
    </label>`).join('');
  document.querySelectorAll('[data-route]').forEach((box) => box.addEventListener('change', () => {
    box.checked ? state.visible.add(box.dataset.route) : state.visible.delete(box.dataset.route);
    drawRoutes(); drawStops(); subscribe();
    Object.keys(vehicleMarkers).forEach((id) => { if (!state.visible.has(id.split('-')[0])) { vehicleLayer.removeLayer(vehicleMarkers[id]); delete vehicleMarkers[id]; } });
  }));
}

// ---- live updates over WebSocket --------------------------------------------------------------
function connect() {
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  state.ws = ws;
  ws.onopen = () => { state.retry = 0; setConn(true); subscribe(); };
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.type === 'update') applyUpdate(m); };
  ws.onclose = () => { setConn(false); setTimeout(connect, Math.min(10000, 1000 * 2 ** state.retry++)); };
}

function subscribe() {
  if (state.ws?.readyState !== WebSocket.OPEN) return;
  state.ws.send(JSON.stringify({ action: 'subscribe', routes: [...state.visible], stop: state.selectedStop }));
}

function setConn(on) {
  const el = $('#conn');
  el.textContent = on ? 'live' : 'reconnecting…';
  el.className = `conn ${on ? 'on' : 'off'}`;
}

function busIcon(color, heading) {
  return L.divIcon({ className: 'bus-icon', iconSize: [22, 22],
    html: `<div class="bus" style="background:${color}"><span style="transform:rotate(${heading}deg)"></span></div>` });
}

function applyUpdate(m) {
  $('#clock').textContent = m.time;
  const seen = new Set();
  m.vehicles.forEach((v) => {
    if (!state.visible.has(v.route_id)) return;
    seen.add(v.id);
    const color = state.routes[v.route_id].color;
    const tip = `<b>${esc(state.routes[v.route_id].name)}</b> → ${esc(v.headsign)}<br>Next: ${esc(v.next_stop)}<br>` +
      `<span class="${delayClass(v.delay_sec)}">${delayText(v.delay_sec)}</span>`;
    let marker = vehicleMarkers[v.id];
    if (!marker) {
      marker = vehicleMarkers[v.id] = L.marker([v.lat, v.lon], { icon: busIcon(color, v.heading), zIndexOffset: 500 })
        .bindPopup(tip).addTo(vehicleLayer);
    } else {
      marker.setLatLng([v.lat, v.lon]).setIcon(busIcon(color, v.heading));
      marker.getPopup().setContent(tip);
    }
  });
  Object.keys(vehicleMarkers).forEach((id) => { if (!seen.has(id)) { vehicleLayer.removeLayer(vehicleMarkers[id]); delete vehicleMarkers[id]; } });
  $('#alerts').innerHTML = m.alerts.map((a) =>
    `<div class="alert">⚠ ${esc(a.route_name)}: +${Math.round(a.extra_delay_sec / 60)} min delays</div>`).join('');
  if (m.arrivals && m.arrivals.stop_id === state.selectedStop) renderArrivals(m.arrivals.items);
}

// ---- stops ------------------------------------------------------------------------------------
let searchTimer;
$('#stopSearch').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    const q = e.target.value.trim();
    if (!q) { $('#stopResults').innerHTML = ''; return; }
    const hits = await api(`/api/stops?q=${encodeURIComponent(q)}`);
    $('#stopResults').innerHTML = hits.slice(0, 6).map((s) =>
      `<li data-stop="${s.id}">${esc(s.name)} ${s.routes.map((r) => swatch(state.routes[r].color)).join('')}</li>`).join('')
      || '<li class="muted">No stops found</li>';
    document.querySelectorAll('[data-stop]').forEach((li) => li.addEventListener('click', () => {
      selectStop(li.dataset.stop, true); $('#stopResults').innerHTML = '';
    }));
  }, 200);
});

async function selectStop(id, pan = false) {
  state.selectedStop = id;
  const s = state.stops[id];
  if (pan) map.flyTo([s.lat, s.lon], 15);
  $('#stopInfo').innerHTML = `<h3>${esc(s.name)}</h3><div class="muted">Loading arrivals…</div>`;
  subscribe();
  const data = await api(`/api/stops/${id}/arrivals?limit=6`);
  renderArrivals(data.arrivals);
}

function renderArrivals(items) {
  const s = state.stops[state.selectedStop];
  $('#stopInfo').innerHTML = `<h3>${esc(s.name)}</h3>` + (items.length ? items.map((a) => `
    <div class="arrival">${swatch(a.color)}<span><b>${esc(a.route_id)}</b> to ${esc(a.headsign)}<br>
      <span class="muted">${a.live ? `<span class="${delayClass(a.delay_sec)}">${delayText(a.delay_sec)}</span> · expected ${a.expected}` : `scheduled ${a.scheduled}`}</span></span>
      <span class="eta">${fmtEta(a.eta_sec)}</span></div>`).join('') : '<div class="muted">No arrivals soon.</div>');
}

// ---- trip planner -----------------------------------------------------------------------------
$('#swap').addEventListener('click', () => { const f = $('#from').value; $('#from').value = $('#to').value; $('#to').value = f; });
$('#go').addEventListener('click', async () => {
  planLayer.clearLayers();
  const out = $('#options');
  try {
    const { options } = await api(`/api/plan?from=${$('#from').value}&to=${$('#to').value}`);
    out.innerHTML = options.length ? options.map((o, i) => `
      <div class="option" data-opt="${i}">
        <div class="head"><span>${Math.round(o.total_sec / 60)} min ${o.label ? `<span class="muted">· ${o.label}</span>` : ''}</span>
          <span class="muted">${o.transfers ? `${o.transfers} transfer` : 'direct'}</span></div>
        ${o.legs.map((l) => `<div class="leg">${swatch(l.color)}<b>${esc(l.route_id)}</b> ${esc(l.from.name)} → ${esc(l.to.name)}
          <span class="muted">(${l.stops_between} stops, leaves in ${fmtEta(l.depart_in_sec)})</span></div>`).join('')}
      </div>`).join('') : '<div class="muted">No route found between these stops.</div>';
    document.querySelectorAll('[data-opt]').forEach((el) => el.addEventListener('click', () => {
      document.querySelectorAll('.option').forEach((x) => x.classList.remove('active'));
      el.classList.add('active');
      showOption(options[el.dataset.opt]);
    }));
    if (options.length) { document.querySelector('[data-opt="0"]').click(); }
  } catch (e) { out.innerHTML = `<div class="alert">${esc(e.message)}</div>`; }
});

function showOption(option) {
  planLayer.clearLayers();
  const bounds = [];
  option.legs.forEach((l) => {
    L.polyline(l.path, { color: '#fff', weight: 11, opacity: 0.9 }).addTo(planLayer);
    L.polyline(l.path, { color: l.color, weight: 7 }).addTo(planLayer);
    bounds.push(...l.path);
  });
  const first = option.legs[0], last = option.legs[option.legs.length - 1];
  [[first.from, 'Start'], [last.to, 'End'], ...option.legs.slice(1).map((l) => [l.from, 'Transfer'])].forEach(([stop, label]) => {
    const s = state.stops[stop.id];
    L.circleMarker([s.lat, s.lon], { radius: 9, color: '#14213d', weight: 3, fillColor: '#ffd166', fillOpacity: 1 })
      .bindTooltip(`${label}: ${s.name}`, { permanent: false }).addTo(planLayer);
  });
  map.fitBounds(bounds, { padding: [40, 40] });
}

init().catch((e) => { document.body.insertAdjacentHTML('beforeend', `<p class="alert">Could not load the network: ${esc(e.message)}</p>`); });
