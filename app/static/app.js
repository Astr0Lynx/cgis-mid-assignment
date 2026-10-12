/* Bhoonidhi WPS Explorer: a small web client of the OGC WPS at /wps.
   It draws no conclusions of its own: every result comes from an ordinary WPS Execute request. */
(function () {
  'use strict';
  const WPS_NS = 'http://www.opengis.net/wps/1.0.0';
  const OWS_NS = 'http://www.opengis.net/ows/1.1';
  const DEMO = { s: 17.35, w: 78.40, n: 17.45, e: 78.55 };  // central Hyderabad, ~10 x 16 km
  const $ = (id) => document.getElementById(id);

  let info = null, scenes = [], map, coverageLayer, rect = null, cornerA = null, drawing = false;
  let bbox = null, theme = null;
  let layers = [], selectedId = null, layerSeq = 0;  // saved results, bottom of the stack first
  const MAX_LAYERS = 20;
  const FETCH = '__fetch__';  // the date choice meaning "fetch the newest suitable scene from Bhoonidhi"
  let account = { enabled: false, logged_in: false, otp_pending: false, user: null };
  let loginDone = null;       // resolves the Run that is waiting for a login (null when the user logged in on their own)
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ---------- light / dark (panel and basemap together) ----------
  // One keyless basemap (OpenStreetMap). Dark mode darkens its tiles with a CSS filter (see style.css), so the
  // result overlay keeps its true colours and no tile provider account is needed.
  function initialTheme() {
    const asked = new URLSearchParams(location.search).get('theme');
    if (asked === 'light' || asked === 'dark') return asked;
    try { const saved = localStorage.getItem('theme'); if (saved === 'light' || saved === 'dark') return saved; } catch (e) { /* storage blocked */ }
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  function applyTheme(t) {
    theme = t;
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem('theme', t); } catch (e) { /* storage blocked */ }
    $('theme').textContent = t === 'dark' ? 'Light mode' : 'Dark mode';
    if (map) map.getContainer().classList.toggle('dark-tiles', t === 'dark');
  }
  document.documentElement.dataset.theme = initialTheme();  // before the map exists, so the panel never flashes

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
    if (!cover.length && account.enabled) {
      date.add(new Option('Newest scene on Bhoonidhi (fetched when you press Run)', FETCH));
      date.disabled = false;
      dinfo.className = 'meta';
      dinfo.textContent = 'No prepared scene covers this area. Pressing Run fetches the newest suitable scene from Bhoonidhi, '
        + 'which needs you to log in to your own Bhoonidhi account (a download of about 140 MB).';
    } else if (!cover.length) {
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
    $('scene-search').hidden = !account.enabled; $('scene-note').textContent = '';
    setRunDisabled(!ok);
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
    if (!drawing) { selectAt(ev.latlng); return; }
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
    bbox = null; setRunDisabled(true); $('scene-search').hidden = true;
    $('bbox-info').className = 'meta'; $('bbox-info').textContent = 'No area selected.';
    $('date').innerHTML = '<option>Draw an area first</option>'; $('date').disabled = true; $('date-info').textContent = '';
  }

  function setRunDisabled(v) { $('run').disabled = v; $('op-exec').disabled = v; }

  // ---------- the three OGC operations, each as a request of its own ----------
  let shownUrl = null;
  function showRequest(url, open = true) {
    shownUrl = location.origin + url;
    $('ogc-url').textContent = `GET ${shownUrl}\n\ncurl "${shownUrl}"`;
    $('ogc-open').href = url;
    $('ogc-actions').hidden = false;
    if (open) $('ogc').open = true;
  }
  async function operation(kind, button) {
    const url = kind === 'caps' ? '/wps?service=WPS&request=GetCapabilities'
      : `/wps?service=WPS&version=1.0.0&request=DescribeProcess&identifier=${encodeURIComponent($('op-process').value)}`;
    showRequest(url);
    const box = $('ogc-response'); box.hidden = false; box.textContent = 'Waiting for the service…';
    button.classList.add('busy'); button.disabled = true;
    const t0 = performance.now();
    try {
      const resp = await fetch(url);
      const text = await resp.text();
      const secs = ((performance.now() - t0) / 1000).toFixed(2);
      const LIMIT = 60000;
      box.textContent = `HTTP ${resp.status} · ${(text.length / 1024).toFixed(1)} kB · ${secs} s\n\n` + text.slice(0, LIMIT)
        + (text.length > LIMIT ? '\n… (shortened here; "Open in a new tab" shows all of it)' : '');
      box.scrollTop = 0;
      setStatus(`${kind === 'caps' ? 'GetCapabilities' : 'DescribeProcess'} reply received (see "The request and reply" below).`, 'ok');
      box.scrollIntoView({ block: 'nearest' });
    } catch (err) {
      box.textContent = 'Could not reach the service: ' + err.message;
      setStatus('Error: ' + err.message, 'err');
    } finally {
      button.classList.remove('busy'); button.disabled = false;
    }
  }
  async function copyCurl(ev) {
    ev.preventDefault();
    if (!shownUrl) return;
    const link = $('ogc-copy'), old = link.textContent;
    try { await navigator.clipboard.writeText(`curl "${shownUrl}"`); link.textContent = 'Copied'; }
    catch (e) { link.textContent = 'Copy not allowed here'; }
    setTimeout(() => { link.textContent = old; }, 1500);
  }

  // ---------- running the WPS process ----------
  function executeUrl(index, date) {
    const di = `bbox=${bbox.s},${bbox.w},${bbox.n},${bbox.e},urn:ogc:def:crs:EPSG::4326;index=${index};date=${date};radiometry=${$('radiometry').value};colour_scale=${$('colour-scale').value}`;
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
    if (layers.length >= MAX_LAYERS) { setStatus(`You have ${MAX_LAYERS} saved layers: remove one first.`, 'err'); return; }
    setRunDisabled(true);
    let date = $('date').value;
    if (date === FETCH || date.startsWith(FETCH + '|')) {
      const [, id, day] = date.split('|');
      date = await fetchFlow(id ? { id, date: day } : null);  // logs in if needed, fetches the scene, returns its date (null if cancelled or failed)
      if (!date) { refreshRunButton(); return; }
    }
    const index = $('index').value, url = executeUrl(index, date);
    const existing = layers.find((l) => l.url === url);
    if (existing) {  // the identical request is already on the map
      selectLayer(existing.id); map.fitBounds(existing.bounds.pad(0.3));
      setStatus('This exact result is already saved as a layer, so it was selected instead of running it again.', 'ok');
      refreshRunButton(); return;
    }
    showRequest(url);
    $('ogc-response').hidden = true;
    setStatus('Sending the WPS Execute request and computing…', 'busy');
    const t0 = performance.now();
    try {
      const resp = await fetch(url);
      const text = await resp.text();
      const out = parseResponse(text);
      const stats = JSON.parse(out.stats.text);
      const secs = ((performance.now() - t0) / 1000).toFixed(1);
      addLayer(out, stats, text, url);
      setStatus(`Done in ${secs} s. Saved as a layer (${layers.length}).`, 'ok');
    } catch (err) {
      setStatus('Error: ' + err.message, 'err');
    } finally {
      refreshRunButton();
    }
  }
  function refreshRunButton() { setRunDisabled(!(bbox && !$('date').disabled)); }
  // ---------- saved layers: every result stays on the map until removed ----------
  const layerById = (id) => layers.find((l) => l.id === id);
  const fileBase = (l) => l.name.replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^_+|_+$/g, '') || `layer_${l.id}`;
  function restack() { layers.forEach((l) => { if (l.visible) l.overlay.bringToFront(); }); }  // bottom first, so the last ends on top
  function addLayer(out, stats, xmlText, url) {
    const pv = stats.preview, b = pv.bounds_south_west_north_east;
    const bounds = L.latLngBounds([b[0], b[1]], [b[2], b[3]]);
    const preview = local(out.preview.href);
    const id = ++layerSeq;
    layers.push({
      id, url, stats, bounds, visible: true, opacity: 0.85,
      name: `${stats.index.toUpperCase()} · ${stats.date} · #${id}`,
      overlay: L.imageOverlay(preview, bounds, { opacity: 0.85, interactive: false }).addTo(map),
      tif: local(out.index.href), png: preview,
      xmlUrl: URL.createObjectURL(new Blob([xmlText], { type: 'text/xml' })),
    });
    restack();
    selectLayer(id);
    map.fitBounds(bounds.pad(0.3));
  }
  function selectLayer(id) {
    selectedId = id; renderLayers(); renderDetails();
    const l = layerById(id); if (l) showRequest(l.url, false);
  }
  function selectAt(latlng) {  // topmost visible layer under the click
    for (let k = layers.length - 1; k >= 0; k--) {
      if (layers[k].visible && layers[k].bounds.contains(latlng)) { selectLayer(layers[k].id); return; }
    }
  }
  function removeLayer(id) {
    const l = layerById(id); if (!l) return;
    l.overlay.remove(); URL.revokeObjectURL(l.xmlUrl);
    layers = layers.filter((x) => x.id !== id);
    if (selectedId === id) selectedId = layers.length ? layers[layers.length - 1].id : null;
    renderLayers(); renderDetails();
  }
  function moveLayer(id, delta) {  // delta +1 = towards the top of the stack
    const i = layers.findIndex((l) => l.id === id), k = i + delta;
    if (i < 0 || k < 0 || k >= layers.length) return;
    [layers[i], layers[k]] = [layers[k], layers[i]];
    restack(); renderLayers();
  }
  function setVisible(id, on) {
    const l = layerById(id); if (!l) return;
    l.visible = on;
    if (on) { l.overlay.addTo(map); restack(); } else l.overlay.remove();
    renderLayers();
  }
  function renderLayers() {
    $('layers').hidden = layers.length === 0;
    $('layer-count').textContent = layers.length ? `(${layers.length})` : '';
    const ul = $('layer-list'); ul.innerHTML = '';
    for (let k = layers.length - 1; k >= 0; k--) {  // top of the stack first
      const l = layers[k], li = document.createElement('li');
      li.className = 'layer' + (l.id === selectedId ? ' selected' : '') + (l.visible ? '' : ' hidden-layer');
      li.dataset.id = l.id;
      const top = document.createElement('div'); top.className = 'layer-top';
      const eye = document.createElement('input'); eye.type = 'checkbox'; eye.checked = l.visible; eye.title = 'Show or hide this layer';
      eye.setAttribute('aria-label', `Show ${l.name}`);
      eye.addEventListener('click', (e) => e.stopPropagation());
      eye.addEventListener('change', () => setVisible(l.id, eye.checked));
      const sw = document.createElement('span'); sw.className = 'layer-swatch';
      sw.style.background = `linear-gradient(to right, ${l.stats.preview.ramp.join(', ')})`;
      const nm = document.createElement('span'); nm.className = 'layer-name'; nm.textContent = l.name; nm.title = l.name;
      top.append(eye, sw, nm);
      const sub = document.createElement('div'); sub.className = 'layer-sub';
      sub.textContent = `${l.stats.preview.scale === 'absolute' ? 'absolute scale' : 'relative contrast'} · ${fmt(l.stats.min)} to ${fmt(l.stats.max)}`;
      const tools = document.createElement('div'); tools.className = 'layer-tools';
      const op = document.createElement('input'); op.type = 'range'; op.min = 0; op.max = 1; op.step = 0.05; op.value = l.opacity;
      op.title = 'Opacity'; op.setAttribute('aria-label', `Opacity of ${l.name}`);
      op.addEventListener('click', (e) => e.stopPropagation());
      op.addEventListener('input', () => { l.opacity = parseFloat(op.value); l.overlay.setOpacity(l.opacity); });
      const btn = (label, title, fn, disabled) => {
        const b = document.createElement('button'); b.type = 'button'; b.className = 'btn ghost'; b.textContent = label; b.title = title;
        b.disabled = !!disabled; b.addEventListener('click', (e) => { e.stopPropagation(); fn(); }); return b;
      };
      tools.append(op,
        btn('Zoom', 'Zoom the map to this layer', () => map.fitBounds(l.bounds.pad(0.3))),
        btn('▲', 'Move up in the stack', () => moveLayer(l.id, +1), k === layers.length - 1),
        btn('▼', 'Move down in the stack', () => moveLayer(l.id, -1), k === 0),
        btn('Rename', 'Rename this layer', () => { const n = window.prompt('Layer name', l.name); if (n && n.trim()) { l.name = n.trim().slice(0, 80); renderLayers(); renderDetails(); } }),
        btn('Remove', 'Remove this layer from the map', () => removeLayer(l.id)));
      const dl = document.createElement('div'); dl.className = 'layer-dl';
      dl.append('Download: ');
      [['GeoTIFF', l.tif, `${fileBase(l)}.tif`, 'The index values as a float32 Cloud Optimized GeoTIFF'],
       ['PNG', l.png, `${fileBase(l)}.png`, 'The coloured preview image'],
       ['XML', l.xmlUrl, `${fileBase(l)}.xml`, 'The WPS response for this run']].forEach(([label, href, file, title], i) => {
        if (i) dl.append(' · ');
        const a = document.createElement('a'); a.href = href; a.textContent = label; a.title = title; a.download = file;
        a.addEventListener('click', (e) => e.stopPropagation());
        dl.append(a);
      });
      li.append(top, sub, tools, dl);
      li.addEventListener('click', () => selectLayer(l.id));
      ul.append(li);
    }
  }
  function renderDetails() {
    const l = layerById(selectedId);
    $('result').hidden = !l;
    if (!l) return;
    const stats = l.stats, pv = stats.preview;
    $('legend-bar').style.background = `linear-gradient(to right, ${pv.ramp.join(', ')})`;
    $('lg-min').textContent = fmt(pv.vmin); $('lg-max').textContent = fmt(pv.vmax);
    const absolute = pv.scale === 'absolute', toa = stats.radiometry === 'toa';
    $('legend-note').textContent = (absolute
      ? 'Colours use a fixed scale from -1 to +1, so the same colour means the same value in every run. '
      : 'Colours are a stretch between the 2nd and 98th percentile of this result: they show relative contrast, not absolute values. ')
      + (toa ? '(Values are top-of-atmosphere reflectance indices, without atmospheric correction.)'
             : '(Values are computed on at-sensor radiance, so standard thresholds such as NDWI > 0 for water do not strictly apply.)');
    const t = $('stats'); t.innerHTML = '';
    row(t, 'Layer', l.name);
    row(t, 'Index', `${stats.index.toUpperCase()}  ${stats.formula}`);
    row(t, 'Scene', stats.scene_id);
    row(t, 'Date', stats.date);
    row(t, 'Size', `${stats.shape[1]} × ${stats.shape[0]} px at 24 m`);
    row(t, 'Min / mean / max', `${fmt(stats.min)} / ${fmt(stats.mean)} / ${fmt(stats.max)}`);
    row(t, 'Valid pixels', `${fmt(stats.valid_fraction * 100, 1)} %`);
    row(t, 'Quantity', (toa ? 'top-of-atmosphere reflectance' : 'at-sensor radiance') + ' (no atmospheric correction)');
    row(t, 'Colour scale', absolute ? 'absolute, fixed -1 to +1' : 'relative, stretched to this result');
    const links = $('links'); links.innerHTML = '';
    [['Download GeoTIFF (float32 COG)', l.tif, true], ['Preview PNG', l.png, false], ['WPS response (XML)', l.xmlUrl, false]].forEach(([label, href, dl], i) => {
      if (i) links.append(' · ');
      const a = document.createElement('a'); a.href = href; a.textContent = label; a.target = '_blank'; a.rel = 'noopener';
      if (dl) a.download = `${fileBase(l)}.tif`;
      links.append(a);
    });
  }
  function removeAllLayers() {
    if (!layers.length || !window.confirm(`Remove all ${layers.length} saved layers from the map?`)) return;
    layers.forEach((l) => { l.overlay.remove(); URL.revokeObjectURL(l.xmlUrl); });
    layers = []; selectedId = null; renderLayers(); renderDetails();
  }

  // ---------- choosing a date: list the scenes Bhoonidhi has for the drawn area ----------
  const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  function initSceneDates() {
    const today = new Date(), from = new Date(today); from.setMonth(from.getMonth() - 6);
    $('scene-to').max = $('scene-from').max = isoDay(today);
    $('scene-to').value = isoDay(today); $('scene-from').value = isoDay(from);
    document.querySelectorAll('#scene-search .presets button').forEach((b) => b.addEventListener('click', () => {
      const months = parseInt(b.dataset.months, 10), t = new Date(), f = new Date(t);
      if (months) f.setMonth(f.getMonth() - months); else f.setTime(new Date('2011-01-01T12:00:00').getTime());
      $('scene-to').value = isoDay(t); $('scene-from').value = isoDay(f);
    }));
  }
  const satellite = (collection) => { const m = /ResourceSat-(\w+?)_/i.exec(collection || ''); return m ? `Resourcesat-${m[1]}` : 'Resourcesat'; };
  async function findScenes() {
    if (!bbox) return;
    const btn = $('scene-find'), note = $('scene-note');
    const start = $('scene-from').value, end = $('scene-to').value;
    if (!start || !end) { note.textContent = 'Pick the first and the last date to search.'; return; }
    if (start > end) { note.textContent = 'The first date is after the last date.'; return; }
    try {
      btn.disabled = true;
      let r = null;
      for (let attempt = 0; attempt < 2 && !(r && r.ok); attempt++) {
        if (!account.logged_in && !(await ensureLogin())) { note.textContent = 'Login cancelled.'; return; }
        setStatus('Searching Bhoonidhi for scenes over this area…', 'busy'); note.textContent = '';
        r = await api('/api/scenes', { bbox: [bbox.s, bbox.w, bbox.n, bbox.e], start, end });
        if (r.status === 401) { await loadAccount(); r = null; }
      }
      if (!r || !r.ok) { throw new Error((r && r.data.error) || 'Could not search Bhoonidhi.'); }
      const scenes = r.data.scenes;
      refreshArea();  // reset the list to this area's prepared scenes
      $('scene-search').hidden = false;
      if (!scenes.length) { note.textContent = `Bhoonidhi lists no online LISS-III scene covering this area between ${start} and ${end}. Try a wider range.`; setStatus(''); return; }
      const date = $('date'), group = document.createElement('optgroup');
      group.label = 'On Bhoonidhi (downloaded when you press Run)';
      let first = null;
      scenes.forEach((s) => {
        const mark = s.covers === true ? '✓ covers the area' : s.covers === false ? '✗ image misses the area' : 'coverage not checked';
        const o = new Option(`${s.date} · ${satellite(s.collection)} · …${s.id.replace(/PS\w+$/, '').slice(-7)} · ${mark}`, `${FETCH}|${s.id}|${s.date}`);
        if (s.covers === false) o.disabled = true; else if (!first) first = o;
        group.append(o);
      });
      date.insertBefore(group, date.firstChild);
      date.disabled = false;
      if (first) date.value = first.value;
      setRunDisabled(false);
      const usable = scenes.filter((s) => s.covers !== false).length;
      note.textContent = `${scenes.length} scene${scenes.length > 1 ? 's' : ''} on Bhoonidhi over this area between ${start} and ${end} (${usable} usable). `
        + (r.data.more ? 'Only the newest are listed: narrow the range to see older ones. ' : '')
        + 'Pick a date in the list above, then press Run. Each download is about 140 MB.';
      setStatus('');
    } catch (e) {
      setStatus('Error: ' + e.message, 'err');
    } finally {
      btn.disabled = false;
    }
  }

  // ---------- Bhoonidhi account: log in (with the emailed code) and fetch a scene ----------
  async function api(path, body) {
    const r = await fetch(path, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }, body: JSON.stringify(body || {}) });
    let data = {}; try { data = await r.json(); } catch (e) { /* not JSON */ }
    return { ok: r.ok, status: r.status, data };
  }
  async function loadAccount() {
    try { account = await fetch('/api/session', { credentials: 'same-origin' }).then((r) => r.json()); } catch (e) { /* keep the last state */ }
    renderAccount();
  }
  function renderAccount() {
    const box = $('account'); box.hidden = !account.enabled;
    if (!account.enabled) return;
    $('account-text').textContent = account.logged_in
      ? `Bhoonidhi account: logged in as ${account.user}` : 'Bhoonidhi account: not logged in (only needed to fetch new scenes)';
    $('account-btn').textContent = account.logged_in ? 'Log out' : 'Log in';
  }
  function openLogin() {
    $('login-error').textContent = ''; $('login-pass').value = ''; $('login-otp').value = '';
    $('login-step1').hidden = account.otp_pending; $('login-step2').hidden = !account.otp_pending;
    $('login-go').disabled = false;
    const d = $('login'); if (!d.open) d.showModal();
    (account.otp_pending ? $('login-otp') : $('login-user')).focus();
  }
  function closeLogin(ok) {
    const d = $('login'); if (d.open) d.close();
    $('login-pass').value = '';  // never keep the password in the page
    const resolve = loginDone; loginDone = null;
    if (resolve) resolve(ok);
  }
  function ensureLogin() {
    if (account.logged_in) return Promise.resolve(true);
    return new Promise((resolve) => { loginDone = resolve; openLogin(); });
  }
  async function submitLogin(ev) {
    ev.preventDefault();
    const err = $('login-error'); err.textContent = ''; $('login-go').disabled = true;
    try {
      let r;
      if (!account.otp_pending) {
        r = await api('/api/login', { user: $('login-user').value.trim(), password: $('login-pass').value });
        $('login-pass').value = '';  // gone from the page as soon as it has been sent
      } else {
        r = await api('/api/otp', { code: $('login-otp').value.trim() });
      }
      if (!r.ok) { err.textContent = r.data.error || 'Login failed.'; if (r.status === 429 || r.status === 409) { await loadAccount(); } return; }
      await loadAccount();
      if (account.logged_in) closeLogin(true);
      else { account.otp_pending = true; openLogin(); }  // the portal wants the emailed code
    } catch (e) { err.textContent = 'Could not reach the service.'; }
    finally { $('login-go').disabled = false; }
  }
  async function logout() { await api('/api/logout'); await loadAccount(); }

  async function reloadCoverage() {
    scenes = await fetch('/api/coverage').then((r) => r.json()).then((c) => c.features);
    coverageLayer.clearLayers(); coverageLayer.addData({ type: 'FeatureCollection', features: scenes });
  }
  function showProgress(v) {
    const p = $('progress'); p.hidden = false; p.value = v.percent || 0;
    setStatus(`${v.message} (${v.percent || 0}%)`, 'busy');
  }
  // Returns the date of the scene that is now available for the drawn area, or null if cancelled or failed.
  async function fetchFlow(choice) {
    try {
      for (let attempt = 0; attempt < 2; attempt++) {
        if (!account.logged_in && !(await ensureLogin())) { setStatus('Login cancelled; nothing was fetched.', ''); return null; }
        setStatus('Starting the fetch from Bhoonidhi…', 'busy');
        const r = await api('/api/fetch', Object.assign({ bbox: [bbox.s, bbox.w, bbox.n, bbox.e] }, choice ? { date: choice.date, scene_id: choice.id } : {}));
        if (r.status === 401) { await loadAccount(); continue; }  // the session expired: log in again once
        if (!r.ok) throw new Error(r.data.error || 'Could not start the fetch.');
        for (;;) {
          await sleep(1500);
          const v = await fetch('/api/fetch/' + r.data.job, { credentials: 'same-origin' }).then((x) => x.json());
          if (v.error && !v.state) throw new Error(v.error);
          showProgress(v);
          if (v.state === 'error') throw new Error(v.error || 'The fetch failed.');
          if (v.state === 'done') {
            await reloadCoverage(); refreshArea();
            if ([...$('date').options].some((o) => o.value === v.scene.date)) $('date').value = v.scene.date;
            return v.scene.date;
          }
        }
      }
      throw new Error('Could not log in to Bhoonidhi.');
    } catch (e) {
      setStatus('Error: ' + e.message, 'err');
      return null;
    } finally {
      $('progress').hidden = true;
    }
  }

  // ---------- advanced options: quantity and colour scale ----------
  function initAdvanced() {
    const toa = $('radiometry').querySelector('option[value="toa"]');
    if (!info.toa_available) {
      toa.disabled = true; toa.textContent = 'Top-of-atmosphere reflectance (not available yet)';
      $('toa-note').textContent = 'Reflectance needs the per-band solar irradiance (ESUN) of LISS-III from a citable source. '
        + 'None is configured on this service, and I do not guess it, so only at-sensor radiance is offered. '
        + 'The option switches on by itself once the table is filled in (see the README).';
    } else {
      $('toa-note').textContent = 'Top-of-atmosphere reflectance uses a configured ESUN table; it has no atmospheric correction.';
    }
    const explain = () => {
      $('scale-note').textContent = $('colour-scale').value === 'absolute'
        ? 'Fixed -1 to +1 colours. On radiance the numbers are biased (sunlight is stronger in red than NIR), so read them as '
          + 'indicative; thresholds are only meaningful on reflectance.'
        : 'Colours are stretched between the 2nd and 98th percentile of the result, so they show contrast within it.';
    };
    $('colour-scale').addEventListener('change', explain); explain();
  }

  // ---------- resizable side panel ----------
  function initSplitter() {
    const bar = $('splitter'), app = $('app'), DEFAULT = 360;
    const limits = () => [260, Math.max(320, Math.min(760, Math.round(app.clientWidth * 0.7)))];
    const apply = (w, save) => {
      const [lo, hi] = limits();
      w = Math.min(hi, Math.max(lo, Math.round(w)));
      document.documentElement.style.setProperty('--panel-w', w + 'px');
      bar.setAttribute('aria-valuenow', String(w));
      map.invalidateSize();
      if (save) { try { localStorage.setItem('panelWidth', String(w)); } catch (e) { /* storage may be blocked */ } }
      return w;
    };
    try { const saved = parseInt(localStorage.getItem('panelWidth'), 10); if (saved) apply(saved, false); } catch (e) { /* ignore */ }
    let dragging = false;
    bar.addEventListener('pointerdown', (e) => { dragging = true; bar.setPointerCapture(e.pointerId); document.body.classList.add('resizing'); e.preventDefault(); });
    bar.addEventListener('pointermove', (e) => { if (dragging) apply(e.clientX - app.getBoundingClientRect().left, false); });
    let lastTap = 0, moved = false;  // pointerdown is cancelled above, so dblclick is not reliable: detect two quick taps ourselves
    bar.addEventListener('pointermove', () => { if (dragging) moved = true; });
    const stop = (e) => {
      if (!dragging) return;
      dragging = false; document.body.classList.remove('resizing');
      try { bar.releasePointerCapture(e.pointerId); } catch (err) { /* already released */ }
      const now = Date.now();
      if (!moved && now - lastTap < 450) { lastTap = 0; apply(DEFAULT, true); return; }
      lastTap = moved ? 0 : now; moved = false;
      apply($('panel').getBoundingClientRect().width, true);
    };
    bar.addEventListener('pointerup', stop); bar.addEventListener('pointercancel', stop);
    bar.addEventListener('keydown', (e) => {
      const step = e.shiftKey ? 60 : 20, w = $('panel').getBoundingClientRect().width;
      if (e.key === 'ArrowLeft') { apply(w - step, true); e.preventDefault(); }
      if (e.key === 'ArrowRight') { apply(w + step, true); e.preventDefault(); }
      if (e.key === 'Home') { apply(DEFAULT, true); e.preventDefault(); }
    });
    window.addEventListener('resize', () => apply($('panel').getBoundingClientRect().width, false));
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
    initSplitter();
    initAdvanced();
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18, attribution: '© OpenStreetMap contributors' }).addTo(map);
    applyTheme(document.documentElement.dataset.theme);
    $('theme').addEventListener('click', () => applyTheme(theme === 'dark' ? 'light' : 'dark'));
    coverageLayer = L.geoJSON({ type: 'FeatureCollection', features: scenes }, {
      style: { color: '#1c7ed6', weight: 1.5, dashArray: '6 4', fill: false },
      onEachFeature: (f, layer) => layer.bindTooltip(`${f.properties.date} · ${f.properties.satellite || ''} ${f.properties.sensor || ''}`, { sticky: true }),
    }).addTo(map);
    if (scenes.length) map.fitBounds(coverageLayer.getBounds().pad(0.05));
    // ?view=lat,lon,zoom opens the map there (handy for sharing a place)
    const view = (new URLSearchParams(location.search).get('view') || '').split(',').map(Number);
    if (view.length === 3 && view.every(Number.isFinite)) map.setView([view[0], view[1]], view[2]);

    info.indices.forEach((i) => $('index').add(new Option(`${i.name.toUpperCase()} — ${i.title}`, i.name)));
    $('index').addEventListener('change', showIndexInfo); showIndexInfo();
    $('date').addEventListener('change', refreshRunButton);
    map.on('click', onMapClick); map.on('mousemove', onMouseMove);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && drawing) { stopDrawing(); if (rect && !bbox) { rect.remove(); rect = null; } setStatus(''); } });
    $('draw').addEventListener('click', () => (drawing ? stopDrawing() : startDrawing()));
    $('demo').addEventListener('click', () => { setBbox(DEMO); map.fitBounds(rect.getBounds().pad(0.6)); });
    $('clear').addEventListener('click', clearAll);
    $('run').addEventListener('click', run);
    $('scene-find').addEventListener('click', findScenes);
    initSceneDates();
    $('op-caps').addEventListener('click', (e) => operation('caps', e.currentTarget));
    $('op-describe').addEventListener('click', (e) => operation('describe', e.currentTarget));
    $('op-exec').addEventListener('click', run);
    $('ogc-copy').addEventListener('click', copyCurl);
    $('login-form').addEventListener('submit', submitLogin);
    $('login-cancel').addEventListener('click', () => closeLogin(false));
    $('login').addEventListener('cancel', () => closeLogin(false));  // Escape
    $('account-btn').addEventListener('click', () => (account.logged_in ? logout() : (loginDone = null, openLogin())));
    await loadAccount();
    $('layers-clear').addEventListener('click', removeAllLayers);

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
