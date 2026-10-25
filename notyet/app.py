"""Web app: one page, two JSON endpoints."""
from __future__ import annotations

import hashlib
import logging
import os
import time
from collections import OrderedDict, defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import core, llm as llm_mod
from .extract import ExtractedTask, ExtractError
from .samples import SAMPLES
from .schedule import Availability

log = logging.getLogger("notyet")
STATIC = Path(__file__).parent / "static"
MAX_TEXT = 3000
RATE = int(os.getenv("NOTYET_RATE_PER_10MIN", "12"))

app = FastAPI(title="Not Yet", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_llm = None
_cache: OrderedDict[str, list[ExtractedTask]] = OrderedDict()
_hits: dict[str, deque] = defaultdict(deque)


def get_llm():
    global _llm
    if _llm is None:
        _llm = llm_mod.from_env()
    return _llm


class Hours(BaseModel):
    weekdays: list[str] | None = Field(default=["19:00", "23:00"])
    weekends: list[str] | None = Field(default=["11:00", "18:00"])


class PlanIn(BaseModel):
    text: str = Field(min_length=3, max_length=MAX_TEXT)
    tz: str = "UTC"
    now: datetime | None = None
    hours: Hours = Hours()
    margin_hours: float = Field(default=12, ge=0, le=72)


class TaskIn(BaseModel):
    id: str
    title: str = Field(max_length=120)
    quote: str = ""
    grounded: bool = True
    kind: str = "task"
    deadline: datetime | None = None
    when: str | None = None
    hours: float = Field(ge=0, le=200)
    estimate_from: str = "you"
    tiny: bool = False
    splittable: bool = True


class RescheduleIn(BaseModel):
    tasks: list[TaskIn] = Field(max_length=60)
    tz: str = "UTC"
    now: datetime | None = None
    hours: Hours = Hours()
    margin_hours: float = Field(default=12, ge=0, le=72)


def _zone(name: str) -> str:
    try:
        ZoneInfo(name)
        return name
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(400, f"unknown time zone {name!r}")


def _availability(h: Hours) -> Availability:
    try:
        return Availability.simple(tuple(h.weekdays) if h.weekdays else None, tuple(h.weekends) if h.weekends else None)
    except (ValueError, TypeError):
        raise HTTPException(400, "working hours must look like 19:00")


def _now(n: datetime | None) -> datetime:
    if n is None:
        return datetime.now(timezone.utc)
    return n if n.tzinfo else n.replace(tzinfo=timezone.utc)


def _limit(request: Request) -> None:
    # the right-most X-Forwarded-For entry is the one our host's proxy added; earlier ones are client-controlled
    fwd = request.headers.get("x-forwarded-for")
    ip = fwd.split(",")[-1].strip() if fwd else (request.client.host if request.client else "?")
    now = time.monotonic()
    for k in [k for k, v in _hits.items() if not v or now - v[-1] > 600]:
        del _hits[k]
    q = _hits[ip]
    while q and now - q[0] > 600:
        q.popleft()
    if len(q) >= RATE:
        raise HTTPException(429, "The sloth needs a breather. Try again in a few minutes.")
    q.append(now)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/config")
def config():
    try:
        label = get_llm().label
    except llm_mod.LLMError as e:
        label = f"not configured: {e}"
    return {"model": label, "samples": SAMPLES, "max_text": MAX_TEXT}


@app.post("/api/plan")
def make_plan(body: PlanIn, request: Request):
    tz, now = _zone(body.tz), _now(body.now)
    av = _availability(body.hours)
    try:
        llm = get_llm()
    except llm_mod.LLMError as e:
        raise HTTPException(503, f"The model is not configured: {e}")
    key = hashlib.sha256(f"{getattr(llm, 'label', '')}|{tz}|{now.astimezone(ZoneInfo(tz)).date()}|{body.text.strip()}".encode()).hexdigest()
    cached = _cache.get(key)
    if cached is None:
        _limit(request)
    try:
        out = core.run(llm, body.text, now, tz, av, timedelta(hours=body.margin_hours), tasks=cached)
    except ExtractError as e:
        raise HTTPException(422, f"Gemma's answer didn't make sense even after a retry ({e}). Try rewording a little.")
    except llm_mod.LLMError as e:
        log.warning("model error: %s", e)
        raise HTTPException(503, f"The model is unavailable right now: {e}")
    if cached is None:
        _cache[key] = [_task_from(t, tz) for t in out["tasks"]]
        while len(_cache) > 256:
            _cache.popitem(last=False)
    out["meta"]["cached"] = cached is not None
    return JSONResponse(out)


@app.post("/api/reschedule")
def reschedule(body: RescheduleIn):
    """Re-plan edited tasks or new working hours. No model call, so it is instant."""
    tz, now = _zone(body.tz), _now(body.now)
    tasks = [_task_from(t.model_dump(), tz) for t in body.tasks]
    out = core.run(None, "", now, tz, _availability(body.hours), timedelta(hours=body.margin_hours), tasks=tasks, write_brief=False)
    return JSONResponse(out)


def _task_from(d: dict, tz: str) -> ExtractedTask:
    dl = d.get("deadline")
    if isinstance(dl, str):
        dl = datetime.fromisoformat(dl)
    if dl is not None and dl.tzinfo is None:
        dl = dl.replace(tzinfo=ZoneInfo(tz))  # values from a datetime-local input are wall-clock times
    return ExtractedTask(
        id=d["id"],
        title=d["title"],
        quote=d.get("quote", ""),
        grounded=d.get("grounded", True),
        kind=d.get("kind", "task"),
        deadline=dl,
        when=d.get("when"),
        hours=float(d["hours"]),
        estimate_from=d.get("estimate_from", "you"),
        tiny=bool(d.get("tiny", False)),
        splittable=bool(d.get("splittable", True)),
    )
