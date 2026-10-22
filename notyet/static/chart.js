"use strict";

// The 14-day chart. One row per day, midnight to midnight across. Lit blocks are
// work the scheduler placed; everything dark is time you don't owe anyone.
// Times are minutes from 00:00 on the first day, in the plan's time zone.
const Nights = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const DAY = 1440;
  const ROWS = 14;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)");

  let root, svg, tip;
  let g = null; // geometry for the current width
  let model = null; // what is on screen once animations settle
  let shown = null; // what is on screen this frame
  let raf = 0;
  let hot = null; // task id under the pointer or focus
  let active = 0; // block that holds the tab stop

  const clamp01 = (v) => Math.max(0, Math.min(1, v));
  const easeInOut = (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
  const easeOut = (p) => 1 - Math.pow(1 - p, 3);
  // a lamp warming up: on, a dip, then steady
  const switchOn = (p) => (p <= 0 ? 0 : p >= 1 ? 1 : p < 0.3 ? (p / 0.3) * 0.85 : p < 0.45 ? 0.4 : 0.4 + ((p - 0.45) / 0.55) * 0.6);

  function el(name, attrs, parent) {
    const n = document.createElementNS(NS, name);
    for (const k in attrs) if (attrs[k] != null) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }

  function mount(container) {
    root = container;
    svg = el("svg", { class: "nights-svg", role: "group", "aria-labelledby": "nights-h nights-sum" }, root);
    tip = document.createElement("div");
    tip.className = "tip";
    tip.hidden = true;
    root.appendChild(tip);
    new ResizeObserver(() => {
      if (root.clientWidth && (!g || g.W !== root.clientWidth)) {
        layout();
        paint(shown || model);
      }
    }).observe(root);
    svg.addEventListener("pointerover", (e) => point(e.target));
    svg.addEventListener("pointerleave", () => point(null));
    svg.addEventListener("focusin", (e) => point(e.target));
    svg.addEventListener("focusout", () => point(null));
    svg.addEventListener("keydown", onKey);
  }

  function layout() {
    const W = root.clientWidth;
    const narrow = W < 560;
    const rowH = narrow ? 24 : 28;
    g = {
      W,
      narrow,
      x0: narrow ? 50 : 84,
      axisH: 26,
      rowH,
      trackH: rowH - (narrow ? 7 : 9),
    };
    g.tw = W - g.x0 - 4;
    g.H = g.axisH + ROWS * rowH + 2;
    svg.setAttribute("viewBox", `0 0 ${W} ${g.H}`);
    svg.setAttribute("width", W);
    svg.setAttribute("height", g.H);
  }

  const xAt = (minuteOfDay) => g.x0 + (minuteOfDay / DAY) * g.tw;
  const rowTop = (row) => g.axisH + row * g.rowH + (g.rowH - g.trackH) / 2;

  // split [a, b) at midnights into per-row pieces, dropping what falls outside the 14 days
  function pieces(a, b) {
    const out = [];
    for (let row = Math.max(0, Math.floor(a / DAY)); row < ROWS && row * DAY < b; row++) {
      const s = Math.max(a, row * DAY) - row * DAY;
      const e = Math.min(b, (row + 1) * DAY) - row * DAY;
      if (e > s) out.push({ row, s, e });
    }
    return out;
  }

  function windowFor(m, row) {
    const wd = m.days[row].weekday;
    return wd < 5 ? m.windows.weekdays : m.windows.weekends;
  }

  function paint(m) {
    if (!m || !g) return;
    shown = m;
    if (active >= m.blocks.length) active = 0; // an edit can leave fewer blocks
    svg.textContent = "";
    const L = {};
    for (const k of ["grid", "tracks", "bands", "past", "wash", "events", "blocks", "deadlines", "now", "labels", "axis"]) {
      L[k] = el("g", { class: `l-${k}` }, svg);
    }

    // hour grid and axis
    const nowX = m.now >= 0 && m.now < DAY ? xAt(m.now) : null;
    for (const h of [0, 6, 12, 18, 24]) {
      const x = xAt(h * 60);
      el("line", { x1: x, x2: x, y1: g.axisH - 4, y2: g.H, class: "grid" }, L.grid);
      if (nowX !== null && Math.abs(nowX - x) < (g.narrow ? 26 : 34)) continue;
      const t = el("text", { x, y: 14, class: "tick", "text-anchor": h === 0 ? "start" : h === 24 ? "end" : "middle" }, L.axis);
      t.textContent = `${String(h).padStart(2, "0")}:00`;
    }

    for (let row = 0; row < ROWS; row++) {
      const y = rowTop(row);
      el("rect", { x: g.x0, y, width: g.tw, height: g.trackH, rx: 4, class: "track" }, L.tracks);
      const d = m.days[row];
      const label = el("text", { x: g.x0 - 12, y: y + g.trackH / 2, class: `day${d.sloth && m.planned ? " sloth" : ""}`, "text-anchor": "end", "dominant-baseline": "central" }, L.labels);
      label.textContent = g.narrow ? d.label : row === 0 || d.month !== m.days[row - 1].month ? `${d.label} ${d.month}` : d.label;
    }

    // the hours you said you'd work, including windows that run past midnight
    for (let row = 0; row < ROWS; row++) {
      const w = windowFor(m, row);
      if (!w) continue;
      const [s, e] = w;
      const spans = e > s ? [[row * DAY + s, row * DAY + e]] : [[row * DAY + s, (row + 1) * DAY + e]];
      for (const [a, b] of spans) for (const p of pieces(a, b)) band(L.bands, p);
    }

    if (nowX !== null) {
      el("rect", { x: g.x0, y: rowTop(0), width: nowX - g.x0, height: g.trackH, rx: 4, class: "past" }, L.past);
      el("line", { x1: nowX, x2: nowX, y1: g.axisH - 6, y2: rowTop(0) + g.trackH + 3, class: "now" }, L.now);
      const t = el("text", { x: nowX, y: 14, class: "tick now-label", "text-anchor": nowX - g.x0 < 20 ? "start" : "middle" }, L.axis);
      t.textContent = "now";
    }

    if (m.free && m.free.p > 0) {
      const end = m.free.a + (m.free.b - m.free.a) * m.free.p;
      for (const p of pieces(m.free.a, end)) {
        el("rect", { x: xAt(p.s), y: rowTop(p.row), width: Math.max(0, xAt(p.e) - xAt(p.s)), height: g.trackH, class: "wash" }, L.wash);
      }
    }

    m.events.forEach((ev, i) => {
      if (ev.alpha <= 0) return;
      for (const p of pieces(ev.a, ev.b)) {
        el("rect", {
          x: xAt(p.s) + 0.75, y: rowTop(p.row) + 0.75, width: Math.max(2, xAt(p.e) - xAt(p.s) - 1.5), height: g.trackH - 1.5,
          rx: 3, class: "event", opacity: ev.alpha, "data-kind": "event", "data-i": i,
        }, L.events);
      }
    });

    m.blocks.forEach((b, i) => {
      if (b.alpha <= 0) return;
      pieces(b.a, b.b).forEach((p, k) => {
        const r = el("rect", {
          x: xAt(p.s), y: rowTop(p.row), width: Math.max(2.5, xAt(p.e) - xAt(p.s) - 2), height: g.trackH,
          rx: 3, class: `blk ${b.status}`, "data-alpha": b.alpha, "data-task": b.task, "data-kind": "block", "data-i": i,
        }, L.blocks);
        if (k === 0 && m.planned && b.alpha === 1) {
          r.setAttribute("tabindex", i === active ? "0" : "-1");
          r.setAttribute("role", "img");
          r.setAttribute("aria-label", `${b.title}: ${b.when}. ${b.statusText}.`);
        }
      });
    });

    m.deadlines.forEach((d, i) => {
      if (d.alpha <= 0 || d.at < 0 || d.at >= ROWS * DAY) return;
      const row = Math.floor(d.at / DAY);
      const x = xAt(d.at - row * DAY);
      const y = rowTop(row);
      const grp = el("g", { class: "due", "data-alpha": d.alpha, "data-task": d.task, "data-kind": "due", "data-i": i }, L.deadlines);
      el("rect", { x: x - 8, y: y - 4, width: 16, height: g.trackH + 8, class: "hit" }, grp);
      el("line", { x1: x, x2: x, y1: y - 3, y2: y + g.trackH + 3, class: "due-line" }, grp);
      el("circle", { cx: x, cy: y - 3, r: 4, class: "due-head" }, grp);
    });
    highlight();
  }

  // dim everything except the task under the pointer, without rebuilding the drawing
  function highlight() {
    for (const n of svg.querySelectorAll("[data-alpha]")) {
      const mine = hot !== null && n.dataset.task === hot;
      n.classList.toggle("hot", mine);
      n.setAttribute("opacity", Number(n.dataset.alpha) * (hot !== null && !mine ? 0.3 : 1));
    }
  }

  function band(layer, p) {
    el("rect", { x: xAt(p.s), y: rowTop(p.row), width: Math.max(0, xAt(p.e) - xAt(p.s)), height: g.trackH, class: "band" }, layer);
  }

  // ---- pointer, focus and keyboard -------------------------------------------------

  function point(target) {
    const node = target && target.closest ? target.closest("[data-kind]") : null;
    const kind = node && !raf ? node.dataset.kind : null;
    const list = kind === "block" ? shown.blocks : kind === "due" ? shown.deadlines : kind === "event" ? shown.events : null;
    const item = list && list[Number(node.dataset.i)];
    if (!item) {
      if (hot !== null) {
        hot = null;
        highlight();
      }
      tip.hidden = true;
      return;
    }
    if (hot !== item.task) {
      hot = item.task;
      highlight();
    }
    showTip(kind, item);
  }

  function showTip(kind, item) {
    tip.textContent = "";
    const line = (cls, text) => {
      const p = document.createElement("p");
      p.className = cls;
      p.textContent = text;
      tip.appendChild(p);
    };
    if (kind === "block") {
      line("tv", item.when);
      line("tt", item.title);
      line("tm", item.due ? `Due ${item.due}. ${item.statusText}.` : `${item.statusText}.`);
    } else if (kind === "due") {
      line("tv", `Due ${item.label}`);
      line("tt", item.title);
    } else {
      line("tv", item.label);
      line("tt", item.title);
      line("tm", "On the calendar anyway.");
    }
    tip.hidden = false;
    // anchor to the first piece of the mark
    const at = kind === "due" ? item.at : item.a;
    const row = Math.max(0, Math.min(ROWS - 1, Math.floor(Math.max(0, at) / DAY)));
    const x = xAt(Math.max(0, at) - row * DAY);
    const y = rowTop(row);
    const w = tip.offsetWidth;
    const h = tip.offsetHeight;
    const left = Math.max(0, Math.min(g.W - w, x - w / 2));
    const top = y - h - 10 < 0 ? y + g.trackH + 10 : y - h - 10;
    tip.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
  }

  function onKey(e) {
    if (!shown || e.target.dataset.kind !== "block") return;
    if (e.key === "Escape") return point(null);
    const stops = [...svg.querySelectorAll("rect.blk[tabindex]")];
    const at = stops.indexOf(e.target);
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (at < 0 || (!step && e.key !== "Home" && e.key !== "End")) return;
    e.preventDefault();
    const next = e.key === "Home" ? 0 : e.key === "End" ? stops.length - 1 : (at + step + stops.length) % stops.length;
    for (const r of stops) r.setAttribute("tabindex", "-1");
    stops[next].setAttribute("tabindex", "0");
    active = Number(stops[next].dataset.i);
    stops[next].focus({ preventScroll: true });
  }

  // ---- what callers use ------------------------------------------------------------

  function stop() {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
  }

  function settle(next) {
    stop();
    model = next;
    paint(model);
  }

  // Draw a fresh plan the way the scheduler built it: deadlines go up and work lights
  // come on from the last deadline backwards, then the free stretch fills in from now.
  function reveal(next) {
    stop();
    hot = null;
    active = 0;
    tip.hidden = true;
    if (reduce.matches || !g) return settle(next);
    const n = next.order.length;
    const gap = n ? Math.min(240, 1500 / n) : 0;
    const at = {};
    next.order.forEach((id, i) => (at[id] = 250 + i * gap));
    const washFrom = 250 + n * gap + 200;
    const washFor = 900;
    const m = {
      ...next,
      events: next.events.map((x) => ({ ...x })),
      deadlines: next.deadlines.map((x) => ({ ...x })),
      blocks: next.blocks.map((x) => ({ ...x })),
      free: next.free && { ...next.free },
    };
    const t0 = performance.now();
    const frame = (now) => {
      const t = now - t0;
      for (const x of m.events) x.alpha = clamp01(t / 400);
      for (const x of m.deadlines) x.alpha = clamp01((t - (at[x.task] ?? 0)) / 220);
      for (const x of m.blocks) x.alpha = switchOn((t - (at[x.task] ?? 0) - 160) / 460);
      if (m.free) m.free.p = easeOut(clamp01((t - washFrom) / washFor));
      paint(m);
      if (t < washFrom + washFor) raf = requestAnimationFrame(frame);
      else settle(next);
    };
    raf = requestAnimationFrame(frame);
  }

  // Slide from the plan on screen to an edited one. Blocks travel through time, so
  // a task that can now start later visibly moves later.
  function morph(next) {
    const prev = model;
    stop();
    tip.hidden = true;
    if (!prev || !prev.planned || reduce.matches || !g) return settle(next);
    const old = new Map(prev.blocks.map((b) => [b.key, b]));
    const keep = new Set(next.blocks.map((b) => b.key));
    const gone = prev.blocks.filter((b) => !keep.has(b.key));
    const dueOld = new Map(prev.deadlines.map((d) => [d.task, d]));
    const lerp = (a, b, p) => a + (b - a) * p;
    const dur = 750;
    const t0 = performance.now();
    const frame = (now) => {
      const p = easeInOut(clamp01((now - t0) / dur));
      const m = {
        ...next,
        blocks: next.blocks
          .map((b) => {
            const o = old.get(b.key);
            return o ? { ...b, a: lerp(o.a, b.a, p), b: lerp(o.b, b.b, p) } : { ...b, alpha: p };
          })
          .concat(gone.map((b) => ({ ...b, alpha: 1 - p }))),
        deadlines: next.deadlines.map((d) => {
          const o = dueOld.get(d.task);
          return o ? { ...d, at: lerp(o.at, d.at, p) } : { ...d, alpha: p };
        }),
        free: next.free && prev.free ? { ...next.free, b: lerp(prev.free.b, next.free.b, p) } : next.free,
      };
      paint(m);
      if (p < 1) raf = requestAnimationFrame(frame);
      else settle(next);
    };
    raf = requestAnimationFrame(frame);
  }

  // Before there is a plan, or when only the working hours change.
  function idle(next) {
    if (model && model.planned && raf) return;
    settle(next);
  }

  function dim(on) {
    root.classList.toggle("waiting", on);
  }

  return { mount, layout, reveal, morph, idle, dim, get model() { return model; } };
})();
