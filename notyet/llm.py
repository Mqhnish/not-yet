"""Two ways to reach Gemma: Google's Gemini API, or any OpenAI-compatible server.

The second one covers local inference (Ollama, mlx_lm.server, llama.cpp,
LM Studio, vLLM) and hosted routers like OpenRouter, so swapping models is a
matter of changing three environment variables.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

import httpx

TIMEOUT = httpx.Timeout(120.0, connect=10.0)


class LLMError(RuntimeError):
    pass


class ModelMissing(LLMError):
    pass


@dataclass
class Usage:
    calls: int = 0
    ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Gemini:
    """Gemma through generativelanguage.googleapis.com (free key from Google AI Studio)."""

    api_key: str
    model: str = "gemma-4-26b-a4b-it"
    # tried in order if the current model is missing or retired on this API
    fallbacks: tuple[str, ...] = ("gemma-4-31b-it", "gemma-3-27b-it")
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    usage: Usage = field(default_factory=Usage)

    @property
    def label(self) -> str:
        return f"{self.model} via Google AI Studio"

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 2048, temperature: float = 0.2) -> str:
        while True:
            try:
                return self._complete(prompt, system, max_tokens, temperature)
            except ModelMissing as e:
                if not self.fallbacks:
                    raise LLMError(f"no Gemma model answered; last error: {e}")
                self.model, self.fallbacks = self.fallbacks[0], self.fallbacks[1:]

    def _complete(self, prompt: str, system: str | None, max_tokens: int, temperature: float) -> str:
        body: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        url = f"{self.base_url}/models/{self.model}:generateContent"
        data = _post(url, body, {"x-goog-api-key": self.api_key}, self.usage)
        if system and data.get("_status") == 400 and "instruction" in str(data).lower():
            # some Gemma versions on this API reject system instructions; fold it into the prompt
            body.pop("systemInstruction")
            body["contents"][0]["parts"][0]["text"] = f"{system}\n\n{prompt}"
            data = _post(url, body, {"x-goog-api-key": self.api_key}, self.usage)
        if data.get("_status") == 404 or (data.get("_status") == 400 and "not found" in str(data).lower()):
            raise ModelMissing(str(data.get("error"))[:200])
        _raise_for(data)
        meta = data.get("usageMetadata", {})
        self.usage.input_tokens += meta.get("promptTokenCount", 0)
        self.usage.output_tokens += meta.get("candidatesTokenCount", 0)
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError):
            raise LLMError(f"unexpected response: {str(data)[:300]}")
        return "".join(p.get("text", "") for p in parts if not p.get("thought"))


@dataclass
class OpenAICompat:
    """Any /v1/chat/completions server. Default: Ollama on this machine."""

    base_url: str = "http://localhost:11434/v1"
    model: str = "gemma4:e4b"
    api_key: str = "local"
    no_thinking: bool = True  # Gemma 4 thinks by default on some servers; it triples latency for no gain here
    usage: Usage = field(default_factory=Usage)

    @property
    def label(self) -> str:
        host = re.sub(r"^https?://", "", self.base_url).split("/")[0]
        where = "this machine" if host.startswith(("localhost", "127.0.0.1")) else host
        return f"{self.model} on {where}"

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 2048, temperature: float = 0.2) -> str:
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        body: dict = {"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if self.no_thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        data = _post(url, body, headers, self.usage)
        if self.no_thinking and data.get("_status") == 400 and "chat_template_kwargs" in str(data):
            body.pop("chat_template_kwargs")
            data = _post(url, body, headers, self.usage)
        _raise_for(data)
        u = data.get("usage") or {}
        self.usage.input_tokens += u.get("prompt_tokens", 0)
        self.usage.output_tokens += u.get("completion_tokens", 0)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError):
            raise LLMError(f"unexpected response: {str(data)[:300]}")
        return strip_thinking(text)


def strip_thinking(text: str) -> str:
    """Drop reasoning that some local servers leave in the message body."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"<\|channel\|?>thought.*?<channel\|>", "", text, flags=re.S)
    return text.strip()


def _post(url: str, body: dict, headers: dict, usage: Usage) -> dict:
    last = None
    for attempt in range(3):
        t0 = time.perf_counter()
        try:
            r = httpx.post(url, json=body, headers=headers, timeout=TIMEOUT)
        except httpx.HTTPError as e:
            r, last = None, e
        usage.calls += 1
        usage.ms += int((time.perf_counter() - t0) * 1000)
        if r is None:
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            time.sleep(2.0 * (attempt + 1))
            continue
        try:
            data = r.json()
        except ValueError:
            data = {"error": {"message": r.text[:300]}}
        if r.status_code >= 400:
            data = {"_status": r.status_code, **(data if isinstance(data, dict) else {"error": data})}
        return data
    raise LLMError(f"could not reach the model server: {last}")


def _raise_for(data: dict) -> None:
    if "_status" in data:
        err = data.get("error")
        msg = err.get("message") if isinstance(err, dict) else err
        raise LLMError(f"model server returned {data['_status']}: {str(msg)[:300]}")


def from_env() -> Gemini | OpenAICompat:
    backend = os.getenv("NOTYET_BACKEND", "").lower()
    if backend == "gemini" or (not backend and os.getenv("GEMINI_API_KEY")):
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise LLMError("NOTYET_BACKEND=gemini needs GEMINI_API_KEY")
        models = [m.strip() for m in os.getenv("GEMMA_MODEL", "").split(",") if m.strip()]
        if not models:
            return Gemini(api_key=key)
        return Gemini(api_key=key, model=models[0], fallbacks=tuple(models[1:]))
    if backend != "openai" and not os.getenv("LLM_BASE_URL"):
        raise LLMError("no model configured: set GEMINI_API_KEY, or LLM_BASE_URL for a local server")
    return OpenAICompat(
        base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434/v1"),
        model=os.getenv("LLM_MODEL", "gemma4:e4b"),
        api_key=os.getenv("LLM_API_KEY", "local"),
        no_thinking=os.getenv("LLM_THINKING", "off").lower() != "on",
    )
