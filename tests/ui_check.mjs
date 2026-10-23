// Dependency-free CDP click-through test for the PeerGap pages (Node 22+).
// Usage: node tests/ui_check.mjs <chrome-debug-port> <http-base-url> <repo-dir>
const [port, base, dir] = process.argv.slice(2);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const tgt = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json();
const ws = new WebSocket(tgt.webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener('open', r, { once: true }));
let id = 0; const pending = new Map(); const errors = [];
ws.addEventListener('message', ev => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  if (msg.method === 'Runtime.exceptionThrown') errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
  if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') errors.push(msg.params.args.map(a => a.value).join(' '));
});
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const ev = async (expr) => { const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true }); if (r.result.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description); return r.result.result.value; };
const results = []; const check = (name, ok, detail = '') => { results.push(!!ok); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail !== '' ? '  -> ' + detail : ''}`); };
await send('Runtime.enable'); await send('Page.enable');
const go = async (url, w = 1600, h = 1000, mobile = false) => { await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile }); await send('Page.navigate', { url }); await sleep(2500); };
const clickDot = name => ev(`[...document.querySelectorAll('circle.dot')].find(c=>c.__data__.s===${JSON.stringify(name)}).dispatchEvent(new MouseEvent('click',{bubbles:true}))`);
const darkest = `(()=>{const ls=[...document.querySelectorAll('line.peerline')];ls.sort((a,b)=>b.getAttribute('stroke-opacity')-a.getAttribute('stroke-opacity'));return ls[0].querySelector('title').textContent;})()`;
const noTwin = `(()=>{document.querySelectorAll('details').forEach(d=>d.open=true);return !/twin/i.test(document.body.innerText) && !/twin/i.test([...document.querySelectorAll('title')].map(t=>t.textContent).join(' '));})()`;
const RED = 'rgb(196, 18, 48)', GREEN = 'rgb(8, 128, 79)';

// ---------- map ----------
await go(`${base}/index.html#miami`);
check('map: 100 metro dots and the state outlines', await ev(`document.querySelectorAll('circle.dot').length`) === 100 && await ev(`document.querySelectorAll('path.state').length`) >= 50);
check('map: Miami selected with a big red +2.84 headline', await ev(`(()=>{const b=document.querySelector('#rail .big');return b.textContent==='+2.84' && getComputedStyle(b).color===${JSON.stringify(RED)} && parseFloat(getComputedStyle(b).fontSize)>=60;})()`));
check('map: 10 gray peer lines drawn from Miami', await ev(`document.querySelectorAll('line.peerline').length`) === 10);
check('map: darkest line goes to the closest peer, Tampa', (await ev(darkest)).startsWith('Tampa'), await ev(darkest));
await clickDot('Orlando'); await sleep(200);
check('map: clicking Orlando redraws lines to its 10 peers', (await ev(`document.querySelector('#rail h2').textContent`)).startsWith('Orlando') && await ev(`[...document.querySelectorAll('line.peerline')].every(l=>l.querySelector('title').textContent.includes("Orlando's"))`) && await ev(`document.querySelectorAll('line.peerline').length`) === 10);
await ev(`(()=>{const q=document.getElementById('q');q.value='Pittsburgh';q.dispatchEvent(new Event('input'));q.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter'}));})()`); await sleep(200);
check('map: a more-affordable metro gets a big green headline', await ev(`(()=>{const b=document.querySelector('#rail .big');return b.textContent.startsWith('−') && getComputedStyle(b).color===${JSON.stringify(GREEN)};})()`), await ev(`document.querySelector('#rail .big').textContent`));
await go(`${base}/index.html#miami`);
const tiles = await ev(`[...document.querySelectorAll('#scenario .tile')].map(t=>t.innerText.replace(/\\s+/g,' ')).join(' | ')`);
check('map: scenario card 64% -> 74%, holds steady, #2 -> #2', /64% → 74%/.test(tiles) && /Holds steady/.test(tiles) && /#2 → #2/.test(tiles), tiles);
await ev(`document.getElementById('bank').click()`); await sleep(200);
check('map: bank play switch gives a 12% smaller gap', /12% smaller/.test(await ev(`document.querySelector('#scenario .result').innerText`)));
const counts = await ev(`(()=>{const out=[];const s=document.getElementById('year');for(let y=2026;y<=2036;y++){s.value=y;s.dispatchEvent(new Event('input'));out.push(+document.querySelector('#kpis .kpi:nth-child(3) .n span[style]').textContent);}return out;})()`);
check('map: slider moves the stretched-metros tile monotonically', counts.every((v, i) => i === 0 || v >= counts[i - 1]) && counts.at(-1) > counts[0], counts.join(','));
await ev(`document.getElementById('mode-squeeze').click()`); await sleep(100);
check('map: renter-squeeze toggle switches the legend', /Renters spending 30%\+/.test(await ev(`document.getElementById('legend').innerText`)));
await ev(`document.querySelector('#rail .peers button').click()`); await sleep(200);
check('map: clicking a listed peer opens it', !(await ev(`document.querySelector('#rail h2').textContent`)).startsWith('Miami'));
await go(`${base}/index.html#miami`);
check('map: peer table has Miami + 10 peers + peer median', await ev(`document.querySelectorAll('#rail table.pt2 tbody tr').length`) === 12);
check('map: nav is Map / Forecast / Compare, no Classic view', await ev(`[...document.querySelectorAll('nav a')].map(a=>a.textContent).join('/')`) === 'Map/Forecast/Compare');
check('map: the word "twin" appears nowhere', await ev(noTwin));

// ---------- forecast ----------
await go(`${base}/forecast.html#miami`);
check('forecast: Miami loads with a map and no dropdown', (await ev(`document.querySelector('#top h2').textContent`)).startsWith('Miami') && await ev(`document.querySelectorAll('circle.dot').length`) === 100 && await ev(`document.getElementById('metro')`) === null);
check('forecast: 10 peer lines from the selected metro', await ev(`document.querySelectorAll('line.peerline').length`) === 10);
await clickDot('Tampa'); await sleep(250);
check('forecast: clicking a map dot forecasts that metro', (await ev(`document.querySelector('#top h2').textContent`)).startsWith('Tampa'));
await ev(`document.getElementById('bank').click()`); await sleep(200);
check('forecast: bank play adds the dashed green line', await ev(`document.querySelectorAll('svg.c path.play').length`) === 1);
check('forecast: year-by-year table has 11 rows', await ev(`document.querySelectorAll('.tbl tbody tr').length`) === 11);
await ev(`document.querySelector('#chips button').click()`); await sleep(200);
check('forecast: priority chip switches metro', !(await ev(`document.querySelector('#top h2').textContent`)).startsWith('Tampa'));
check('forecast: the word "twin" appears nowhere', await ev(noTwin));

// ---------- compare ----------
await go(`${base}/compare.html`);
check('compare: defaults to A Miami vs B Pittsburgh', await ev(`document.getElementById('nameA').textContent`) === 'Miami' && await ev(`document.getElementById('nameB').textContent`) === 'Pittsburgh');
check('compare: two big headlines, red for Miami and green for Pittsburgh', await ev(`(()=>{const b=[...document.querySelectorAll('#h2h .big')];return b.length===2 && getComputedStyle(b[0]).color===${JSON.stringify(RED)} && getComputedStyle(b[1]).color===${JSON.stringify(GREEN)};})()`));
check('compare: peer lines for both cities', await ev(`document.querySelectorAll('line.peerline').length`) === 20);
const rel = await ev(`document.querySelector('#h2h .rel').innerText`);
check('compare: says how the two relate as peers', /peer/.test(rel) && /Biggest differences/i.test(rel), rel.split('\n')[0]);
for (const [tab, n] of [['aff', 4], ['acc', 2], ['drv', 5], ['fun', 7]]) {
  await ev(`document.querySelector('button[data-tab="${tab}"]').click()`); await sleep(100);
  check(`compare: ${tab} tab shows ${n} side-by-side rows`, await ev(`document.querySelectorAll('#fbody .track').length`) === n);
}
await ev(`document.querySelector('button[data-tab="fc"]').click()`); await sleep(200);
check('compare: forecast tab overlays both bands', await ev(`document.querySelectorAll('#cchart path.band').length`) === 2 && await ev(`document.querySelectorAll('#fbody .tbl tbody tr').length`) === 5);
await ev(`document.getElementById('cbank').click()`); await sleep(150);
check('compare: bank play adds two dashed lines', await ev(`document.querySelectorAll('#cchart path.play').length`) === 2);
await clickDot('Tampa'); await sleep(200);
check('compare: clicking the map sets city A, then B is next', await ev(`document.getElementById('nameA').textContent`) === 'Tampa' && await ev(`document.getElementById('slotB').getAttribute('aria-pressed')`) === 'true');
await ev(`document.getElementById('swap').click()`); await sleep(150);
check('compare: swap button swaps A and B', await ev(`document.getElementById('nameA').textContent`) === 'Pittsburgh' && await ev(`document.getElementById('nameB').textContent`) === 'Tampa');
await ev(`document.querySelector('#presets button').click()`); await sleep(150);
check('compare: preset loads a pair and a shareable link', await ev(`location.hash`) === '#miami-vs-pittsburgh');
check('compare: the word "twin" appears nowhere', await ev(noTwin));
check('classic view is gone', (await (await fetch(`${base}/legacy.html`)).status) === 404);

// ---------- phone width, every section open ----------
for (const pg of ['index', 'forecast', 'compare']) {
  await go(`${base}/${pg}.html#miami`, 390, 844, true);
  await ev(`document.querySelectorAll('details').forEach(d=>d.open=true)`); await sleep(300);
  const [sw, iw] = await ev(`[document.documentElement.scrollWidth, innerWidth]`);
  check(`${pg}: no sideways scroll at 390px`, sw <= 390 && iw === 390, `scrollWidth ${sw}, innerWidth ${iw}`);
}
// ---------- offline, straight from disk ----------
await send('Network.enable');
await send('Network.emulateNetworkConditions', { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
for (const pg of ['index', 'forecast', 'compare']) { await go(`file://${dir}/${pg}.html`); check(`${pg}: works offline from disk`, await ev(`document.querySelectorAll('circle.dot').length`) === 100); }
check('zero console errors or exceptions', errors.length === 0, errors.join(' | '));
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`);
ws.close(); process.exit(results.every(Boolean) ? 0 : 1);
