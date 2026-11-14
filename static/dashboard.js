/* Distillation Watchdog investigator dashboard. Vanilla JS (ES2020), no build step.
 * It uses only the endpoints in docs/API.md section 4.
 * It shows pseudonyms only. It never shows a persona label or a data source. */
"use strict";

const STATE_COLORS = {
  normal: "#1b7f2a", review: "#f2a900", block: "#c62828", disclosed: "#1557b0", insufficient: "#6b6b6b",
};
const STATES = ["normal", "review", "block", "disclosed", "insufficient"];
const POLL_ACCOUNTS_MS = 2000;
const POLL_SUMMARY_MS = 2000;
const POLL_ACCOUNT_MS = 3000;
const POLL_CAMPAIGNS_MS = 4000;
const FEED_MAX = 60;

const $ = (id) => document.getElementById(id);
const S = {
  tab: "live",
  online: null,
  lastSseAt: 0,
  accountId: null,
  campaignId: null,
  pseudo: {},          // account id -> pseudonym (from API rows)
  rateChart: null,
  contribChart: null,
  cy: null,
  graphSig: "",
  evSig: "",
  evalLoaded: false,
  es: null,
};

// ---------- helpers ----------
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtNum(v) {
  if (typeof v !== "number" || !isFinite(v)) return String(v);
  if (Number.isInteger(v)) return v.toLocaleString("en-US");
  const a = Math.abs(v);
  if (a !== 0 && (a < 0.001 || a >= 1e6)) return v.toExponential(2);
  return v.toLocaleString("en-US", { maximumFractionDigits: a < 1 ? 4 : 2 });
}
function fmtTime(t) {
  if (typeof t !== "number") return "";
  const d = new Date(t * 1000);
  return isNaN(d) ? "" : d.toISOString().slice(11, 19);
}
function fmtDateTime(t) {
  if (typeof t !== "number") return "";
  const d = new Date(t * 1000);
  return isNaN(d) ? "" : d.toISOString().replace("T", " ").slice(0, 19) + " (sim)";
}
function badge(state) {
  const s = STATES.includes(state) ? state : "insufficient";
  return `<span class="badge st-${s}">${esc(state || "?")}</span>`;
}
function youMark(you) { return you ? '<span class="you" title="This is your agent-chat account">YOU</span>' : ""; }
function nameOf(id) { return S.pseudo[id] || "(account)"; }

function setOnline(ok) {
  if (S.online === ok) return;
  S.online = ok;
  $("offline").hidden = ok;
  const c = $("conn");
  c.className = "conn " + (ok ? "conn-ok" : "conn-off");
  c.textContent = ok ? "online" : "offline";
}

async function api(path, opts = {}) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), opts.timeout || 8000);
  try {
    const r = await fetch(path, { method: opts.method || "GET", signal: ctl.signal, headers: { Accept: "application/json" } });
    setOnline(true);
    if (!r.ok) {
      const err = new Error(`HTTP ${r.status} for ${path}`);
      err.status = r.status;
      throw err;
    }
    return await r.json();
  } catch (e) {
    if (e.status === undefined) setOnline(false);
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

function every(ms, fn) {
  const run = async () => {
    try { await fn(); } catch (e) { console.warn("poll:", e && e.message); /* the banner shows offline; try again later */ }
    setTimeout(run, ms);
  };
  run();
}

// ---------- tabs ----------
function showTab(name) {
  S.tab = name;
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + name));
  if (name === "campaigns") {
    refreshCampaigns().catch((e) => console.warn("campaigns:", e));
    if (S.cy) { S.cy.resize(); S.cy.fit(undefined, 30); }
  }
  if (name === "account" && S.accountId) refreshAccount().catch(() => {});
  if (name === "eval" && !S.evalLoaded) loadEval();
  const sel = name === "account" ? S.accountId : name === "campaigns" ? S.campaignId : null;
  const hash = "#" + name + (sel ? "/" + encodeURIComponent(sel) : "");
  if (location.hash !== hash) history.replaceState(null, "", hash);
}

