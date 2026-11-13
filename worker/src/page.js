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
.unproven{color:var(--warn)}.bt-good{border-left:3px solid var(--buy)}.bt-bad{border-left:3px solid var(--sell);color:var(--sell)}details{font-size:13px;margin-top:8px}summary{cursor:pointer;color:var(--mute)}
ul{margin:6px 0 0;padding-left:18px}.hl a{color:var(--ink)}
.open{display:flex;gap:7px;flex-wrap:wrap;align-items:end;margin-top:10px;font-size:11.5px;color:var(--mute)}
.open label{display:flex;flex-direction:column;gap:2px}
input{width:86px;padding:5px 7px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);font:inherit;font-size:13px}
button{padding:6px 11px;border:0;border-radius:6px;font:inherit;font-size:12.5px;font-weight:700;color:#fff;cursor:pointer;white-space:nowrap}
button.long{background:var(--buy)}button.short{background:var(--sell)}button.plain{background:var(--hold)}
.note{color:var(--mute);font-size:13px;margin-top:18px}.err{color:var(--sell)}
.demo{border-left:4px solid var(--sell);font-weight:600}
.tabs{margin:4px 0 16px}.chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}.chip{font-size:12.5px;padding:3px 9px;border:1px solid var(--line);border-radius:999px;background:var(--bg)}.chip b{margin-left:4px}.ev td{font-size:13px}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:0 0 16px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.tile span{display:block;color:var(--mute);font-size:12px}.tile b{font-size:20px;font-variant-numeric:tabular-nums}
svg.curve{height:150px}.rules{font-size:13px;color:var(--mute)}.rules li{margin:2px 0}
`;

export const LOGIN = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Signals: sign in</title><style>${CSS}</style></head><body><main style="max-width:360px;padding-top:80px">
<div class="card"><h1>Trading signals</h1><p class="sub">Sign in to continue</p><!--msg-->
<form method="post" action="/login" style="display:flex;gap:8px"><input type="password" name="password" placeholder="Password" style="flex:1;width:auto" autofocus>
<button class="long">Sign in</button></form></div></main></body></html>`;

