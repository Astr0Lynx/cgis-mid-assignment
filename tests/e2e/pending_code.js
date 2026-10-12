// A login abandoned at the code step (the page was closed, the PC restarted) must not trap the user at the code step.
//   python scripts/dev_fake_bhoonidhi.py 5058     then     BASE=http://localhost:5058 node pending_code.js
const puppeteer = require('puppeteer-core');
const BASE = process.env.BASE || 'http://localhost:5058';
const CHROME = process.env.CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const res = [];
const ok = (n, c, x) => res.push([c ? 'PASS' : 'FAIL', n, x || '']);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox'] });
  try {
    const p = await (await b.createBrowserContext()).newPage();   // one browser profile: its cookie survives the reload
    await p.setViewport({ width: 1300, height: 900 });
    const errs = []; p.on('pageerror', (e) => errs.push(String(e)));
    const open = async () => {
      await p.goto(`${BASE}/?theme=light&view=16.5,80.6,12`, { waitUntil: 'domcontentloaded' });
      await p.waitForSelector('#account-btn');
      await p.waitForFunction(() => document.querySelector('#account-text').textContent.length > 0);
      await wait(600);
    };
    await open();
    await p.click('#account-btn'); await p.waitForSelector('#login[open]');
    ok('1 a fresh visit asks for the credentials first', !(await p.$eval('#login-step1', (e) => e.hidden)) && await p.$eval('#login-step2', (e) => e.hidden));
    await p.type('#login-user', 'student'); await p.type('#login-pass', 'correct-horse'); await p.click('#login-go');
    await p.waitForFunction(() => !document.querySelector('#login-step2').hidden, { timeout: 8000 });
    ok('2 after the password the dialog asks for the emailed code', true);
    await p.click('#login-cancel'); await wait(300);                 // ...and the user never types the code (PC restarted)

    await open();                                                     // same cookie, fresh page
    await p.click('#account-btn'); await p.waitForSelector('#login[open]');
    ok('3 coming back, the dialog resumes at the code step (the login is still pending)', !(await p.$eval('#login-step2', (e) => e.hidden)));
    ok('4 and there is a visible way out: "Start over"', await p.$eval('#login-restart', (e) => e.offsetParent !== null));
    await p.click('#login-restart');
    await p.waitForFunction(() => !document.querySelector('#login-step1').hidden && document.querySelector('#login-step2').hidden, { timeout: 8000 });
    ok('5 "Start over" brings back the user id and password', true);
    ok('6 the pending login was discarded on the server', !(await p.evaluate(() => fetch('/api/session').then((r) => r.json()).then((s) => s.otp_pending))));
    await p.type('#login-user', 'student'); await p.type('#login-pass', 'correct-horse'); await p.click('#login-go');
    await p.waitForFunction(() => !document.querySelector('#login-step2').hidden, { timeout: 8000 });
    await p.type('#login-otp', '123456'); await p.click('#login-go');
    await p.waitForFunction(() => !document.querySelector('#login').open, { timeout: 8000 });
    await wait(500);
    ok('7 a normal login then works', /logged in as student/.test(await p.$eval('#account-text', (e) => e.textContent)));
    ok('8 no script errors', errs.length === 0, errs.join(' | '));
  } catch (e) { ok('SCRIPT', false, String(e.stack || e).slice(0, 500)); } finally { await b.close(); }
  for (const [s, n, x] of res) console.log(s, n, x ? '-> ' + x.slice(0, 170) : '');
})();
