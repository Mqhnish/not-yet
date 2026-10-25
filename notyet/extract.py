"""Brain dump in, structured tasks out.

Gemma does the reading. Python does the checking: the JSON has to parse,
every field has to make sense, and every task has to quote the words it came
from. A task whose quote is not in the brain dump gets flagged in the UI
instead of silently trusted.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .dates import resolve

SYSTEM = (
    "You turn a messy to-do brain dump into structured tasks. "
    "You never invent tasks, never give advice, and reply with JSON only."
)

PROMPT = """Current local time: {now} ({tz})

Calendar for resolving dates:
{calendar}

A weekday name on its own ("friday") means the first matching day after today in this calendar.
"Next week" means the week starting on the Monday after this one.

Brain dump:
<<<
{text}
>>>

Return {{"tasks": [...]}} with one object per thing this person has to do:
- "title": short imperative, at most 8 words, in their words where possible.
- "quote": copy 3 to 12 words from the brain dump that this task comes from. Copy exactly. Do not paraphrase.
- "when": copy the words that say when it is due or happens, exactly as written, keeping words like "by" or "before" in front (like "friday 11:59pm", "by monday" or "before the rent is due on the 1st"), or null.
- "kind": "event" if it happens at a fixed time and needs no prep work (a class, a dinner, an appointment), otherwise "task".
- "deadline": "YYYY-MM-DDTHH:MM" in local time, or null if no date is given. If a date has no time, use 17:00. For an event, use the time it starts.
- "hours": total hands-on effort in hours, not the time until the deadline. Use their estimate if they gave one.
- "estimate_from": "you" if they gave the estimate, otherwise "model".
- "tiny": true if it takes under 5 minutes (a quick reply, a payment, a booking).
- "splittable": false if it has to happen in one sitting (a call, an exam, a trip to a shop), otherwise true.

Example of the shape (not of the content):
{{"tasks": [{{"title": "Water the plants", "quote": "plants are dying, water them by sunday", "when": "by sunday", "kind": "task", "deadline": "2030-01-06T17:00", "hours": 0.25, "estimate_from": "model", "tiny": false, "splittable": true}}]}}
"""

REPAIR = """Your last reply could not be used: {error}

