// Choosing a particular Bhoonidhi scene (date) in the real page, against the dev server with a FAKE Bhoonidhi:
//   python scripts/dev_fake_bhoonidhi.py 5057     then     BASE=http://localhost:5057 node choose_scene.js
const puppeteer = require('puppeteer-core');
const BASE = process.env.BASE || 'http://localhost:5057';
const CHROME = process.env.CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const res = [];
const ok = (n, c, x) => res.push([c ? 'PASS' : 'FAIL', n, x || '']);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox'] });
  try {
    const p = await (await b.createBrowserContext()).newPage();
    await p.setViewport({ width: 1300, height: 900 });
    const errs = []; p.on('pageerror', (e) => errs.push(String(e)));
    await p.goto(`${BASE}/?theme=light&view=16.5,80.6,12`, { waitUntil: 'domcontentloaded' });
    await p.waitForSelector('.leaflet-container');
    await p.waitForFunction(() => document.querySelector('#account-text') && document.querySelector('#account-text').textContent.length > 0);
    await wait(800);
    ok('1 no search button before an area is drawn', await p.$eval('#scene-search', (e) => e.hidden));
    await p.click('#draw');
    const box = await (await p.$('#map')).boundingBox(); const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
    await p.mouse.click(cx - 50, cy - 40); await p.mouse.move(cx, cy); await p.mouse.click(cx + 50, cy + 40); await wait(500);
    ok('2 the button to choose a date appears once an area is drawn', await p.$eval('#scene-search', (e) => !e.hidden));
    ok('3 the default is still the newest scene', /Newest scene on Bhoonidhi/.test(await p.$eval('#date', (s) => s.options[0].text)));

    await p.click('#scene-find');
    await p.waitForSelector('#login[open]', { timeout: 5000 });
    ok('4 searching while logged out asks you to log in', true);
    await p.type('#login-user', 'student'); await p.type('#login-pass', 'correct-horse'); await p.click('#login-go');
    await p.waitForFunction(() => !document.querySelector('#login-step2').hidden, { timeout: 8000 });
    await p.type('#login-otp', '123456'); await p.click('#login-go');
    await p.waitForFunction(() => document.querySelectorAll('#date optgroup option').length > 0, { timeout: 20000 });
    const opts = await p.$$eval('#date optgroup option', (o) => o.map((x) => ({ t: x.textContent, v: x.value, d: x.disabled })));
    ok('5 after login the search lists the scenes by date', opts.length === 3, JSON.stringify(opts.map((o) => o.t.slice(0, 40))));
    ok('6 newest first, with the satellite and a coverage mark', /^2026-10-01 · Resourcesat-2 · .*covers the area/.test(opts[0].t), opts[0].t);
    ok('7 a scene whose true image misses the area is shown but cannot be chosen', opts.some((o) => /misses the area/.test(o.t) && o.d), opts.map((o) => o.d).join());
    ok('8 the first usable scene is preselected', (await p.$eval('#date', (s) => s.value)).includes('2026-10-01'));
    ok('9 the note says how many were found', /3 scenes/.test(await p.$eval('#scene-note', (e) => e.textContent)), await p.$eval('#scene-note', (e) => e.textContent));
    ok('10 logging in because of the search did not start a run', await p.$eval('#layers', (e) => e.hidden));

    const older = opts.find((o) => o.t.startsWith('2026-09-15'));
    await p.select('#date', older.v);
    await p.click('#run');
    await p.waitForSelector('#result:not([hidden])', { timeout: 60000 });
    const stats = await p.$eval('#stats', (e) => e.textContent);
    ok('11 running fetches exactly the chosen date, not the newest', /2026-09-15/.test(stats) && /R2315SEP2026/.test(stats), stats.slice(0, 120));
    ok('12 the result is saved as a layer for that date', /2026-09-15/.test(await p.$eval('#layer-list .layer-name', (e) => e.textContent)));
    ok('13 afterwards that date is a prepared scene for the area', (await p.$$eval('#date option', (o) => o.map((x) => x.value))).includes('2026-09-15'));
    ok('14 no script errors', errs.length === 0, errs.join(' | '));
    await p.screenshot({ path: (process.env.TEMP || '.') + '/choose_scene.png' });
  } catch (e) { ok('SCRIPT', false, String(e.stack || e).slice(0, 500)); } finally { await b.close(); }
  for (const [s, n, x] of res) console.log(s, n, x ? '-> ' + x.slice(0, 170) : '');
})();
