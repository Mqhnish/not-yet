"use strict";

const $ = (id) => document.getElementById(id);
const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
let samples = {};
let state = null; // last plan from the server
let lastBrief = null;

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function hours() {
  return {
    weekdays: $("wd-on").checked ? [$("wd-s").value, $("wd-e").value] : null,
    weekends: $("we-on").checked ? [$("we-s").value, $("we-e").value] : null,
  };
}

function show(which) {
  for (const id of ["empty", "loading", "error", "out"]) $(id).classList.toggle("hidden", id !== which);
}

async function post(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    let msg = data.detail || `Something went wrong (${r.status}).`;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg).join("; ");
    throw new Error(msg);
  }
  return data;
}

async function go() {
  const text = $("text").value.trim();
  if (text.length < 3) return;
  $("go").disabled = true;
  show("loading");
  const t0 = Date.now();
  const timer = setInterval(() => ($("elapsed").textContent = Math.round((Date.now() - t0) / 1000)), 500);
  try {
    const out = await post("/api/plan", {
      text, tz, now: new Date().toISOString(), hours: hours(), margin_hours: Number($("margin").value),
    });
    lastBrief = { text: out.brief, source: out.brief_source };
    render(out);
  } catch (e) {
    $("error").textContent = e.message;
    show("error");
  } finally {
    clearInterval(timer);
    $("go").disabled = false;
  }
}

let pending = null;
function reschedule() {
  clearTimeout(pending);
  pending = setTimeout(async () => {
    if (!state) return;
    try {
      const out = await post("/api/reschedule", {
        tasks: state.tasks, tz, now: new Date().toISOString(), hours: hours(), margin_hours: Number($("margin").value),
      });
      out.meta = { ...state.meta, rescheduled: true };
      render(out);
    } catch (e) {
      $("error").textContent = e.message;
      $("error").classList.remove("hidden");
    }
  }, 250);
}

const ORDER = { short: 0, overdue: 1, tight: 2, scheduled: 3, do_now: 4, event: 5, someday: 6, later: 7 };

function render(out) {
  state = out;
  const s = out.summary;
  show("out");

  if (s.first_start_label) {
    $("until").textContent = s.first_start_label;
    $("free").textContent = `That's ${s.free_for_label} of doing absolutely nothing about this list.`;
  } else {
    $("until").textContent = "whenever you like";
    $("free").textContent = "Nothing here needs scheduled work.";
  }
  if (s.short) $("free").textContent += ` Except: ${s.short} task${s.short > 1 ? "s" : ""} won't fit even if you start now.`;

  const fromModel = out.brief_source === "model";
  const briefText = out.meta.rescheduled ? out.brief : lastBrief ? lastBrief.text : out.brief;
  const label = out.meta.rescheduled
    ? "Plan changed, so this is a plain summary of the new plan."
    : fromModel
      ? "Written by Gemma, then checked: every time it mentions has to exist in the plan."
      : "Plain summary. Gemma's version didn't pass the fact check, so it was dropped.";
  $("brief").innerHTML = `${esc(briefText)}<span class="src">${esc(label)}</span>`;

  $("stats").innerHTML = [
    stat(`${fmtH(s.work_hours)}`, "of real work, total"),
    stat(`${s.sloth_days} / 14`, "sloth days ahead"),
    stat(String(s.do_now), "two-minute jobs"),
    s.short ? stat(String(s.short), "won't fit", "bad") : "",
  ].join("");

  const now = out.tasks.filter((t) => t.status === "do_now");
  $("now-card").classList.toggle("hidden", !now.length);
  $("now-list").innerHTML = now.map((t) => `<li>${esc(t.title)}${t.deadline_label ? ` <span class="slack">by ${esc(t.deadline_label)}</span>` : ""}</li>`).join("");

  $("days").innerHTML = out.days
    .map((d) => {
      const head = `<div class="d">${esc(d.label)}<span class="m">${esc(d.month)}</span></div>`;
      if (d.sloth) return `<div class="day sloth">${head}<div class="zzz"><span aria-hidden="true">🦥</span>Sloth day</div></div>`;
      return `<div class="day">${head}${d.blocks
        .map((b) => `<div class="blk"><div class="t">${esc(b.time)}</div>${esc(b.title)}</div>`)
        .join("")}</div>`;
    })
    .join("");

  const rows = [...out.tasks].sort((a, b) => ORDER[a.status] - ORDER[b.status] || String(a.ignore_until).localeCompare(String(b.ignore_until)));
  $("tasks").querySelector("tbody").innerHTML = rows.map(row).join("");

  const m = out.meta || {};
  const bits = [];
  if (m.model) bits.push(`Read by ${m.model}${m.extract_ms ? ` in ${(m.extract_ms / 1000).toFixed(1)} s` : ""}${m.cached ? " (cached)" : ""}`);
  if (m.repairs) bits.push(`${m.repairs} JSON repair round${m.repairs > 1 ? "s" : ""}`);
  bits.push(`times shown in ${out.tz}`);
  $("meta").textContent = bits.join(" · ");
}

