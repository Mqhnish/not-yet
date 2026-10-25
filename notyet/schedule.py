"""Backwards scheduling: put every task as late as it can safely go.

The person says which hours they are willing to work. Each task has an effort
in hours and a deadline. We cut the working hours into 30 minute slots and,
starting with the task due last, give each task the latest free slots that
still end before its deadline minus a safety margin.

Run backwards in time, this is the classic "jobs with release dates, one
machine" problem, where any schedule that never idles while work is waiting
finishes as early as possible. Taking tasks latest-deadline first and filling
the latest free slots is such a schedule, so if every splittable task can fit,
this finds a fit. The tests check that against a brute-force capacity check.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

SLOT = timedelta(minutes=30)
MAX_HORIZON = timedelta(days=180)
TINY_HOURS = 5 / 60


@dataclass(frozen=True)
class Window:
    start: time
    end: time  # an end at or before the start means the window runs past midnight


@dataclass
class Availability:
    by_weekday: dict[int, list[Window]]  # 0 = Monday

    @classmethod
    def simple(cls, weekdays: tuple[str, str] | None, weekends: tuple[str, str] | None) -> "Availability":
        """One window for Mon to Fri and one for Sat and Sun, as "HH:MM" strings."""
        by_day: dict[int, list[Window]] = {d: [] for d in range(7)}
        for days, span in ((range(0, 5), weekdays), (range(5, 7), weekends)):
            if not span:
                continue
            w = Window(parse_hhmm(span[0]), parse_hhmm(span[1]))
            if w.start == w.end:
                continue
            for d in days:
                by_day[d].append(w)
        return cls(by_day)


@dataclass
class Task:
    id: str
    title: str
    hours: float
    deadline: datetime | None = None  # timezone aware
    splittable: bool = True
    tiny: bool = False
    kind: str = "task"  # "task" or "event"


@dataclass
class Block:
    task_id: str
    start: datetime  # UTC
    end: datetime

    @property
    def hours(self) -> float:
        return (self.end - self.start) / timedelta(hours=1)


@dataclass
class TaskPlan:
    task_id: str
    # scheduled: fits before deadline minus margin
    # tight:     only fits if you eat into the margin
    # short:     does not fit at all, shortfall_hours says by how much
    # do_now:    under five minutes, so just do it
    # someday:   no deadline
    # event:     happens at a fixed time, nothing to schedule
    # overdue:   deadline already passed
    # later:     deadline is more than 180 days away
    status: str
    blocks: list[Block] = field(default_factory=list)
    shortfall_hours: float = 0.0

    @property
    def latest_start(self) -> datetime | None:
        return min((b.start for b in self.blocks), default=None)


@dataclass
class Plan:
    now: datetime
    tz: ZoneInfo
    margin: timedelta
    tasks: dict[str, TaskPlan]
    blocks: list[Block]  # merged and in time order

    @property
    def first_start(self) -> datetime | None:
        return self.blocks[0].start if self.blocks else None

    @property
    def work_hours(self) -> float:
        return sum(b.hours for b in self.blocks)


def parse_hhmm(s: str) -> time:
    h, m = s.strip().split(":")
    h, m = int(h), int(m)
    if h == 24 and m == 0:
        return time(0, 0)
    return time(h, m)


def slots_needed(hours: float) -> int:
    return max(1, math.ceil(round(hours * 2, 6)))


def free_slots(av: Availability, start: datetime, end: datetime, tz: ZoneInfo) -> list[tuple[datetime, datetime]]:
    """All 30 minute slots inside the availability windows between start and end, in UTC, sorted."""
    out: list[tuple[datetime, datetime]] = []
    day = start.astimezone(tz).date() - timedelta(days=1)  # a window from yesterday can run past midnight
    last = end.astimezone(tz).date()
    while day <= last:
        for w in av.by_weekday.get(day.weekday(), []):
            ws = datetime.combine(day, w.start, tzinfo=tz).astimezone(timezone.utc)
            we_day = day if w.end > w.start else day + timedelta(days=1)
            we = datetime.combine(we_day, w.end, tzinfo=tz).astimezone(timezone.utc)
            t = ws
            while t + SLOT <= we:
                if t >= start and t + SLOT <= end:
                    out.append((t, t + SLOT))
                t += SLOT
        day += timedelta(days=1)
    out.sort()
    return out


def _pick(free: list[tuple[datetime, datetime]], taken: set[int], limit: datetime, need: int, splittable: bool) -> list[int] | None:
    cands = [i for i, s in enumerate(free) if i not in taken and s[1] <= limit]
    if len(cands) < need:
        return None
    if splittable:
        return cands[-need:]
    # needs one sitting: find the latest run of back-to-back free slots that is long enough
    run: list[int] = []
    for i in reversed(cands):
        if run and free[i][1] != free[run[-1]][0]:
            run = []
        run.append(i)
        if len(run) == need:
            return sorted(run)
    return None


def _merge(task_id: str, idxs: list[int], free: list[tuple[datetime, datetime]]) -> list[Block]:
    blocks: list[Block] = []
    for i in sorted(idxs):
        s, e = free[i]
        if blocks and blocks[-1].end == s:
            blocks[-1].end = e
        else:
            blocks.append(Block(task_id, s, e))
    return blocks


def plan(tasks: list[Task], av: Availability, now: datetime, tz: ZoneInfo | str, margin: timedelta = timedelta(hours=12)) -> Plan:
    tz = ZoneInfo(tz) if isinstance(tz, str) else tz
    now = now.astimezone(timezone.utc)
    result: dict[str, TaskPlan] = {}
    dated: list[Task] = []
    for t in tasks:
        if t.kind == "event":
            result[t.id] = TaskPlan(t.id, "event")
        elif t.tiny:
            result[t.id] = TaskPlan(t.id, "do_now")
        elif t.deadline is None:
            result[t.id] = TaskPlan(t.id, "someday")
        elif t.hours <= TINY_HOURS:
            result[t.id] = TaskPlan(t.id, "do_now")
        elif t.deadline <= now:
            result[t.id] = TaskPlan(t.id, "overdue")
        elif t.deadline - now > MAX_HORIZON:
            result[t.id] = TaskPlan(t.id, "later")
        else:
            dated.append(t)

    horizon = max((t.deadline for t in dated), default=now)
    free = free_slots(av, now, horizon, tz)
    taken: set[int] = set()

    for t in sorted(dated, key=lambda t: (t.deadline, t.id), reverse=True):
        need = slots_needed(t.hours)
        status, short = "scheduled", 0.0
        idxs = _pick(free, taken, t.deadline - margin, need, t.splittable)
        if idxs is None:
            status = "tight"
            idxs = _pick(free, taken, t.deadline, need, t.splittable)
        if idxs is None:
            status = "short"
            idxs = [i for i, s in enumerate(free) if i not in taken and s[1] <= t.deadline]
            short = (need - len(idxs)) / 2
        taken.update(idxs)
        result[t.id] = TaskPlan(t.id, status, _merge(t.id, idxs, free), short)

    blocks = sorted((b for p in result.values() for b in p.blocks), key=lambda b: b.start)
    return Plan(now, tz, margin, {t.id: result[t.id] for t in tasks}, blocks)


def sloth_days(p: Plan, days: int = 14) -> list[date]:
    """Days in the next two weeks with no work at all."""
    today = p.now.astimezone(p.tz).date()
    busy = {b.start.astimezone(p.tz).date() for b in p.blocks}
    return [today + timedelta(days=i) for i in range(days) if today + timedelta(days=i) not in busy]
