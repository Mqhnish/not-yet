from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import FakeLLM
from notyet.extract import ExtractError, build_prompt, extract, is_grounded, parse_json

NOW = datetime(2026, 10, 20, 9, 12, tzinfo=ZoneInfo("Asia/Singapore"))
TEXT = "history essay due friday 11:59pm, 2000 words, probably 4h. reply to mum about diwali plans. learn rust someday lol"

GOOD = {
    "tasks": [
        {"title": "Write history essay", "quote": "history essay due friday 11:59pm", "kind": "task",
         "deadline": "2026-10-23T23:59", "hours": 4, "estimate_from": "user", "tiny": False, "splittable": True},
        {"title": "Reply to mum", "quote": "reply to mum about diwali plans", "kind": "task",
         "deadline": None, "hours": 0.05, "estimate_from": "model", "tiny": True, "splittable": False},
        {"title": "Learn Rust", "quote": "learn rust someday", "kind": "task",
         "deadline": None, "hours": None, "estimate_from": "model", "tiny": False, "splittable": True},
    ]
}


def test_prompt_has_a_calendar_so_weekdays_resolve():
    p = build_prompt(TEXT, NOW, "Asia/Singapore")
    assert "Tue 20 Oct 2026 (2026-10-20) = today" in p
    assert "Fri 23 Oct 2026 (2026-10-23)" in p
    assert "Tue 20 Oct 2026, 09:12" in p


def test_parse_json_handles_fences_and_bare_lists():
    assert parse_json('```json\n{"tasks": []}\n```') == {"tasks": []}
    assert parse_json('Sure! [{"title": "x"}]'.replace("[", '{"tasks": [', 1).replace("]", "]}", 1))["tasks"][0]["title"] == "x"
    with pytest.raises(ExtractError):
        parse_json("no json here")


def test_extract_validates_and_normalises():
    tasks, repairs = extract(FakeLLM(GOOD), TEXT, NOW, "Asia/Singapore")
    assert repairs == 0
    essay, mum, rust = tasks
    assert essay.deadline == datetime(2026, 10, 23, 23, 59, tzinfo=ZoneInfo("Asia/Singapore"))
    assert essay.estimate_from == "you" and essay.hours == 4
    assert mum.tiny and mum.deadline is None
    assert rust.hours == 1.0  # unknown effort becomes 1 h instead of dropping the task
    assert all(t.grounded for t in tasks)


def test_bad_json_gets_one_repair_round():
    llm = FakeLLM('{"tasks": [ {"title": "Write essay",, } ]', GOOD)
    tasks, repairs = extract(llm, TEXT, NOW, "Asia/Singapore")
    assert repairs == 1 and len(tasks) == 3
    assert "could not be used" in llm.prompts[1]


def test_gives_up_after_the_repair_round():
    with pytest.raises(ExtractError):
        extract(FakeLLM("nope", "still nope"), TEXT, NOW, "Asia/Singapore")


def test_invented_tasks_are_flagged_not_trusted():
    data = {"tasks": [{"title": "Book flights to Bali", "quote": "book flights to bali", "deadline": None, "hours": 1}]}
    (t,), _ = extract(FakeLLM(data), TEXT, NOW, "Asia/Singapore")
    assert t.grounded is False


def test_grounding_tolerates_small_quote_drift():
    assert is_grounded("History essay due Friday, 11:59pm!", TEXT)
    assert is_grounded("reply to my mum about diwali plans", TEXT)
    assert not is_grounded("pay the electricity bill", TEXT)


def test_copied_date_words_beat_the_models_calendar_maths():
    data = {"tasks": [{"title": "Write history essay", "quote": "history essay due friday 11:59pm",
                       "when": "friday 11:59pm", "deadline": "2026-10-30T23:59", "hours": 4}]}
    (t,), _ = extract(FakeLLM(data), TEXT, NOW, "Asia/Singapore")
    assert t.deadline == datetime(2026, 10, 23, 23, 59, tzinfo=ZoneInfo("Asia/Singapore"))


def test_when_that_is_not_in_the_text_is_ignored():
    data = {"tasks": [{"title": "Write history essay", "quote": "history essay due friday 11:59pm",
                       "when": "monday 9am", "deadline": "2026-10-23T23:59", "hours": 4}]}
    (t,), _ = extract(FakeLLM(data), TEXT, NOW, "Asia/Singapore")
    assert t.deadline.day == 23


def test_a_before_earlier_in_the_clause_is_kept():
    from notyet.extract import with_clause_before
    text = "cancel the gym free trial before it charges me on the 28th!! file taxes by 30 nov"
    assert with_clause_before("28th", text) == "before 28th"
    assert with_clause_before("by 30 nov", text) == "by 30 nov"  # the earlier "before" is in another clause
    assert with_clause_before("before the 28th", text) == "before the 28th"