// Deep links: #live, #account/<id>, #campaigns/<id>, #eval, #evidence/<id>.
function applyHash() {
  const evm = location.hash.match(/^#evidence\/(.+)$/);
  if (evm) { openEvidence(decodeURIComponent(evm[1])); return; }
  const m = location.hash.match(/^#(live|account|campaigns|eval)(?:\/(.+))?$/);
  if (!m) return;
  const id = m[2] ? decodeURIComponent(m[2]) : null;
  if (m[1] === "account" && id) openAccount(id);
  else if (m[1] === "campaigns" && id) openCampaign(id);
  else showTab(m[1]);
}

// ---------- summary and live chart ----------
function applySummary(s) {
  if (!s || typeof s !== "object") return;
  $("c-rps").textContent = fmtNum(Math.round((s.rps || 0) * 10) / 10);
  $("c-served").textContent = fmtNum(s.served ?? 0);
  $("c-blocked").textContent = fmtNum(s.blocked ?? 0);
  const acc = s.accounts || {};
  for (const st of STATES) $("c-" + st).textContent = fmtNum(acc[st] ?? 0);
  $("c-campaigns").textContent = fmtNum(s.campaigns ?? 0);
  $("simtime").textContent = fmtDateTime(s.sim_time);
  const hist = Array.isArray(s.rate_history) ? s.rate_history : [];
  if (S.rateChart) {
    S.rateChart.data.labels = hist.map((h) => fmtTime(h.t));
    S.rateChart.data.datasets[0].data = hist.map((h) => h.served ?? 0);
    S.rateChart.data.datasets[1].data = hist.map((h) => h.blocked ?? 0);
    S.rateChart.update("none");
  }
}

function initRateChart() {
  if (typeof Chart === "undefined") return;
  Chart.defaults.font.size = 15;
  Chart.defaults.color = "#111";
  S.rateChart = new Chart($("rate-chart"), {
    type: "line",
    data: { labels: [], datasets: [
      { label: "served", data: [], borderColor: STATE_COLORS.normal, backgroundColor: STATE_COLORS.normal, borderWidth: 3, pointRadius: 0, tension: 0.2 },
      { label: "blocked", data: [], borderColor: STATE_COLORS.block, backgroundColor: STATE_COLORS.block, borderWidth: 3, pointRadius: 0, tension: 0.2 },
    ] },
    options: {
      animation: false, responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: { maxTicksLimit: 8 }, title: { display: true, text: "simulation time (UTC)" } },
        y: { beginAtZero: true, title: { display: true, text: "requests in interval" } },
      },
      plugins: { legend: { labels: { font: { size: 16, weight: "bold" } } } },
    },
  });
}

// ---------- SSE ----------
function connectStream() {
  if (typeof EventSource === "undefined") return;
  if (S.es) S.es.close();
  const es = new EventSource("/api/stream");
  S.es = es;
  es.addEventListener("summary", (ev) => {
    S.lastSseAt = Date.now();
    setOnline(true);
    try { applySummary(JSON.parse(ev.data)); } catch (e) { /* ignore a bad event */ }
  });
  for (const kind of ["flag", "block", "campaign"]) {
    es.addEventListener(kind, (ev) => {
      try { addFeed(kind, JSON.parse(ev.data)); } catch (e) { /* ignore a bad event */ }
    });
  }
  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) {
      setOnline(false);
      setTimeout(connectStream, 3000);
    }
  };
}

function addFeed(kind, d) {
  const feed = $("feed");
  const first = feed.querySelector(".muted");
  if (first) first.remove();
  const li = document.createElement("li");
  const now = fmtTime(Date.now() / 1000);
  if (kind === "campaign") {
    li.innerHTML = `<span class="ftime">${now}</span>${badge(d.state)} <b>campaign ${esc(d.id)}</b>: ${fmtNum(d.size)} accounts, score ${fmtNum(d.score)}`;
    li.onclick = () => openCampaign(d.id);
  } else {
    if (d.account_id && d.pseudonym) S.pseudo[d.account_id] = d.pseudonym;
    const label = kind === "block" ? "BLOCK" : "FLAG";
    li.innerHTML = `<span class="ftime">${now}</span>${badge(d.state)} <b>${label}</b> ${esc(d.pseudonym)}`
      + ` score ${typeof d.score === "number" ? d.score.toFixed(2) : "?"} <span class="muted">${esc(d.reason || "")}</span>`;
    li.onclick = () => openAccount(d.account_id);
  }
  feed.prepend(li);
  while (feed.children.length > FEED_MAX) feed.lastElementChild.remove();
}

