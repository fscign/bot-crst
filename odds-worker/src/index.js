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

const stateResponse = async (env) =>
  new Response((await env.KV.get("state")) || "null", { headers: { "content-type": "application/json", "cache-control": "no-store" } });

export default {
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
      if (path === "/api/state") return stateResponse(env);
      return new Response("Not found", { status: 404 });
    } catch (e) {
      return json({ error: e.message }, 500);
    }
  },
};
