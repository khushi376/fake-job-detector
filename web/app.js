"use strict";
const API = "http://localhost:8000";
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const fmtDate = ts => { if (!ts) return ""; const d = new Date(/Z$|[+-]\d\d:\d\d$/.test(ts) ? ts : ts + "Z"); return isNaN(d) ? "" : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }); };
const clsOf = v => ({ Low: "low", Medium: "mid", High: "high" }[v] || "mid");
const cleanReason = r => String(r).replace(/\s*\(\+[\d.]+ risk\)/, "");
const catColor = r => /fee|payment|pay /i.test(r) ? "var(--fee)" : /aadhaar|bank|personal|financial|otp/i.test(r) ? "var(--pii)" : /whatsapp|telegram|email|contact/i.test(r) ? "var(--contact)" : /urgen|capital|exclamation|seats/i.test(r) ? "var(--urgent)" : "var(--line)";

const state = { token: null, email: null, role: null, authTab: "login", mode: "text", draft: "", result: null, text: "", shown: "", fbMsg: "", qStatus: "pending", poll: null };

const RULES = [
  ["fee", /registration fee|security deposit|processing fee|training fee|pay\s|payment|send money|advance fee|kit charges|refundable/],
  ["pii", /aadhaar|aadhar|bank details?|account number|\botp\b|passport copy|\bssn\b|credit card|password/],
  ["contact", /whatsapp|telegram|wa\.me|[\w.+-]+@(?:gmail|yahoo|hotmail|outlook)\.com/],
  ["urgent", /urgent|immediate(?:ly)?|limited seats|apply now|hurry|last date|act fast|today only|asap/]
];
const COMBINED = new RegExp(RULES.map(r => "(" + r[1].source + ")").join("|"), "gi");
const KEY = [["var(--fee)", "asks for money"], ["var(--pii)", "asks for ID or bank details"], ["var(--contact)", "contact outside the company"], ["var(--urgent)", "pressure to act fast"]];
const chips = () => `<div class="chips">${KEY.map(k => `<span class="chip" style="--sw:${k[0]}">${k[1]}</span>`).join("")}</div>`;
const SAY = {
  high: ["This looks like a scam.", "Don't pay anything or send documents until you've checked the company yourself."],
  mid: ["Be careful with this one.", "Some details look off. Check the company on its official website before you reply."],
  low: ["Nothing alarming stood out.", "That isn't a guarantee. Still check the company on its official website before sharing personal details."]
};
const SAMPLE = "URGENT hiring! Work from home, no experience needed. Earn Rs 40,000 a month. Pay a Rs 1,500 registration fee and send your Aadhaar and bank details on WhatsApp today. Limited seats, apply now!";

function highlight(t) {
  return esc(String(t || "").slice(0, 6000)).replace(COMBINED, (m, ...g) => {
    const i = g.slice(0, RULES.length).findIndex(x => x !== undefined);
    return `<mark class="${RULES[i][0]}">${m}</mark>`;
  });
}
const ring = (s, c, size) => `<svg class="ring ${c}" width="${size}" height="${size}" viewBox="0 0 120 120" style="--s:${s}" role="img" aria-label="Risk score ${Math.round(s)} out of 100"><circle class="trk" cx="60" cy="60" r="50" pathLength="100"/><circle class="val" cx="60" cy="60" r="50" pathLength="100" transform="rotate(-90 60 60)"/><text x="60" y="62" text-anchor="middle" dominant-baseline="central">${Math.round(s)}</text></svg>`;
const sampleHtml = () => `<div class="stage" aria-label="Example of a checked offer">
  <p class="from">A message like the ones people forward on WhatsApp</p>
  <div class="bubble">${highlight(SAMPLE)}</div>
  ${chips()}
  <div class="float high">${ring(94, "high", 76)}<div><strong>Likely scam</strong><span>Example result</span></div></div>
</div>`;