// ---------- accounts table ----------
function sparkline(hist, state) {
  const w = 130, h = 32, pad = 2;
  if (!Array.isArray(hist) || hist.length === 0) return '<span class="muted">-</span>';
  const pts = hist.slice(-40);
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
  const span = t1 - t0 || 1;
  const xy = pts.map((p, i) => {
    const x = pts.length === 1 ? w / 2 : pad + ((p[0] - t0) / span) * (w - 2 * pad);
    const y = h - pad - Math.max(0, Math.min(1, p[1])) * (h - 2 * pad);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const color = STATE_COLORS[state] || "#333";
  const yb = (h - pad - 0.9 * (h - 2 * pad)).toFixed(1);
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="score history">`
    + `<rect x="0" y="0" width="${w}" height="${h}" fill="#f6f6f6"/>`
    + `<line x1="0" x2="${w}" y1="${yb}" y2="${yb}" stroke="#c62828" stroke-dasharray="3 3" stroke-width="1"/>`
    + `<polyline fill="none" stroke="${color}" stroke-width="2.5" points="${xy.join(" ")}"/></svg>`;
}

async function refreshAccounts() {
  const state = $("state-filter").value;
  const q = `/api/accounts?state=${encodeURIComponent(state)}&sort=score&limit=50`;
  const data = await api(q);
  const rows = Array.isArray(data.accounts) ? data.accounts : [];
  for (const r of rows) S.pseudo[r.id] = r.pseudonym;
  const body = $("acct-body");
  if (!rows.length) { body.innerHTML = '<tr><td colspan="7" class="muted">No accounts.</td></tr>'; return; }
  body.innerHTML = rows.map((r) => `<tr class="click row-${esc(r.state)}" data-acct="${esc(r.id)}">
      <td><b>${esc(r.pseudonym)}</b>${youMark(r.you)}</td>
      <td>${badge(r.state)}</td>
      <td class="num"><b>${typeof r.score === "number" ? r.score.toFixed(2) : "-"}</b></td>
      <td>${sparkline(r.history, r.state)}</td>
      <td class="num">${fmtNum(r.requests ?? 0)}</td>
      <td>${r.campaign_id ? `<button class="link" data-cmp="${esc(r.campaign_id)}">${esc(r.campaign_id)}</button>` : ""}</td>
      <td class="reasons">${(r.reasons || []).slice(0, 3).map(esc).join("<br>")}</td></tr>`).join("");
}

// ---------- account tab ----------
function openAccount(id) {
  if (!id) return;
  S.accountId = id;
  S.evSig = "";
  $("a-case-card").hidden = true;
  $("a-case").innerHTML = "";
  showTab("account");
}

async function refreshAccount() {
  const id = S.accountId;
  if (!id) return;
  let a;
  try {
    a = await api(`/api/accounts/${encodeURIComponent(id)}`);
  } catch (e) {
    if (e.status === 404) {
      $("acct-empty").hidden = false;
      $("acct-empty").textContent = "The watchdog does not know this account now.";
      $("acct-view").hidden = true;
    }
    throw e;
  }
  if (id !== S.accountId) return;
  S.pseudo[a.id] = a.pseudonym;
  $("acct-empty").hidden = true;
  $("acct-view").hidden = false;
  $("a-name").innerHTML = `${esc(a.pseudonym)}${youMark(a.you)}`;
  const meta = [`${fmtNum(a.requests ?? 0)} requests`];
  if (a.campaign_id) meta.push(`campaign <button class="link" data-cmp="${esc(a.campaign_id)}">${esc(a.campaign_id)}</button>`);
  if (a.declared_use) meta.push(`declared use: ${esc(a.declared_use)}`);
  $("a-meta").innerHTML = meta.join(" &middot; ");
  $("a-score").textContent = typeof a.score === "number" ? a.score.toFixed(2) : "-";
  $("a-state").className = "badge st-" + (STATES.includes(a.state) ? a.state : "insufficient");
  $("a-state").textContent = a.state || "?";

  // Contributions: top 10 by absolute value.
  const contrib = (a.contributions || []).slice().sort((x, y) => Math.abs(y.value) - Math.abs(x.value)).slice(0, 10);
  if (typeof Chart !== "undefined") {
    const labels = contrib.map((c) => c.name);
    const vals = contrib.map((c) => c.value);
    const colors = vals.map((v) => (v >= 0 ? STATE_COLORS.block : STATE_COLORS.normal));
    if (!S.contribChart) {
      S.contribChart = new Chart($("contrib-chart"), {
        type: "bar",
        data: { labels, datasets: [{ label: "contribution to the log-odds", data: vals, backgroundColor: colors }] },
        options: {
          indexAxis: "y", animation: false, responsive: true, maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: { x: { title: { display: true, text: "contribution (log-odds)" } }, y: { ticks: { font: { size: 14, family: "Consolas, monospace" } } } },
        },
      });
    } else {
      S.contribChart.data.labels = labels;
      S.contribChart.data.datasets[0].data = vals;
      S.contribChart.data.datasets[0].backgroundColor = colors;
      S.contribChart.update("none");
    }
  }

  $("feat-body").innerHTML = (a.features || []).map((f) => {
    const p = typeof f.percentile === "number" ? Math.max(0, Math.min(100, f.percentile)) : null;
    const col = p === null ? "#999" : p >= 95 ? STATE_COLORS.block : p >= 75 ? STATE_COLORS.review : STATE_COLORS.normal;
    const bar = p === null ? '<span class="muted">-</span>'
      : `<div class="pbar"><span style="width:${p}%;background:${col}"></span><em>${p.toFixed(1)}</em></div>`;
    return `<tr><td><code>${esc(f.name)}</code></td><td class="num">${fmtNum(f.value)}</td><td>${bar}</td></tr>`;
  }).join("") || '<tr><td colspan="3" class="muted">No features.</td></tr>';

  const ev = Array.isArray(a.evidence) ? a.evidence : [];
  const sig = ev.map((e) => e.id).join(",");
  if (sig !== S.evSig) {
    S.evSig = sig;
    $("ev-count").textContent = `(${ev.length})`;
    $("ev-list").innerHTML = ev.length ? ev.map(evItem).join("") : '<li class="muted">No evidence yet.</li>';
  }
}

function evItem(e) {
  return `<li data-ev="${esc(e.id)}"><span class="evid">${esc(e.id)}</span>`
    + (e.detector ? `<span class="det">${esc(e.detector)}</span>` : "")
    + `${esc(e.summary || "")}`
    + (typeof e.sim_time === "number" && e.sim_time > 0 ? ` <span class="muted small">${fmtTime(e.sim_time)}</span>` : "")
    + `</li>`;
}

// ---------- evidence modal ----------
async function openEvidence(id) {
  const modal = $("modal");
  $("m-title").textContent = `Evidence ${id}`;
  $("m-body").innerHTML = '<p class="muted">Loading.</p>';
  modal.hidden = false;
  let d;
  try {
    d = await api(`/api/evidence/${encodeURIComponent(id)}`);
  } catch (e) {
    $("m-body").innerHTML = `<p class="note">The watchdog cannot give this evidence (${esc(e.message)}).</p>`;
    return;
  }
  const ev = d.evidence || {};
  const req = d.request || {};
  const msgs = Array.isArray(req.messages) ? req.messages : [];
  const hl = d.message && typeof d.message.index === "number" ? d.message.index
    : typeof ev.message_index === "number" ? ev.message_index : null;
  const params = [
    ["account", esc(req.pseudonym || "")],
    ["request", esc(req.request_id || "")],
    ["time", esc(fmtDateTime(req.sim_time))],
    ["temperature", esc(req.temperature)],
    ["n", esc(req.n)],
    ["max_tokens", esc(req.max_tokens)],
    ["tools schema", esc(req.tools_schema_id)],
  ].map(([k, v]) => `<b>${k}:</b> ${v}`).join(" &middot; ");
  const body = msgs.map((m, i) => {
    const idx = typeof m.index === "number" ? m.index : i;
    const isHl = idx === hl;
    const head = `#${idx} ${esc(m.role)}${m.tool ? " (" + esc(m.tool) + ")" : ""}${isHl ? " &larr; EVIDENCE POINTS HERE" : ""}`;
    const args = m.args ? `<pre>${esc(JSON.stringify(m.args, null, 2))}</pre>` : "";
    const text = m.text ? `<pre>${esc(m.text)}</pre>` : "";
    return `<div class="msg role-${esc(m.role)}${isHl ? " hl" : ""}" data-idx="${idx}"><div class="msg-h">${head}</div>${text}${args}</div>`;
  }).join("");
  $("m-body").innerHTML = `<p><b>${esc(ev.detector || "")}</b>: ${esc(ev.summary || "")}</p>`
    + `<p class="meta">${params}</p>`
    + `<p class="muted small">The request has ${msgs.length} messages.${hl !== null ? ` The evidence points to message #${hl}.` : ""}</p>`
    + (body || '<p class="muted">No messages.</p>');
  const target = $("m-body").querySelector(".msg.hl");
  if (target) target.scrollIntoView({ block: "center" });
}

