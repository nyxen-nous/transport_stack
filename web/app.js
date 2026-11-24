(() => {
  const $ = id => document.getElementById(id);
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const api = async (path, params) => {
    const r = await fetch(path + '?' + new URLSearchParams(params));
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail || 'Request failed');
    return body;
  };

  // ---------- map ----------
  const map = L.map('map', {zoomControl: true}).setView([28.63, 77.2], 11);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 18, attribution: '&copy; OpenStreetMap contributors'}).addTo(map);
  const journeyLayer = L.layerGroup().addTo(map);
  const speedLayer = L.layerGroup();
  const css = getComputedStyle(document.documentElement);
  const busColor = css.getPropertyValue('--bus').trim() || '#1f3a2b';

  map.on('click', e => {
    const v = e.latlng.lat.toFixed(5) + ',' + e.latlng.lng.toFixed(5);
    (e.originalEvent.shiftKey ? $('to') : $('from')).value = v;
  });

  // ---------- stop autocomplete ----------
  for (const id of ['from', 'to']) {
    let timer;
    $(id).addEventListener('input', () => {
      clearTimeout(timer);
      const q = $(id).value.trim();
      if (q.length < 2 || /^-?\d/.test(q)) return;
      timer = setTimeout(async () => {
        try {
          const hits = await api('/api/stops', {q, limit: 8});
          $(id + '-list').innerHTML = hits.map(h => `<option value="${esc(h.name)}">${h.routes} routes</option>`).join('');
        } catch {}
      }, 180);
    });
  }
  $('swap').onclick = () => { [$('from').value, $('to').value] = [$('to').value, $('from').value]; };

  // ---------- journeys ----------
  let current = null;
  async function search() {
    $('error').hidden = true;
    $('results').innerHTML = '<p class="spinner">Finding journeys…</p>';
    const params = {from: $('from').value, to: $('to').value,
      depart: $('date').value + 'T' + $('time').value, live: $('live').checked, options: 3};
    try {
      const res = await api('/api/journeys', params);
      current = res;
      renderResults(res);
      loadProfile(params);
    } catch (e) {
      $('results').innerHTML = '';
      $('error').textContent = e.message;
      $('error').hidden = false;
    }
  }

  function bar(label, minutes, max, cls) {
    return `<div class="barrow"><span>${label}</span><i class="${cls}" style="width:${Math.max(4, 100 * minutes / max)}%"></i><b>${Math.round(minutes)} min</b></div>`;
  }

  function renderResults(res) {
    const q = res.query;
    const day = {weekday: 'weekday', saturday: 'Saturday', sunday: 'Sunday / holiday'}[q.day_type];
    let html = `<p class="meta">${esc(q.origin.label)} → ${esc(q.destination.label)} · ${q.depart.replace('T', ' ')} (${day}) · ${q.mode === 'historical' ? 'historical model' : 'live + historical'}</p>`;
    if (res.notes.length) html += `<p class="empty">${esc(res.notes.join(' '))}</p>`;
    res.options.forEach((o, n) => {
      const routes = o.legs.filter(l => l.type === 'bus').map(l => `<span class="chip bus">${esc(l.route)}</span>`).join('<span>→</span>');
      const max = Math.max(o.in_vehicle_minutes, o.timetable_in_vehicle_minutes, o.quiet_hour.in_vehicle_minutes);
      const delay = o.quiet_hour.traffic_delay_minutes;
      html += `<article class="card${n === 0 ? ' on' : ''}" data-i="${n}">
        <button class="card-head" type="button" aria-expanded="${n === 0}">
          <div class="routes">${routes}<span class="conf ${o.evidence.confidence}" title="How much GPS evidence supports this estimate">${o.evidence.confidence} confidence</span></div>
          <div class="big"><b>${o.total_minutes} min</b><small>likely ${o.total_range_minutes[0]}–${o.total_range_minutes[1]}</small></div>
          <div class="times">${o.depart} → arrive ${o.arrive} · ${o.transfers ? o.transfers + ' change' + (o.transfers > 1 ? 's' : '') : 'direct'} · ${o.walk_and_wait_minutes} min walking & waiting</div>
        </button>
        <div class="traffic"><b>${delay >= 1 ? `Traffic adds ~${Math.round(delay)} min` : 'Little traffic effect'}</b> to the bus ride compared with ${o.quiet_hour.time}, the quietest time of day.
          <div class="bars">${bar('Now', o.in_vehicle_minutes, max, '')}${bar('At ' + o.quiet_hour.time, o.quiet_hour.in_vehicle_minutes, max, 'q')}${bar('Timetable', o.timetable_in_vehicle_minutes, max, 't')}</div>
        </div>
        <ol class="legs">${o.legs.map(legHtml).join('')}</ol>
      </article>`;
    });
    $('results').innerHTML = html;
    $('results').querySelectorAll('.card').forEach(card => {
      card.querySelector('.card-head').onclick = () => {
        $('results').querySelectorAll('.card').forEach(c => c.classList.toggle('on', c === card));
        drawJourney(res.options[+card.dataset.i], res.query);
      };
    });
    if (res.options.length) drawJourney(res.options[0], res.query);
  }

  function legHtml(l) {
    if (l.type === 'walk') {
      const what = l.transfer ? `Walk to ${esc(l.to_stop)} to change` : l.to_stop ? `Walk to ${esc(l.to_stop)}` : 'Walk to your destination';
      return `<li><span class="t">${l.depart}</span><div>${what}<div class="d">${l.minutes} min · ${l.metres} m</div></div></li>`;
    }
    return `<li><span class="t">${l.depart}</span><div><span class="chip bus">${esc(l.route)}</span> from <b>${esc(l.board_stop)}</b> to <b>${esc(l.alight_stop)}</b>
      <div class="d">Wait ${l.wait_minutes} min · ride ${l.ride_minutes} min (likely ${l.ride_range_minutes[0]}–${l.ride_range_minutes[1]}) · ${l.stops} stops · ${l.distance_km} km${l.live_segments ? ` · live data on ${l.live_segments} stretches` : ''}</div>
      <div class="d">Moving ${l.running_minutes} min + stopped at stops ${l.dwell_minutes} min. Timetable says ${l.timetable_minutes} min.</div>
      <details><summary>${l.stops + 1} stops, arrive ${l.arrive}</summary><ol>${l.stop_names.map(s => `<li>${esc(s)}</li>`).join('')}</ol></details></div></li>`;
  }

  function drawJourney(o, q) {
    journeyLayer.clearLayers();
    const bounds = [];
    const dot = (ll, color, r = 6) => L.circleMarker(ll, {radius: r, color: '#fff', weight: 2, fillColor: color, fillOpacity: 1});
    dot([q.origin.lat, q.origin.lon], '#2f7d4a', 8).bindTooltip('Start').addTo(journeyLayer);
    dot([q.destination.lat, q.destination.lon], '#b3541e', 8).bindTooltip('Destination').addTo(journeyLayer);
    bounds.push([q.origin.lat, q.origin.lon], [q.destination.lat, q.destination.lon]);
    let prev = [q.origin.lat, q.origin.lon];
    for (const l of o.legs) {
      if (l.type === 'bus') {
        L.polyline([prev, l.path[0]], {color: '#7a8a7f', dashArray: '4 6', weight: 3}).addTo(journeyLayer);
        L.polyline(l.path, {color: busColor, weight: 6, opacity: .9}).bindTooltip(`${l.route}: ${l.ride_minutes} min`).addTo(journeyLayer);
        l.path.forEach((p, i) => { if (i === 0 || i === l.path.length - 1) dot(p, busColor, 5).bindTooltip(l.stop_names[i]).addTo(journeyLayer); });
        bounds.push(...l.path);
        prev = l.path.at(-1);
      } else if (l.to && l.transfer) {
        L.polyline([prev, l.to], {color: '#7a8a7f', dashArray: '4 6', weight: 3}).addTo(journeyLayer);
        prev = l.to;
      }
    }
    L.polyline([prev, [q.destination.lat, q.destination.lon]], {color: '#7a8a7f', dashArray: '4 6', weight: 3}).addTo(journeyLayer);
    if (activeTab === 'journey') map.fitBounds(bounds, {padding: [40, 40]});
  }

  // ---------- time-of-day profile ----------
  async function loadProfile(params) {
    try {
      const p = await api('/api/journeys/profile', {from: params.from, to: params.to, date: $('date').value});
      if (!p.series) { $('profile').hidden = true; return; }
      $('profile').hidden = false;
      $('profile-sub').textContent = `${p.summary}: total time if you left at each half hour (bus ride + ${p.walk_minutes} min walking; waiting not included). Shaded band = likely range. Dashed = timetable.`;
      $('chart').innerHTML = chart(p);
    } catch { $('profile').hidden = true; }
  }

  function chart(p) {
    const W = 440, H = 220, L0 = 34, R = 8, T = 10, B = 26;
    const s = p.series;
    const all = Object.values(s).flat();
    const hi = Math.max(...all.map(d => d.high), p.timetable_minutes) * 1.05;
    const lo = Math.max(0, Math.min(...all.map(d => d.low), p.timetable_minutes) * 0.9);
    const n = s.weekday.length;
    const x = i => L0 + (W - L0 - R) * i / (n - 1);
    const y = v => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
    const line = (pts, key) => pts.map((d, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(d[key]).toFixed(1)}`).join('');
    const colors = {weekday: busColor, saturday: '#c98f1d', sunday: '#7a8a7f'};
    let out = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Travel time by departure time">`;
    for (let v = Math.ceil(lo / 10) * 10; v <= hi; v += 10)
      out += `<line class="axis" x1="${L0}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L0 - 4}" y="${y(v) + 4}" text-anchor="end">${v}</text>`;
    s.weekday.forEach((d, i) => { if (d.time.endsWith(':00') && +d.time.slice(0, 2) % 3 === 0) out += `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${d.time}</text>`; });
    const band = s.weekday.map((d, i) => `${i ? 'L' : 'M'}${x(i)},${y(d.high)}`).join('') + s.weekday.slice().reverse().map((d, i) => `L${x(n - 1 - i)},${y(d.low)}`).join('') + 'Z';
    out += `<path d="${band}" fill="${busColor}" opacity=".13"/>`;
    out += `<line x1="${L0}" x2="${W - R}" y1="${y(p.timetable_minutes)}" y2="${y(p.timetable_minutes)}" stroke="#7a8a7f" stroke-dasharray="5 5"/>`;
    for (const k of ['sunday', 'saturday', 'weekday'])
      out += `<path d="${line(s[k], 'minutes')}" fill="none" stroke="${colors[k]}" stroke-width="${k === 'weekday' ? 2.6 : 1.6}"/>`;
    const [hh, mm] = $('time').value.split(':').map(Number);
    const sel = (hh * 60 + mm - 300) / 30;
    if (sel >= 0 && sel <= n - 1) out += `<line x1="${x(sel)}" x2="${x(sel)}" y1="${T}" y2="${H - B}" stroke="${busColor}" stroke-width="1" stroke-dasharray="2 3"/>`;
    out += `</svg><div class="legend" style="margin-top:6px"><span style="--c:${colors.weekday}">Weekday</span><span style="--c:${colors.saturday}">Saturday</span><span style="--c:${colors.sunday}">Sunday/holiday</span><span style="--c:#7a8a7f">Timetable</span></div>`;
    return out;
  }

  // ---------- network speeds ----------
  let activeTab = 'journey';
  const speedColor = r => r > .85 ? '#1a9850' : r > .72 ? '#91cf60' : r > .6 ? '#fee08b' : r > .5 ? '#fc8d59' : '#d73027';
  let speedTimer;
  async function loadSpeeds() {
    const b = +$('sp-time').value;
    const time = String(Math.floor(b / 2)).padStart(2, '0') + ':' + (b % 2 ? '30' : '00');
    $('sp-out').textContent = time;
    const data = await api('/api/segments', {day_type: $('sp-day').value, time});
    speedLayer.clearLayers();
    const renderer = L.canvas({padding: .2});
    for (const [a, b2, c, d, sp, r] of data.segments)
      L.polyline([[a, b2], [c, d]], {color: speedColor(r), weight: 3, opacity: .85, renderer, interactive: false}).addTo(speedLayer);
  }
  document.querySelectorAll('.maptabs button').forEach(btn => btn.onclick = () => {
    activeTab = btn.dataset.tab;
    document.querySelectorAll('.maptabs button').forEach(b => b.setAttribute('aria-selected', b === btn));
    const speeds = activeTab === 'speeds';
    $('speedctl').hidden = !speeds;
    if (speeds) { map.removeLayer(journeyLayer); speedLayer.addTo(map); loadSpeeds(); }
    else { map.removeLayer(speedLayer); journeyLayer.addTo(map); }
  });
  $('sp-time').oninput = () => { clearTimeout(speedTimer); speedTimer = setTimeout(loadSpeeds, 120); };
  $('sp-day').onchange = loadSpeeds;

  // ---------- wiring ----------
  $('search').onsubmit = e => { e.preventDefault(); search(); };
  document.querySelectorAll('[data-preset]').forEach(b => b.onclick = () => {
    const [f, t, d, tm] = b.dataset.preset.split('|');
    $('from').value = f; $('to').value = t; $('date').value = d; $('time').value = tm;
    search();
  });
  document.querySelectorAll('[data-open]').forEach(b => b.onclick = () => $(b.dataset.open).showModal());
  $('from').value = 'Kashmere Gate ISBT'; $('to').value = 'Nehru Place';
  search();
})();
