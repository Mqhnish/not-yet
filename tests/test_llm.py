import httpx
import pytest

from notyet import llm


class Recorder:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        status, body = self.responses.pop(0)
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))


def test_gemini_request_shape_and_skips_thought_parts(monkeypatch):
    rec = Recorder((200, {
        "candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True}, {"text": '{"tasks": []}'}]}}],
        "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 8},
    }))
    monkeypatch.setattr(llm.httpx, "post", rec)
    g = llm.Gemini(api_key="k", model="gemma-4-26b-a4b-it")
    assert g.complete("hi", system="sys") == '{"tasks": []}'
    call = rec.calls[0]
    assert call["url"].endswith("/models/gemma-4-26b-a4b-it:generateContent")
    assert call["headers"]["x-goog-api-key"] == "k"
    assert call["json"]["systemInstruction"]["parts"][0]["text"] == "sys"
    assert g.usage.input_tokens == 120 and g.usage.output_tokens == 8


def test_gemini_folds_system_prompt_in_when_rejected(monkeypatch):
    rec = Recorder(
        (400, {"error": {"message": "Developer instruction is not enabled for this model"}}),
        (200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}),
    )
    monkeypatch.setattr(llm.httpx, "post", rec)
    assert llm.Gemini(api_key="k").complete("hi", system="sys") == "ok"
    retry = rec.calls[1]["json"]
    assert "systemInstruction" not in retry and retry["contents"][0]["parts"][0]["text"].startswith("sys")


def test_openai_compat_turns_thinking_off_and_strips_leftovers(monkeypatch):
    rec = Recorder((200, {"choices": [{"message": {"content": "<think>hmm</think>{\"tasks\": []}"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}))
    monkeypatch.setattr(llm.httpx, "post", rec)
    o = llm.OpenAICompat(base_url="http://localhost:11434/v1", model="gemma4:e4b")
    assert o.complete("hi") == '{"tasks": []}'
    assert rec.calls[0]["json"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert o.label == "gemma4:e4b on this machine"


def test_server_errors_are_retried_then_raised(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    rec = Recorder(*[(503, {"error": {"message": "overloaded"}})] * 3)
    monkeypatch.setattr(llm.httpx, "post", rec)
    with pytest.raises(llm.LLMError, match="503"):
        llm.Gemini(api_key="k").complete("hi")
    assert len(rec.calls) == 3


def test_from_env_picks_gemini_when_a_key_is_set(monkeypatch):
    monkeypatch.delenv("NOTYET_BACKEND", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert isinstance(llm.from_env(), llm.Gemini)
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(llm.LLMError, match="no model configured"):
        llm.from_env()
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    assert isinstance(llm.from_env(), llm.OpenAICompat)


def test_gemini_falls_back_when_a_model_is_missing(monkeypatch):
    rec = Recorder(
        (404, {"error": {"message": "models/gemma-4-26b-a4b-it is not found for API version v1beta"}}),
        (200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}),
    )
    monkeypatch.setattr(llm.httpx, "post", rec)
    g = llm.Gemini(api_key="k")
    assert g.complete("hi") == "ok"
    assert rec.calls[1]["url"].endswith("/models/gemma-4-31b-it:generateContent")
    assert g.label == "gemma-4-31b-it via Google AI Studio"


def test_gemini_gives_up_when_every_model_is_missing(monkeypatch):
    rec = Recorder(*[(404, {"error": {"message": "not found"}})] * 2)
    monkeypatch.setattr(llm.httpx, "post", rec)
    with pytest.raises(llm.LLMError, match="no Gemma model answered"):
        llm.Gemini(api_key="k", model="a", fallbacks=("b",)).complete("hi")


def test_gemma_model_env_is_a_comma_separated_preference_list(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMMA_MODEL", "gemma-4-31b-it, gemma-3-27b-it")
    g = llm.from_env()
    assert g.model == "gemma-4-31b-it" and g.fallbacks == ("gemma-3-27b-it",)
