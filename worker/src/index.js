// Dashboard for the signal bot. Runs on Cloudflare Workers.
//  - receives scan results from the GitHub Actions scanner (POST /api/ingest)
//  - shows them on a password-protected page
//  - stores the positions you really opened, checks their prices every minute
//    and sends Telegram alerts when the advice changes
import { PAGE, LOGIN } from "./page.js";

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });
const html = (body, status = 200, extra = {}) =>
  new Response(body, { status, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", ...extra } });

async function sha256(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
const sessionToken = (env) => sha256("signal-bot-session:" + (env.DASH_PASSWORD || ""));

async function loggedIn(request, env) {
  if (!env.DASH_PASSWORD) return false;
  const cookie = request.headers.get("cookie") || "";
  const m = cookie.match(/(?:^|;\s*)sb=([a-f0-9]+)/);
  return !!m && m[1] === (await sessionToken(env));
}
const bearerOk = (request, env) =>
  !!env.INGEST_TOKEN && request.headers.get("authorization") === "Bearer " + env.INGEST_TOKEN;

const getJSON = async (env, key, fallback) => (await env.KV.get(key, "json")) ?? fallback;
const putJSON = (env, key, value) => env.KV.put(key, JSON.stringify(value));

async function telegram(env, text) {
  if (!env.TG_TOKEN || !env.TG_CHAT) return;
  try {
    await fetch(`https://api.telegram.org/bot${env.TG_TOKEN}/sendMessage`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ chat_id: env.TG_CHAT, text, disable_web_page_preview: true }),
    });
  } catch (e) { console.log("telegram failed", e.message); }
}

const fmt = (p) => (p >= 1 ? p.toLocaleString("en-US", { maximumFractionDigits: 2 }) : Number(p).toPrecision(4));

// ---- live prices -----------------------------------------------------------
async function livePrice(pos) {
  const ua = { "user-agent": "Mozilla/5.0 (signal-bot)" };
  try {
    if (pos.kind === "crypto") {
      let r = await fetch(`https://data-api.binance.vision/api/v3/ticker/price?symbol=${pos.symbol}`, { headers: ua });
      if (r.ok) return parseFloat((await r.json()).price);
      const base = pos.symbol.replace(/USDT$/, "");
      r = await fetch(`https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=${base}-USDT`, { headers: ua });
      if (r.ok) return parseFloat((await r.json()).data.price);
    } else {
      const r = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(pos.symbol)}?interval=1m&range=1d`, { headers: ua });
      if (r.ok) return (await r.json()).chart.result[0].meta.regularMarketPrice;
    }
  } catch (e) { console.log("price failed", pos.symbol, e.message); }
  return null;
}

// Many prices at once, for the paper test: one request covers every crypto pair,
// stocks are fetched one by one (capped, the free plan allows 50 requests per page load).
async function livePrices(list) {
  const ua = { "user-agent": "Mozilla/5.0 (signal-bot)" };
  const out = {};
  const crypto = list.filter((x) => x.kind === "crypto"), stocks = [...new Set(list.filter((x) => x.kind !== "crypto").map((x) => x.symbol))];
  if (crypto.length) {
    try {
      let r = await fetch("https://data-api.binance.vision/api/v3/ticker/price", { headers: ua });
      if (r.ok) for (const t of await r.json()) out[t.symbol] = parseFloat(t.price);
      else {
        r = await fetch("https://api.kucoin.com/api/v1/market/allTickers", { headers: ua });
        if (r.ok) for (const t of (await r.json()).data.ticker) if (t.symbol.endsWith("-USDT") && t.last) out[t.symbol.replace("-USDT", "USDT")] = parseFloat(t.last);
      }
    } catch (e) { console.log("crypto prices failed", e.message); }
  }
  await Promise.all(stocks.slice(0, 35).map(async (s) => {
    try {
      const r = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(s)}?interval=1m&range=1d`, { headers: ua });
      if (r.ok) out[s] = (await r.json()).chart.result[0].meta.regularMarketPrice;
    } catch (e) { console.log("stock price failed", s, e.message); }
  }));
  return out;
}

