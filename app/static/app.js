/* Bhoonidhi WPS Explorer: a small web client of the OGC WPS at /wps.
   It draws no conclusions of its own: every result comes from an ordinary WPS Execute request. */
(function () {
  'use strict';
  const WPS_NS = 'http://www.opengis.net/wps/1.0.0';
  const OWS_NS = 'http://www.opengis.net/ows/1.1';
  const DEMO = { s: 17.35, w: 78.40, n: 17.45, e: 78.55 };  // central Hyderabad, ~10 x 16 km
  const $ = (id) => document.getElementById(id);

  let info = null, scenes = [], map, coverageLayer, rect = null, cornerA = null, drawing = false;
  let bbox = null, overlay = null, resultUrls = [], theme = null;
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
    bbox = null; $('result').hidden = true; setRunDisabled(true);
    $('bbox-info').className = 'meta'; $('bbox-info').textContent = 'No area selected.';
    $('date').innerHTML = '<option>Draw an area first</option>'; $('date').disabled = true; $('date-info').textContent = '';
  }

  function setRunDisabled(v) { $('run').disabled = v; $('op-exec').disabled = v; }

  // ---------- the three OGC operations, each as a request of its own ----------
  let shownUrl = null;
  function showRequest(url) {
    shownUrl = location.origin + url;
    $('ogc-url').textContent = `GET ${shownUrl}\n\ncurl "${shownUrl}"`;
    $('ogc-open').href = url;
    $('ogc-actions').hidden = false;
    $('ogc').open = true;
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
    setRunDisabled(true); $('result').hidden = true;
    let date = $('date').value;
    if (date === FETCH) {
      date = await fetchFlow();  // logs in if needed, fetches the scene, returns its date (null if cancelled or failed)
      if (!date) { refreshRunButton(); return; }
    }
    const index = $('index').value, url = executeUrl(index, date);
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
      showResult(out, stats, text);
      setStatus(`Done in ${secs} s.`, 'ok');
    } catch (err) {
      setStatus('Error: ' + err.message, 'err');
    } finally {
      refreshRunButton();
    }
  }
  function refreshRunButton() { setRunDisabled(!(bbox && !$('date').disabled)); }
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
  async function fetchFlow() {
    try {
      for (let attempt = 0; attempt < 2; attempt++) {
        if (!account.logged_in && !(await ensureLogin())) { setStatus('Login cancelled; nothing was fetched.', ''); return null; }
        setStatus('Starting the fetch from Bhoonidhi…', 'busy');
        const r = await api('/api/fetch', { bbox: [bbox.s, bbox.w, bbox.n, bbox.e] });
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
    $('op-caps').addEventListener('click', (e) => operation('caps', e.currentTarget));
    $('op-describe').addEventListener('click', (e) => operation('describe', e.currentTarget));
    $('op-exec').addEventListener('click', run);
    $('ogc-copy').addEventListener('click', copyCurl);
    $('login-form').addEventListener('submit', submitLogin);
    $('login-cancel').addEventListener('click', () => closeLogin(false));
    $('login').addEventListener('cancel', () => closeLogin(false));  // Escape
    $('account-btn').addEventListener('click', () => (account.logged_in ? logout() : (loginDone = null, openLogin())));
    await loadAccount();
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
