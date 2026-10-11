// End-to-end test of the login + fetch workflow in a real browser, against the dev server with a FAKE Bhoonidhi.
const puppeteer = require('puppeteer-core');
const path = require('path');
const BASE = process.env.BASE || 'http://localhost:5056';
const OUT = process.env.OUT || __dirname;
const URL = `${BASE}/?theme=light&view=16.5,80.6,12`;
const results = [];
const ok = (name, cond, extra) => { results.push([cond ? 'PASS' : 'FAIL', name, extra || '']); };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function newPage(browser) {
  const ctx = await browser.createBrowserContext();   // fresh cookies per scenario
  const page = await ctx.newPage();
  await page.setViewport({ width: 1300, height: 860 });
  page.errors = [];
  page.on('pageerror', (e) => page.errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error' && !/tile|ERR_|Failed to load resource/.test(m.text())) page.errors.push(m.text()); });
  await page.goto(URL, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.leaflet-container');
  await page.waitForFunction(() => document.querySelector('#account-text') && document.querySelector('#account-text').textContent.length > 0, { timeout: 15000 });
  await wait(800);
  return page;
}
async function drawBox(page) {
  await page.click('#draw');
  const box = await (await page.$('#map')).boundingBox();
  const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
  await page.mouse.click(cx - 50, cy - 40); await page.mouse.move(cx, cy); await page.mouse.click(cx + 50, cy + 40);
  await wait(500);
}
const text = (page, sel) => page.$eval(sel, (e) => e.textContent);
const shot = (page, name) => page.screenshot({ path: path.join(OUT, name + '.png') });
async function waitResult(page, ms) { await page.waitForSelector('#result:not([hidden])', { timeout: ms }); }

(async () => {
  const browser = await puppeteer.launch({ executablePath: process.env.CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: 'new', args: ['--no-sandbox'] });
  try {
    // ---- A: full workflow, with a wrong code first ----
    let page = await newPage(browser);
    ok('A1 account line says not logged in', /not logged in/.test(await text(page, '#account-text')));
    await drawBox(page);
    const dateText = await page.$eval('#date', (s) => s.options[0].text);
    ok('A2 an uncovered box offers "Newest scene on Bhoonidhi"', /Newest scene on Bhoonidhi/.test(dateText), dateText);
    ok('A3 Run is enabled', !(await page.$eval('#run', (b) => b.disabled)));
    await shot(page, 'a1_box_not_covered');
    await page.click('#run');
    await page.waitForSelector('#login[open]', { timeout: 5000 });
    ok('A4 pressing Run while logged out opens the login dialog', true);
    await shot(page, 'a2_login_dialog');
    await page.type('#login-user', 'student'); await page.type('#login-pass', 'correct-horse');
    await page.click('#login-go');
    await page.waitForFunction(() => !document.querySelector('#login-step2').hidden, { timeout: 8000 });
    ok('A5 after the password the dialog asks for the emailed code', true);
    ok('A6 the password is no longer in the page', (await page.$eval('#login-pass', (e) => e.value)) === '');
    await page.type('#login-otp', '000000'); await page.click('#login-go');
    await page.waitForFunction(() => /rejected/i.test(document.querySelector('#login-error').textContent), { timeout: 8000 });
    ok('A7 a wrong code is rejected with a message and the dialog stays open', await page.$eval('#login', (d) => d.open));
    await page.$eval('#login-otp', (e) => { e.value = ''; });
    await page.type('#login-otp', '123456'); await page.click('#login-go');
    await page.waitForFunction(() => !document.querySelector('#login').open, { timeout: 8000 });
    ok('A8 the right code closes the dialog', true);
    let sawProgress = false;
    for (let i = 0; i < 60 && !sawProgress; i++) { sawProgress = await page.$eval('#progress', (p) => !p.hidden); if (!sawProgress) await wait(100); }
    ok('A9 the pending Run continues by itself with a progress bar', sawProgress);
    await shot(page, 'a3_progress');
    await waitResult(page, 45000);
    const stats = await text(page, '#stats');
    ok('A10 the result appears for the fetched scene', /R2301OCT2026/.test(stats) && /2026-10-01/.test(stats), stats.slice(0, 120));
    ok('A11 account line says logged in as student', /logged in as student/.test(await text(page, '#account-text')));
    await wait(1500); await shot(page, 'a4_result');
    ok('A12 no script errors', page.errors.length === 0, page.errors.join(' | '));
    await page.click('#account-btn'); await wait(600);
    ok('A13 Log out works', /not logged in/.test(await text(page, '#account-text')));
    await page.browserContext().close();

    // ---- D: a second visitor, same box: the fetched scene is now shared, so no login is needed ----
    page = await newPage(browser);
    await drawBox(page);
    const d2 = await page.$eval('#date', (s) => s.options[0].text);
    ok('D1 the fetched scene now counts as prepared (no fetch option)', /2026-10-01/.test(d2) && !/Newest scene on Bhoonidhi/.test(d2), d2);
    await page.click('#run'); await waitResult(page, 20000);
    ok('D2 it runs immediately without any login', !(await page.$eval('#login', (d) => d.open)));
    await page.browserContext().close();

    // ---- C: logging in by choice, with no Run pending, must not start anything ----
    page = await newPage(browser);
    await page.click('#account-btn'); await page.waitForSelector('#login[open]');
    await page.type('#login-user', 'student'); await page.type('#login-pass', 'correct-horse'); await page.click('#login-go');
    await page.waitForFunction(() => !document.querySelector('#login-step2').hidden, { timeout: 8000 });
    await page.type('#login-otp', '123456'); await page.click('#login-go');
    await page.waitForFunction(() => !document.querySelector('#login').open, { timeout: 8000 });
    await wait(1500);
    ok('C1 logged in by choice', /logged in as student/.test(await text(page, '#account-text')));
    ok('C2 nothing was run or fetched', (await page.$eval('#result', (r) => r.hidden)) && (await text(page, '#status')) === '');
    await page.browserContext().close();

    // ---- E: cancelling the login leaves things as they were ----
    page = await newPage(browser);
    await page.click('#clear').catch(() => {});
    // an area that no scene (prepared or fetched) covers: pan away first
    await page.goto(`${BASE}/?theme=light&view=15.0,79.0,12`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.leaflet-container'); await wait(1200);
    await drawBox(page);
    await page.click('#run'); await page.waitForSelector('#login[open]', { timeout: 5000 });
    await page.click('#login-cancel'); await wait(500);
    ok('E1 cancel closes the dialog and fetches nothing', !(await page.$eval('#login', (d) => d.open)) && /cancelled/i.test(await text(page, '#status')), await text(page, '#status'));
    ok('E2 Run is available again', !(await page.$eval('#run', (b) => b.disabled)));
    await page.browserContext().close();
  } catch (e) {
    ok('SCRIPT', false, String(e && e.stack || e).slice(0, 600));
  } finally {
    await browser.close();
  }
  for (const [s, n, x] of results) console.log(s, n, x ? '-> ' + x : '');
  console.log(results.some((r) => r[0] === 'FAIL') ? 'SOME FAILED' : 'ALL PASSED');
})();