const APP = `
let S=null,F={kind:'all',tf:'all',side:'all',proven:false,tested:true,acct:'main',view:location.hash==='#paper'?'paper':'signals'};
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
 +(i.bt?'<div class="box '+(i.bt.v==='good'?'bt-good':i.bt.v==='bad'?'bt-bad':'unproven')+'"><b>Backtest: '+(i.bt.v==='good'?'this kind of signal made money':i.bt.v==='bad'?'this kind of signal LOST money':'no clear edge')+'.</b> Average result per trade '+(i.bt.is>0?'+':'')+i.bt.is+'R in the older period, '+(i.bt.oos>0?'+':'')+i.bt.oos+'R in the recent one ('+i.bt.n.toLocaleString('en-US')+' past trades across this market).'+(i.bt.v==='good'?' Tested this way: no leverage, stop as shown, then a stop trailing 3 ATR behind the best price, no fixed target.':'')+'</div>':'')
 +'<div class="box'+(i.proven?'':' unproven')+'">'+(i.proven?'':'Weak track record on this asset. ')+track+'</div>'
 +'<div class="box"><b>Leverage: up to '+i.lev.x+'x.</b> Position size '+i.lev.size+' (margin '+i.lev.margin+'). Stop is '+i.lev.stop_pct+'% away; if hit, loss is about '+i.lev.risk+'.</div>'
 +'<details><summary>Why, and headlines</summary><ul>'+i.reasons.map(r=>'<li>'+esc(r)+'</li>').join('')+'</ul>'
 +(i.headlines.length?'<ul class="hl">'+i.headlines.map(h=>'<li><a href="'+esc(h.l)+'" target="_blank" rel="noopener">'+esc(h.t)+'</a> ('+(h.s>0?'+':'')+h.s+')</li>').join('')+'</ul>':'')+'</details>'
 +'<div class="open"><label>Entry price<input id="e'+n+'" value="'+i.price+'"></label><label>Leverage<input id="l'+n+'" value="'+i.lev.x+'"></label><label>Position size<input id="s'+n+'" value="'+i.lev.size+'"></label>'
 +'<button class="long" data-open="'+n+'" data-side="long">I bought (long)</button><button class="short" data-open="'+n+'" data-side="short">I went short</button></div></div>';}
function money(v){return (v<0?'-':'')+Math.abs(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});}
function curve(pts,start){if(pts.length<2)return '<p class="sub">The equity curve appears after a few scans.</p>';
 const v=pts.map(p=>p[1]),lo=Math.min(start,...v),hi=Math.max(start,...v),r=(hi-lo)||1,y=x=>(146-(x-lo)/r*140).toFixed(1);
 const d=v.map((x,i)=>(i?'L':'M')+(i/(v.length-1)*600).toFixed(1)+','+y(x)).join(' ');
 return '<svg class="curve" viewBox="0 0 600 150" preserveAspectRatio="none" role="img" aria-label="Equity over time"><line x1="0" x2="600" y1="'+y(start)+'" y2="'+y(start)+'" stroke="var(--line)" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/><path d="'+d+'" fill="none" stroke="var(--accent)" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>'
 +'<div class="sub" style="display:flex;justify-content:space-between;margin:2px 0 0"><span>'+pts[0][0].replace('T',' ')+'</span><span>'+pts[pts.length-1][0].replace('T',' ')+' UTC</span></div>';}
function paperView(){const M=S.paper,accts=M&&M.accounts?Object.keys(M.accounts).filter(a=>M.accounts[a]&&M.accounts[a].balance!=null):[];
 if(F.acct!=='main'&&!accts.includes(F.acct))F.acct='main';
 const P=F.acct==='main'?M:M.accounts[F.acct];
 const pick=accts.length?'<div class="tabs">'+seg('acct',[['main',esc(M.title||'Main account')]].concat(accts.map(a=>[a,esc(M.accounts[a].title||a)])))+'</div>':'';
 if(!P||P.balance==null)return '<section class="card"><h2>Paper trading test</h2><p class="sub" style="margin:0">'+(P&&P.reset?'Reset done. ':'')+'The test starts with the next scan. The first scan only notes which signals already qualify; trades open from the second scan on, when a new one appears.</p></section>';
 const L=P.live||{},up=t=>(L[t.id]?L[t.id].upnl:t.upnl),now=t=>(L[t.id]?L[t.id].price:t.last);
 const eq=Math.round((P.balance+P.open.reduce((a,t)=>a+up(t),0))*100)/100;
 const st=P.stats,ret=(eq/P.start-1)*100,wr=st.trades?Math.round(st.wins/st.trades*100):null,
  aw=st.wins?st.gross_win/st.wins:null,al=(st.trades-st.wins)?st.gross_loss/(st.trades-st.wins):null,pf=st.gross_loss?st.gross_win/st.gross_loss:null,
  margin=P.open.reduce((a,t)=>a+t.notional,0);
 const tile=(k,v,c)=>'<div class="tile"><span>'+k+'</span><b class="'+(c||'')+'">'+v+'</b></div>';
 let h=pick+'<div class="tiles">'+tile('Equity'+(M.live_time?' (live)':''),money(eq),eq>=P.start?'pos':'neg')+tile('Return',(ret>=0?'+':'')+ret.toFixed(2)+'%',ret>=0?'pos':'neg')
  +tile('Balance (closed trades)',money(P.balance))+tile('Closed trades',st.trades)+tile('Win rate',wr==null?'n/a':wr+'%')
  +tile('Avg win / avg loss',(aw==null?'n/a':money(aw))+' / '+(al==null?'n/a':money(al)))+tile('Profit factor',pf==null?'n/a':pf.toFixed(2))
  +tile('Max drawdown',st.max_dd_pct.toFixed(1)+'%')+tile('Open trades',P.open.length+' <small style="font-size:12px;color:var(--mute)">('+money(margin)+' in use)</small>')
  +tile('Fees paid',money(st.fees))+tile('Avg result per trade',st.trades?((st.sum_r/st.trades)>=0?'+':'')+(st.sum_r/st.trades).toFixed(2)+'R':'n/a')+'</div>';
 h+='<section class="card"><h2>Equity</h2>'+curve(P.curve,P.start)+'</section>';
 h+='<section class="card"><h2>Open test trades</h2><p class="sub" style="margin:-4px 0 10px">'+(M.live_time?'Prices live, refreshed every 30 seconds (last '+M.live_time.slice(11,19)+' UTC). ':'')+'Stops are applied at each scan, every 15 minutes, using 5-minute bars, so the fill is at the stop price even if it was hit in between.</p>';
 if(!P.open.length)h+='<p class="sub" style="margin:0">None right now. A trade opens when a new signal that fits the rules below appears.</p>';
 else{h+='<div class="scroll"><table><thead><tr><th>Opened (UTC)</th><th>Asset</th><th>Side</th><th class="num">Size</th><th class="num">Entry</th><th class="num">Now</th><th class="num">Stop</th><th class="num">Target</th><th class="num">Result</th><th>Closes by</th></tr></thead><tbody>';
  for(const t of P.open.slice().reverse())h+='<tr><td>'+t.opened.slice(0,16).replace('T',' ')+'</td><td><b>'+esc(t.symbol)+'</b> '+t.tf+'</td><td>'+t.side.toUpperCase()+(t.partial_done?' <small>(half sold)</small>':'')+'</td><td class="num">'+money(t.notional)+'</td><td class="num">'+fp(t.entry)+'</td><td class="num">'+fp(now(t))+'</td><td class="num">'+fp(t.stop)+'</td><td class="num">'+(t.target==null?'none':fp(t.target))+'</td><td class="num '+(up(t)>=0?'pos':'neg')+'">'+money(up(t))+(L[t.id]&&L[t.id].hit?'<br><small class="a-warn">'+L[t.id].hit+' reached, closes at next scan</small>':'')+'</td><td>'+t.until.slice(0,16).replace('T',' ')+'</td></tr>';
  h+='</tbody></table></div>';}
 h+='</section>';
 const cl=P.closed.slice().sort((a,b)=>a.closed<b.closed?1:-1).slice(0,50);
 if(cl.length){h+='<section class="card"><h2>Closed test trades <small style="color:var(--mute);font-weight:400">last 50</small></h2><div class="scroll"><table><thead><tr><th>Closed (UTC)</th><th>Asset</th><th>Side</th><th class="num">Size</th><th class="num">Entry</th><th class="num">Exit</th><th>Why</th><th class="num">Profit</th><th class="num">Balance</th></tr></thead><tbody>';
  for(const t of cl)h+='<tr><td>'+t.closed.slice(0,16).replace('T',' ')+'</td><td><b>'+esc(t.symbol)+'</b> '+t.tf+'</td><td>'+t.side.toUpperCase()+'</td><td class="num">'+money(t.margin)+'</td><td class="num">'+fp(t.entry)+'</td><td class="num">'+fp(t.exit)+'</td><td>'+esc(t.reason)+'</td><td class="num '+(t.pnl>=0?'pos':'neg')+'">'+money(t.pnl)+' <small>('+(t.r>0?'+':'')+(t.r==null?t.pct+'%':t.r+'R')+')</small></td><td class="num">'+money(t.balance)+'</td></tr>';
  h+='</tbody></table></div></section>';}
 h+='<section class="card"><h2>Rules of this test</h2><ul class="rules"><li>Started '+P.started.slice(0,16).replace('T',' ')+' UTC.</li>'+(P.rules_text||[]).map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>'
  +'<button class="plain" id="reset" style="margin-top:8px">Reset the test</button></section>';
 return h;}
function contextCard(C){if(!C)return '';
 const show=['btc_above_ema100','btc_above_ema200','spx_above_200d','vix_below_20','dollar_weakening','yields_falling','stables_growing','funding_hot'],v=C.values||{},f=C.flags||{};
 const nums=[['Fear & Greed',v.fear_greed,0,''],['VIX',v.vix,1,''],['Stablecoin supply, 30 days',v.stables_30d_pct,1,'%'],['BTC funding, 7-day avg',v.funding_7d_pct,3,'%']].filter(x=>x[1]!=null);
 let h='<section class="card"><h2>Market context <small style="color:var(--mute);font-weight:400">as of '+esc(C.day)+' close</small></h2><div class="chips">'
  +nums.map(x=>'<span class="chip">'+x[0]+'<b>'+Number(x[1]).toFixed(x[2])+x[3]+'</b></span>').join('')
  +show.filter(k=>f[k]!=null).map(k=>'<span class="chip">'+esc((C.labels||{})[k]||k)+'<b class="'+(f[k]?'pos':'neg')+'">'+(f[k]?'yes':'no')+'</b></span>').join('')+'</div>';
 if(C.note)h+='<p class="sub" style="margin:6px 0">'+esc(C.note)+'</p>';
 if(C.events&&C.events.length){h+='<div class="scroll"><table class="ev"><thead><tr><th>Major releases this week (UTC)</th><th></th><th>Event</th><th class="num">Forecast</th><th class="num">Previous</th></tr></thead><tbody>'
  +C.events.map(e=>'<tr><td>'+esc(e.time)+'</td><td>'+esc(e.country)+'</td><td>'+esc(e.title)+'</td><td class="num">'+esc(e.forecast||'')+'</td><td class="num">'+esc(e.previous||'')+'</td></tr>').join('')+'</tbody></table></div><p class="sub" style="margin:6px 0 0">Prices often jump around these releases. The bot does not predict their outcome.</p>';}
 else h+='<p class="sub" style="margin:6px 0 0">No major US or euro-area releases left this week.</p>';
 return h+'</section>';}
function render(){
 const R=S.results;let h='<h1>Trading signals</h1><div class="tabs">'+seg('view',[['signals','Signals'],['paper','Paper test']])+'</div>';
 if(F.view==='paper'){$('#app').innerHTML=h+paperView();return;}
 if(!R){$('#app').innerHTML=h+'<p class="sub">No scan results yet. The first scan arrives after the GitHub "Scan markets" job has run once.</p>'+positions();return;}
 const st=R.stats,age=Math.round((Date.now()-Date.parse(R.generated.replace(' ','T')+'Z'))/60000);
 h+='<p class="sub">Last scan '+R.generated+' UTC ('+age+' min ago) &middot; '+st.crypto+' crypto pairs and '+st.stocks+' stocks scanned &middot; '+st.signals+' signals'+(st.fear_greed!=null?' &middot; crypto Fear &amp; Greed '+st.fear_greed:'')+(age>120?' &middot; <b class="err">scanner looks stalled</b>':'')+'</p>';
 if(R.demo)h+='<div class="card demo">DEMO DATA: these prices are randomly generated, not real.</div>';
 h+=positions()+contextCard(R.context);
 h+='<div class="filters">'+seg('kind',[['all','All'],['crypto','Crypto'],['stock','Stocks']])+seg('tf',[['all','All'],['1h','1h'],['4h','4h'],['1d','1d']])+seg('side',[['all','Buy + Sell'],['buy','Buy'],['sell','Sell']])
 +'<label class="chk"><input type="checkbox" id="tested" style="width:auto" '+(F.tested?'checked':'')+'> only kinds of signal that made money in the backtest</label>'
 +'<label class="chk"><input type="checkbox" id="proven" style="width:auto" '+(F.proven?'checked':'')+'> good track record on this asset</label></div>';
 const list=R.items.map((i,n)=>[i,n]).filter(([i])=>(F.kind==='all'||i.kind===F.kind)&&(F.tf==='all'||i.tf===F.tf)&&(F.side==='all'||(F.side==='buy')===(i.score>0))&&(!F.proven||i.proven)&&(!F.tested||(i.bt&&i.bt.v==='good')))
  .sort((a,b)=>Math.abs(b[0].score)-Math.abs(a[0].score));
 h+='<p class="sub">'+list.length+' signals match, strongest first'+(list.length>60?' (showing 60)':'')+'</p><div class="grid">'+list.slice(0,60).map(([i,n])=>card(i,n)).join('')+'</div>';
 h+='<p class="note">Score runs from -100 to +100. Signals use closed bars only. "Backtest" is how every signal of that kind (market, timeframe, direction) did over the last years, net of fees, with a trailing stop; R is the amount risked to the stop. It is history, not a promise. Leverage is the highest that keeps liquidation at least twice as far as the stop; liquidation prices are approximate. * = live price unavailable, last known price shown. Not financial advice.</p>';
 $('#app').innerHTML=h;}
document.addEventListener('click',async ev=>{const t=ev.target;
 if(t.dataset.f){F[t.dataset.f]=t.dataset.v;if(t.dataset.f==='view')history.replaceState(null,'',t.dataset.v==='paper'?'#paper':location.pathname);render();}
 else if(t.dataset.open){const n=t.dataset.open,i=S.results.items[n];t.disabled=true;
  await api('/api/open',{symbol:i.symbol,tf:i.tf,side:t.dataset.side,entry:$('#e'+n).value,leverage:$('#l'+n).value,size:$('#s'+n).value});await load();scrollTo({top:0,behavior:'smooth'});}
 else if(t.dataset.close){t.disabled=true;await api('/api/close',{id:t.dataset.close,exit:$('#x'+t.dataset.close).value});await load();}});
document.addEventListener('click',async ev=>{if(ev.target.id==='reset'&&confirm('Start the paper test again from the beginning? All test trades in all accounts are cleared.')){ev.target.disabled=true;await api('/api/paper/reset',{});await load();}});
document.addEventListener('change',ev=>{const id=ev.target.id;if(id==='proven'||id==='tested'){F[id]=ev.target.checked;render();}});
setInterval(()=>{const a=document.activeElement;if(!a||a.tagName!=='INPUT'||a.type==='checkbox')load();},30000);
load();
`;

export const PAGE = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading signals</title><style>${CSS}</style></head><body><main id="app"><p class="sub">Loading...</p></main><script>${APP}</script></body></html>`;