function closeModal() { $("modal").hidden = true; }

// ---------- case files ----------
function inlineMd(s) {
  let out = esc(s);
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
  out = out.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  // Citations: [ev-123] or [ev-1, ev-2].
  out = out.replace(/\[((?:ev-[A-Za-z0-9_-]+)(?:\s*[,;]\s*ev-[A-Za-z0-9_-]+)*)\]/g, (_, ids) =>
    "[" + ids.split(/\s*[,;]\s*/).map((id) => `<a class="cite" href="#" data-ev="${id}">${id}</a>`).join(", ") + "]");
  return out;
}

function renderMarkdown(md) {
  const lines = String(md || "").split(/\r?\n/);
  let html = "", para = [], list = null, code = null;
  const flushPara = () => { if (para.length) { html += `<p>${inlineMd(para.join(" "))}</p>`; para = []; } };
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const line of lines) {
    if (code !== null) {
      if (/^\s*```/.test(line)) { html += `<pre class="template">${esc(code.join("\n"))}</pre>`; code = null; }
      else code.push(line);
      continue;
    }
    if (/^\s*```/.test(line)) { flushPara(); closeList(); code = []; continue; }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) { flushPara(); closeList(); const lvl = Math.min(5, h[1].length + 2); html += `<h${lvl}>${inlineMd(h[2])}</h${lvl}>`; continue; }
    const ul = line.match(/^\s*[-*+]\s+(.*)$/);
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ul || ol) {
      flushPara();
      const kind = ul ? "ul" : "ol";
      if (list !== kind) { closeList(); html += `<${kind}>`; list = kind; }
      html += `<li>${inlineMd((ul || ol)[1])}</li>`;
      continue;
    }
    if (!line.trim()) { flushPara(); closeList(); continue; }
    closeList();
    para.push(line.trim());
  }
  if (code !== null) html += `<pre class="template">${esc(code.join("\n"))}</pre>`;
  flushPara(); closeList();
  return html;
}

