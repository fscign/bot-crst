// The dashboard page. Plain HTML + JS, rendered in the browser from /api/state.
const CSS = `
:root{--bg:#f6f7f9;--card:#fff;--ink:#16202b;--mute:#667382;--line:#e3e7ec;--buy:#0f8a5f;--sell:#c8323c;
--hold:#7a8694;--accent:#2f6fde;--warn:#b97a00}
@media(prefers-color-scheme:dark){:root{--bg:#0f1318;--card:#181e26;--ink:#e8edf2;--mute:#93a0ae;--line:#28313c;
--buy:#3fcf96;--sell:#ff6b73;--hold:#93a0ae;--accent:#6ea3ff;--warn:#e7b04a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1240px;margin:0 auto;padding:22px 16px 60px}
h1{font-size:22px;margin:0 0 2px}h2{font-size:17px;margin:0 0 10px}.sub{color:var(--mute);margin:0 0 16px;font-size:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:0 0 16px}
.mine{border-left:4px solid var(--accent)}.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);font-size:13.5px}
th{color:var(--mute);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.04em;white-space:nowrap}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;font-weight:700;font-size:11.5px;color:#fff;white-space:nowrap}
.b-buy{background:var(--buy)}.b-sell{background:var(--sell)}.pos{color:var(--buy)}.neg{color:var(--sell)}
.a-close{color:var(--sell)}.a-profit{color:var(--buy)}.a-warn{color:var(--warn)}.adv{font-weight:700}
.filters{display:flex;gap:14px;flex-wrap:wrap;align-items:center;margin:0 0 14px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--card)}
.seg button{background:none;color:var(--ink);border:0;padding:6px 12px;font:inherit;font-size:13px;font-weight:600;cursor:pointer;border-radius:0}
.seg button.on{background:var(--accent);color:#fff}
label.chk{font-size:13px;color:var(--mute);display:flex;gap:6px;align-items:center}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}
.sig{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.sig h3{margin:0;font-size:15px;display:flex;justify-content:space-between;gap:8px;align-items:center}
.sig h3 small{color:var(--mute);font-weight:400}
.big{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums;margin-top:4px}
.big small{font-size:13px;color:var(--mute);font-weight:400}
svg{display:block;width:100%;height:54px;margin:6px 0}
.kv{display:flex;gap:16px;flex-wrap:wrap;font-size:13px}.kv span{color:var(--mute)}
.box{font-size:12.5px;margin-top:8px;padding:7px 9px;background:var(--bg);border-radius:8px}
.unproven{color:var(--warn)}details{font-size:13px;margin-top:8px}summary{cursor:pointer;color:var(--mute)}
ul{margin:6px 0 0;padding-left:18px}.hl a{color:var(--ink)}
.open{display:flex;gap:7px;flex-wrap:wrap;align-items:end;margin-top:10px;font-size:11.5px;color:var(--mute)}
.open label{display:flex;flex-direction:column;gap:2px}
input{width:86px;padding:5px 7px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);font:inherit;font-size:13px}
button{padding:6px 11px;border:0;border-radius:6px;font:inherit;font-size:12.5px;font-weight:700;color:#fff;cursor:pointer;white-space:nowrap}
button.long{background:var(--buy)}button.short{background:var(--sell)}button.plain{background:var(--hold)}
.note{color:var(--mute);font-size:13px;margin-top:18px}.err{color:var(--sell)}
.demo{border-left:4px solid var(--sell);font-weight:600}
`;

export const LOGIN = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Signals: sign in</title><style>${CSS}</style></head><body><main style="max-width:360px;padding-top:80px">
<div class="card"><h1>Trading signals</h1><p class="sub">Sign in to continue</p><!--msg-->
<form method="post" action="/login" style="display:flex;gap:8px"><input type="password" name="password" placeholder="Password" style="flex:1;width:auto" autofocus>
<button class="long">Sign in</button></form></div></main></body></html>`;

const APP = `
let S=null,F={kind:'all',tf:'all',side:'all',proven:true};
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fp=p=>p==null?'':(p>=1?p.toLocaleString('en-US',{maximumFractionDigits:2}):Number(p).toPrecision(4));
const badge=l=>'<span class="badge '+(l.includes('BUY')?'b-buy':'b-sell')+'">'+l+'</span>';
function spark(v,up){const lo=Math.min(...v),hi=Math.max(...v),r=(hi-lo)||1;
 const d=v.map((x,i)=>(i?'L':'M')+(i/(v.length-1)*300).toFixed(1)+','+(50-(x-lo)/r*46).toFixed(1)).join(' ');
 return '<svg viewBox="0 0 300 54" preserveAspectRatio="none" role="img" aria-label="Last 60 bars"><path d="'+d+'" fill="none" stroke="var(--'+(up?'buy':'sell')+')" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>';}