Reply again with only the corrected JSON object, nothing else."""


class ExtractError(ValueError):
    pass


@dataclass
class ExtractedTask:
    id: str
    title: str
    quote: str
    grounded: bool
    kind: str
    deadline: datetime | None
    when: str | None
    hours: float
    estimate_from: str
    tiny: bool
    splittable: bool

    def to_json(self) -> dict:
        d = asdict(self)
        d["deadline"] = self.deadline.isoformat() if self.deadline else None
        return d


def calendar_lines(now: datetime, days: int = 21) -> str:
    today = now.date()
    out = []
    for i in range(days):
        d = today + timedelta(days=i)
        tag = " = today" if i == 0 else " = tomorrow" if i == 1 else ""
        out.append(f"{d:%a %d %b %Y} ({d.isoformat()}){tag}")
    return "\n".join(out)


def build_prompt(text: str, now: datetime, tz: str) -> str:
    local = now.astimezone(ZoneInfo(tz))
    return PROMPT.format(now=f"{local:%a %d %b %Y, %H:%M}", tz=tz, calendar=calendar_lines(local), text=text.strip())


def parse_json(reply: str) -> dict:
    s = reply.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        raise ExtractError("there was no JSON object in it")
    try:
        data = json.loads(s[start : end + 1])
    except json.JSONDecodeError as e:
        raise ExtractError(f"the JSON did not parse ({e.msg} at character {e.pos})")
    if isinstance(data, list):
        data = {"tasks": data}
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise ExtractError('the object needs a "tasks" list')
    return data


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def is_grounded(quote: str, text: str) -> bool:
    """True if the quote really appears in the brain dump (ignoring case and punctuation)."""
    q = _norm(quote)
    if len(q) < 3:
        return False
    if q in _norm(text):
        return True
    # small models sometimes drop or add a word; accept 80% of the quote's words appearing in order
    words, hay = q.split(), _norm(text).split()
    i = hits = 0
    for w in words:
        try:
            i = hay.index(w, i) + 1
            hits += 1
        except ValueError:
            pass  # a word the model added; skip it without losing our place
    return hits >= max(2, round(0.8 * len(words)))


def with_clause_before(when: str, text: str) -> str:
    """Puts back a "before" the model dropped: "...trial before it charges me on the 28th" -> "before 28th"."""
    if re.search(r"\bbefore\b", when, re.I):
        return when
    i = text.lower().find(when.lower())
    if i == -1:
        return when
    clause = re.split(r"[.!?;\n]", text[:i])[-1]
    return f"before {when}" if re.search(r"\bbefore\b", clause, re.I) else when


def _bool(v, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1")
    return default


def validate(data: dict, text: str, tz: str, now: datetime | None = None) -> list[ExtractedTask]:
    zone = ZoneInfo(tz)
    local_now = (now or datetime.now(zone)).astimezone(zone)
    tasks: list[ExtractedTask] = []
    problems: list[str] = []
    for n, raw in enumerate(data["tasks"], 1):
        if not isinstance(raw, dict):
            problems.append(f"task {n} is not an object")
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            problems.append(f"task {n} has no title")
            continue
        deadline = None
        dl = raw.get("deadline")
        if dl not in (None, "", "null"):
            try:
                deadline = datetime.fromisoformat(str(dl).strip().replace("Z", "+00:00"))
            except ValueError:
                problems.append(f'task {n} ("{title}") has a deadline that is not YYYY-MM-DDTHH:MM: {dl!r}')
                continue
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=zone)
        when = str(raw.get("when") or "").strip() or None
        if when and is_grounded(when, text):
            # the model copied the date words; do the calendar maths ourselves
            deadline = resolve(with_clause_before(when, text), local_now) or deadline
        kind = "event" if str(raw.get("kind", "task")).lower() == "event" else "task"
        try:
            hours = float(raw.get("hours"))
        except (TypeError, ValueError):
            # unknown effort: assume an hour rather than drop the task
            hours = 0.0 if kind == "event" else 0.05 if _bool(raw.get("tiny"), False) else 1.0
        hours = min(max(hours, 0.0), 200.0)
        quote = str(raw.get("quote") or "").strip()
        tasks.append(
            ExtractedTask(
                id=f"t{len(tasks) + 1}",
                title=title[:120],
                quote=quote[:200],
                grounded=is_grounded(quote, text) or is_grounded(title, text),
                kind=kind,
                deadline=deadline,
                when=when,
                hours=round(hours, 2),
                estimate_from="you" if str(raw.get("estimate_from", "")).lower() in ("you", "user", "person") else "model",
                tiny=_bool(raw.get("tiny"), hours <= 5 / 60),
                splittable=_bool(raw.get("splittable"), True),
            )
        )
    if problems and not tasks:
        raise ExtractError("; ".join(problems))
    return tasks


def extract(llm, text: str, now: datetime, tz: str, max_repairs: int = 1) -> tuple[list[ExtractedTask], int]:
    """Returns the tasks and how many repair rounds it took."""
    prompt = build_prompt(text, now, tz)
    reply = llm.complete(prompt, system=SYSTEM, max_tokens=3000)
    for attempt in range(max_repairs + 1):
        try:
            return validate(parse_json(reply), text, tz, now), attempt
        except ExtractError as e:
            if attempt == max_repairs:
                raise
            reply = llm.complete(
                f"{prompt}\n\nYour reply was:\n{reply[:4000]}\n\n{REPAIR.format(error=e)}", system=SYSTEM, max_tokens=3000
            )
    raise AssertionError("unreachable")
