// ---- shared by every PeerGap page; assemble.py inlines it after DATA, V2 and US ----
const M = DATA.metros, byId = Object.fromEntries(M.map(x=>[x.id,x]));
const slug = s => s.toLowerCase().replace(/[^a-z0-9]/g,'');
const bySlug = Object.fromEntries(M.map(x=>[slug(x.s),x]));
const esc = s => String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const sgn = v => (v>0?'+':v<0?'−':'')+Math.abs(v).toFixed(2);
const YEARS = V2.years, CFG = V2.cfg;
const TREND = {diverging:'↗ Diverging', recovering:'↘ Recovering', chronic:'→ Chronic', stable:'→ Stable'};
const gapInk = g => g>0 ? 'var(--red)' : g<0 ? 'var(--green)' : 'var(--ink)';
const tierColor = g => g >= CFG.wide_margin_sd ? 'var(--red)' : g >= CFG.gap_metro_sd ? 'var(--orange)' : g <= -CFG.gap_metro_sd ? 'var(--green)' : 'var(--dot)';
const TIER_LEGEND = `<span><i style="background:var(--red)"></i>Wide margin</span><span><i style="background:var(--orange)"></i>Behind</span><span><i style="background:var(--dot)"></i>In line</span><span><i style="background:var(--green)"></i>Ahead</span>`;
const LINE_LEGEND = `<span><i class="ln"></i>Darker line = closer peer</span>`;
const scOf = (id, w) => V2.metros[id].sc[w];
function rankIn(id, w, y, play=false){ const g=(play?scOf(id,w).gp:scOf(id,w).g)[y]; return 1 + M.filter(x=>x.id!==id && scOf(x.id,w).g[y] > g).length; }

// The headline: how far a metro sits from its 10 peers. Red = less affordable, green = more affordable.
function heroGap(g, tail=''){
  const word = g>0 ? 'less affordable' : g<0 ? 'more affordable' : 'as affordable';
  return `<div class="hero"><span class="big" style="color:${gapInk(g)}">${sgn(g)}</span><span class="herotext">standard deviations <b>${word}</b> than its 10 peers${tail}</span></div>`;
}

// US map with one dot per metro. focus() metros get thin gray lines to their 10 peers: darker = closer peer.
function PeerMap(sel, o){
  const proj = d3.geoAlbersUsa().scale(1300).translate([487.5,305]);
  const svg = d3.select(sel).attr('viewBox','0 0 975 610');
  svg.append('g').selectAll('path').data(topojson.feature(US, US.objects.states).features).join('path').attr('class','state').attr('d', d3.geoPath());
  const P = {}; M.forEach(x=>{ const v=V2.metros[x.id], p=proj([v.lon,v.lat]); if(p) P[x.id]=p; });
  const gL = svg.append('g'), gD = svg.append('g'), gT = svg.append('g');
  const dots = gD.selectAll('circle').data(M.filter(x=>P[x.id])).join('circle').attr('class','dot')
    .attr('cx',x=>P[x.id][0]).attr('cy',x=>P[x.id][1]).attr('r',4.5).on('click',(e,x)=>o.onPick(x.id));
  dots.append('title');
  function paint(){
    const focus = o.focus().filter(Boolean), lines = [];
    focus.forEach(id=>{ const td = V2.metros[id].td, lo = Math.min(...td), hi = Math.max(...td);
      byId[id].tw.forEach((t,i)=>{ if(P[id] && P[t]) lines.push({a:id, b:t, i, d:td[i], op: hi===lo ? .8 : .2 + .7*(hi-td[i])/(hi-lo)}); }); });
    gL.selectAll('line').data(lines, l=>l.a+'-'+l.b).join(e=>{ const l=e.append('line').attr('class','peerline'); l.append('title'); return l; })
      .attr('x1',l=>P[l.a][0]).attr('y1',l=>P[l.a][1]).attr('x2',l=>P[l.b][0]).attr('y2',l=>P[l.b][1]).attr('stroke-opacity',l=>l.op.toFixed(2))
      .select('title').text(l=>`${byId[l.b].s}: ${byId[l.a].s}'s #${l.i+1} peer, distance ${l.d.toFixed(2)}`);
    const peers = new Set(lines.map(l=>l.b));
    dots.attr('fill', x=>o.fill(x)).classed('sel', x=>focus.includes(x.id)).classed('peer', x=>peers.has(x.id) && !focus.includes(x.id))
      .attr('r', x=>focus.includes(x.id) ? 7 : peers.has(x.id) ? 5.5 : 4.5)
      .select('title').text(x=>o.title(x));
    gD.selectAll('circle.peer').raise(); gD.selectAll('circle.sel').raise();
    const labs = (o.labels ? o.labels() : focus.map(id=>({id, text:byId[id].s}))).filter(l=>P[l.id]);
    gT.selectAll('text').data(labs).join('text').attr('class','lbl').attr('x',l=>P[l.id][0]+10).attr('y',l=>P[l.id][1]+5).text(l=>l.text);
  }
  return {paint};
}