async function api(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:{'content-type':'application/json'},body:body?JSON.stringify(body):undefined});
 if(r.status===401){location.reload();throw new Error('login')}const j=await r.json();if(j.error)alert(j.error);return j;}
async function load(){S=await api('/api/state');render();}
function seg(name,opts){return '<div class="seg">'+opts.map(o=>'<button data-f="'+name+'" data-v="'+o[0]+'" class="'+(F[name]===o[0]?'on':'')+'">'+o[1]+'</button>').join('')+'</div>';}
function positions(){
 const open=S.positions.filter(p=>p.status==='open'),closed=S.positions.filter(p=>p.status==='closed').slice(-10).reverse();
 let h='<section class="card mine"><h2>My open positions</h2>';
 if(!open.length)h+='<p class="sub" style="margin:0">None yet. When you really open a trade, press "I bought" or "I went short" on that signal and it is watched here every minute.</p>';
 else{h+='<div class="scroll"><table><thead><tr><th>Asset</th><th>Side</th><th class="num">Entry</th><th class="num">Now</th><th class="num">Result</th><th class="num">Money</th><th class="num">Stop (trailing)</th><th class="num">Target</th><th class="num">Liquidation</th><th>What to do</th><th>Exit price</th></tr></thead><tbody>';
  for(const p of open){const e=S.evals[p.id];if(!e)continue;const c=e.move_pct>=0?'pos':'neg';
   h+='<tr><td><b>'+esc(p.symbol)+'</b> '+p.tf+'</td><td>'+p.side.toUpperCase()+' '+p.leverage+'x</td><td class="num">'+fp(p.entry)+'</td><td class="num">'+fp(e.price)+(e.stale?' *':'')+'</td>'
   +'<td class="num '+c+'">'+e.move_pct.toFixed(2)+'% <small>('+e.lev_pct.toFixed(1)+'% on margin)</small></td><td class="num '+c+'">'+(e.money==null?'':e.money.toFixed(2))+'</td>'
   +'<td class="num">'+fp(e.stop)+'</td><td class="num">'+fp(p.target)+'</td><td class="num">'+(e.liq?fp(e.liq):'none')+'</td><td class="adv a-'+e.code+'">'+esc(e.text)+'</td>'
   +'<td style="white-space:nowrap"><input id="x'+p.id+'" value="'+Number(e.price.toPrecision(6))+'"> <button class="plain" data-close="'+p.id+'">I closed it</button></td></tr>';}
  h+='</tbody></table></div>';}
 h+='</section>';
 if(closed.length){h+='<section class="card"><h2>Closed trades <small style="color:var(--mute);font-weight:400">last 10</small></h2><div class="scroll"><table><thead><tr><th>Asset</th><th>Side</th><th>Opened (UTC)</th><th>Closed (UTC)</th><th class="num">Entry</th><th class="num">Exit</th><th class="num">Result</th><th class="num">Money</th></tr></thead><tbody>';
  for(const p of closed){const mv=(p.side==='long'?1:-1)*(p.exit/p.entry-1);
   h+='<tr><td><b>'+esc(p.symbol)+'</b> '+p.tf+'</td><td>'+p.side.toUpperCase()+' '+p.leverage+'x</td><td>'+p.opened+'</td><td>'+p.closed+'</td><td class="num">'+fp(p.entry)+'</td><td class="num">'+fp(p.exit)+'</td>'
   +'<td class="num '+(mv>=0?'pos':'neg')+'">'+(mv*100).toFixed(2)+'% ('+(mv*p.leverage*100).toFixed(1)+'% on margin)</td><td class="num">'+(p.size?(p.size*mv).toFixed(2):'')+'</td></tr>';}
  h+='</tbody></table></div></section>';}
 return h;}
