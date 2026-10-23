// Dependency-free CDP click-through test for the PeerGap pages. Usage: node cdp_test.mjs <debug-port> <base-url>
const [port, base] = process.argv.slice(2);
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
const results = []; const check = (name, ok, detail = '') => { results.push(ok); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  -> ' + detail : ''}`); };
await send('Runtime.enable'); await send('Page.enable');
const go = async (url, w = 1600, h = 1000, mobile = false) => { await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile }); await send('Page.navigate', { url }); await sleep(2500); };

// ---------- map page ----------
await go(`${base}/index.html#miami`);
check('map draws 100 metro dots', await ev(`document.querySelectorAll('circle.dot').length`) === 100);
check('map draws state outlines', await ev(`document.querySelectorAll('path.state').length`) >= 50, String(await ev(`document.querySelectorAll('path.state').length`)));
check('Miami selected on load', (await ev(`document.querySelector('#rail h2').textContent`)).startsWith('Miami'));
const tiles = await ev(`[...document.querySelectorAll('#scenario .tile')].map(t=>t.innerText.replace(/\\s+/g,' ')).join(' | ')`);
check('scenario card: 64% -> 74%, holds steady, #2 -> #2', /64% → 74%/.test(tiles) && /Holds steady/.test(tiles) && /#2 → #2/.test(tiles), tiles);
await ev(`document.getElementById('bank').click()`); await sleep(200);
const res = await ev(`document.querySelector('#scenario .result').innerText`);
check('bank play switch: gap 12% smaller by 2031', /12% smaller/.test(res), res);
const counts = await ev(`(()=>{const out=[];const s=document.getElementById('year');for(let y=2026;y<=2036;y++){s.value=y;s.dispatchEvent(new Event('input'));out.push(+document.querySelector('#kpis .kpi:nth-child(3) .n span[style]').textContent);}return out;})()`);
check('slider 2026->2036 moves the stretched-metros tile monotonically', counts.every((v, i) => i === 0 || v >= counts[i - 1]) && counts.at(-1) > counts[0], counts.join(','));
await ev(`document.getElementById('mode-squeeze').click()`); await sleep(100);
check('renter-squeeze toggle switches the legend', /Renters spending 30%\+/.test(await ev(`document.getElementById('legend').innerText`)));
await ev(`(()=>{const q=document.getElementById('q');q.value='Tampa';q.dispatchEvent(new Event('input'));q.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter'}));})()`); await sleep(200);
check('search + Enter selects Tampa', (await ev(`document.querySelector('#rail h2').textContent`)).startsWith('Tampa'));
await ev(`document.querySelector('#rail .twins button').click()`); await sleep(200);
const afterTwin = await ev(`document.querySelector('#rail h2').textContent`);
check('clicking a twin opens that metro', !afterTwin.startsWith('Tampa'), afterTwin);
await ev(`document.querySelector('circle.dot').dispatchEvent(new MouseEvent('click',{bubbles:true}))`); await sleep(200);
check('clicking a map dot selects a metro', (await ev(`document.querySelector('circle.dot.sel')!==null`)) === true);
await go(`${base}/index.html#miami`);
check('peer table: Miami + 10 twins + twin median', await ev(`document.querySelectorAll('#rail table.pt tbody tr').length`) === 12);
check('every illustrative element on the rail carries a Phase 1 badge', await ev(`document.querySelectorAll('#rail .badge.p1').length`) >= 3);

// ---------- forecast page ----------
await go(`${base}/forecast.html#miami`);
check('forecast loads Miami', (await ev(`document.querySelector('#top h2').textContent`)).startsWith('Miami'));
check('forecast draws two band charts', await ev(`document.querySelectorAll('svg.c path.band, svg.c path.bandb').length`) === 2);
await ev(`document.getElementById('bank').click()`); await sleep(200);
check('bank play adds the dashed green line', await ev(`document.querySelectorAll('svg.c path.play').length`) === 1);
check('year-by-year table has 11 rows', await ev(`document.querySelectorAll('.tbl tbody tr').length`) === 11);
await ev(`document.querySelector('#chips button').click()`); await sleep(200);
check('priority-market chip switches metro', !(await ev(`document.querySelector('#top h2').textContent`)).startsWith('Miami'));
check('legacy page link still works', (await (await fetch(`${base}/legacy.html`)).status) === 200);

// ---------- phone width ----------
for (const pg of ['index', 'forecast']) {
  await go(`${base}/${pg}.html#miami`, 390, 844, true);
  await ev(`document.querySelectorAll('details').forEach(d=>d.open=true)`); await sleep(300);
  const [sw, iw] = await ev(`[document.documentElement.scrollWidth, innerWidth]`);
  check(`${pg}: no sideways scroll at 390px with every section open`, sw <= 390 && iw === 390, `scrollWidth ${sw}, innerWidth ${iw}`);
}
// ---------- offline, straight from disk ----------
await send('Network.enable');
await send('Network.emulateNetworkConditions', { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
await go(`file://${process.argv[4]}/index.html#miami`);
check('offline from disk: map still draws 100 dots', await ev(`document.querySelectorAll('circle.dot').length`) === 100);
await go(`file://${process.argv[4]}/forecast.html#miami`);
check('offline from disk: forecast still draws its charts', await ev(`document.querySelectorAll('svg.c').length`) === 2);
check('zero console errors or exceptions', errors.length === 0, errors.join(' | '));
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`);
ws.close(); process.exit(results.every(Boolean) ? 0 : 1);