function decodeRole(t) {
  try { let p = t.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"); p += "=".repeat((4 - p.length % 4) % 4); return JSON.parse(atob(p)).role || "user"; }
  catch (e) { return "user"; }
}
function setSession(token, email) {
  state.token = token; state.email = email; state.role = decodeRole(token);
  try { sessionStorage.setItem("rf_token", token); sessionStorage.setItem("rf_email", email || ""); } catch (e) {}
}
function clearSession(msg) {
  state.token = state.email = state.role = null; state.result = null; state.fbMsg = "";
  try { sessionStorage.removeItem("rf_token"); sessionStorage.removeItem("rf_email"); } catch (e) {}
  if (msg) toast(msg);
  render();
}
try { const t = sessionStorage.getItem("rf_token"); if (t) setSession(t, sessionStorage.getItem("rf_email")); } catch (e) {}

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 3800);
}

async function api(path, { method = "GET", json, form, auth = true, params } = {}) {
  const url = new URL(API + path);
  if (params) Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== "") url.searchParams.set(k, v); });
  const headers = {}; let body;
  if (json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
  if (form) body = new URLSearchParams(form);
  if (auth && state.token) headers["Authorization"] = "Bearer " + state.token;
  let res;
  try { res = await fetch(url, { method, headers, body }); }
  catch (e) { throw new Error("Can't reach the backend. Check that uvicorn is running on port 8000."); }
  let data = null; try { data = await res.json(); } catch (e) {}
  if (res.status === 401 && auth && state.token) { clearSession("Your session ended. Sign in again."); throw new Error("Session ended"); }
  if (!res.ok) {
    let d = data && data.detail;
    if (Array.isArray(d)) d = d.map(x => x.msg || JSON.stringify(x)).join("; ");
    throw new Error(d || "Request failed (" + res.status + ")");
  }
  return data;
}

function currentRoute() {
  let r = (location.hash || "").slice(1);
  if (!["check", "feed", "admin"].includes(r)) r = "check";
  if (r === "check" && !state.token) return "signin";
  if (r === "admin" && state.role !== "admin") return state.token ? "check" : "signin";
  return r;
}

function render() {
  clearTimeout(state.poll);
  const initial = (state.email || "?").charAt(0).toUpperCase();
  $("#who").innerHTML = state.token ? `<span class="avatar" title="${esc(state.email)}">${esc(initial)}</span><button class="btn small" id="logout">Sign out</button>` : "";
  const r = currentRoute();
  const tabs = [["check", "Check an offer"], ["feed", "Flagged offers"]];
  if (state.role === "admin") tabs.push(["admin", "Back office"]);
  $("#nav").innerHTML = tabs.map(([id, l]) => `<a href="#${id}" class="${(r === id || (r === "signin" && id === "check")) ? "on" : ""}">${l}</a>`).join("");
  const lo = $("#logout"); if (lo) lo.onclick = () => { clearSession(); location.hash = "#check"; };
  ({ signin: viewSignin, check: viewCheck, feed: viewFeed, admin: viewAdmin })[r]();
}
window.addEventListener("hashchange", render);

function viewSignin() {
  const reg = state.authTab === "register";
  $("#view").innerHTML = `<section class="hero">
    <div>
      <h1 class="hero-title">Is that job offer real?</h1>
      <p class="lead">Paste the posting, email or WhatsApp message. Redflag marks what looks like a scam and tells you why.</p>
      <div class="panel auth">
        <div class="seg" id="authpick"><button data-t="login" class="${reg ? "" : "on"}">Sign in</button><button data-t="register" class="${reg ? "on" : ""}">Create account</button></div>
        <form id="authform">
          <label for="em">Email</label>
          <input id="em" type="email" required autocomplete="username">
          <label for="pw">Password${reg ? " (at least 8 characters)" : ""}</label>
          <input id="pw" type="password" required minlength="${reg ? 8 : 1}" autocomplete="${reg ? "new-password" : "current-password"}">
          <p class="err" id="autherr" role="alert"></p>
          <button class="btn solid wide" type="submit" style="margin-top:6px">${reg ? "Create account" : "Sign in"}</button>
        </form>
      </div>
      <p class="hint">You need an account so moderators can review the rulings you submit.</p>
    </div>
    ${sampleHtml()}
  </section>`;
  $("#authpick").onclick = e => { const t = e.target.dataset.t; if (t) { state.authTab = t; viewSignin(); } };
  $("#authform").onsubmit = async e => {
    e.preventDefault();
    const email = $("#em").value.trim(), password = $("#pw").value, err = $("#autherr"), btn = e.target.querySelector("button[type=submit]");
    err.textContent = ""; btn.disabled = true;
    try {
      if (reg) await api("/auth/register", { method: "POST", json: { email, password }, auth: false });
      const d = await api("/auth/login", { method: "POST", form: { username: email, password }, auth: false });
      setSession(d.access_token, email.toLowerCase());
      location.hash = "#check"; render();
    } catch (ex) { err.textContent = ex.message; btn.disabled = false; }
  };
}