function card(i,n){const up=i.score>0,hi=i.hist;
 const track=hi.n?('Past '+(up?'BUY':'SELL')+' signals here: '+hi.n+', average move '+(hi.avg>0?'+':'')+hi.avg+'% in your favour over '+hi.bars+' bars, '+hi.win+'% worked.'):'No past signals of this kind on this asset.';
 return '<div class="sig"><h3><span>'+esc(i.symbol)+' <small>'+esc(i.name)+' &middot; '+i.tf+'</small></span>'+badge(i.label)+'</h3>'
 +'<div class="big">'+fp(i.price)+' <small>score '+(i.score>0?'+':'')+i.score+' &middot; bar '+i.time+' UTC</small></div>'+spark(i.spark,up)
 +'<div class="kv"><div><span>Stop</span> '+fp(i.stop)+'</div><div><span>Target</span> '+fp(i.target)+'</div><div><span>News</span> '+(i.news==null?'n/a':(i.news>0?'+':'')+i.news)+'</div></div>'
 +'<div class="box'+(i.proven?'':' unproven')+'">'+(i.proven?'':'Weak track record. ')+track+'</div>'
 +'<div class="box"><b>Leverage: up to '+i.lev.x+'x.</b> Position size '+i.lev.size+' (margin '+i.lev.margin+'). Stop is '+i.lev.stop_pct+'% away; if hit, loss is about '+i.lev.risk+'.</div>'
 +'<details><summary>Why, and headlines</summary><ul>'+i.reasons.map(r=>'<li>'+esc(r)+'</li>').join('')+'</ul>'
 +(i.headlines.length?'<ul class="hl">'+i.headlines.map(h=>'<li><a href="'+esc(h.l)+'" target="_blank" rel="noopener">'+esc(h.t)+'</a> ('+(h.s>0?'+':'')+h.s+')</li>').join('')+'</ul>':'')+'</details>'
 +'<div class="open"><label>Entry price<input id="e'+n+'" value="'+i.price+'"></label><label>Leverage<input id="l'+n+'" value="'+i.lev.x+'"></label><label>Position size<input id="s'+n+'" value="'+i.lev.size+'"></label>'
 +'<button class="long" data-open="'+n+'" data-side="long">I bought (long)</button><button class="short" data-open="'+n+'" data-side="short">I went short</button></div></div>';}
function render(){
 const R=S.results;let h='<h1>Trading signals</h1>';
 if(!R){$('#app').innerHTML=h+'<p class="sub">No scan results yet. The first scan arrives after the GitHub "Scan markets" job has run once.</p>'+positions();return;}
 const st=R.stats,age=Math.round((Date.now()-Date.parse(R.generated.replace(' ','T')+'Z'))/60000);
 h+='<p class="sub">Last scan '+R.generated+' UTC ('+age+' min ago) &middot; '+st.crypto+' crypto pairs and '+st.stocks+' stocks scanned &middot; '+st.signals+' signals'+(st.fear_greed!=null?' &middot; crypto Fear &amp; Greed '+st.fear_greed:'')+(age>120?' &middot; <b class="err">scanner looks stalled</b>':'')+'</p>';
 if(R.demo)h+='<div class="card demo">DEMO DATA: these prices are randomly generated, not real.</div>';
 h+=positions();
 h+='<div class="filters">'+seg('kind',[['all','All'],['crypto','Crypto'],['stock','Stocks']])+seg('tf',[['all','All'],['1h','1h'],['4h','4h'],['1d','1d']])+seg('side',[['all','Buy + Sell'],['buy','Buy'],['sell','Sell']])
 +'<label class="chk"><input type="checkbox" id="proven" style="width:auto" '+(F.proven?'checked':'')+'> only signals with a good track record</label></div>';
 const list=R.items.map((i,n)=>[i,n]).filter(([i])=>(F.kind==='all'||i.kind===F.kind)&&(F.tf==='all'||i.tf===F.tf)&&(F.side==='all'||(F.side==='buy')===(i.score>0))&&(!F.proven||i.proven))
  .sort((a,b)=>Math.abs(b[0].score)-Math.abs(a[0].score));
 h+='<p class="sub">'+list.length+' signals match, strongest first'+(list.length>60?' (showing 60)':'')+'</p><div class="grid">'+list.slice(0,60).map(([i,n])=>card(i,n)).join('')+'</div>';
 h+='<p class="note">Score runs from -100 to +100. Signals use closed bars only. "Track record" looks at earlier signals of the same kind on the same asset; it is history, not a promise. Leverage is the highest that keeps liquidation at least twice as far as the stop; liquidation prices are approximate. * = live price unavailable, last known price shown. Not financial advice.</p>';
 $('#app').innerHTML=h;}
document.addEventListener('click',async ev=>{const t=ev.target;
 if(t.dataset.f){F[t.dataset.f]=t.dataset.v;render();}
 else if(t.dataset.open){const n=t.dataset.open,i=S.results.items[n];t.disabled=true;
  await api('/api/open',{symbol:i.symbol,tf:i.tf,side:t.dataset.side,entry:$('#e'+n).value,leverage:$('#l'+n).value,size:$('#s'+n).value});await load();scrollTo({top:0,behavior:'smooth'});}
 else if(t.dataset.close){t.disabled=true;await api('/api/close',{id:t.dataset.close,exit:$('#x'+t.dataset.close).value});await load();}});
document.addEventListener('change',ev=>{if(ev.target.id==='proven'){F.proven=ev.target.checked;render();}});
setInterval(()=>{const a=document.activeElement;if(!a||a.tagName!=='INPUT'||a.type==='checkbox')load();},60000);
load();
`;

export const PAGE = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading signals</title><style>${CSS}</style></head><body><main id="app"><p class="sub">Loading...</p></main><script>${APP}</script></body></html>`;