async function livePaper(raw) {
  if (!raw) return "null";
  const P = JSON.parse(raw);
  const accounts = [P, ...Object.values(P.accounts || {})].filter((a) => a && Array.isArray(a.open));
  const all = accounts.flatMap((a) => a.open);
  if (!all.length) return raw;
  const prices = await livePrices(all);
  for (const A of accounts) {
    A.live = {};
    for (const t of A.open) {
      const price = prices[t.symbol];
      if (!price) continue;
      const side = t.side === "long" ? 1 : -1;
      const move = side * (price / t.entry - 1);
      const fee = (t.fee_pct ?? 0.1) / 100;
      const upnl = t.notional * move - t.notional * fee * (1 + price / t.entry);
      const hit = (price - t.stop) * side <= 0 ? "stop" : (t.target != null && (price - t.target) * side >= 0) ? "target" : null;
      A.live[t.id] = { price, upnl: Math.round(upnl * 100) / 100, hit };
    }
  }
  P.live_time = new Date().toISOString().slice(0, 19) + "Z";
  return JSON.stringify(P);
}

// Telegram lines for trades an account opened or closed since the previous scan
function tradeNotes(before, now) {
  if (!before || !now || before.balance == null || before.version !== now.version) return [];
  const tag = `[${now.title || "Test account"}]`;
  const seen = new Set([...(before.open || []), ...(before.closed || [])].map((t) => t.id));
  const wasClosed = new Set((before.closed || []).map((t) => t.id));
  const out = [];
  for (const t of (now.open || []).filter((t) => !seen.has(t.id)).slice(0, 8)) {
    out.push(`${tag} OPENED ${t.side.toUpperCase()} ${t.symbol} (${t.name}) ${t.tf} @ ${fmt(t.entry)}\n  size ${fmt(t.size0)} USD, risk ${fmt(t.risk)} USD, stop ${fmt(t.stop)} (trailing)\n  rule: ${t.rule}`);
  }
  for (const t of (now.closed || []).filter((t) => !wasClosed.has(t.id)).slice(-8)) {
    out.push(`${tag} CLOSED ${t.symbol} ${t.tf} @ ${fmt(t.exit)}: ${t.pnl >= 0 ? "+" : ""}${t.pnl} USD (${t.r >= 0 ? "+" : ""}${t.r}R), ${t.reason}\n  balance ${fmt(t.balance)} USD`);
  }
  return out;
}

// ---- what to do with an open position ---------------------------------------
function evaluate(pos, frame, price, risk) {
  const side = pos.side === "long" ? 1 : -1;
  const best = side === 1 ? Math.max(pos.best, price) : Math.min(pos.best, price);
  const atr = frame?.atr || pos.atr;
  const trail = best - side * risk.stop_atr * atr;                      // trailing stop follows the best price
  const stop = side === 1 ? Math.max(pos.stop, trail) : Math.min(pos.stop, trail);
  const move = side * (price / pos.entry - 1);
  const liq = pos.leverage > 1 ? pos.entry * (1 - side / pos.leverage) : null;
  let code = "hold", text = "Hold: nothing has changed against you";
  if ((price - stop) * side <= 0) { code = "close"; text = "CLOSE: stop level hit"; }
  else if (frame && ((side === 1 && frame.label.includes("SELL")) || (side === -1 && frame.label.includes("BUY")))) {
    code = "close"; text = `CLOSE: signal flipped to ${frame.label}`;
  } else if ((price - pos.target) * side >= 0) { code = "profit"; text = "TAKE PROFIT: target reached (close, or keep it with the trailing stop)"; }
  else if (liq !== null && Math.abs(price - liq) < 1.5 * atr) { code = "warn"; text = "DANGER: price is close to liquidation, reduce or close"; }
  else if (frame && frame.score * side < 0) { code = "warn"; text = `Weakening: score turned against you (${frame.score}), consider reducing`; }
  return {
    price, best, stop, liq, code, text, move_pct: move * 100, lev_pct: move * pos.leverage * 100,
    money: pos.size ? pos.size * move : null,
  };
}

