"""Terminal version, handy for the fully offline setup:

    echo "essay due friday, 4h. pay rent by the 1st" | python -m notyet
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

from . import core, llm
from .schedule import Availability


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="notyet", description="Find the laziest schedule that still hits every deadline.")
    ap.add_argument("text", nargs="?", help="brain dump (default: read stdin)")
    ap.add_argument("--tz", default=_local_tz(), help="IANA time zone, e.g. Asia/Singapore")
    ap.add_argument("--weekdays", default="19:00-23:00", help='working hours Mon to Fri, or "off"')
    ap.add_argument("--weekends", default="11:00-18:00", help='working hours Sat and Sun, or "off"')
    ap.add_argument("--margin", type=float, default=12, help="finish this many hours before each deadline")
    ap.add_argument("--ics", help="also write the plan to this .ics file")
    a = ap.parse_args(argv)

    text = a.text or sys.stdin.read()
    def span(s: str):
        return None if s == "off" else tuple(s.split("-"))

    out = core.run(
        llm.from_env(), text, datetime.now(timezone.utc), a.tz,
        Availability.simple(span(a.weekdays), span(a.weekends)), timedelta(hours=a.margin),
    )
    s = out["summary"]
    print(f"\nRelax until {s['first_start_label']} ({s['free_for_label']} from now).\n" if s["first_start_label"] else "\nNothing needs scheduled work.\n")
    print(out["brief"], "\n")
    for t in out["tasks"]:
        when = f" -> start {t['ignore_until_label']}" if t["ignore_until_label"] else ""
        print(f"  [{t['status_text']}] {t['title']} ({t['hours']:g} h){when}")
    if a.ics:
        with open(a.ics, "w", encoding="utf-8", newline="") as f:
            f.write(out["ics"])
        print(f"\nWrote {a.ics}")
    print(f"\n{out['meta']['model']}, {out['meta'].get('extract_ms', 0) / 1000:.1f} s")
    return 0


def _local_tz() -> str:
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "UTC"


if __name__ == "__main__":
    raise SystemExit(main())