function renderCasefile(cf) {
  const ev = Array.isArray(cf.evidence) ? cf.evidence : [];
  const evList = `<h4>Evidence</h4><ul class="ev-list">${ev.map(evItem).join("") || '<li class="muted">No evidence.</li>'}</ul>`;
  if (cf.status === "fallback" || !cf.text) {
    return `<p class="note">The case-file writer did not give a valid text. This view shows only the evidence list.</p>${evList}`;
  }
  const info = `<p class="muted small">Status: ${esc(cf.status)}${cf.cached ? " (cached)" : ""}. Cited: ${(cf.cited || []).length} evidence items.</p>`;
  return `${info}<div class="casefile">${renderMarkdown(cf.text)}</div>${evList}`;
}

async function generateCasefile(kind) {
  const id = kind === "account" ? S.accountId : S.campaignId;
  if (!id) return;
  const btn = $(kind === "account" ? "a-casefile" : "c-casefile");
  const card = $(kind === "account" ? "a-case-card" : "c-case-card");
  const out = $(kind === "account" ? "a-case" : "c-case");
  btn.disabled = true;
  btn.textContent = "Writing the case file...";
  card.hidden = false;
  out.innerHTML = '<p class="muted">Wait. The watchdog writes the case file.</p>';
  try {
    const path = kind === "account" ? `/api/accounts/${encodeURIComponent(id)}/casefile` : `/api/campaigns/${encodeURIComponent(id)}/casefile`;
    const cf = await api(path, { method: "POST", timeout: 120000 });
    out.innerHTML = renderCasefile(cf);
    card.scrollIntoView({ block: "start", behavior: "smooth" });
  } catch (e) {
    out.innerHTML = `<p class="note">The case file failed (${esc(e.message)}).</p>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Generate case file";
  }
}

// ---------- campaigns tab ----------
function openCampaign(id) {
  if (!id) return;
  S.campaignId = id;
  $("c-case-card").hidden = true;
  $("c-case").innerHTML = "";
  showTab("campaigns");
}

async function refreshCampaigns() {
  const detail = S.campaignId ? refreshCampaignDetail() : Promise.resolve();
  const [list, graph] = await Promise.all([api("/api/campaigns"), api("/api/graph")]);
  const cmps = Array.isArray(list.campaigns) ? list.campaigns : [];
  $("cmp-body").innerHTML = cmps.length ? cmps.map((c) => `<tr class="click row-${esc(c.state)}" data-cmp="${esc(c.id)}">
      <td><b>${esc(c.id)}</b></td><td>${badge(c.state)}</td><td class="num">${fmtNum(c.size)}</td>
      <td class="num">${fmtNum(c.score)}</td><td class="num">${typeof c.mean_member_score === "number" ? c.mean_member_score.toFixed(2) : "-"}</td>
      <td>${(c.top_edge_types || []).map(esc).join(", ")}</td></tr>`).join("")
    : '<tr><td colspan="6" class="muted">No campaigns yet.</td></tr>';
  renderGraph(graph);
  await detail;
}

function renderGraph(g) {
  if (typeof cytoscape === "undefined") { $("cy").textContent = "Cytoscape.js did not load."; return; }
  const nodes = Array.isArray(g.nodes) ? g.nodes : [];
  const edges = Array.isArray(g.edges) ? g.edges : [];
  for (const n of nodes) S.pseudo[n.id] = n.pseudonym;
  const ids = new Set(nodes.map((n) => n.id));
  const els = nodes.map((n) => ({ group: "nodes", data: {
    id: n.id, label: n.pseudonym + (n.you ? " (you)" : ""), state: n.state, campaign: n.campaign_id || "",
    color: STATE_COLORS[n.state] || "#333", size: 22 + 26 * Math.max(0, Math.min(1, n.score || 0)), you: n.you ? 1 : 0 } }))
    .concat(edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e) => ({ group: "edges", data: {
      id: `${e.source}__${e.target}`, source: e.source, target: e.target, weight: e.weight || 0 } })));
  const sig = els.map((e) => e.data.id).sort().join("|");
  if (!S.cy) {
    S.cy = cytoscape({
      container: $("cy"),
      elements: [],
      style: [
        { selector: "node", style: { "background-color": "data(color)", label: "data(label)", width: "data(size)", height: "data(size)",
          "font-size": 15, "font-weight": "bold", color: "#111", "text-valign": "bottom", "text-margin-y": 4,
          "text-background-color": "#fff", "text-background-opacity": 0.8, "text-background-padding": 2, "border-width": 2, "border-color": "#111" } },
        { selector: "node[you = 1]", style: { "border-width": 5, "border-color": "#000", shape: "star" } },
        { selector: "node.hl", style: { "border-width": 7, "border-color": "#000", "border-style": "double" } },
        { selector: "edge", style: { width: "mapData(weight, 0, 4, 1, 14)", "line-color": "#777", opacity: 0.75, "curve-style": "haystack" } },
        { selector: "edge.hl", style: { "line-color": "#c62828", opacity: 0.95 } },
      ],
    });
    S.cy.on("tap", "node", (evt) => {
      const d = evt.target.data();
      if (d.campaign) openCampaign(d.campaign);
      else openAccount(d.id);
    });
  }
  if (sig !== S.graphSig) {
    S.graphSig = sig;
    S.cy.elements().remove();
    S.cy.add(els);
    if (S.tab === "campaigns") S.cy.layout({ name: "cose", animate: false, nodeRepulsion: () => 9000, idealEdgeLength: () => 90, padding: 30 }).run();
    else S.graphSig = ""; // lay out again when the tab is visible
  } else {
    for (const e of els) if (e.group === "nodes") S.cy.getElementById(e.data.id).data(e.data);
  }
  highlightCampaign();
}

function highlightCampaign() {
  if (!S.cy) return;
  S.cy.elements().removeClass("hl");
  if (!S.campaignId) return;
  const ns = S.cy.nodes().filter((n) => n.data("campaign") === S.campaignId);
  ns.addClass("hl");
  ns.edgesWith(ns).addClass("hl");
}

async function refreshCampaignDetail() {
  const id = S.campaignId;
  let c;
  try {
    c = await api(`/api/campaigns/${encodeURIComponent(id)}`);
  } catch (e) {
    if (e.status === 404) {
      $("cmp-view").hidden = false;
      $("c-name").textContent = `Campaign ${id}`;
      $("c-meta").textContent = "The watchdog does not know this campaign now.";
    }
    throw e;
  }
  if (id !== S.campaignId) return;
  const members = Array.isArray(c.members) ? c.members : [];
  for (const m of members) S.pseudo[m.id] = m.pseudonym;
  $("cmp-view").hidden = false;
  $("c-name").innerHTML = `Campaign ${esc(c.id)} ${badge(c.state)}`;
  $("c-meta").textContent = `${fmtNum(c.size ?? members.length)} accounts, score ${fmtNum(c.score)}`;
  $("c-members").innerHTML = members.map((m) => `<tr class="click row-${esc(m.state)}" data-acct="${esc(m.id)}">
      <td><b>${esc(m.pseudonym)}</b></td><td>${badge(m.state)}</td><td class="num">${typeof m.score === "number" ? m.score.toFixed(2) : "-"}</td></tr>`).join("")
    || '<tr><td colspan="3" class="muted">No members.</td></tr>';
  const ev = Array.isArray(c.evidence) ? c.evidence : [];
  $("c-evidence").innerHTML = ev.map(evItem).join("") || '<li class="muted">No evidence.</li>';
  const edges = (Array.isArray(c.edges) ? c.edges : []).slice().sort((a, b) => (b.weight || 0) - (a.weight || 0));
  $("c-edges").innerHTML = edges.slice(0, 200).map((e) => `<tr>
      <td>${esc(nameOf(e.source))}</td><td>${esc(nameOf(e.target))}</td><td class="num">${fmtNum(e.weight)}</td>
      <td>${Object.entries(e.types || {}).map(([k, v]) => `${esc(k)} ${fmtNum(v)}`).join("<br>")}</td>
      <td>${esc(e.evidence || "")}</td></tr>`).join("") || '<tr><td colspan="5" class="muted">No links.</td></tr>';
  highlightCampaign();
}

// ---------- evaluation tab ----------
function isPlain(v) { return v !== null && typeof v === "object" && !Array.isArray(v); }
function isScalar(v) { return v === null || ["string", "number", "boolean"].includes(typeof v); }
function scalarHtml(v) {
  if (v === null || v === undefined) return '<span class="muted">-</span>';
  if (typeof v === "number") return fmtNum(v);
  if (typeof v === "boolean") return v ? "yes" : "no";
  return esc(v);
}

function jsonHtml(v, depth = 0) {
  if (isScalar(v) || v === undefined) return scalarHtml(v);
  if (depth > 6) return `<code>${esc(JSON.stringify(v))}</code>`;
  if (Array.isArray(v)) {
    if (!v.length) return '<span class="muted">(empty)</span>';
    if (v.every(isScalar)) return v.map(scalarHtml).join(", ");
    if (v.every(isPlain)) {
      const cols = [...new Set(v.flatMap((o) => Object.keys(o)))];
      return `<table class="jt"><thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>`
        + v.map((o) => `<tr>${cols.map((c) => `<td${typeof o[c] === "number" ? ' class="n"' : ""}>${jsonHtml(o[c], depth + 1)}</td>`).join("")}</tr>`).join("")
        + "</tbody></table>";
    }
    return `<ul>${v.map((x) => `<li>${jsonHtml(x, depth + 1)}</li>`).join("")}</ul>`;
  }
  const keys = Object.keys(v);
  if (!keys.length) return '<span class="muted">(empty)</span>';
  return `<table class="jt"><tbody>${keys.map((k) => `<tr><th>${esc(k)}</th><td${typeof v[k] === "number" ? ' class="n"' : ""}>${jsonHtml(v[k], depth + 1)}</td></tr>`).join("")}</tbody></table>`;
}

function findPerRequest(obj, path, out) {
  if (!obj || typeof obj !== "object" || out.length > 10) return out;
  for (const [k, v] of Object.entries(obj)) {
    const p = path ? `${path}.${k}` : k;
    if (/per[_ -]?request/i.test(k)) out.push([p, v]);
    else if (isPlain(v)) findPerRequest(v, p, out);
  }
  return out;
}

async function loadEval() {
  $("eval-body").innerHTML = '<p class="muted">Loading.</p>';
  let d;
  try {
    d = await api("/api/eval", { timeout: 20000 });
  } catch (e) {
    $("eval-body").innerHTML = `<p class="note">The evaluation is not available (${esc(e.message)}). Run scripts/eval.sh, then click Reload.</p>`;
    return;
  }
  S.evalLoaded = true;
  const sections = Object.keys(d).filter((k) => k !== "images");
  const keyHits = [];
  for (const k of sections) findPerRequest(d[k], k, keyHits);
  $("eval-key").innerHTML = keyHits.length ? `<div class="key-slide"><h2>Per-request versus per-account</h2>`
    + keyHits.map(([p, v]) => `<h3><code>${esc(p)}</code></h3>${jsonHtml(v)}`).join("") + "</div>" : "";
  $("eval-body").innerHTML = sections.map((k) => {
    const v = d[k];
    const empty = v === null || v === undefined || (isPlain(v) && !Object.keys(v).length);
    return `<div class="card"><h2>${esc(k)}</h2>${empty ? '<p class="muted">No data yet.</p>' : jsonHtml(v)}</div>`;
  }).join("") || '<p class="muted">No evaluation data.</p>';
  const imgs = (Array.isArray(d.images) ? d.images : []).filter((u) => typeof u === "string" && u.startsWith("/") && !u.startsWith("//"));
  $("eval-images").innerHTML = imgs.map((u) => {
    const name = u.split("/").pop();
    return `<figure><img src="${esc(u)}" alt="${esc(name)}" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('p'),{textContent:'The image is missing.'}))"><figcaption>${esc(name)}</figcaption></figure>`;
  }).join("");
}