async function checkPositions(env, { save = true } = {}) {
  const positions = await getJSON(env, "positions", []);
  const open = positions.filter((p) => p.status === "open");
  const evals = {};
  if (!open.length) return { positions, evals };
  const meta = await getJSON(env, "meta", {});
  const watch = meta.watch || {}, risk = meta.risk || { stop_atr: 2, target_atr: 3 };
  const prices = await Promise.all(open.map(livePrice));
  let dirty = false;
  const alerts = [];
  open.forEach((p, i) => {
    const frame = watch[`${p.symbol}|${p.tf}`];
    const price = prices[i] ?? frame?.price ?? p.last ?? p.entry;
    const e = evaluate(p, frame, price, risk);
    e.stale = prices[i] == null;
    evals[p.id] = e;
    // KV allows 1,000 writes a day on the free plan, so only save meaningful changes
    if (Math.abs(e.best / p.best - 1) > 0.003) { p.best = e.best; dirty = true; }
    if (e.code !== p.last_code) {
      p.last_code = e.code; p.last = price; dirty = true;
      if (e.code !== "hold") {
        alerts.push(`${p.symbol} ${p.tf} ${p.side.toUpperCase()} ${p.leverage}x\n${e.text}\nprice ${fmt(price)}, result ${e.move_pct.toFixed(2)}% (${e.lev_pct.toFixed(1)}% on margin)`);
      }
    }
  });
  if (dirty && save) await putJSON(env, "positions", positions);
  for (const a of alerts) await telegram(env, "Position alert\n" + a);
  return { positions, evals };
}

// ---- scanner upload ----------------------------------------------------------
async function ingest(request, env) {
  const data = JSON.parse(await request.text());
  if (!Array.isArray(data.items)) return json({ error: "bad payload" }, 400);
  const paperIn = data.paper;
  delete data.paper;
  const text = JSON.stringify(data);
  const positions = await getJSON(env, "positions", []);
  const watch = { ...(data.watch || {}) };
  for (const p of positions.filter((x) => x.status === "open")) {
    const key = `${p.symbol}|${p.tf}`;
    if (!watch[key]) {
      const it = data.items.find((i) => i.symbol === p.symbol && i.tf === p.tf);
      if (it) watch[key] = { label: it.label, score: it.score, atr: it.atr, price: it.price, time: it.time };
    }
  }
  const writes = [env.KV.put("results", text),
    putJSON(env, "meta", { watch, risk: data.risk || { stop_atr: 2, target_atr: 3 } })];
  const notes = [];
  if (paperIn) {
    // ignore results from a scan that started before the test was reset
    const current = await getJSON(env, "paper", null);
    if (!current || !current.epoch || current.epoch === paperIn.epoch) {
      writes.push(putJSON(env, "paper", paperIn));
      // Telegram: what the bot itself just did in its test accounts
      if (current && !data.demo) {
        notes.push(...tradeNotes(current, paperIn));
        for (const [name, acc] of Object.entries(paperIn.accounts || {})) notes.push(...tradeNotes((current.accounts || {})[name], acc));
      }
    }
  }
  await Promise.all(writes);
  if (notes.length) await telegram(env, notes.join("\n\n"));
  await checkPositions(env);
  return json({ ok: true, items: data.items.length });
}

// ---- positions ----------------------------------------------------------------
async function openPosition(request, env) {
  const f = await request.json();
  const results = await getJSON(env, "results", { items: [] });
  const it = results.items.find((i) => i.symbol === f.symbol && i.tf === f.tf);
  if (!it || !["long", "short"].includes(f.side)) return json({ error: "Signal not found, refresh the page" }, 400);
  const num = (v, d) => { const n = parseFloat(String(v ?? "").replace(",", ".")); return Number.isFinite(n) && n > 0 ? n : d; };
  const side = f.side === "long" ? 1 : -1;
  const entry = num(f.entry, it.price);
  const risk = results.risk || { stop_atr: 2, target_atr: 3 };
  const pos = {
    id: crypto.randomUUID().slice(0, 8), symbol: it.symbol, name: it.name, kind: it.kind, tf: it.tf, side: f.side,
    entry, leverage: Math.max(1, num(f.leverage, 1)), size: num(f.size, null), atr: it.atr,
    stop: entry - side * risk.stop_atr * it.atr, target: entry + side * risk.target_atr * it.atr,
    best: entry, opened: new Date().toISOString().slice(0, 16).replace("T", " "), status: "open", last_code: "hold",
  };
  const [positions, meta] = await Promise.all([getJSON(env, "positions", []), getJSON(env, "meta", {})]);
  positions.push(pos);
  meta.watch = { ...(meta.watch || {}), [`${pos.symbol}|${pos.tf}`]: { label: it.label, score: it.score, atr: it.atr, price: it.price, time: it.time } };
  await Promise.all([putJSON(env, "positions", positions), putJSON(env, "meta", meta)]);
  return json({ ok: true });
}

