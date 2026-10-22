"use strict";

const $ = (id) => document.getElementById(id);
const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
let samples = {};
let state = null; // last plan from the server
let lastBrief = null;
const doneNow = new Set(); // two-minute jobs ticked off on this page

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function hours() {
  return {
    weekdays: $("wd-on").checked ? [$("wd-s").value, $("wd-e").value] : null,
    weekends: $("we-on").checked ? [$("we-s").value, $("we-e").value] : null,
  };
}

// The hero has four moods: an invitation, waiting, an error, and the answer.
function show(which) {
  for (const id of ["empty", "loading", "error", "answer"]) $(id).classList.toggle("hidden", id !== which);
  $("out").classList.toggle("hidden", which !== "answer");
  Nights.dim(which === "loading");
}

async function post(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    let msg = data.detail || `The server answered with an error (${r.status}).`;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg).join("; ");
    throw new Error(msg);
  }
  return data;
}

async function go() {
  const text = $("text").value.trim();
  if (text.length < 3) {
    $("text").focus();
    return;
  }
  $("go").disabled = true;
  show("loading");
  if (matchMedia("(max-width: 1023px)").matches) $("hero").scrollIntoView({ behavior: reduceMotion.matches ? "auto" : "smooth", block: "start" });
  const t0 = Date.now();
  $("elapsed").textContent = "0";
  const timer = setInterval(() => ($("elapsed").textContent = Math.round((Date.now() - t0) / 1000)), 500);
  try {
    const out = await post("/api/plan", {
      text, tz, now: new Date().toISOString(), hours: hours(), margin_hours: Number($("margin").value),
    });
    lastBrief = { text: out.brief, source: out.brief_source };
    doneNow.clear();
    render(out, "reveal");
  } catch (e) {
    $("error-msg").textContent = e.message;
    show("error");
  } finally {
    clearInterval(timer);
    $("go").disabled = false;
  }
}

let pending = null;
function reschedule() {
  clearTimeout(pending);
  if (!state) {
    Nights.idle(emptyModel());
    return;
  }
  pending = setTimeout(async () => {
    try {
      const out = await post("/api/reschedule", {
        tasks: state.tasks, tz, now: new Date().toISOString(), hours: hours(), margin_hours: Number($("margin").value),
      });
      out.meta = { ...state.meta, rescheduled: true };
      $("edit-error").classList.add("hidden");
      render(out, "morph");
    } catch (e) {
      $("edit-error").textContent = e.message;
      $("edit-error").classList.remove("hidden");
    }
  }, 250);
}

const ORDER = { short: 0, overdue: 1, tight: 2, scheduled: 3, do_now: 4, event: 5, someday: 6, later: 7 };

function render(out, how) {
  const before = state && state.summary.first_start_label;
  state = out;
  const s = out.summary;
  show("answer");

  const until = s.first_start_label || "whenever you like";
  // date and time as two pieces, so a phone can put them on two lines
  const cut = until.lastIndexOf(", ");
  $("until").innerHTML = cut > 0
    ? `<span>${esc(until.slice(0, cut + 1))}</span> <span>${esc(until.slice(cut + 2))}</span>`
    : `<span>${esc(until)}</span>`;
  if (s.first_start_label) {
    $("free").textContent = `That's ${s.free_for_label} of doing absolutely nothing about this list.`;
  } else {
    $("free").textContent = "Nothing here needs scheduled work.";
  }
  $("short-note").classList.toggle("hidden", !s.short);
  $("short-note").textContent = s.short
    ? `Except: ${s.short} task${s.short > 1 ? "s" : ""} won't fit even if you start now.`
    : "";
  fitUntil();
  if (how === "reveal" || until !== before) stretch(how === "reveal");

  const fromModel = out.brief_source === "model";
  const briefText = out.meta.rescheduled ? out.brief : lastBrief ? lastBrief.text : out.brief;
  $("brief").textContent = briefText;
  $("brief-src").textContent = out.meta.rescheduled
    ? "Plan changed, so this is a plain summary of the new plan."
    : fromModel
      ? "Written by Gemma, then checked: every time it mentions has to exist in the plan."
      : "Plain summary. Gemma's version didn't pass the fact check, so it was dropped.";

  figures(s);

  const now = out.tasks.filter((t) => t.status === "do_now");
  $("now-card").classList.toggle("hidden", !now.length);
  $("now-list").innerHTML = now
    .map((t) => {
      const done = doneNow.has(t.id);
      return `<li class="${done ? "done" : ""}"><label><input type="checkbox" data-now="${esc(t.id)}"${done ? " checked" : ""}><span>${esc(t.title)}${t.deadline_label ? ` <small>by ${esc(t.deadline_label)}</small>` : ""}</span></label></li>`;
    })
    .join("");

  const rows = [...out.tasks].sort((a, b) => ORDER[a.status] - ORDER[b.status] || String(a.ignore_until).localeCompare(String(b.ignore_until)));
  $("tasks").querySelector("tbody").innerHTML = rows.map(row).join("");

  agenda(out);
  const model = toModel(out);
  if (how === "reveal") Nights.reveal(model);
  else Nights.morph(model);
  legend(model);

  const m = out.meta || {};
  const bits = [];
  if (m.model) bits.push(`Read by ${m.model}${m.extract_ms ? ` in ${(m.extract_ms / 1000).toFixed(1)} s` : ""}${m.cached ? " (cached)" : ""}.`);
  if (m.repairs) bits.push(`${m.repairs} JSON repair round${m.repairs > 1 ? "s" : ""}.`);
  bits.push(`Times shown in ${out.tz}.`);
  $("meta").textContent = bits.join(" ");
}

