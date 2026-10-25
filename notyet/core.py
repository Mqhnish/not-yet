"""Glue: brain dump -> tasks -> lazy schedule -> brief + calendar, as one JSON-ready dict."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import brief as brief_mod
from . import ics
from .extract import ExtractedTask, extract
from .schedule import Availability, Plan, Task, plan, sloth_days

STATUS_TEXT = {
    "scheduled": "Not yet",
    "tight": "Not yet, but it eats your safety margin",
    "short": "Start now",
    "do_now": "Just do it now",
    "someday": "Someday",
    "event": "On the calendar anyway",
    "overdue": "Already late",
    "later": "Way later",
}


def fmt(dt: datetime, tz: ZoneInfo) -> str:
    return f"{dt.astimezone(tz):%a %d %b, %H:%M}"


def fmt_span(start: datetime, end: datetime, tz: ZoneInfo) -> str:
    s, e = start.astimezone(tz), end.astimezone(tz)
    if s.date() == e.date() or (e - s <= timedelta(hours=24) and e.hour == 0 and e.minute == 0):
        return f"{s:%a %d %b}, {s:%H:%M}–{e:%H:%M}"
    return f"{s:%a %d %b, %H:%M} – {e:%a %d %b, %H:%M}"


def fmt_duration(td: timedelta) -> str:
    mins = max(0, int(td.total_seconds() // 60))
    days, rem = divmod(mins, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days} day{'s' if days != 1 else ''} {hours} h" if hours else f"{days} day{'s' if days != 1 else ''}"
    if hours:
        return f"{hours} h {mins} min" if mins else f"{hours} h"
    return f"{mins} min"


def fmt_hours(h: float) -> str:
    return f"{h:g} h"


def to_schedule_tasks(tasks: list[ExtractedTask]) -> list[Task]:
    return [Task(t.id, t.title, t.hours, t.deadline, t.splittable, t.tiny, t.kind) for t in tasks]


def _facts(p: Plan, tasks: list[ExtractedTask]) -> tuple[list[str], dict]:
    tz, by_id = p.tz, {t.id: t for t in tasks}
    lines = [f"Now: {fmt(p.now, tz)}"]
    fallback: dict = {}
    if p.first_start:
        first = p.blocks[0]
        title = by_id[first.task_id].title
        lines.append(f"Nothing is scheduled until {fmt(first.start, tz)}, which is {fmt_duration(first.start - p.now)} from now")
        lines.append(f'First work block: "{title}", {fmt_span(first.start, first.end, tz)}')
        fallback["first"] = fmt(first.start, tz)
        fallback["first_task"] = f'"{title}" ({fmt_span(first.start, first.end, tz)})'
    else:
        lines.append("No work blocks are scheduled at all")
    do_now = [by_id[i].title for i, tp in p.tasks.items() if tp.status == "do_now"]
    if do_now:
        lines.append("Tiny tasks to do right away: " + "; ".join(do_now))
        fallback["do_now"] = " and ".join(f'"{t}"' for t in do_now[:3])
    days = sloth_days(p, 7)
    if days:
        lines.append("Days this week with no work at all: " + ", ".join(f"{d:%a %d %b}" for d in days))
    short = [(by_id[i].title, tp.shortfall_hours) for i, tp in p.tasks.items() if tp.status == "short"]
    for title, h in short:
        lines.append(f'Problem: "{title}" does not fit, it is {fmt_hours(h)} short even if started now')
    if short:
        fallback["short"] = f'"{short[0][0]}" is {fmt_hours(short[0][1])} short even if you start now'
    return lines, fallback


def present(p: Plan, tasks: list[ExtractedTask]) -> dict:
    tz = p.tz
    out_tasks = []
    for t in tasks:
        tp = p.tasks[t.id]
        ls = tp.latest_start
        out_tasks.append(
            {
                **t.to_json(),
                "deadline_label": fmt(t.deadline, tz) if t.deadline else None,
                "status": tp.status,
                "status_text": STATUS_TEXT[tp.status],
                "ignore_until": ls.astimezone(tz).isoformat() if ls else None,
                "ignore_until_label": fmt(ls, tz) if ls else None,
                "slack_label": fmt_duration(ls - p.now) if ls else None,
                "shortfall_hours": tp.shortfall_hours,
                "blocks": [fmt_span(b.start, b.end, tz) for b in tp.blocks],
            }
        )

    by_id = {t.id: t for t in tasks}
    today = p.now.astimezone(tz).date()
    days = []
    for i in range(14):
        d = today + timedelta(days=i)
        blocks = [b for b in p.blocks if b.start.astimezone(tz).date() == d]
        days.append(
            {
                "date": d.isoformat(),
                "label": f"{d:%a %d}",
                "month": f"{d:%b}",
                "sloth": not blocks,
                "blocks": [
                    {
                        "task_id": b.task_id,
                        "title": by_id[b.task_id].title,
                        "time": f"{b.start.astimezone(tz):%H:%M}–{b.end.astimezone(tz):%H:%M}",
                        "hours": b.hours,
                    }
                    for b in blocks
                ],
            }
        )

    events = [
        {
            "start": b.start,
            "end": b.end,
            "summary": f"Not Yet: {by_id[b.task_id].title}",
            "description": (
                f"Deadline: {fmt(by_id[b.task_id].deadline, tz)}. You put this off for as long as it was safe to."
                if by_id[b.task_id].deadline
                else ""
            ),
        }
        for b in p.blocks
    ]

    first = p.first_start
    return {
        "now": p.now.astimezone(tz).isoformat(),
        "tz": str(tz),
        "margin_hours": p.margin / timedelta(hours=1),
        "tasks": out_tasks,
        "days": days,
        "summary": {
            "first_start_label": fmt(first, tz) if first else None,
            "free_for_label": fmt_duration(first - p.now) if first else None,
            "work_hours": p.work_hours,
            "sloth_days": sum(1 for d in days if d["sloth"]),
            "do_now": sum(1 for t in out_tasks if t["status"] == "do_now"),
            "short": sum(1 for t in out_tasks if t["status"] == "short"),
        },
        "ics": ics.build(events, p.now),
    }


def run(
    llm,
    text: str,
    now: datetime,
    tz: str,
    av: Availability,
    margin: timedelta,
    tasks: list[ExtractedTask] | None = None,
    write_brief: bool = True,
) -> dict:
    meta: dict = {"model": getattr(llm, "label", None), "repairs": 0}
    if tasks is None:
        t0 = time.perf_counter()
        tasks, meta["repairs"] = extract(llm, text, now, tz)
        meta["extract_ms"] = int((time.perf_counter() - t0) * 1000)
    p = plan(to_schedule_tasks(tasks), av, now, tz, margin)
    out = present(p, tasks)
    lines, fallback = _facts(p, tasks)
    t0 = time.perf_counter()
    out["brief"], out["brief_source"] = brief_mod.write(llm if write_brief else None, lines, fallback)
    meta["brief_ms"] = int((time.perf_counter() - t0) * 1000)
    out["facts"] = lines
    out["meta"] = meta
    return out