async function closePosition(request, env) {
  const f = await request.json();
  const positions = await getJSON(env, "positions", []);
  const p = positions.find((x) => x.id === f.id && x.status === "open");
  if (!p) return json({ error: "Position not found" }, 400);
  const exit = parseFloat(String(f.exit ?? "").replace(",", "."));
  p.status = "closed";
  p.exit = Number.isFinite(exit) && exit > 0 ? exit : (await livePrice(p)) ?? p.entry;
  p.closed = new Date().toISOString().slice(0, 16).replace("T", " ");
  await putJSON(env, "positions", positions.slice(-200));
  return json({ ok: true });
}

// ---- start a scan on GitHub every 15 minutes ----------------------------------
async function dispatchScan(env) {
  if (!env.GH_DISPATCH_TOKEN || !env.GH_REPO) return;
  const r = await fetch(`https://api.github.com/repos/${env.GH_REPO}/actions/workflows/scan.yml/dispatches`, {
    method: "POST",
    headers: { authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`, accept: "application/vnd.github+json",
      "user-agent": "bot-crst-dashboard", "x-github-api-version": "2022-11-28" },
    body: JSON.stringify({ ref: "main" }),
  });
  if (!r.ok) console.log("scan dispatch failed", r.status, await r.text());
}

// ---- router --------------------------------------------------------------------
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    try {
      if (path === "/api/ingest" && request.method === "POST") {
        return bearerOk(request, env) ? ingest(request, env) : json({ error: "unauthorized" }, 401);
      }
      if (path === "/api/watch") {
        if (!bearerOk(request, env)) return json({ error: "unauthorized" }, 401);
        const positions = await getJSON(env, "positions", []);
        return json(positions.filter((p) => p.status === "open").map((p) => ({ symbol: p.symbol, kind: p.kind, tf: p.tf })));
      }
      if (path === "/api/paper" && request.method === "GET") {
        if (!bearerOk(request, env)) return json({ error: "unauthorized" }, 401);
        return json(await getJSON(env, "paper", null));
      }
      if (path === "/login" && request.method === "POST") {
        const form = await request.formData();
        if (env.DASH_PASSWORD && form.get("password") === env.DASH_PASSWORD) {
          return new Response(null, { status: 303, headers: {
            location: "/", "set-cookie": `sb=${await sessionToken(env)}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=2592000` } });
        }
        return html(LOGIN.replace("<!--msg-->", '<p class="err">Wrong password</p>'), 401);
      }
      if (!(await loggedIn(request, env))) {
        if (path.startsWith("/api/")) return json({ error: "login required" }, 401);
        return html(LOGIN.replace("<!--msg-->", env.DASH_PASSWORD ? "" : '<p class="err">DASH_PASSWORD is not set on the Worker yet.</p>'));
      }
      if (path === "/") return html(PAGE);
      if (path === "/api/state") {
        const [raw, paperRaw, state] = await Promise.all([env.KV.get("results"),
          env.KV.get("paper").then(livePaper), checkPositions(env, { save: false })]);
        return new Response(`{"results":${raw || "null"},"paper":${paperRaw},"positions":${JSON.stringify(state.positions)},"evals":${JSON.stringify(state.evals)}}`,
          { headers: { "content-type": "application/json", "cache-control": "no-store" } });
      }
      if (path === "/api/open" && request.method === "POST") return openPosition(request, env);
      if (path === "/api/close" && request.method === "POST") return closePosition(request, env);
      if (path === "/api/paper/reset" && request.method === "POST") {
        await putJSON(env, "paper", { epoch: crypto.randomUUID().slice(0, 8), reset: new Date().toISOString() });
        return json({ ok: true });
      }
      return new Response("Not found", { status: 404 });
    } catch (e) {
      return json({ error: e.message }, 500);
    }
  },

  // every minute: check open positions and alert on changes
  async scheduled(event, env, ctx) {
    const minute = new Date(event.scheduledTime).getUTCMinutes();
    if (minute % 15 === 0) ctx.waitUntil(dispatchScan(env));
    ctx.waitUntil(checkPositions(env));
  },
};