function figures(s) {
  const items = [
    [fmtH(s.work_hours), "of real work, total"],
    [`${s.sloth_days} of 14`, "days are sloth days"],
    [String(s.do_now), s.do_now === 1 ? "two-minute job" : "two-minute jobs"],
  ];
  if (s.short) items.push([String(s.short), "won't fit", "bad"]);
  $("figures").innerHTML = items.map(([b, t, cls]) => `<p class="${cls || ""}"><b>${esc(b)}</b> ${esc(t)}</p>`).join("");
  $("nights-sum").textContent = `${fmtH(s.work_hours)} of work. ${s.sloth_days} of the next 14 days have nothing scheduled.`;
}

function fmtH(h) {
  return `${Math.round(h * 10) / 10} h`;
}

const MARK = { scheduled: "lit", tight: "tight", short: "short", overdue: "short", event: "event", do_now: "tick" };

function row(t) {
  const flag = t.grounded ? "" : `<p class="flag">Couldn't find this in your text. Check it.</p>`;
  const quote = t.quote ? `<p class="q">“${esc(t.quote)}”</p>` : "";
  const dl = t.deadline ? t.deadline.slice(0, 16) : "";
  const until = t.ignore_until_label
    ? `<p class="iu">${esc(t.ignore_until_label)}</p><p class="slack">${esc(t.slack_label)} from now</p>`
    : "";
  const why = t.status === "short" ? `<p class="slack bad">${fmtH(t.shortfall_hours)} short</p>` : "";
  const mark = MARK[t.status] || "none";
  return `<tr data-id="${esc(t.id)}">
    <td class="c-task"><p class="tt">${esc(t.title)}</p>${quote}${flag}</td>
    <td class="c-due" data-label="Deadline">${t.kind === "event" || !t.deadline ? `<p class="plain">${esc(t.deadline_label || "none")}</p>` : `<input type="datetime-local" data-f="deadline" value="${esc(dl)}" aria-label="Deadline for ${esc(t.title)}">`}</td>
    <td class="c-effort" data-label="Effort">${t.kind === "event" ? "" : `<span class="hrs"><input type="number" min="0" max="200" step="0.5" data-f="hours" value="${esc(t.hours)}" aria-label="Hours for ${esc(t.title)}"><span aria-hidden="true">h</span></span><p class="guess">${t.estimate_from === "you" ? "your estimate" : "Gemma's guess"}</p>`}</td>
    <td class="c-until" data-label="Ignore until">${until}<p class="status"><i class="mk ${mark}" aria-hidden="true"></i>${esc(t.status_text)}</p>${why}</td>
    <td class="c-x"><button class="x" data-del="${esc(t.id)}" title="Remove" aria-label="Remove ${esc(t.title)}">×</button></td>
  </tr>`;
}

// ---- the chart's data --------------------------------------------------------------

const hm = (s) => {
  const [h, m] = String(s).split(":").map(Number);
  return h * 60 + m;
};

