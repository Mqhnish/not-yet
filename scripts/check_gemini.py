"""Check that your Google AI Studio key can reach Gemma, then plan one sample end to end.

    GEMINI_API_KEY=... python scripts/check_gemini.py
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from notyet import core, llm  # noqa: E402
from notyet.samples import SAMPLES  # noqa: E402
from notyet.schedule import Availability  # noqa: E402


def main() -> int:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        print("Set GEMINI_API_KEY first (https://aistudio.google.com/apikey).")
        return 1

    r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models", params={"pageSize": 1000},
                  headers={"x-goog-api-key": key}, timeout=30)
    if r.status_code != 200:
        print(f"Listing models failed ({r.status_code}): {r.text[:300]}")
        return 1
    gemma = sorted(m["name"].split("/", 1)[1] for m in r.json().get("models", []) if "gemma" in m["name"])
    print("Gemma models this key can see:", ", ".join(gemma) or "none")

    model = llm.from_env()
    print(f"Planning the '{SAMPLES['student']['label']}' sample with {model.label} ...")
    t0 = time.perf_counter()
    out = core.run(model, SAMPLES["student"]["text"], datetime.now(timezone.utc), "Asia/Singapore",
                   Availability.simple(("19:00", "23:00"), ("11:00", "18:00")), timedelta(hours=12))
    print(f"Done in {time.perf_counter() - t0:.1f} s with {model.label}.")
    print(f"Relax until: {out['summary']['first_start_label']} ({out['summary']['free_for_label']} from now)")
    print(f"Brief ({out['brief_source']}): {out['brief']}")
    for t in out["tasks"]:
        print(f"  [{t['status_text']}] {t['title']} | due {t['deadline_label'] or '-'} | {t['hours']:g} h")
    print(f"Tokens: {model.usage.input_tokens} in, {model.usage.output_tokens} out, {model.usage.calls} calls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
