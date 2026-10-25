"""Measure extraction on the three sample brain dumps against hand labels.

    LLM_BASE_URL=http://127.0.0.1:8089/v1 LLM_MODEL=mlx-community/gemma-4-e4b-it-4bit python scripts/bench.py --runs 5

Reports, per run and in total:
  recall          expected tasks that Gemma found (matched by keyword)
  dates (model)   deadlines right using Gemma's own calendar maths
  dates (final)   deadlines right after notyet/dates.py resolves the copied words
  tiny            two-minute jobs correctly marked tiny
  grounded        extracted tasks whose quote is really in the text
  seconds         wall time of the model call
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from notyet import llm as llm_mod  # noqa: E402
from notyet.extract import SYSTEM, build_prompt, parse_json, validate  # noqa: E402
from notyet.samples import SAMPLES  # noqa: E402

TZ = "Asia/Singapore"
NOW = datetime(2026, 10, 20, 9, 0, tzinfo=ZoneInfo(TZ))  # a Tuesday morning


def at(m, d, hh=17, mm=0):
    return datetime(2026, m, d, hh, mm, tzinfo=ZoneInfo(TZ))


ANY = "any"  # deadline not scored (genuinely ambiguous in the text)
# key words to find the task, then accepted deadlines (None = no deadline, a date = any time that day)
LABELS = {
    "student": [
        (["essay"], [at(10, 23, 23, 59)], False),
        (["algorithm", "problem set"], [at(10, 27, 9)], False),
        (["mum"], [None], True),
        (["library"], [at(10, 22)], False),
        (["slides"], [at(10, 25, 20)], False),
        (["phone bill"], [at(10, 24, 23, 59), at(10, 25, 0), at(10, 24)], True),
        (["tutorial"], [at(10, 21, 14)], False),
        (["rust"], [None], False),
    ],
    "adulting": [
        (["gym"], [at(10, 27, 23, 59), at(10, 28, 0), at(10, 27)], None),
        (["passport"], ANY, False),
        (["tax"], [at(11, 30), at(11, 30, 23, 59)], False),
        (["table", "birthday"], ANY, None),
        (["tap", "washer"], [None], False),
        (["fridge"], [at(10, 24).date()], False),
        (["bank"], [None], False),
    ],
    "freelance": [
        (["logo"], [at(10, 21, 15)], False),
        (["invoice"], [at(11, 1)], None),
        (["blog"], [at(10, 23)], False),
        (["sam"], [None], True),
        (["client call", "call thursday", "thursday 11am"], [at(10, 22, 11)], False),
        (["portfolio"], [None], False),
        (["back up", "backup", "laptop"], [None], False),
    ],
}


def _ok(got, accepted) -> bool:
    if accepted == ANY:
        return True
    for a in accepted:
        if a is None and got is None:
            return True
        if got is None or a is None:
            continue
        if isinstance(a, datetime) and got == a:
            return True
        if not isinstance(a, datetime) and got.astimezone(ZoneInfo(TZ)).date() == a:
            return True
    return False


def _raw_deadline(raw: dict):
    dl = raw.get("deadline")
    if dl in (None, "", "null"):
        return None
    try:
        d = datetime.fromisoformat(str(dl))
    except ValueError:
        return "unparseable"
    return d if d.tzinfo else d.replace(tzinfo=ZoneInfo(TZ))


def run_once(llm, key: str) -> dict:
    text = SAMPLES[key]["text"]
    t0 = time.perf_counter()
    reply = llm.complete(build_prompt(text, NOW, TZ), system=SYSTEM, max_tokens=3000)
    secs = time.perf_counter() - t0
    try:
        data = parse_json(reply)
    except Exception as e:  # count a parse failure as finding nothing
        return {"sample": key, "seconds": secs, "error": str(e), "expected": len(LABELS[key]), "found": 0,
                "dates_scored": 0, "dates_model": 0, "dates_final": 0, "tiny_scored": 0, "tiny_ok": 0, "extracted": 0, "grounded": 0}
    tasks = validate(data, text, TZ, NOW)
    raws = {str(r.get("title") or "").strip()[:120]: r for r in data["tasks"] if isinstance(r, dict)}
    row = {"sample": key, "seconds": round(secs, 1), "expected": len(LABELS[key]), "found": 0, "dates_scored": 0,
           "dates_model": 0, "dates_final": 0, "tiny_scored": 0, "tiny_ok": 0,
           "extracted": len(tasks), "grounded": sum(t.grounded for t in tasks), "misses": []}
    for keys, accepted, tiny in LABELS[key]:
        idx = next((i for i, t in enumerate(tasks) if any(k in f"{t.title} {t.quote} {t.when or ''}".lower() for k in keys)), None)
        if idx is None:
            row["misses"].append(keys[0])
            continue
        row["found"] += 1
        if accepted != ANY:
            row["dates_scored"] += 1
            row["dates_model"] += _ok(_raw_deadline(raws.get(tasks[idx].title, {})), accepted)
            row["dates_final"] += _ok(tasks[idx].deadline, accepted)
        if tiny is not None:
            row["tiny_scored"] += 1
            row["tiny_ok"] += tasks[idx].tiny == tiny
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "docs" / "bench-results.json"))
    a = ap.parse_args()
    llm = llm_mod.from_env()
    rows = []
    for r in range(a.runs):
        for key in SAMPLES:
            row = run_once(llm, key)
            rows.append(row)
            print(json.dumps(row), flush=True)
    tot = {k: sum(r[k] for r in rows) for k in ("expected", "found", "dates_scored", "dates_model", "dates_final", "tiny_scored", "tiny_ok", "extracted", "grounded")}
    secs = sorted(r["seconds"] for r in rows)
    summary = {
        "model": llm.label,
        "runs": a.runs,
        "calls": len(rows),
        "recall": f"{tot['found']}/{tot['expected']}",
        "dates_model": f"{tot['dates_model']}/{tot['dates_scored']}",
        "dates_final": f"{tot['dates_final']}/{tot['dates_scored']}",
        "tiny": f"{tot['tiny_ok']}/{tot['tiny_scored']}",
        "grounded": f"{tot['grounded']}/{tot['extracted']}",
        "median_seconds": secs[len(secs) // 2],
        "min_seconds": secs[0],
        "max_seconds": secs[-1],
        "date": datetime.now().isoformat(timespec="minutes"),
    }
    print("\n" + json.dumps(summary, indent=2))
    Path(a.out).write_text(json.dumps({"summary": summary, "rows": rows}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