// Wall-clock minutes since 00:00 on the plan's first day, in the plan's zone.
function minutesFrom(day0, iso, zone) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const p = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
      .formatToParts(d)
      .map((x) => [x.type, x.value]),
  );
  const days = (Date.UTC(+p.year, +p.month - 1, +p.day) - day0) / 86400000;
  return days * 1440 + Number(p.hour) * 60 + Number(p.minute);
}

function windows() {
  const h = hours();
  return { weekdays: h.weekdays && h.weekdays.map(hm), weekends: h.weekends && h.weekends.map(hm) };
}

function toModel(out) {
  const [y, mo, d] = out.days[0].date.split("-").map(Number);
  const day0 = Date.UTC(y, mo - 1, d);
  const byId = Object.fromEntries(out.tasks.map((t) => [t.id, t]));
  const seen = {};
  const blocks = [];
  out.days.forEach((day, i) => {
    for (const b of day.blocks) {
      const [s, e] = b.time.split(/[–-]/);
      const a = i * 1440 + hm(s);
      let end = i * 1440 + hm(e);
      if (end <= a) end += 1440;
      const t = byId[b.task_id] || {};
      seen[b.task_id] = (seen[b.task_id] || 0) + 1;
      blocks.push({
        key: `${b.task_id}#${seen[b.task_id]}`, task: b.task_id, title: b.title, a, b: end, alpha: 1,
        status: t.status || "scheduled", statusText: t.status_text || "Not yet", due: t.deadline_label,
        when: `${day.label} ${day.month}, ${b.time}`,
      });
    }
  });
  blocks.sort((p, q) => p.a - q.a);
  const placed = out.tasks.filter((t) => ["scheduled", "tight", "short"].includes(t.status) && t.deadline);
  const deadlines = placed
    .map((t) => ({ task: t.id, title: t.title, label: t.deadline_label, at: minutesFrom(day0, t.deadline, out.tz), alpha: 1 }))
    .filter((x) => x.at !== null);
  const events = out.tasks
    .filter((t) => t.kind === "event" && t.deadline)
    .map((t) => {
      const a = minutesFrom(day0, t.deadline, out.tz);
      return { task: t.id, title: t.title, label: t.deadline_label, a, b: a + Math.max(30, (t.hours || 0) * 60), alpha: 1 };
    })
    .filter((x) => x.a !== null && x.a < 14 * 1440 && x.b > 0);
  // the scheduler places tasks latest deadline first
  const order = [...placed].sort((p, q) => (q.deadline > p.deadline ? 1 : q.deadline < p.deadline ? -1 : q.id > p.id ? 1 : -1)).map((t) => t.id);
  const now = minutesFrom(day0, out.now, out.tz);
  const first = blocks.length ? blocks[0].a : 14 * 1440;
  return {
    planned: true,
    days: out.days.map((x) => ({ label: x.label, month: x.month, weekday: (new Date(`${x.date}T12:00:00Z`).getUTCDay() + 6) % 7, sloth: x.sloth })),
    now,
    windows: windows(),
    blocks,
    deadlines,
    events,
    order,
    free: { a: now, b: Math.max(now, first), p: 1 },
  };
}

function emptyModel() {
  const t = new Date();
  const days = [];
  for (let i = 0; i < 14; i++) {
    const d = new Date(t.getFullYear(), t.getMonth(), t.getDate() + i);
    days.push({
      label: `${d.toLocaleDateString("en-GB", { weekday: "short" })} ${String(d.getDate()).padStart(2, "0")}`,
      month: d.toLocaleDateString("en-GB", { month: "short" }),
      weekday: (d.getDay() + 6) % 7,
      sloth: true,
    });
  }
  return { planned: false, days, now: t.getHours() * 60 + t.getMinutes(), windows: windows(), blocks: [], deadlines: [], events: [], order: [], free: null };
}

function legend(m) {
  const items = [];
  const has = (st) => m.blocks.some((b) => b.status === st);
  if (m.blocks.length) items.push(["lit", "Work, as late as it can safely go"]);
  if (has("tight")) items.push(["tight", "Eats your safety margin"]);
  if (has("short")) items.push(["short", "Won't fit"]);
  if (m.deadlines.length) items.push(["due", "Deadline"]);
  if (m.events.length) items.push(["event", "Fixed event"]);
  items.push(["band", "Hours you're willing to work"]);
  if (m.planned) items.push(["wash", "Yours, free and clear"]);
  $("legend").innerHTML = items.map(([k, t]) => `<li><i class="mk ${k}" aria-hidden="true"></i>${esc(t)}</li>`).join("");
}

