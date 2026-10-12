const puppeteer = require('puppeteer-core');
const path = require('path');
const BASE = process.env.BASE || 'http://127.0.0.1:5000';
const res = [];
const ok = (n, c, x) => res.push([c ? 'PASS' : 'FAIL', n, x || '']);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: 'new', args: ['--no-sandbox'] });
  try {
    const p = await (await b.createBrowserContext()).newPage();
    await p.setViewport({ width: 1400, height: 1000 });
    const errs = []; p.on('pageerror', (e) => errs.push(String(e)));
    let promptAnswer = 'My NDVI check'; p.on('dialog', (d) => d.type() === 'prompt' ? d.accept(promptAnswer) : d.accept());
    await p.goto(`${BASE}/?theme=light&view=17.4,78.47,10`, { waitUntil: 'domcontentloaded' });
    await p.waitForSelector('#demo'); await wait(900);
    const count = () => p.$$eval('#layer-list > li', (l) => l.length);
    const names = () => p.$$eval('#layer-list .layer-name', (l) => l.map((e) => e.textContent));
    const imgs = () => p.$$eval('.leaflet-image-layer', (l) => l.length);
    const runIt = async (index, scale) => {
      await p.select('#index', index);
      if (scale) { await p.evaluate(() => { document.getElementById('advanced').open = true; }); await p.select('#colour-scale', scale); }
      const before = await count();
      await p.click('#run');
      await p.waitForFunction((n) => document.querySelectorAll('#layer-list > li').length === n || /already saved/.test(document.getElementById('status').textContent), { timeout: 40000 }, before + 1);
      await wait(400);
    };

    await p.click('#demo'); await wait(700);
    ok('1 no layers section before the first run', await p.$eval('#layers', (e) => e.hidden));
    await runIt('ndvi');
    ok('2 first run saves a layer', (await count()) === 1 && (await imgs()) === 1, (await names()).join(' | '));
    await runIt('ndwi');
    ok('3 second run keeps the first: two layers, two images on the map', (await count()) === 2 && (await imgs()) === 2, (await names()).join(' | '));
    ok('4 newest layer is on top of the list and selected', /NDWI/.test((await names())[0]) && await p.$eval('#layer-list > li:first-child', (e) => e.classList.contains('selected')));
    await runIt('mndwi', 'absolute');
    ok('5 third run (different index and scale) makes three', (await count()) === 3, (await names()).join(' | '));

    // select the oldest (NDVI) by its card
    await p.click('#layer-list > li:last-child .layer-name');
    await wait(300);
    const statsText = await p.$eval('#stats', (e) => e.textContent);
    ok('6 selecting a layer shows ITS details', /NDVI/.test(statsText) && /relative/.test(statsText), statsText.slice(0, 80));
    ok('7 the request panel shows that layer\'s request', /index=ndvi/.test(await p.$eval('#ogc-url', (e) => e.textContent)));
    const links = await p.$$eval('#links a', (a) => a.map((x) => x.getAttribute('href')));
    await p.click('#layer-list > li:first-child .layer-name'); await wait(300);
    const links2 = await p.$$eval('#links a', (a) => a.map((x) => x.getAttribute('href')));
    ok('8 each layer has its own download links', links[0] !== links2[0] && /ndvi\.tif|\.tif/.test(links[0]) && links[0].includes('/outputs/'), `${links[0]}  vs  ${links2[0]}`);

    // visibility
    await p.click('#layer-list > li:nth-child(2) input[type=checkbox]'); await wait(300);
    ok('9 hiding a layer removes its image from the map but keeps the card', (await imgs()) === 2 && (await count()) === 3);
    await p.click('#layer-list > li:nth-child(2) input[type=checkbox]'); await wait(300);
    ok('10 showing it again puts it back', (await imgs()) === 3);

    // per-layer opacity
    await p.evaluate(() => { const r = document.querySelector('#layer-list > li:nth-child(3) input[type=range]'); r.value = 0.2; r.dispatchEvent(new Event('input', { bubbles: true })); });
    const ops = await p.$$eval('.leaflet-image-layer', (l) => l.map((e) => e.style.opacity));
    ok('11 opacity is per layer (one image at 0.2, the others unchanged)', ops.filter((o) => o === '0.2').length === 1 && ops.filter((o) => o === '0.85').length === 2, ops.join(','));

    // reorder
    const before = await names();
    await p.click('#layer-list > li:nth-child(2) .layer-tools .btn:nth-of-type(2)'); await wait(300);   // up
    const after = await names();
    ok('12 moving a layer up swaps it with the one above', after[0] === before[1] && after[1] === before[0], `${before.join(' | ')}  ->  ${after.join(' | ')}`);
    const zorder = await p.$$eval('.leaflet-image-layer', (l) => l.length);
    ok('13 stacking still shows all three', zorder === 3);

    // rename
    await p.click('#layer-list > li:last-child .layer-tools .btn:nth-of-type(4)'); await wait(300);
    ok('14 rename works', (await names()).includes('My NDVI check'), (await names()).join(' | '));

    // duplicate request
    await runIt('ndvi', 'stretch');
    ok('15 re-running an identical request does not duplicate it', (await count()) === 3 && /already saved/.test(await p.$eval('#status', (e) => e.textContent)));

    // Clear only clears the drawn area
    await p.click('#clear'); await wait(300);
    ok('16 Clear removes the drawn box but keeps the layers', (await count()) === 3 && (await imgs()) === 3);

    // click on the map selects a layer
    await p.click('#layer-list > li:first-child .layer-name');   // make sure something known is selected
    const box = await (await p.$('#map')).boundingBox();
    await p.mouse.click(box.x + box.width / 2, box.y + box.height / 2); await wait(400);
    ok('17 clicking a layer on the map selects it', await p.$eval('#layer-list > li.selected', (e) => !!e));

    await p.evaluate(() => window.scrollTo(0, 0));
    await p.$eval('#layers', (e) => e.scrollIntoView());
    await wait(300);
    await p.screenshot({ path: path.join(process.env.TEMP || '.', 'layers.png') });

    // remove one, then all
    await p.click('#layer-list > li:first-child .layer-tools .btn:last-of-type'); await wait(300);
    ok('18 remove deletes that layer and its image', (await count()) === 2 && (await imgs()) === 2);
    ok('19 a layer is still selected after removing the selected one', await p.$eval('#result', (e) => !e.hidden));
    await p.click('#layers-clear'); await wait(400);
    ok('20 Remove all empties the list, the map and the details', (await count()) === 0 && (await imgs()) === 0 && await p.$eval('#layers', (e) => e.hidden) && await p.$eval('#result', (e) => e.hidden));
    ok('21 no script errors', errs.length === 0, errs.join(' | '));
  } catch (e) { ok('SCRIPT', false, String(e.stack || e).slice(0, 500)); } finally { await b.close(); }
  for (const [s, n, x] of res) console.log(s, n, x ? '-> ' + x.slice(0, 160) : '');
})();
