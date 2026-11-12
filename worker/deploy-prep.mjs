// Runs in the deploy job: finds (or creates) the KV storage and writes its id
// and your domain into wrangler.toml before `wrangler deploy`.
import { readFileSync, writeFileSync } from "node:fs";

const { CLOUDFLARE_API_TOKEN: token, CLOUDFLARE_ACCOUNT_ID: account, DASH_DOMAIN: domain } = process.env;
if (!token || !account) {
  console.error("Missing secrets: add CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID in the repo settings (see README).");
  process.exit(1);
}
const TITLE = "bot-crst-data";
const base = `https://api.cloudflare.com/client/v4/accounts/${account}/storage/kv/namespaces`;
const headers = { authorization: `Bearer ${token}`, "content-type": "application/json" };
const call = async (url, init = {}) => {
  const r = await fetch(url, { headers, ...init });
  const j = await r.json();
  if (!j.success) throw new Error(`Cloudflare API: ${JSON.stringify(j.errors)}`);
  return j.result;
};

let ns = (await call(`${base}?per_page=100`)).find((n) => n.title === TITLE);
if (!ns) {
  ns = await call(base, { method: "POST", body: JSON.stringify({ title: TITLE }) });
  console.log("Created KV storage", TITLE);
}
let toml = readFileSync("wrangler.toml", "utf8").replace("KV_ID_PLACEHOLDER", ns.id);
toml = toml.replace(/^#ROUTES#.*$/m, domain ? `routes = [{ pattern = "${domain}", custom_domain = true }]` : "");
writeFileSync("wrangler.toml", toml);
console.log(domain ? `Dashboard domain: ${domain}` : "No DASH_DOMAIN set: using the workers.dev address only");
