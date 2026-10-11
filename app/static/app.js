/* Bhoonidhi WPS Explorer: a small web client of the OGC WPS at /wps.
   It draws no conclusions of its own: every result comes from an ordinary WPS Execute request. */
(function () {
  'use strict';
  const WPS_NS = 'http://www.opengis.net/wps/1.0.0';
  const OWS_NS = 'http://www.opengis.net/ows/1.1';
  const DEMO = { s: 17.35, w: 78.40, n: 17.45, e: 78.55 };  // central Hyderabad, ~10 x 16 km
  const $ = (id) => document.getElementById(id);

  let info = null, scenes = [], map, coverageLayer, rect = null, cornerA = null, drawing = false;
  let bbox = null, overlay = null, resultUrls = [];

  // ---------- helpers ----------
  function setStatus(text, kind) { const s = $('status'); s.textContent = text || ''; s.className = 'status ' + (kind || ''); }
  function pointInRing(x, y, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i], [xj, yj] = ring[j];
      if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  function coveringScenes(b) {
    const corners = [[b.w, b.s], [b.w, b.n], [b.e, b.s], [b.e, b.n]];
    return scenes.filter((f) => corners.every(([x, y]) => pointInRing(x, y, f.geometry.coordinates[0])));
  }
  function sizeKm(b) {
    const midLat = (b.s + b.n) / 2 * Math.PI / 180;
    return { w: (b.e - b.w) * 111.32 * Math.cos(midLat), h: (b.n - b.s) * 110.57 };
  }
  // The service may advertise its public (tunnel) address in links; always talk to the origin serving this page.
  function local(href) { const u = new URL(href, location.href); return location.origin + u.pathname + u.search; }
  function fmt(v, d) { return typeof v === 'number' && isFinite(v) ? v.toFixed(d === undefined ? 3 : d) : '—'; }
  function row(table, k, v) {
    const tr = table.insertRow(); tr.insertCell().textContent = k; tr.insertCell().textContent = v;
  }

  // ---------- area selection ----------
  function setBbox(b) {
    bbox = { s: Math.max(-90, Math.min(b.s, b.n)), n: Math.min(90, Math.max(b.s, b.n)),
             w: Math.max(-180, Math.min(b.w, b.e)), e: Math.min(180, Math.max(b.w, b.e)) };
    if (rect) rect.remove();
    rect = L.rectangle([[bbox.s, bbox.w], [bbox.n, bbox.e]], { color: '#e8590c', weight: 2, fillOpacity: 0.08 }).addTo(map);
    refreshArea();
  }
  function refreshArea() {
    const sz = sizeKm(bbox), px = sz.w * sz.h * 1e6 / (info.resolution_m * info.resolution_m);
    const info1 = $('bbox-info');
    info1.className = 'meta';
    info1.textContent = `S ${fmt(bbox.s, 4)}  W ${fmt(bbox.w, 4)}  N ${fmt(bbox.n, 4)}  E ${fmt(bbox.e, 4)} · about `
      + `${fmt(sz.w, 1)} × ${fmt(sz.h, 1)} km (${fmt(px / 1e6, 2)} Mpixel)`;
    const date = $('date'), dinfo = $('date-info');
    date.innerHTML = '';
    let ok = true;
    if (px > info.max_pixels) {
      ok = false; info1.className = 'meta warn';
      info1.textContent += ` — too large: the limit is ${fmt(info.max_pixels / 1e6, 0)} Mpixel`;
    }
    const cover = coveringScenes(bbox).sort((a, b) => b.properties.date.localeCompare(a.properties.date));
    if (!cover.length) {
      ok = false;
      date.add(new Option('No prepared scene covers this area', ''));
      date.disabled = true;
      dinfo.className = 'meta warn';
      dinfo.textContent = 'This instance only holds the scenes outlined on the map. Move the box fully inside one outline '
        + '(the edges of a scene are slanted, so stay a little away from them).';
    } else {
      cover.forEach((f) => date.add(new Option(`${f.properties.date} · ${f.properties.id.slice(0, 22)}…`, f.properties.date)));
      date.disabled = false;
      dinfo.className = 'meta';
      dinfo.textContent = `${cover.length} prepared scene${cover.length > 1 ? 's' : ''} cover${cover.length > 1 ? '' : 's'} this area.`;
    }
    $('run').disabled = !ok;
  }
  function startDrawing() {
    drawing = true; cornerA = null; $('draw').classList.add('active');
    map.getContainer().style.cursor = 'crosshair';
    setStatus('Click the first corner of the area.', 'busy');
  }
  function stopDrawing() {
    drawing = false; cornerA = null; $('draw').classList.remove('active');
    map.getContainer().style.cursor = '';
  }
  function onMapClick(ev) {
    if (!drawing) return;
    if (!cornerA) {
      cornerA = ev.latlng;
      if (rect) rect.remove();
      rect = L.rectangle([cornerA, cornerA], { color: '#e8590c', weight: 2, dashArray: '4', fillOpacity: 0.05 }).addTo(map);
      setStatus('Now click the opposite corner.', 'busy');
    } else {
      const a = cornerA, b = ev.latlng;
      stopDrawing(); setStatus('');
      if (Math.abs(a.lat - b.lat) < 1e-4 || Math.abs(a.lng - b.lng) < 1e-4) { setStatus('That box is too small.', 'err'); return; }
      setBbox({ s: a.lat, n: b.lat, w: a.lng, e: b.lng });
    }
  }
  function onMouseMove(ev) { if (drawing && cornerA && rect) rect.setBounds(L.latLngBounds(cornerA, ev.latlng)); }
  function clearAll() {
    stopDrawing(); setStatus('');
    if (rect) { rect.remove(); rect = null; }
    if (overlay) { overlay.remove(); overlay = null; }
    resultUrls.forEach((u) => URL.revokeObjectURL(u)); resultUrls = [];
    bbox = null; $('result').hidden = true; $('run').disabled = true;
    $('bbox-info').className = 'meta'; $('bbox-info').textContent = 'No area selected.';
    $('date').innerHTML = '<option>Draw an area first</option>'; $('date').disabled = true; $('date-info').textContent = '';
  }

  // ---------- running the WPS process ----------
  function executeUrl(index, date) {
    const di = `bbox=${bbox.s},${bbox.w},${bbox.n},${bbox.e},urn:ogc:def:crs:EPSG::4326;index=${index};date=${date};radiometry=radiance`;
    return `/wps?service=WPS&version=1.0.0&request=Execute&identifier=spectral-index&DataInputs=${di}`;
  }
  function parseResponse(text) {
    const doc = new DOMParser().parseFromString(text, 'application/xml');
    if (doc.getElementsByTagName('parsererror').length) throw new Error('The service did not return XML.');
    const messages = (root) => Array.from(root.getElementsByTagNameNS(OWS_NS, 'ExceptionText')).map((n) => n.textContent.trim()).filter(Boolean).join(' ');
    if (doc.documentElement.localName === 'ExceptionReport') throw new Error(messages(doc) || 'The service reported an error.');
    const failed = doc.getElementsByTagNameNS(WPS_NS, 'ProcessFailed')[0];
    if (failed) throw new Error(messages(failed) || 'The process failed.');
    if (!doc.getElementsByTagNameNS(WPS_NS, 'ProcessSucceeded').length) throw new Error('The process did not finish.');
    const outputs = {};
    const container = doc.getElementsByTagNameNS(WPS_NS, 'ProcessOutputs')[0];
    Array.from(container ? container.children : []).forEach((o) => {
      const id = o.getElementsByTagNameNS(OWS_NS, 'Identifier')[0].textContent.trim();
      const ref = o.getElementsByTagNameNS(WPS_NS, 'Reference')[0];
      const data = o.getElementsByTagNameNS(WPS_NS, 'ComplexData')[0];
      outputs[id] = { href: ref ? ref.getAttribute('href') : null, text: data ? data.textContent : null };
    });
    return outputs;
  }
  async function run() {
    if (!bbox) return;
    const index = $('index').value, date = $('date').value, url = executeUrl(index, date);
    $('run').disabled = true; $('result').hidden = true;
    $('ogc-url').textContent = `GET ${location.origin}${url}\n\ncurl "${location.origin}${url}"`;
    $('ogc').open = true;
    setStatus('Sending the WPS Execute request and computing…', 'busy');
    const t0 = performance.now();
    try {
      const resp = await fetch(url);
      const text = await resp.text();
      const out = parseResponse(text);
      const stats = JSON.parse(out.stats.text);
      const secs = ((performance.now() - t0) / 1000).toFixed(1);
      showResult(out, stats, text);
      setStatus(`Done in ${secs} s.`, 'ok');
    } catch (err) {
      setStatus('Error: ' + err.message, 'err');
    } finally {
      refreshRunButton();
    }
  }
  function refreshRunButton() { $('run').disabled = !(bbox && !$('date').disabled); }
  function showResult(out, stats, xmlText) {
    const pv = stats.preview, b = pv.bounds_south_west_north_east;
    if (overlay) overlay.remove();
    overlay = L.imageOverlay(local(out.preview.href), [[b[0], b[1]], [b[2], b[3]]], { opacity: parseFloat($('opacity').value), interactive: false }).addTo(map);
    $('legend-bar').style.background = `linear-gradient(to right, ${pv.ramp.join(', ')})`;
    $('lg-min').textContent = fmt(pv.vmin); $('lg-max').textContent = fmt(pv.vmax);
    const t = $('stats'); t.innerHTML = '';
    row(t, 'Index', `${stats.index.toUpperCase()}  ${stats.formula}`);
    row(t, 'Scene', stats.scene_id);
    row(t, 'Date', stats.date);
    row(t, 'Size', `${stats.shape[1]} × ${stats.shape[0]} px at 24 m`);
    row(t, 'Min / mean / max', `${fmt(stats.min)} / ${fmt(stats.mean)} / ${fmt(stats.max)}`);
    row(t, 'Valid pixels', `${fmt(stats.valid_fraction * 100, 1)} %`);
    row(t, 'Quantity', stats.radiometry + ' (no atmospheric correction)');
    resultUrls.forEach((u) => URL.revokeObjectURL(u));
    const xmlUrl = URL.createObjectURL(new Blob([xmlText], { type: 'text/xml' })); resultUrls = [xmlUrl];
    const links = $('links'); links.innerHTML = '';
    [['Download GeoTIFF (float32 COG)', local(out.index.href), true], ['Preview PNG', local(out.preview.href), false],
     ['WPS response (XML)', xmlUrl, false]].forEach(([label, href, dl], i) => {
      if (i) links.append(' · ');
      const a = document.createElement('a'); a.href = href; a.textContent = label; a.target = '_blank'; a.rel = 'noopener';
      if (dl) a.download = `${stats.index}_${stats.date}.tif`;
      links.append(a);
    });
    $('result').hidden = false;
    map.fitBounds(L.latLngBounds([b[0], b[1]], [b[2], b[3]]).pad(0.3));
  }

  // ---------- start-up ----------
  function showIndexInfo() {
    const i = info.indices.find((x) => x.name === $('index').value);
    $('index-info').textContent = i ? `${i.title}: ${i.formula}. ${i.meaning}.` : '';
  }
  async function init() {
    try {
      [info, scenes] = await Promise.all([
        fetch('/api/info').then((r) => r.json()),
        fetch('/api/coverage').then((r) => r.json()).then((c) => c.features),
      ]);
    } catch (e) { setStatus('Could not reach the service: ' + e.message, 'err'); return; }

    map = L.map('map', { zoomControl: true }).setView([17.4, 78.45], 9);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18, attribution: '© OpenStreetMap contributors' }).addTo(map);
    coverageLayer = L.geoJSON({ type: 'FeatureCollection', features: scenes }, {
      style: { color: '#1c7ed6', weight: 1.5, dashArray: '6 4', fill: false },
      onEachFeature: (f, layer) => layer.bindTooltip(`${f.properties.date} · ${f.properties.satellite || ''} ${f.properties.sensor || ''}`, { sticky: true }),
    }).addTo(map);
    if (scenes.length) map.fitBounds(coverageLayer.getBounds().pad(0.05));

    info.indices.forEach((i) => $('index').add(new Option(`${i.name.toUpperCase()} — ${i.title}`, i.name)));
    $('index').addEventListener('change', showIndexInfo); showIndexInfo();
    $('date').addEventListener('change', refreshRunButton);
    map.on('click', onMapClick); map.on('mousemove', onMouseMove);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && drawing) { stopDrawing(); if (rect && !bbox) { rect.remove(); rect = null; } setStatus(''); } });
    $('draw').addEventListener('click', () => (drawing ? stopDrawing() : startDrawing()));
    $('demo').addEventListener('click', () => { setBbox(DEMO); map.fitBounds(rect.getBounds().pad(0.6)); });
    $('clear').addEventListener('click', clearAll);
    $('run').addEventListener('click', run);
    $('opacity').addEventListener('input', () => { if (overlay) overlay.setOpacity(parseFloat($('opacity').value)); });

    // ?demo=1 (optionally &index=ndwi&date=YYYY-MM-DD) pre-fills the demo area and runs it, for screenshots and rehearsals
    const q = new URLSearchParams(location.search);
    if (q.has('demo')) {
      setBbox(DEMO);
      if (q.get('index')) $('index').value = q.get('index');
      if (q.get('date')) $('date').value = q.get('date');
      showIndexInfo(); map.fitBounds(rect.getBounds().pad(0.6));
      if (!$('run').disabled) run();
    }
  }
  init();
})();
