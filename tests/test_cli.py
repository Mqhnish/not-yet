import pytest

from conftest import FakeLLM
from notyet import __main__ as cli

TASKS = {"tasks": [{"title": "Write essay", "quote": "essay due friday, 4h", "kind": "task", "deadline": "2030-01-04T17:00",
                    "hours": 4, "estimate_from": "you", "tiny": False, "splittable": True}]}


def test_tz_from_env(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Singapore")
    assert cli._local_tz() == "Asia/Singapore"


def test_no_zone_falls_back_to_utc_and_says_so(monkeypatch, capsys):
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(cli.os.path, "realpath", lambda p: r"C:\etc\localtime")  # what Windows gives back
    assert cli._local_tz() == "UTC"
    assert "--tz" in capsys.readouterr().err


def test_explicit_tz_skips_detection(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_local_tz", lambda: pytest.fail("should not look up the local zone"))
    monkeypatch.setattr(cli.llm, "from_env", lambda: FakeLLM(TASKS, "Rest."))
    assert cli.main(["essay due friday, 4h", "--tz", "Asia/Singapore"]) == 0
    assert "Write essay" in capsys.readouterr().out
