"""Deterministic date resolution for the phrases people actually type.

Small models read "friday 11:59pm" fine but sometimes put it on the wrong
week. So Gemma copies the words that say when, and this module does the
calendar maths. If a phrase is outside what it understands, it returns None
and the model's own answer is used instead.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

WEEKDAYS = {
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4, "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}
MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
    "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
PARTS_OF_DAY = {"morning": time(9), "afternoon": time(14), "evening": time(18), "tonight": time(21), "night": time(21)}
DEFAULT_TIME = time(17)

_WD = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
_ORD = r"(\d{1,2})(?:st|nd|rd|th)"


def _time_of(s: str) -> time | None:
    if m := re.search(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)\b", s):
        h, mi = int(m.group(1)) % 12, int(m.group(2) or 0)
        if m.group(3) == "pm":
            h += 12
        return time(h, mi) if h < 24 and mi < 60 else None
    if m := re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", s):
        return time(int(m.group(1)), int(m.group(2)))
    if re.search(r"\b(noon|midday)\b", s):
        return time(12)
    if re.search(r"\bmidnight\b", s):
        return time(23, 59)
    if re.search(r"\b(eod|end of (the )?day)\b", s):
        return DEFAULT_TIME
    for word, t in PARTS_OF_DAY.items():
        if re.search(rf"\b{word}\b", s):
            return t
    return None


def _month_day(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _date_of(s: str, today: date) -> date | None:
    if re.search(r"\bday after tomorrow\b", s):
        return today + timedelta(days=2)
    if re.search(r"\b(today|tonight|eod|end of (the )?day)\b", s):
        return today
    if re.search(r"\b(tomorrow|tmrw|tmr|tomorow)\b", s):
        return today + timedelta(days=1)
    if m := re.search(r"\bin (\d{1,2}) days?\b", s):
        return today + timedelta(days=int(m.group(1)))

    # "25 oct", "25th of october", "oct 25", "october 25th"
    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?(?: of)? ({_MON})\b", s) or re.search(rf"\b({_MON})\.? (\d{{1,2}})(?:st|nd|rd|th)?\b", s)
    if m:
        a, b = m.groups()
        day, mon = (int(a), MONTHS[b]) if a.isdigit() else (int(b), MONTHS[a])
        d = _month_day(today.year, mon, day)
        if d and d < today:
            d = _month_day(today.year + 1, mon, day)
        return d

    if m := re.search(rf"\b(?:(next|this|coming) )?({_WD})\b", s):
        target = WEEKDAYS[m.group(2)]
        if m.group(1) == "next":
            monday = today + timedelta(days=7 - today.weekday())
            return monday + timedelta(days=target)
        ahead = (target - today.weekday()) % 7 or 7
        return today + timedelta(days=ahead)

    # "the 25th", "before the 25th", "on the 28th"
    if m := re.search(rf"\bthe {_ORD}\b|\b{_ORD}\b", s):
        day = int(m.group(1) or m.group(2))
        d = _month_day(today.year, today.month, day)
        if d is None or d < today:
            y, mo = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
            d = _month_day(y, mo, day)
        return d
    return None


def resolve(phrase: str | None, now: datetime) -> datetime | None:
    """Turns "friday 11:59pm" into a datetime in now's time zone, or None if unsure."""
    if not phrase:
        return None
    s = re.sub(r"\s+", " ", phrase.lower().replace(",", " ")).strip()
    d = _date_of(s, now.date())
    if d is None:
        return None
    t = _time_of(s)
    if t is None and re.search(r"\bbefore\b", s):
        # "before the 25th" means it has to be done by the end of the 24th
        return datetime.combine(d - timedelta(days=1), time(23, 59), tzinfo=now.tzinfo)
    return datetime.combine(d, t or DEFAULT_TIME, tzinfo=now.tzinfo)
