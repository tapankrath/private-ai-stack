/* Hand-off between Token Savings Coach and Right-Size Advisor.
 * The same file ships with both apps. Nothing is sent anywhere: the Coach makes a short code,
 * the person copies it, and the Advisor reads it when they paste it in.
 *
 * Code format: "TSC1." + base64url(JSON). JSON fields (all optional except monthly_usd):
 *   v              1
 *   monthly_usd    what they pay for AI now, per month (number > 0)
 *   items          [{ name, usd, kind: "subscription" | "api" | "other" }]   what makes up the bill
 *   users          people using AI
 *   org_type       "individual" | "household" | "small_business" | "smb"
 *   jobs           Advisor job ids (general_qa, writing, coding, knowledge_recall, ...)
 *   regulated      ["health", "financial", "legal", "children", "client_confidential"]
 *   queries_per_user_per_day   number
 *   agents         number of always-running agents or automations
 *   platforms      names of the AI services they use (for display only)
 */
(function (root) {
  "use strict";
  const PREFIX = "TSC1.";
  const ORG = ["individual", "household", "small_business", "smb"];
  const JOBS = ["general_qa", "writing", "research", "drafting", "knowledge_recall", "conversation_recall", "coding", "data_analysis",
    "scanned_docs", "image_understanding", "transcription", "translation", "agent_tasks"];
  const REG = ["health", "financial", "legal", "children", "client_confidential"];
  const KINDS = ["subscription", "api", "other"];

  const b64e = (s) => btoa(unescape(encodeURIComponent(s))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const b64d = (s) => { s = s.replace(/-/g, "+").replace(/_/g, "/"); while (s.length % 4) s += "="; return decodeURIComponent(escape(atob(s))); };
  const num = (v, min, max) => { const n = Number(v); return Number.isFinite(n) && n >= min && n <= max ? n : undefined; };
  const str = (v, n) => (typeof v === "string" && v.trim() ? v.trim().slice(0, n) : undefined);
  const pickList = (v, allowed) => (Array.isArray(v) ? Array.from(new Set(v.filter((x) => allowed.includes(x)))) : undefined);

  // Keep only known fields with sane values, so a pasted code can never inject anything odd.
  function clean(d) {
    if (!d || typeof d !== "object") throw new Error("not a hand-off");
    const out = { v: 1 };
    out.monthly_usd = num(d.monthly_usd, 0.01, 10000000);
    if (out.monthly_usd === undefined) throw new Error("the code has no monthly AI cost");
    if (Array.isArray(d.items)) {
      const items = d.items.slice(0, 30).map((i) => ({ name: str(i && i.name, 60), usd: num(i && i.usd, 0, 10000000), kind: KINDS.includes(i && i.kind) ? i.kind : "other" }))
        .filter((i) => i.name && i.usd !== undefined);
      if (items.length) out.items = items;
    }
    const users = num(d.users, 1, 5000); if (users !== undefined) out.users = Math.round(users);
    if (ORG.includes(d.org_type)) out.org_type = d.org_type;
    const jobs = pickList(d.jobs, JOBS); if (jobs && jobs.length) out.jobs = jobs;
    const reg = pickList(d.regulated, REG); if (reg && reg.length) out.regulated = reg;
    const q = num(d.queries_per_user_per_day, 1, 2000); if (q !== undefined) out.queries_per_user_per_day = Math.round(q);
    const a = num(d.agents, 0, 1000); if (a !== undefined) out.agents = Math.round(a);
    if (Array.isArray(d.platforms)) { const p = d.platforms.map((x) => str(x, 40)).filter(Boolean).slice(0, 20); if (p.length) out.platforms = p; }
    return out;
  }

  function encode(d) { return PREFIX + b64e(JSON.stringify(clean(d))); }

  // Accepts the bare code or a longer pasted message that contains it.
  function decode(text) {
    const m = String(text || "").match(/TSC1\.([A-Za-z0-9_-]+)/);
    if (!m) throw new Error("no Token Savings Coach code found");
    let d;
    try { d = JSON.parse(b64d(m[1])); } catch (e) { throw new Error("the code looks cut off or changed"); }
    return clean(d);
  }

  // Should the Coach suggest looking at local? Returns null or a reason string.
  function localWorthALook(d) {
    const c = clean(d);
    if (c.regulated && c.regulated.length) return "private";
    if (c.agents) return "agents";
    if (c.monthly_usd >= 100) return "spend";
    return null;
  }

  const api = { PREFIX, encode, decode, clean, localWorthALook };
  if (typeof module === "object" && module.exports) module.exports = api;
  root.TSCHandoff = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
