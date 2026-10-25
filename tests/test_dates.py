from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from notyet.dates import resolve

SG = ZoneInfo("Asia/Singapore")
NOW = datetime(2026, 10, 20, 10, 45, tzinfo=SG)  # Tuesday


def d(month, day, hh=17, mm=0, year=2026):
    return datetime(year, month, day, hh, mm, tzinfo=SG)


@pytest.mark.parametrize(
    "phrase, expected",
    [
        ("friday 11:59pm", d(10, 23, 23, 59)),
        ("due friday", d(10, 23)),
        ("next tuesday 9am", d(10, 27, 9)),
        ("tuesday", d(10, 27)),  # a bare weekday means the next one, not today
        ("by thursday", d(10, 22)),
        ("sunday 8pm", d(10, 25, 20)),
        ("saturday morning", d(10, 24, 9)),
        ("wednesday 3pm", d(10, 21, 15)),
        ("before the 25th", d(10, 24, 23, 59)),
        ("before it charges me on the 28th", d(10, 27, 23, 59)),
        ("on the 5th", d(11, 5)),  # already past this month, so next month
        ("by 30 nov", d(11, 30)),
        ("1 nov", d(11, 1)),
        ("Oct 1st", d(10, 1, year=2027)),  # passed this year
        ("tomorrow noon", d(10, 21, 12)),
        ("tonight", d(10, 20, 21)),
        ("friday midnight", d(10, 23, 23, 59)),
        ("in 3 days", d(10, 23)),
        ("23:30 thu", d(10, 22, 23, 30)),
    ],
)
def test_resolves_common_phrases(phrase, expected):
    assert resolve(phrase, NOW) == expected


@pytest.mark.parametrize("phrase", ["someday", "in march", "they close at 5pm weekdays", "2000 words", "", None, "next week"])
def test_returns_none_when_unsure(phrase):
    assert resolve(phrase, NOW) is None