// ---------- wiring ----------
function wire() {
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  $("state-filter").addEventListener("change", () => refreshAccounts().catch(() => {}));
  $("a-casefile").addEventListener("click", () => generateCasefile("account"));
  $("c-casefile").addEventListener("click", () => generateCasefile("campaign"));
  $("eval-reload").addEventListener("click", () => loadEval());
  $("m-close").addEventListener("click", closeModal);
  $("modal").addEventListener("click", (e) => { if (e.target === $("modal")) closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
  // One delegated handler for evidence, campaign and account links.
  document.addEventListener("click", (e) => {
    const t = e.target instanceof Element ? e.target : null;
    if (!t) return;
    const ev = t.closest("[data-ev]");
    if (ev) { e.preventDefault(); openEvidence(ev.dataset.ev); return; }
    const cmp = t.closest("[data-cmp]");
    if (cmp) { e.preventDefault(); e.stopPropagation(); openCampaign(cmp.dataset.cmp); return; }
    const acct = t.closest("[data-acct]");
    if (acct) { openAccount(acct.dataset.acct); }
  });
}

function start() {
  wire();
  initRateChart();
  // "?nostream" turns off SSE (the page then polls /api/summary). Headless screenshots use it.
  if (!new URLSearchParams(location.search).has("nostream")) connectStream();
  every(POLL_ACCOUNTS_MS, refreshAccounts);
  every(POLL_SUMMARY_MS, async () => {
    // The SSE stream gives the summary each second. Poll only when the stream is quiet.
    if (Date.now() - S.lastSseAt > 3000) applySummary(await api("/api/summary"));
  });
  every(POLL_ACCOUNT_MS, async () => { if (S.tab === "account" && S.accountId) await refreshAccount(); });
  every(POLL_CAMPAIGNS_MS, async () => { if (S.tab === "campaigns") await refreshCampaigns(); });
  applyHash();
}

document.addEventListener("DOMContentLoaded", start);
