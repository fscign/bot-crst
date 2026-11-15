// The odds dashboard page. Plain HTML + JS, rendered in the browser from /api/state.
const CSS = `
:root{--bg:#f6f7f9;--card:#fff;--ink:#16202b;--mute:#667382;--line:#e3e7ec;--good:#0f8a5f;--bad:#c8323c;
--accent:#2f6fde;--warn:#b97a00;--hi:#e6f5ee}
@media(prefers-color-scheme:dark){:root{--bg:#0f1318;--card:#181e26;--ink:#e8edf2;--mute:#93a0ae;--line:#28313c;
--good:#3fcf96;--bad:#ff6b73;--accent:#6ea3ff;--warn:#e7b04a;--hi:#16352a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:22px 16px 60px}
h1{font-size:22px;margin:0 0 2px}h2{font-size:17px;margin:0 0 10px}h3{font-size:15px;margin:0}
.sub{color:var(--mute);margin:0 0 16px;font-size:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:0 0 16px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);font-size:13.5px}
th{color:var(--mute);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.04em;white-space:nowrap}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr.first td{border-top:2px solid var(--line)}td.game{vertical-align:top}td.game small{display:block;color:var(--mute)}
td.hit{background:var(--hi);color:var(--good);font-weight:700}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;font-weight:700;font-size:12px;color:#fff;white-space:nowrap;background:var(--good)}
.badge.arb{background:var(--accent)}
.pos{color:var(--good)}.neg{color:var(--bad)}.mute{color:var(--mute)}.warn{color:var(--warn)}.err{color:var(--bad)}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--card);margin:4px 0 16px}
.seg button{background:none;color:var(--ink);border:0;padding:7px 14px;font:inherit;font-size:13.5px;font-weight:600;cursor:pointer}
.seg button.on{background:var(--accent);color:#fff}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px;margin:0 0 16px}
.alert{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--good);border-radius:12px;padding:14px}
.alert.arb{border-left-color:var(--accent)}
.alert h3{display:flex;justify-content:space-between;gap:8px;align-items:center}
.alert .when{color:var(--mute);font-size:13px;margin:2px 0 10px}
.pick{font-size:17px;font-weight:700;margin:0 0 6px}.pick b{font-variant-numeric:tabular-nums}
.kv{display:flex;gap:14px;flex-wrap:wrap;font-size:13px}.kv span{color:var(--mute)}
.box{font-size:12.5px;margin-top:10px;padding:7px 9px;background:var(--bg);border-radius:8px;color:var(--mute)}
.legs{margin:0 0 8px;padding:0;list-style:none;font-size:14px}.legs li{display:flex;justify-content:space-between;gap:10px;padding:3px 0}
.calc{display:flex;gap:8px;align-items:center;font-size:13px;color:var(--mute)}
input{width:90px;padding:5px 7px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);font:inherit;font-size:13px}
button.go{padding:7px 12px;border:0;border-radius:6px;font:inherit;font-size:13px;font-weight:700;color:#fff;background:var(--accent);cursor:pointer}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:0 0 16px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.tile span{display:block;color:var(--mute);font-size:12px}.tile b{font-size:20px;font-variant-numeric:tabular-nums}
.note{color:var(--mute);font-size:13px}.empty{color:var(--mute);padding:6px 0}
ol,ul.plain{margin:6px 0 0;padding-left:20px}li{margin:3px 0}
`;

