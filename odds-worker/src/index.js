// Dashboard for the odds scanner. Runs on Cloudflare Workers.
//  - receives each scan from the GitHub Actions scanner (POST /api/ingest)
//  - hands the previous state back to the scanner (GET /api/state with the token)
//  - shows it on a password-protected page
// It only stores and shows data. It never places bets.
import { PAGE, LOGIN } from "./page.js";

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });
const html = (body, status = 200, extra = {}) =>
  new Response(body, { status, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", ...extra } });

async function sha256(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
const sessionToken = (env) => sha256("odds-session:" + (env.DASH_PASSWORD || ""));

async function loggedIn(request, env) {
  if (!env.DASH_PASSWORD) return false;
  const m = (request.headers.get("cookie") || "").match(/(?:^|;\s*)ob=([a-f0-9]+)/);
  return !!m && m[1] === (await sessionToken(env));
}
const bearerOk = (request, env) =>
  !!env.INGEST_TOKEN && request.headers.get("authorization") === "Bearer " + env.INGEST_TOKEN;

// The scanner gets the state plus any results entered by hand, so a scan running at the same moment
// as a click cannot lose them (it re-applies them on its next run).
const stateResponse = async (env) => {
  const state = JSON.parse((await env.KV.get("state")) || "null");
  if (state) state.manual = JSON.parse((await env.KV.get("manual")) || "{}");
  return json(state);
};

// Same rules as apply_manual and paper_summary in odds/engine.py.
function applyManual(paper, manual) {
  for (const b of paper.settled) {
    const res = manual[b.key];
    if (b.result !== "unscored" || !["win", "lose", "void"].includes(res)) continue;
    b.result = res;
    b.pnl = res === "win" ? Math.round(b.stake * (b.odds - 1) * 100) / 100 : res === "lose" ? -b.stake : 0;
    b.manual = true;
    paper.bankroll = Math.round((paper.bankroll + b.pnl) * 100) / 100;
    b.balance = paper.bankroll;
  }
}
function summary(paper) {
  const graded = paper.settled.filter((b) => !["void", "unscored"].includes(b.result));
  const every = [...paper.settled, ...paper.open], r4 = (x) => Math.round(x * 10000) / 10000, r2 = (x) => Math.round(x * 100) / 100;
  const staked = graded.reduce((t, b) => t + b.stake, 0), pnl = graded.reduce((t, b) => t + b.pnl, 0);
  return {
    bets: every.length, open: paper.open.length, won: graded.filter((b) => b.result === "win").length,
    lost: graded.filter((b) => b.result === "lose").length, staked: r2(staked), pnl: r2(pnl), roi: staked ? r4(pnl / staked) : null,
    expected: r2(graded.reduce((t, b) => t + b.stake * b.edge, 0)),
    avg_clv: every.length ? r4(every.reduce((t, b) => t + b.clv, 0) / every.length) : null,
    beat_close: every.length ? r4(every.filter((b) => b.clv > 0).length / every.length) : null,
  };
}
async function setResult(env, key, result) {
  if (!key || !["win", "lose", "void"].includes(result)) return json({ error: "bad request" }, 400);
  const manual = JSON.parse((await env.KV.get("manual")) || "{}");
  manual[key] = result;
  await env.KV.put("manual", JSON.stringify(manual));
  const state = JSON.parse((await env.KV.get("state")) || "null");
  if (state && state.paper) {
    applyManual(state.paper, manual);
    state.summary = summary(state.paper);
    await env.KV.put("state", JSON.stringify(state));
  }
  return json({ ok: true });
}

// Every hour Cloudflare's timer starts the "Odds scan" workflow on GitHub (GitHub's own schedule
// skips most runs on free accounts). Needs GH_DISPATCH_TOKEN: a GitHub token allowed to run Actions.
async function startScan(env) {
  const result = { at: new Date().toISOString() };
  if (!env.GH_DISPATCH_TOKEN) result.error = "GH_DISPATCH_TOKEN is not set";
  else {
    const r = await fetch(`https://api.github.com/repos/${env.GH_REPO || "fscign/bot-crst"}/actions/workflows/odds-scan.yml/dispatches`, {
      method: "POST",
      headers: { authorization: "Bearer " + env.GH_DISPATCH_TOKEN, accept: "application/vnd.github+json",
                 "user-agent": "bot-odds-worker", "content-type": "application/json" },
      body: JSON.stringify({ ref: "main" }),
    });
    if (r.status !== 204) result.error = "GitHub answered " + r.status + ": " + (await r.text()).slice(0, 200);
  }
  await env.KV.put("dispatch", JSON.stringify(result));
  return result;
}

const stateWithScheduler = async (env) => {
  const state = JSON.parse((await env.KV.get("state")) || "null");
  if (state) state.scheduler = JSON.parse((await env.KV.get("dispatch")) || "null");
  return json(state);
};

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(startScan(env));
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    try {
      if (path === "/api/ingest" && request.method === "POST") {
        if (!bearerOk(request, env)) return json({ error: "unauthorized" }, 401);
        const text = await request.text();
        const data = JSON.parse(text);
        if (!data || typeof data.board !== "object") return json({ error: "bad payload" }, 400);
        await env.KV.put("state", text);
        return json({ ok: true });
      }
      if (path === "/api/state" && bearerOk(request, env)) return stateResponse(env);
      if (path === "/login" && request.method === "POST") {
        const form = await request.formData();
        if (env.DASH_PASSWORD && form.get("password") === env.DASH_PASSWORD) {
          return new Response(null, { status: 303, headers: {
            location: "/", "set-cookie": `ob=${await sessionToken(env)}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=2592000` } });
        }
        return html(LOGIN.replace("<!--msg-->", '<p class="err">Wrong password</p>'), 401);
      }
      if (!(await loggedIn(request, env))) {
        if (path.startsWith("/api/")) return json({ error: "login required" }, 401);
        return html(LOGIN.replace("<!--msg-->", env.DASH_PASSWORD ? "" : '<p class="err">DASH_PASSWORD is not set on the Worker yet.</p>'));
      }
      if (path === "/") return html(PAGE);
      if (path === "/api/state") return stateWithScheduler(env);
      if (path === "/api/paper-result" && request.method === "POST") {
        const body = await request.json();
        return setResult(env, body.key, body.result);
      }
      if (path === "/api/start-scan" && request.method === "POST") return json(await startScan(env));
      return new Response("Not found", { status: 404 });
    } catch (e) {
      return json({ error: e.message }, 500);
    }
  },
};
