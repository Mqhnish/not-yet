from datetime import datetime, timedelta, timezone

from conftest import FakeLLM
from notyet import brief, ics

FACTS = ["Now: Tue 20 Oct, 09:12", "Nothing is scheduled until Thu 22 Oct, 19:00", 'First work block: "Essay", Thu 22 Oct, 19:00–23:00']


def test_brief_from_model_is_kept_when_it_sticks_to_the_facts():
    text, src = brief.write(FakeLLM("Rest until Thu 22 Oct, 19:00. Then the essay."), FACTS, {"first": "Thu 22 Oct, 19:00"})
    assert src == "model" and text.startswith("Rest until")


def test_brief_with_invented_time_falls_back_to_template():
    text, src = brief.write(FakeLLM("Start the essay at 08:30 tomorrow."), FACTS, {"first": "Thu 22 Oct, 19:00"})
    assert src == "template"
    assert "Thu 22 Oct, 19:00" in text


def test_brief_check_rejects_long_rambles():
    assert brief.check("word " * 120, FACTS) == "too long"


def test_ics_is_valid_enough_for_calendar_apps():
    start = datetime(2026, 10, 22, 11, 0, tzinfo=timezone.utc)
    out = ics.build([{"start": start, "end": start + timedelta(hours=4), "summary": "Not Yet: essay, part 1; draft",
                      "description": "Deadline: Fri 23 Oct, 23:59. " + "x" * 120}], start)
    lines = out.split("\r\n")
    assert lines[0] == "BEGIN:VCALENDAR" and out.endswith("END:VCALENDAR\r\n")
    assert "DTSTART:20261022T110000Z" in lines and "DTEND:20261022T150000Z" in lines
    assert r"SUMMARY:Not Yet: essay\, part 1\; draft" in lines
    assert all(len(l.encode()) <= 75 for l in lines)
    assert any(l.startswith(" ") for l in lines)  # long description was folded