export const LOGIN = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Odds watch: sign in</title><style>${CSS}</style></head><body><main style="max-width:360px;padding-top:80px">
<div class="card"><h1>Odds watch</h1><p class="sub">Sign in to continue</p><!--msg-->
<form method="post" action="/login" style="display:flex;gap:8px"><input type="password" name="password" placeholder="Password" style="flex:1;width:auto" autofocus>
<button class="go">Sign in</button></form></div></main></body></html>`;

// Everything below runs in the browser.
function app() {
  let S = null, view = ({ "#games": "games", "#paper": "paper", "#help": "help" })[location.hash] || "alerts";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const od = (x) => (x == null ? "" : Number(x).toFixed(2));
  const pct = (x, d = 1) => (x == null ? "n/a" : (x >= 0 ? "+" : "") + (x * 100).toFixed(d) + "%");
  const money = (x) => (x == null ? "" : Number(x).toFixed(2));
  const cls = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "");
  const tz = () => (S && S.config && S.config.timezone) || "Europe/Tallinn";
  const when = (t) => new Date(t).toLocaleString("en-GB", { timeZone: tz(), weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const ago = (t) => { const m = Math.round((Date.now() - new Date(t)) / 60000); return m < 1 ? "just now" : m < 90 ? m + " min ago" : m < 2880 ? Math.round(m / 60) + " h ago" : Math.round(m / 1440) + " days ago"; };
  const book = (k) => esc((S.config.my_books || {})[k] || k);
  const market = (m) => (m === "h2h" ? "" : " (" + esc(m) + ")");
  const upcoming = (x) => new Date(x.start) > Date.now();

  function alertsView() {
    const all = Object.values(S.board || {}).flatMap((b) => b.alerts || []).filter(upcoming);
    const values = all.filter((a) => a.kind === "value").sort((a, b) => b.edge - a.edge);
    const arbs = all.filter((a) => a.kind === "arb").sort((a, b) => b.profit - a.profit);
    const minEdge = S.config.min_edge;
    let h = "<h2>Value bets</h2>";
    h += values.length ? '<div class="grid">' + values.map((a) => {
      const floor = (1 + minEdge) / a.prob, note = (S.config.book_notes || {})[a.book];
      return '<div class="alert"><h3><span>' + esc(a.home) + " v " + esc(a.away) + '</span><span class="badge">' + pct(a.edge) + "</span></h3>"
        + '<div class="when">' + esc(a.league) + ", " + when(a.start) + "</div>"
        + '<p class="pick">' + esc(a.outcome) + market(a.market) + " @ <b>" + od(a.odds) + "</b> at " + book(a.book) + "</p>"
        + '<div class="kv"><div><span>Fair price</span> ' + od(a.fair) + "</div><div><span>Still value above</span> " + od(floor)
        + "</div><div><span>Paper stake</span> " + money(a.stake) + " " + esc(S.config.currency) + "</div></div>"
        + '<div class="box">Found ' + ago(a.found) + ", fair price from " + esc(a.source) + (a.confirmed ? ", confirmed by the exchange" : "")
        + ". Check the price is still there before betting." + (note ? "<br>" + esc(note) : "") + "</div></div>";
    }).join("") + "</div>" : '<div class="card empty">No value bets right now. That is normal: most scans find none.</div>';
    h += "<h2>Arbitrage</h2>";
    h += arbs.length ? '<div class="grid">' + arbs.map((a, i) =>
      '<div class="alert arb"><h3><span>' + esc(a.home) + " v " + esc(a.away) + '</span><span class="badge arb">' + pct(a.profit, 2) + "</span></h3>"
      + '<div class="when">' + esc(a.league) + ", " + when(a.start) + market(a.market) + "</div>"
      + '<ul class="legs" id="legs' + i + '">' + legs(a, 100) + "</ul>"
      + '<div class="calc">Total stake <input type="number" min="1" value="100" data-arb="' + i + '"> ' + esc(S.config.currency) + "</div>"
      + '<div class="box">Found ' + ago(a.found) + ". Place the leg at the bookmaker most likely to change its price first, and confirm both prices before the first bet.</div></div>").join("") + "</div>"
      : '<div class="card empty">No arbitrage between your bookmakers right now.</div>';
    window._arbs = arbs;
    return h;
  }
  function legs(a, total) {
    const pay = total * a.legs[0].share * a.legs[0].odds;
    return a.legs.map((l) => "<li><span>" + esc(l.outcome) + " @ <b>" + od(l.odds) + "</b> at " + book(l.book) + "</span><b>" + money(total * l.share) + "</b></li>").join("")
      + '<li class="mute"><span>Returns whichever way it ends</span><b class="pos">' + money(pay) + " (" + (pay - total >= 0 ? "+" : "") + money(pay - total) + ")</b></li>";
  }

  function gamesView() {
    const mine = Object.keys(S.config.my_books || {});
    const manual = (S.config.manual_books || []).join(" and ");
    let h = manual ? '<p class="note" style="margin:0 0 14px">' + esc(manual) + " are not in the data feed. Compare their price with the <b>Value from</b> column yourself: anything above it is a value bet by the same rule.</p>" : "";
    const leagues = Object.entries(S.board || {}).map(([k, b]) => [k, b, (b.rows || []).filter(upcoming).sort((x, y) => x.start.localeCompare(y.start))]).filter((x) => x[2].length);
    if (!leagues.length) return h + '<div class="card empty">No upcoming games scanned yet.</div>';
    for (const [, b, rows] of leagues) {
      h += '<div class="card"><h2>' + esc(rows[0].league) + ' <small class="mute" style="font-weight:400;font-size:13px">scanned ' + ago(b.scanned) + '</small></h2><div class="scroll"><table><tr><th>Game</th><th>Outcome</th><th class="num">Fair price</th><th class="num">Value from</th>'
        + mine.map((m) => '<th class="num">' + book(m) + "</th>").join("") + "</tr>";
      for (const r of rows) for (const m of r.markets) {
        m.outcomes.forEach((o, i) => {
          h += "<tr" + (i === 0 ? ' class="first"' : "") + ">" + (i === 0 ? '<td class="game" rowspan="' + m.outcomes.length + '">' + esc(r.home) + " v " + esc(r.away) + "<small>" + when(r.start) + market(m.market) + (m.note ? '<br><span class="warn">' + esc(m.note) + "</span>" : "") + "</small></td>" : "")
            + "<td>" + esc(o.name) + '</td><td class="num">' + od(o.fair) + '</td><td class="num">' + od(o.value_from) + "</td>"
            + mine.map((k) => { const p = (o.prices || {})[k]; return '<td class="num' + (p && o.value_from && p >= o.value_from ? " hit" : "") + '">' + (p ? od(p) : '<span class="mute">n/a</span>') + "</td>"; }).join("") + "</tr>";
        });
      }
      h += "</table></div></div>";
    }
    return h;
  }

  function paperView() {
    const P = S.paper, s = S.summary || {};
    if (!P) return '<div class="card empty">The paper test starts with the first scan.</div>';
    const cur = " " + esc(S.config.currency);
    const tile = (label, value, c) => '<div class="tile"><span>' + label + '</span><b class="' + (c || "") + '">' + value + "</b></div>";
    let h = '<div class="tiles">' + tile("Paper bankroll", money(P.bankroll) + cur) + tile("Result", (s.pnl >= 0 ? "+" : "") + money(s.pnl || 0) + cur, cls(s.pnl))
      + tile("Bets (open)", (s.bets || 0) + " (" + (s.open || 0) + ")") + tile("Won / lost", (s.won || 0) + " / " + (s.lost || 0))
      + tile("Return on stakes", pct(s.roi), cls(s.roi)) + tile("Average closing line value", pct(s.avg_clv, 2), cls(s.avg_clv))
      + tile("Beat the closing line", s.beat_close == null ? "n/a" : Math.round(s.beat_close * 100) + "%") + "</div>";
    h += '<p class="note" style="margin:0 0 16px">The test places every value alert at the alerted price, since ' + when(P.started) + ". Judge it first by the closing line value: if the average stays above zero over 100+ bets, the alerts are finding real value. Win and loss need a few hundred bets before they mean anything. Expected profit on the settled bets so far: " + money(s.expected || 0) + cur + ".</p>";
    const head = '<tr><th>Game</th><th>Bet</th><th>Bookmaker</th><th class="num">Odds</th><th class="num">Edge</th><th class="num">Stake</th><th class="num">Closing line value</th>';
    const cells = (b) => "<td>" + esc(b.home) + " v " + esc(b.away) + '<br><small class="mute">' + esc(b.league) + ", " + when(b.start) + "</small></td><td>" + esc(b.outcome) + market(b.market) + "</td><td>" + book(b.book)
      + '</td><td class="num">' + od(b.odds) + '</td><td class="num">' + pct(b.edge) + '</td><td class="num">' + money(b.stake) + '</td><td class="num ' + cls(b.clv) + '">' + pct(b.clv, 2) + "</td>";
    h += '<div class="card"><h2>Open bets</h2>' + (P.open.length ? '<div class="scroll"><table>' + head + "</tr>" + P.open.map((b) => "<tr>" + cells(b) + "</tr>").join("") + "</table></div>" : '<div class="empty">None.</div>') + "</div>";
    const done = [...P.settled].reverse();
    h += '<div class="card"><h2>Settled bets</h2>' + (done.length ? '<div class="scroll"><table>' + head + '<th>Result</th><th class="num">Profit</th><th class="num">Bankroll</th></tr>'
      + done.map((b) => "<tr>" + cells(b) + "<td>" + esc(b.result) + '</td><td class="num ' + cls(b.pnl) + '">' + (b.pnl >= 0 ? "+" : "") + money(b.pnl) + '</td><td class="num">' + money(b.balance) + "</td></tr>").join("") + "</table></div>" : '<div class="empty">None yet.</div>') + "</div>";
    return h;
  }

  function helpView() {
    const c = S.config;
    return '<div class="card"><h2>What this does</h2><p>It does not predict games. It compares prices. Pinnacle and the Betfair Exchange take large bets from professionals, so their prices are the best public estimate of the real chances. The scanner removes their margin to get a <b>fair price</b> for every outcome, then looks at the bookmakers you can use.</p>'
      + '<ul class="plain"><li><b>Value bet</b>: your bookmaker pays at least ' + (c.min_edge * 100).toFixed(0) + "% more than the fair price. One bet can still lose; the edge shows over hundreds of bets.</li>"
      + "<li><b>Arbitrage</b>: your bookmakers disagree so much that backing every outcome returns more than the total stake.</li>"
      + "<li><b>Value from</b>: the lowest price that still counts as value. Use it to check any bookmaker that is not in the feed.</li></ul></div>"
      + '<div class="card"><h2>Rules the scanner applies</h2><ul class="plain"><li>Odds between ' + od(c.min_odds) + " and " + od(c.max_odds) + " only.</li>"
      + "<li>Edges above " + (c.max_edge * 100).toFixed(0) + "% are dropped: they are almost always an old or mistaken price, and bookmakers can cancel bets on obvious errors.</li>"
      + "<li>Both prices must have been refreshed in the last few minutes, and the game must not be about to start.</li>"
      + "<li>When Pinnacle and the exchange are both available, the bet must be value against both.</li>"
      + "<li>Paper stake: " + (c.kelly_fraction * 100).toFixed(0) + "% of the Kelly stake, never more than " + (c.max_stake_pct * 100).toFixed(0) + "% of the bankroll.</li></ul></div>"
      + '<div class="card"><h2>Before real money</h2><ul class="plain"><li>The feed is a few minutes behind. Always confirm the price on the bookmaker site first; if it has dropped below <b>Still value above</b>, skip it.</li>'
      + "<li>Bookmakers limit accounts that keep taking value prices. Round stakes and mainstream leagues draw less attention.</li>"
      + "<li>Let the paper test run first. A positive average closing line value over 100+ bets is the sign to look for.</li>"
      + "<li>Only stake money you can afford to lose. This is information, not advice.</li></ul></div>";
  }

  function render() {
    if (!S) { $("#app").innerHTML = '<div class="card empty">No scan has arrived yet. Run the <b>Odds scan</b> workflow on GitHub.</div>'; $("#status").textContent = ""; return; }
    const q = S.quota || {};
    $("#status").textContent = "Last scan " + ago(S.updated) + " (" + when(S.updated) + "). Data credits left this month: " + (q.remaining ?? "?") + " of " + (q.monthly ?? "?") + ".";
    document.querySelectorAll(".seg button").forEach((b) => b.classList.toggle("on", b.dataset.v === view));
    $("#app").innerHTML = ({ alerts: alertsView, games: gamesView, paper: paperView, help: helpView })[view]();
  }
  async function load() {
    try {
      const r = await fetch("/api/state");
      if (r.status === 401) { location.reload(); return; }
      S = await r.json();
      render();
    } catch (e) { $("#status").textContent = "Could not load the data: " + e.message; }
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest(".seg button");
    if (b) { view = b.dataset.v; history.replaceState(null, "", view === "alerts" ? location.pathname : "#" + view); render(); }
  });
  document.addEventListener("input", (e) => {
    const i = e.target.dataset && e.target.dataset.arb;
    if (i != null) document.getElementById("legs" + i).innerHTML = legs(window._arbs[i], Math.max(0, parseFloat(e.target.value) || 0));
  });
  if (window.__STATE__ !== undefined) { S = window.__STATE__; render(); } else { load(); setInterval(load, 120000); }
}

export const PAGE = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Odds watch</title><style>${CSS}</style></head><body><main>
<h1>Odds watch</h1><p class="sub" id="status">Loading</p>
<div class="seg"><button data-v="alerts">Alerts</button><button data-v="games">All games</button><button data-v="paper">Paper test</button><button data-v="help">How it works</button></div>
<div id="app"></div></main><script>(${app.toString()})()</script></body></html>`;