function viewCheck() {
  const link = state.mode === "link";
  $("#view").innerHTML = `<section class="split">
    <div class="composer">
      <h2>Check an offer</h2>
      <p class="sub">Paste a posting, email or message, or give a link to the posting.</p>
      <div class="seg" id="modepick"><button data-m="text" class="${link ? "" : "on"}">Paste the text</button><button data-m="link" class="${link ? "on" : ""}">Use a link</button></div>
      ${link
        ? `<label for="inp">Link to the posting</label><input id="inp" type="url" placeholder="https://company.com/careers/job-123" value="${esc(state.draft)}"><p class="hint">LinkedIn and Naukri often block automated reading. If a link fails, paste the text instead.</p>`
        : `<label for="inp">Offer text</label><textarea id="inp" placeholder="Paste the job offer here…">${esc(state.draft)}</textarea>`}
      <button class="btn solid wide" id="go">Check this offer</button>
      <p class="err" id="cerr" role="alert"></p>
      <p class="hint">Offers that score 60 or higher are added to the public feed so others can spot the same scam. Remove names and phone numbers first.</p>
    </div>
    <div id="result"></div>
  </section>`;
  $("#modepick").onclick = e => { const m = e.target.dataset.m; if (m) { state.draft = $("#inp").value; state.mode = m; viewCheck(); } };
  $("#go").onclick = analyze;
  if (state.result) renderResult(); else renderEmpty();
}

function renderEmpty() {
  const box = $("#result"); if (!box) return;
  box.innerHTML = `<div class="report empty"><h3>Your result appears here</h3><p>Paste an offer you received. Redflag highlights every phrase that looks like a scam and gives you a risk score from 0 to 100.</p>${chips()}</div>`;
}

async function analyze() {
  const raw = $("#inp").value, val = raw.trim(), err = $("#cerr"), btn = $("#go");
  state.draft = raw; err.textContent = "";
  if (!val) { err.textContent = state.mode === "link" ? "Enter a link to the posting." : "Paste the job offer first."; return; }
  btn.disabled = true; btn.textContent = "Checking…";
  try {
    const d = state.mode === "link"
      ? await api("/predict/url", { method: "POST", json: { url: val } })
      : await api("/predict", { method: "POST", json: { text: val } });
    state.result = d; state.fbMsg = "";
    state.shown = state.mode === "link" ? (d.text_preview || "") : val;
    state.text = state.mode === "link" ? (d.source_url + "\n" + (d.text_preview || "")) : val;
    renderResult();
  } catch (ex) { err.textContent = ex.message; }
  finally { btn.disabled = false; btn.textContent = "Check this offer"; }
}

function renderResult() {
  const r = state.result, box = $("#result"); if (!box) return;
  const s = Math.max(0, Math.min(100, Number(r.risk_score) || 0)), c = clsOf(r.verdict), say = SAY[c];
  box.innerHTML = `<div class="report ${c}" aria-live="polite">
    <div class="head">${ring(s, c, 132)}<div class="say"><h3>${say[0]}</h3><p>${say[1]}</p></div></div>
    <h4>The offer, marked up</h4>
    <div class="bubble sm">${highlight(state.shown)}</div>
    ${chips()}
    <h4>What we found</h4>
    <ul class="finds">${(r.reasons || []).map(x => `<li style="--d:${catColor(x)}">${esc(cleanReason(x))}</li>`).join("")}</ul>
    <h4>Was this right?</h4>
    <div class="row"><button class="btn coral" data-l="scam">This is a scam</button><button class="btn green" data-l="genuine">This is genuine</button></div>
    <p class="hint" id="fb">${esc(state.fbMsg)}</p>
  </div>`;
  box.querySelectorAll("[data-l]").forEach(b => b.onclick = () => sendFeedback(b.dataset.l));
}