function wireSearch(onPick){
  const q=document.getElementById('q'), hits=document.getElementById('hits');
  const close=()=>{ q.value=''; hits.classList.remove('open'); };
  q.addEventListener('input', ()=>{ const f=q.value.trim().toLowerCase(); if(!f){ hits.classList.remove('open'); return; }
    const list = M.filter(x=>x.n.toLowerCase().includes(f)).slice(0,8);
    hits.innerHTML = list.map(x=>`<li role="option"><button data-pick="${x.id}">${esc(x.s)}<small>#${x.rank} · ${sgn(x.gap)}</small></button></li>`).join('') || '<li class="note" style="padding:8px">No metro matches.</li>';
    hits.classList.add('open'); });
  q.addEventListener('keydown', e=>{ if(e.key==='Enter'){ const b=hits.querySelector('button'); if(b){ onPick(b.dataset.pick); close(); } } if(e.key==='Escape') hits.classList.remove('open'); });
  hits.addEventListener('click', e=>{ const b=e.target.closest('button[data-pick]'); if(b){ onPick(b.dataset.pick); close(); } });
  document.addEventListener('click', e=>{ if(!e.target.closest('.search')) hits.classList.remove('open'); });
}

// Year slider + play button. Returns setYear(y).
function wireYear(onYear, ids={year:'year', play:'play', label:'yearlbl'}){
  const yr=document.getElementById(ids.year), btn=document.getElementById(ids.play); let timer=null;
  const set = y => { yr.value=y; yr.style.setProperty('--p', ((y-2026)/10*100)+'%'); document.getElementById(ids.label).textContent=y; onYear(+y); };
  yr.addEventListener('input', e=>set(e.target.value));
  btn.addEventListener('click', ()=>{ if(timer){ clearInterval(timer); timer=null; btn.innerHTML='&#9654;'; return; }
    btn.innerHTML='&#10074;&#10074;'; let y = +yr.value >= 2036 ? 2026 : +yr.value; set(y);
    timer = setInterval(()=>{ y++; if(y>2036){ clearInterval(timer); timer=null; btn.innerHTML='&#9654;'; return; } set(y); }, 650); });
  return set;
}

// Band chart: series = [{lo,hi,line,play?,color}] over YEARS, marker at year index y.
function bandChart(el, title, sub, series, y, fmt, opts={}){
  const W=Math.max(320, el.clientWidth-40), H=opts.h||270, L=52, R=16, T=18, B=30;
  const vals = series.flatMap(s=>[...s.lo,...s.hi,...s.line,...(opts.play&&s.play?s.play:[])]);
  let lo=Math.min(...vals), hi=Math.max(...vals); if(opts.zero!==undefined){ lo=Math.min(lo,0); hi=Math.max(hi,0); }
  const pad=(hi-lo)*0.12||1; lo-=pad; hi+=pad;
  const X=i=>L+(W-L-R)*i/(YEARS.length-1), Yv=v=>T+(H-T-B)*(1-(v-lo)/(hi-lo));
  const ln=a=>`M${a.map((v,i)=>`${X(i)},${Yv(v)}`).join('L')}`;
  const area=s=>`M${s.lo.map((v,i)=>`${X(i)},${Yv(v)}`).join('L')}L${s.hi.map((v,i)=>`${X(i)},${Yv(v)}`).reverse().join('L')}Z`;
  const ticks=[0,.25,.5,.75,1].map(t=>lo+(hi-lo)*t);
  el.innerHTML = `${title?`<h3>${title}</h3>`:''}${sub?`<p>${sub}</p>`:''}<svg class="c" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(title||'chart')}">
    ${ticks.map(t=>`<line class="ax" x1="${L}" x2="${W-R}" y1="${Yv(t)}" y2="${Yv(t)}"/><text class="axt" x="${L-6}" y="${Yv(t)+4}" text-anchor="end">${fmt(t)}</text>`).join('')}
    ${YEARS.map((yr,i)=>(W>=600||i%5===0)?`<text class="axt" x="${X(i)}" y="${H-8}" text-anchor="middle">${yr}</text>`:'').join('')}
    ${opts.zero!==undefined?`<line class="zero" x1="${L}" x2="${W-R}" y1="${Yv(0)}" y2="${Yv(0)}"/><text class="labm" x="${W-R}" y="${Yv(0)-5}" text-anchor="end">${opts.zero}</text>`:''}
    ${series.map(s=>`<path class="band" style="fill:${s.color}" d="${area(s)}"/>`).join('')}
    ${series.map(s=>`<path class="line" style="stroke:${s.color}" d="${ln(s.line)}"/>`).join('')}
    ${opts.play?series.filter(s=>s.play).map(s=>`<path class="play" d="${ln(s.play)}"/>`).join(''):''}
    <line class="cur" x1="${X(y)}" x2="${X(y)}" y1="${T}" y2="${H-B}"/>
    ${series.map(s=>`<circle class="pt" style="fill:${s.color}" cx="${X(y)}" cy="${Yv(s.line[y])}" r="5"/><text class="lab" x="${X(y)+9}" y="${Yv(s.line[y])-8}">${s.name?esc(s.name)+' ':''}${fmt(s.line[y])}</text>`).join('')}
    ${opts.play?series.filter(s=>s.play).map(s=>`<circle class="ptp" cx="${X(y)}" cy="${Yv(s.play[y])}" r="5"/><text class="labp" x="${X(y)+9}" y="${Yv(s.play[y])+16}">${fmt(s.play[y])} with play</text>`).join(''):''}
  </svg>`;
}