function stat(big, small, cls = "") {
  return `<div class="stat ${cls}"><b>${esc(big)}</b><span>${esc(small)}</span></div>`;
}

function fmtH(h) {
  return `${Math.round(h * 10) / 10} h`;
}

function row(t) {
  const flag = t.grounded ? "" : `<div class="flag">Couldn't find this in your text. Check it.</div>`;
  const quote = t.quote ? `<div class="q">“${esc(t.quote)}”</div>` : "";
  const dl = t.deadline ? t.deadline.slice(0, 16) : "";
  const until =
    t.ignore_until_label
      ? `${esc(t.ignore_until_label)}<div class="slack">${esc(t.slack_label)} from now</div>`
      : "";
  const why = t.status === "short" ? `<div class="slack">${fmtH(t.shortfall_hours)} short</div>` : "";
  return `<tr data-id="${esc(t.id)}">
    <td><b>${esc(t.title)}</b>${quote}${flag}</td>
    <td>${t.kind === "event" || !t.deadline ? esc(t.deadline_label || "none") : `<input type="datetime-local" data-f="deadline" value="${esc(dl)}" aria-label="Deadline for ${esc(t.title)}">`}</td>
    <td>${t.kind === "event" ? "" : `<input type="number" min="0" max="200" step="0.5" data-f="hours" value="${esc(t.hours)}" aria-label="Hours for ${esc(t.title)}"><div class="guess">${t.estimate_from === "you" ? "your estimate" : "Gemma's guess"}</div>`}</td>
    <td>${until}<span class="pill ${esc(t.status)}">${esc(t.status_text)}</span>${why}</td>
    <td><button class="x" data-del="${esc(t.id)}" title="Remove" aria-label="Remove ${esc(t.title)}">×</button></td>
  </tr>`;
}

function onTableInput(e) {
  const tr = e.target.closest("tr");
  if (!tr || !state) return;
  const t = state.tasks.find((x) => x.id === tr.dataset.id);
  if (!t) return;
  if (e.target.dataset.f === "hours") {
    t.hours = Math.max(0, Number(e.target.value) || 0);
    t.estimate_from = "you";
    t.tiny = t.hours > 0 && t.hours <= 5 / 60;
  } else if (e.target.dataset.f === "deadline") {
    t.deadline = e.target.value || null;
  }
  reschedule();
}

function onTableClick(e) {
  const id = e.target.dataset && e.target.dataset.del;
  if (!id || !state) return;
  state.tasks = state.tasks.filter((x) => x.id !== id);
  reschedule();
}

function downloadIcs() {
  if (!state) return;
  const blob = new Blob([state.ics], { type: "text/calendar" });
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "not-yet-plan.ics" });
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

function pickSample(key) {
  $("text").value = samples[key].text;
  updateCount();
  for (const b of document.querySelectorAll(".chip")) b.setAttribute("aria-pressed", String(b.dataset.key === key));
}

function updateCount() {
  $("count").textContent = $("text").value.length;
}

async function init() {
  try {
    const cfg = await fetch("/api/config").then((r) => r.json());
    samples = cfg.samples;
    $("max").textContent = cfg.max_text;
    $("text").maxLength = cfg.max_text;
    $("model").textContent = `Model: ${cfg.model}. Your text goes only to this model, and the server keeps a short-lived in-memory cache. Nothing is saved to disk. For full privacy, run it locally.`;
    $("samples").innerHTML =
      `<span class="hint" style="margin:0;align-self:center">Try:</span>` +
      Object.entries(samples).map(([k, v]) => `<button class="chip" data-key="${esc(k)}" aria-pressed="false">${esc(v.label)}</button>`).join("");
    pickSample(Object.keys(samples)[0]);
  } catch {
    $("model").textContent = "Couldn't reach the server.";
  }
}

$("samples").addEventListener("click", (e) => e.target.dataset.key && pickSample(e.target.dataset.key));
$("text").addEventListener("input", updateCount);
$("go").addEventListener("click", go);
$("ics").addEventListener("click", downloadIcs);
$("tasks").addEventListener("change", onTableInput);
$("tasks").addEventListener("click", onTableClick);
for (const id of ["wd-on", "wd-s", "wd-e", "we-on", "we-s", "we-e", "margin"]) $(id).addEventListener("change", reschedule);
init();