async function sendFeedback(label) {
  try {
    await api("/feedback", { method: "POST", json: { posting_text: state.text, predicted_score: Number(state.result.risk_score), user_label: label } });
    state.fbMsg = "Thanks. A moderator will check your ruling before it is used to retrain the model.";
  } catch (ex) { state.fbMsg = "Couldn't save your ruling: " + ex.message; }
  const el = $("#fb"); if (el) el.textContent = state.fbMsg;
}

const feedItem = p => {
  const c = clsOf(p.verdict), t = p.text || "";
  return `<article class="offer ${c}">
    <div class="offer-top"><span class="score">${Math.round(p.risk_score)}</span><strong>${esc(p.verdict)} risk</strong><span class="when">${esc(fmtDate(p.timestamp))}</span></div>
    <div class="bubble sm">${esc(t.slice(0, 280))}${t.length > 280 ? "…" : ""}</div>
    <details><summary>Why it was flagged</summary><ul class="finds">${(p.reasons || []).map(r => `<li style="--d:${catColor(r)}">${esc(cleanReason(r))}</li>`).join("")}</ul></details>
  </article>`;
};

function viewFeed() {
  $("#view").innerHTML = `<section style="padding-top:12px">
    <h2 class="page-title">Flagged offers</h2>
    <p class="lead" style="margin-bottom:8px">Offers that scored 60 or higher. Search for a phrase to see whether someone has already reported the same scam.</p>
    <div class="row"><input id="q" type="search" placeholder="Try “registration fee” or “WhatsApp”" aria-label="Search flagged offers"><select id="lim" class="narrow" aria-label="How many to show"><option value="10">10 offers</option><option value="20" selected>20 offers</option><option value="50">50 offers</option></select></div>
    <div id="list"></div>
  </section>`;
  const load = async () => {
    const list = $("#list"); if (!list) return;
    try {
      const items = await api("/postings", { auth: false, params: { q: $("#q").value.trim(), limit: $("#lim").value, offset: 0 } });
      list.innerHTML = items.length ? `<div class="offers">${items.map(feedItem).join("")}</div>` : `<p class="hint" style="margin-top:24px">No flagged offers match yet. If an offer you check scores 60 or higher, it shows up here.</p>`;
    } catch (ex) { list.innerHTML = `<p class="err">${esc(ex.message)}</p>`; }
  };
  $("#q").oninput = debounce(load, 300); $("#lim").onchange = load; load();
}

function bars(o, colors = {}) {
  const e = Object.entries(o || {});
  if (!e.length) return `<p class="hint">Nothing yet.</p>`;
  const max = Math.max(...e.map(x => x[1]), 1);
  return `<div class="bars">${e.map(([k, v]) => `<div class="bar"><span>${esc(k)}</span><div class="bt"><i style="width:${(v / max * 100).toFixed(1)}%;background:${colors[k] || "var(--ink)"}"></i></div><span class="bv">${v}</span></div>`).join("")}</div>`;
}

