import pytest
from fastapi.testclient import TestClient

from conftest import FakeLLM
from notyet import app as app_mod

TASKS = {
    "tasks": [
        {"title": "Write history essay", "quote": "history essay due friday 11:59pm", "kind": "task",
         "deadline": "2026-10-23T23:59", "hours": 4, "estimate_from": "you", "tiny": False, "splittable": True},
        {"title": "Pay phone bill", "quote": "pay phone bill before the 25th", "kind": "task",
         "deadline": "2026-10-25T17:00", "hours": 0.05, "estimate_from": "model", "tiny": True, "splittable": False},
    ]
}
BODY = {"text": "history essay due friday 11:59pm, probably 4h. pay phone bill before the 25th",
        "tz": "Asia/Singapore", "now": "2026-10-20T01:12:00Z"}


@pytest.fixture
def client(monkeypatch):
    fake = FakeLLM(TASKS, "Rest until Thu 22 Oct, 19:00.")
    monkeypatch.setattr(app_mod, "_llm", fake)
    app_mod._cache.clear()
    app_mod._hits.clear()
    return TestClient(app_mod.app), fake


def test_index_and_config(client):
    c, _ = client
    assert "Not Yet" in c.get("/").text
    cfg = c.get("/api/config").json()
    assert cfg["model"] == "fake-gemma" and "student" in cfg["samples"]


def test_plan_end_to_end(client):
    c, fake = client
    r = c.post("/api/plan", json=BODY)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["summary"]["first_start_label"] == "Thu 22 Oct, 19:00"
    assert out["brief"] == "Rest until Thu 22 Oct, 19:00." and out["brief_source"] == "model"
    status = {t["title"]: t["status"] for t in out["tasks"]}
    assert status == {"Write history essay": "scheduled", "Pay phone bill": "do_now"}
    assert out["ics"].startswith("BEGIN:VCALENDAR")
    assert len(fake.prompts) == 2  # one extraction, one brief


def test_same_text_same_day_is_cached(client):
    c, fake = client
    c.post("/api/plan", json=BODY)
    fake.replies = ["Rest."]
    out = c.post("/api/plan", json=BODY).json()
    assert out["meta"]["cached"] is True
    assert len(fake.prompts) == 3  # only the brief was re-written


def test_reschedule_needs_no_model(client):
    c, fake = client
    out = c.post("/api/plan", json=BODY).json()
    essay = next(t for t in out["tasks"] if t["title"] == "Write history essay")
    essay["hours"] = 8
    essay["deadline"] = "2026-10-23T23:59"  # naive, as a datetime-local input sends it
    before = len(fake.prompts)
    r = c.post("/api/reschedule", json={"tasks": out["tasks"], "tz": "Asia/Singapore", "now": BODY["now"]})
    assert r.status_code == 200, r.text
    new = next(t for t in r.json()["tasks"] if t["title"] == "Write history essay")
    assert new["ignore_until_label"] == "Wed 21 Oct, 19:00"
    assert len(fake.prompts) == before


def test_rejects_bad_input(client):
    c, _ = client
    assert c.post("/api/plan", json={**BODY, "tz": "Mars/Olympus"}).status_code == 400
    assert c.post("/api/plan", json={**BODY, "text": "x" * 5000}).status_code == 422
    assert c.post("/api/plan", json={**BODY, "hours": {"weekdays": ["7pm", "11pm"]}}).status_code == 400


def test_rate_limit(client, monkeypatch):
    c, fake = client
    monkeypatch.setattr(app_mod, "RATE", 2)
    fake.replies = [TASKS, "a", TASKS, "b", TASKS, "c"]
    codes = [c.post("/api/plan", json={**BODY, "text": BODY["text"] + "!" * i}).status_code for i in range(3)]
    assert codes == [200, 200, 429]


def test_rate_limit_ignores_spoofed_forwarded_for(client, monkeypatch):
    c, fake = client
    monkeypatch.setattr(app_mod, "RATE", 2)
    fake.replies = [TASKS, "a", TASKS, "b", TASKS, "c"]
    codes = [
        c.post("/api/plan", json={**BODY, "text": BODY["text"] + "!" * i},
               headers={"x-forwarded-for": f"10.0.0.{i}, 203.0.113.7"}).status_code
        for i in range(3)
    ]
    assert codes == [200, 200, 429]


def test_plan_without_a_model_is_a_clear_503(monkeypatch):
    for k in ("GEMINI_API_KEY", "LLM_BASE_URL", "NOTYET_BACKEND"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(app_mod, "_llm", None)
    app_mod._cache.clear()
    app_mod._hits.clear()
    r = TestClient(app_mod.app).post("/api/plan", json=BODY)
    assert r.status_code == 503 and "not configured" in r.json()["detail"]
