import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from notyet.schedule import Availability, Task, free_slots, plan, slots_needed, sloth_days

SG = ZoneInfo("Asia/Singapore")
NOW = datetime(2026, 10, 20, 9, 0, tzinfo=SG)  # a Tuesday
EVENINGS = Availability.simple(("19:00", "23:00"), ("11:00", "18:00"))


def at(day, hh, mm=0):
    return datetime(2026, 10, day, hh, mm, tzinfo=SG)


def local(blocks):
    return [(b.start.astimezone(SG), b.end.astimezone(SG)) for b in blocks]


def test_single_task_goes_as_late_as_possible():
    # due Fri 23 Oct 23:59 with a 12 h margin -> must end by Fri 11:59, so the last evening before is Thu
    p = plan([Task("t1", "essay", 4, at(23, 23, 59))], EVENINGS, NOW, SG)
    assert p.tasks["t1"].status == "scheduled"
    assert local(p.tasks["t1"].blocks) == [(at(22, 19), at(22, 23))]


def test_zero_margin_uses_the_deadline_evening():
    p = plan([Task("t1", "essay", 4, at(23, 23, 59))], EVENINGS, NOW, SG, margin=timedelta(0))
    assert local(p.tasks["t1"].blocks) == [(at(23, 19), at(23, 23))]


def test_two_tasks_share_evenings_and_both_fit():
    tasks = [Task("a", "essay", 4, at(23, 23, 59)), Task("b", "slides", 6, at(23, 12))]
    p = plan(tasks, EVENINGS, NOW, SG, margin=timedelta(0))
    assert p.tasks["a"].status == p.tasks["b"].status == "scheduled"
    # the essay takes Fri evening; slides (due Fri noon) take Thu and Wed evenings
    assert local(p.tasks["a"].blocks) == [(at(23, 19), at(23, 23))]
    assert local(p.tasks["b"].blocks) == [(at(21, 21), at(21, 23)), (at(22, 19), at(22, 23))]


def test_tight_when_only_the_margin_has_room():
    # 8 h due Wed 21 Oct 23:00. With a 12 h margin it must end by Wed 11:00, and Tue evening only has 4 h
    p = plan([Task("t1", "x", 8, at(21, 23))], EVENINGS, NOW, SG)
    assert p.tasks["t1"].status == "tight"
    assert sum(b.hours for b in p.tasks["t1"].blocks) == 8


def test_short_reports_the_shortfall():
    p = plan([Task("t1", "x", 10, at(21, 23))], EVENINGS, NOW, SG)
    tp = p.tasks["t1"]
    assert tp.status == "short"
    assert tp.shortfall_hours == 2  # only 8 h of evenings exist before Wed 23:00


def test_one_sitting_tasks_get_a_contiguous_block():
    tasks = [Task("call", "bank call", 2, at(22, 23), splittable=False), Task("x", "other", 3, at(22, 23))]
    p = plan(tasks, EVENINGS, NOW, SG, margin=timedelta(0))
    blocks = p.tasks["call"].blocks
    assert len(blocks) == 1 and blocks[0].hours == 2


def test_statuses_for_things_that_are_not_scheduled():
    tasks = [
        Task("tiny", "pay bill", 0.05, at(25, 17), tiny=True),
        Task("some", "learn rust", 0, None),
        Task("ev", "tutorial", 0, at(21, 14), kind="event"),
        Task("late", "old", 1, at(19, 17)),
        Task("far", "passport", 1, NOW + timedelta(days=400)),
    ]
    p = plan(tasks, EVENINGS, NOW, SG)
    assert {k: v.status for k, v in p.tasks.items()} == {
        "tiny": "do_now", "some": "someday", "ev": "event", "late": "overdue", "far": "later",
    }
    assert p.blocks == []


def test_window_past_midnight():
    av = Availability.simple(("22:00", "01:00"), None)
    slots = free_slots(av, at(20, 21), at(21, 2), SG)
    assert [s.astimezone(SG) for s, _ in slots][0] == at(20, 22)
    assert len(slots) == 6


def test_dst_change_does_not_break_slots():
    ny = ZoneInfo("America/New_York")  # clocks go back on Sun 1 Nov 2026
    av = Availability.simple(None, ("00:00", "04:00"))
    start = datetime(2026, 10, 31, 12, tzinfo=ny)
    slots = free_slots(av, start, start + timedelta(days=2), ny)
    assert all(e - s == timedelta(minutes=30) for s, e in slots)


def test_sloth_days_counts_empty_days():
    p = plan([Task("t1", "essay", 4, at(23, 23, 59))], EVENINGS, NOW, SG)
    days = sloth_days(p, 7)
    assert at(22, 0).date() not in days and len(days) == 6


def _fits(tasks, av, now, tz):
    """Brute-force check: every deadline has enough free slots before it for all work due by then."""
    horizon = max(t.deadline for t in tasks)
    free = free_slots(av, now.astimezone(timezone.utc), horizon, tz)
    for d in sorted({t.deadline for t in tasks}):
        need = sum(slots_needed(t.hours) for t in tasks if t.deadline <= d)
        if sum(1 for s in free if s[1] <= d) < need:
            return False
    return True


def test_greedy_finds_a_fit_whenever_one_exists():
    rng = random.Random(7)
    for _ in range(300):
        tasks = [
            Task(f"t{i}", "x", rng.choice([0.5, 1, 1.5, 2, 3, 4, 6]), NOW + timedelta(hours=rng.randint(4, 24 * 6)))
            for i in range(rng.randint(1, 6))
        ]
        p = plan(tasks, EVENINGS, NOW, SG, margin=timedelta(0))
        all_fit = all(tp.status == "scheduled" for tp in p.tasks.values())
        assert all_fit == _fits(tasks, EVENINGS, NOW, SG)
        # and nothing is ever double-booked or placed after its deadline
        seen = set()
        for t in tasks:
            for b in p.tasks[t.id].blocks:
                assert b.end <= t.deadline
                s = b.start
                while s < b.end:
                    assert s not in seen
                    seen.add(s)
                    s += timedelta(minutes=30)