function lineChart(vs) {
  const pts = vs.filter(v => v.metrics);
  if (!pts.length) return "";
  const W = 560, H = 210, L = 48, R = 20, T = 20, B = 40;
  const S = [["f1", "var(--coral)"], ["pr_auc", "var(--ink)"]];
  const all = pts.flatMap(v => S.map(s => v.metrics[s[0]]).filter(x => typeof x === "number"));
  let lo = Math.min(...all), hi = Math.max(...all);
  const pad = Math.max((hi - lo) * 0.5, 0.01); lo -= pad; hi += pad;
  const x = i => pts.length === 1 ? (L + W - R) / 2 : L + i * (W - L - R) / (pts.length - 1);
  const y = v => T + (hi - v) / (hi - lo) * (H - T - B);
  const grid = [lo, (lo + hi) / 2, hi].map(g => `<line x1="${L}" x2="${W - R}" y1="${y(g)}" y2="${y(g)}" style="stroke:var(--line)"/><text x="${L - 6}" y="${y(g) + 4}" text-anchor="end" font-size="12" style="fill:var(--muted)">${g.toFixed(3)}</text>`).join("");
  const lines = S.map(([k, col]) => {
    const p = pts.map((v, i) => typeof v.metrics[k] === "number" ? [x(i), y(v.metrics[k])] : null).filter(Boolean);
    return `<polyline fill="none" stroke-width="3" stroke-linejoin="round" style="stroke:${col}" points="${p.map(q => q.join(",")).join(" ")}"/>` + p.map(q => `<circle cx="${q[0]}" cy="${q[1]}" r="4.5" style="fill:${col}"/>`).join("");
  }).join("");
  const labels = pts.map((v, i) => `<text x="${x(i)}" y="${H - 14}" text-anchor="middle" font-size="13" style="fill:var(--ink)">v${v.version}</text>`).join("");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="F1 and PR-AUC for each model version">${grid}${lines}${labels}</svg>
    <div class="chips"><span class="chip" style="--sw:var(--coral)">F1</span><span class="chip" style="--sw:var(--ink)">PR-AUC</span></div>`;
}

const queueItem = it => {
  const c = it.predicted_score >= 60 ? "high" : it.predicted_score >= 30 ? "mid" : "low";
  return `<article class="q ${c}">
    <div class="offer-top"><span class="score">${Math.round(it.predicted_score)}</span><div><strong>${esc(it.user_email || "Unknown user")} says ${esc(it.user_label)}</strong>${it.disputed ? `<span class="flag">disagrees with the model</span>` : ""}</div><span class="when">${esc(it.status)}</span></div>
    <div class="bubble sm">${esc((it.posting_text || "").slice(0, 320))}</div>
    <div class="row"><button class="btn green small" data-act="approved" data-id="${it.id}">Approve</button><button class="btn coral small" data-act="rejected" data-id="${it.id}">Reject</button></div>
  </article>`;
};

async function review(id, status) {
  try { await api(`/admin/feedback/${id}/review`, { method: "POST", json: { status } }); toast("Ruling " + status + "."); }
  catch (ex) { toast(ex.message); }
  viewAdmin(true);
}
async function startRetrain() {
  const n = parseInt($("#minfb").value, 10) || 5;
  try { await api("/admin/retrain", { method: "POST", params: { min_feedback: n } }); toast("Retraining started."); }
  catch (ex) { toast(ex.message); }
  viewAdmin(true);
}

async function viewAdmin(silent) {
  clearTimeout(state.poll);
  if (!silent) $("#view").innerHTML = `<p class="hint">Opening the back office…</p>`;
  let stats, models, border, queue;
  try {
    [stats, models, border, queue] = await Promise.all([
      api("/admin/stats"), api("/admin/models"), api("/admin/borderline"),
      api("/admin/feedback", { params: { status: state.qStatus, limit: 50 } })
    ]);
  } catch (ex) { if (currentRoute() === "admin") $("#view").innerHTML = `<p class="err">${esc(ex.message)}</p>`; return; }
  if (currentRoute() !== "admin") return;
  const vs = models.versions || [], rt = models.retrain || {}, active = models.active_version, fs = stats.feedback_status || {};
  const top = stats.top_patterns || [];
  $("#view").innerHTML = `
    <h2 class="page-title" style="padding-top:12px">Back office</h2>
    <div class="stats">
      <div class="stat"><b>${stats.users}</b><span>people signed up</span></div>
      <div class="stat"><b>${stats.predictions}</b><span>offers checked</span></div>
      <div class="stat"><b>${fs.pending || 0}</b><span>rulings waiting</span></div>
      <div class="stat"><b>v${active ?? "?"}</b><span>model in use</span></div>
    </div>
    <div class="cols">
      <section class="panel plain">
        <h3>Rulings waiting for review</h3>
        <p class="hint">Only approved rulings are used when the model is retrained.</p>
        <div class="seg" id="qpick" style="margin-top:14px">${["pending", "approved", "rejected", "all"].map(s => `<button data-s="${s}" class="${state.qStatus === s ? "on" : ""}">${s[0].toUpperCase() + s.slice(1)}</button>`).join("")}</div>
        <div id="queue">${queue.length ? queue.map(queueItem).join("") : `<p class="hint" style="margin-top:16px">Nothing here right now.</p>`}</div>
      </section>
      <div class="stack">
        <section class="panel plain">
          <h3>Model versions</h3>
          <div class="row">
            <label class="inline" for="minfb">Needs at least <input id="minfb" class="num" type="number" min="1" max="1000" value="5"> approved rulings</label>
            <button class="btn solid" id="retrain" ${rt.running ? "disabled" : ""}>${rt.running ? "Retraining…" : "Retrain now"}</button>
          </div>
          ${rt.running ? `<p class="hint">Retraining takes a minute or two. This page updates when it finishes.</p>` : ""}
          ${rt.output ? `<p class="hint">Last run finished ${esc(rt.finished_at || "")} (exit code ${esc(rt.exit_code)}).</p><pre class="out">${esc(rt.output)}</pre>` : ""}
          <div class="scroll" style="margin-top:18px"><table class="ledger"><thead><tr><th>Version</th><th>In use</th><th>Rulings used</th><th>Precision</th><th>Recall</th><th>F1</th><th>PR-AUC</th><th>Went live</th></tr></thead><tbody>
            ${vs.map(v => { const m = v.metrics || {}; return `<tr><td>v${v.version}</td><td>${v.version === active ? "yes" : ""}</td><td>${v.trained_on_feedback ?? 0}</td><td>${m.precision ?? ""}</td><td>${m.recall ?? ""}</td><td>${m.f1 ?? ""}</td><td>${m.pr_auc ?? ""}</td><td>${v.promoted ? "yes" : "no"}</td></tr>`; }).join("")}
          </tbody></table></div>
          ${lineChart(vs)}
        </section>
        <section class="panel plain">
          <h3>What offers get flagged for</h3>
          ${top.length ? `<ul class="plain">${top.map(p => `<li><strong>${p.count}</strong> ${esc(p.pattern)}</li>`).join("")}</ul>` : `<p class="hint">Nothing yet.</p>`}
          <h4>Offers by verdict</h4>
          ${bars(stats.verdicts, { Low: "var(--low)", Medium: "var(--mid)", High: "var(--high)" })}
          <h4>Rulings by status</h4>
          ${bars(stats.feedback_status, { pending: "var(--muted)", approved: "var(--low)", rejected: "var(--high)" })}
        </section>
      </div>
    </div>
    <section class="panel plain">
      <h3>Close calls</h3>
      <p class="hint">Offers that scored between 40 and 60. A person should look at these.</p>
      ${border.length ? border.map(b => `<article class="q mid"><div class="offer-top"><span class="score">${Math.round(b.risk_score)}</span><span class="when">${esc(fmtDate(b.timestamp))}</span></div><div class="bubble sm">${esc((b.text || "").slice(0, 300))}</div></article>`).join("") : `<p class="hint" style="margin-top:14px">No close calls yet.</p>`}
    </section>`;
  $("#qpick").onclick = e => { const s = e.target.dataset.s; if (s) { state.qStatus = s; viewAdmin(true); } };
  $("#queue").onclick = e => { const b = e.target.closest("[data-act]"); if (b) review(b.dataset.id, b.dataset.act); };
  $("#retrain").onclick = startRetrain;
  if (rt.running) state.poll = setTimeout(() => { if (currentRoute() === "admin") viewAdmin(true); }, 5000);
}

render();
