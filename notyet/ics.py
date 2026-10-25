"""A minimal iCalendar writer, so the plan can go straight into any calendar app."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545: lines longer than 75 octets continue on the next line after a space."""
    out, cur = [], b""
    for ch in line:
        b = ch.encode()
        if len(cur) + len(b) > 75:
            out.append(cur.decode())
            cur = b" "
        cur += b
    out.append(cur.decode())
    return "\r\n".join(out)


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build(events: list[dict], stamp: datetime) -> str:
    """events: dicts with start, end (aware datetimes), summary, description."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Not Yet//Lazy planner//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    for e in events:
        uid = hashlib.sha1(f"{e['summary']}|{_ts(e['start'])}".encode()).hexdigest()[:16]
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}@notyet",
            f"DTSTAMP:{_ts(stamp)}",
            f"DTSTART:{_ts(e['start'])}",
            f"DTEND:{_ts(e['end'])}",
            f"SUMMARY:{_esc(e['summary'])}",
            f"DESCRIPTION:{_esc(e.get('description', ''))}",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_esc(e['summary'])}",
            "TRIGGER:-PT10M",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"