function agenda(out) {
  const lines = [];
  let run = [];
  const flush = () => {
    if (!run.length) return;
    const a = run[0];
    const b = run[run.length - 1];
    const span = run.length === 1 ? `${a.label} ${a.month}` : `${a.label} ${a.month} to ${b.label} ${b.month}`;
    lines.push(`<li class="rest"><span class="when">${esc(span)}</span><span>Nothing scheduled</span></li>`);
    run = [];
  };
  for (const d of out.days) {
    if (d.sloth) {
      run.push(d);
      continue;
    }
    flush();
    const items = d.blocks.map((b) => `<li><span class="tm">${esc(b.time)}</span> ${esc(b.title)}</li>`).join("");
    lines.push(`<li><span class="when">${esc(d.label)} ${esc(d.month)}</span><ul>${items}</ul></li>`);
  }
  flush();
  $("agenda").innerHTML = lines.join("");
}

// ---- the relax-until line ----------------------------------------------------------

// Size the time so each line fits at its full stretch.
function fitUntil() {
  const u = $("until");
  const box = u.parentElement.clientWidth;
  if (!box) return;
  const probe = u.cloneNode(true);
  probe.removeAttribute("id");
  Object.assign(probe.style, { position: "absolute", visibility: "hidden", fontSize: "100px", whiteSpace: "nowrap", animation: "none", width: "auto" });
  probe.classList.remove("stretch", "nudge");
  document.body.appendChild(probe);
  const w = probe.getBoundingClientRect().width;
  probe.remove();
  const max = matchMedia("(max-width: 640px)").matches ? 56 : 92;
  u.style.fontSize = `${Math.max(26, Math.min(max, Math.floor((100 * box) / w)))}px`;
}

function stretch(full) {
  const u = $("until");
  u.classList.remove("stretch", "nudge");
  void u.offsetWidth;
  u.classList.add(full ? "stretch" : "nudge");
}

// ---- table, list and buttons -------------------------------------------------------

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

function onNowTick(e) {
  const id = e.target.dataset.now;
  if (!id) return;
  if (e.target.checked) doneNow.add(id);
  else doneNow.delete(id);
  e.target.closest("li").classList.toggle("done", e.target.checked);
}

function downloadIcs() {
  if (!state) return;
  const blob = new Blob([state.ics], { type: "text/calendar" });
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "not-yet-plan.ics" });
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    URL.revokeObjectURL(a.href);
    a.remove();
  }, 1000);
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
  Nights.mount($("chart"));
  Nights.layout();
  Nights.idle(emptyModel());
  legend(emptyModel());
  try {
    const cfg = await fetch("/api/config").then((r) => r.json());
    samples = cfg.samples;
    $("max").textContent = cfg.max_text;
    $("text").maxLength = cfg.max_text;
    $("model").textContent = `Model: ${cfg.model}. Your text goes only to this model, and the server keeps a short-lived in-memory cache. Nothing is saved to disk. For full privacy, run it locally.`;
    $("samples").innerHTML = Object.entries(samples)
      .map(([k, v]) => `<button type="button" class="chip" data-key="${esc(k)}" aria-pressed="false">${esc(v.label)}</button>`)
      .join("");
    pickSample(Object.keys(samples)[0]);
  } catch {
    $("model").textContent = "Couldn't reach the server. Reload the page to try again.";
  }
}

$("samples").addEventListener("click", (e) => e.target.dataset.key && pickSample(e.target.dataset.key));
$("text").addEventListener("input", () => {
  updateCount();
  for (const b of document.querySelectorAll(".chip")) b.setAttribute("aria-pressed", String(samples[b.dataset.key] && samples[b.dataset.key].text === $("text").value));
});
$("text").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) go();
});
$("go").addEventListener("click", go);
$("ics").addEventListener("click", downloadIcs);
$("tasks").addEventListener("change", onTableInput);
$("tasks").addEventListener("click", onTableClick);
$("now-list").addEventListener("change", onNowTick);
for (const id of ["wd-on", "wd-s", "wd-e", "we-on", "we-s", "we-e", "margin"]) $(id).addEventListener("change", reschedule);
addEventListener("resize", () => state && fitUntil());
if (document.fonts) document.fonts.ready.then(() => state && fitUntil());
init();
