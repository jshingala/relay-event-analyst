"""Local LLM clients. Offline by design: only loopback hosts are accepted.

Two backends:
* ``ollama``  - Ollama's /api/chat with a JSON-schema ``format`` (constrained decoding).
* ``openai``  - any OpenAI-compatible server on localhost, e.g. llama.cpp's
  ``llama-server`` or ``python -m llama_cpp.server``, using ``response_format``.

Requests bypass HTTP(S)_PROXY settings so a loopback call can never be routed
through a proxy. Set REA_ALLOW_REMOTE_LLM=1 to deliberately lift the loopback rule.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

LOOPBACK = {"localhost", "127.0.0.1", "::1"}


class LLMError(RuntimeError):
    pass


class OfflineViolation(LLMError):
    pass


@dataclass
class LLMResponse:
    text: str
    backend: str
    model: str
    seconds: float  # wall clock for the whole request
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    tokens_per_s: float | None = None  # generation speed
    load_s: float | None = None  # model load (cold start), when the server reports it
    prompt_s: float | None = None  # prompt processing
    gen_s: float | None = None  # token generation


class LLMClient(Protocol):
    backend: str
    model: str

    def chat(self, messages: list[dict], schema: dict | None = None) -> LLMResponse: ...

    def ping(self) -> tuple[bool, str]: ...


def ensure_loopback(url: str, allow_remote: bool | None = None) -> None:
    if allow_remote is None:
        allow_remote = os.environ.get("REA_ALLOW_REMOTE_LLM") == "1"
    host = urllib.parse.urlparse(url).hostname or ""
    if host not in LOOPBACK and not allow_remote:
        raise OfflineViolation(
            f"refusing to contact {host!r}: the narrator only talks to a model on this device "
            "(set REA_ALLOW_REMOTE_LLM=1 to override)"
        )


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _request(url: str, payload: dict | None, timeout_s: float) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        raise LLMError(f"HTTP {exc.code} from {url}: {body}") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise LLMError(f"cannot reach {url}: {exc}") from exc


class OllamaClient:
    backend = "ollama"

    def __init__(self, model: str = "llama3.2:3b", host: str = "http://127.0.0.1:11434", *,
                 timeout_s: float = 600.0, temperature: float = 0.0, seed: int = 7, num_ctx: int = 4096,
                 num_predict: int = 700, keep_alive: str = "30m", allow_remote: bool | None = None):
        ensure_loopback(host, allow_remote)
        self.model, self.host = model, host.rstrip("/")
        self.timeout_s, self.temperature, self.seed, self.num_ctx = timeout_s, temperature, seed, num_ctx
        self.num_predict, self.keep_alive = num_predict, keep_alive

    def chat(self, messages: list[dict], schema: dict | None = None) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": self.keep_alive,  # keep the model in memory between events
            "options": {"temperature": self.temperature, "seed": self.seed, "num_ctx": self.num_ctx,
                        "num_predict": self.num_predict},
        }
        if schema is not None:
            payload["format"] = schema
        t0 = time.perf_counter()
        data = _request(f"{self.host}/api/chat", payload, self.timeout_s)
        dt = time.perf_counter() - t0
        msg = data.get("message") or {}
        n_eval = data.get("eval_count")
        eval_ns = data.get("eval_duration")

        def sec(key: str) -> float | None:
            v = data.get(key)
            return v / 1e9 if isinstance(v, (int, float)) else None

        tps = (n_eval / (eval_ns / 1e9)) if n_eval and eval_ns else None
        return LLMResponse(text=msg.get("content", ""), backend=self.backend, model=self.model, seconds=dt,
                           prompt_tokens=data.get("prompt_eval_count"), completion_tokens=n_eval, tokens_per_s=tps,
                           load_s=sec("load_duration"), prompt_s=sec("prompt_eval_duration"),
                           gen_s=sec("eval_duration"))

    def server_version(self) -> str | None:
        try:
            return "Ollama " + str(_request(f"{self.host}/api/version", None, 5.0).get("version"))
        except LLMError:
            return None

    def ping(self) -> tuple[bool, str]:
        try:
            data = _request(f"{self.host}/api/tags", None, 5.0)
        except LLMError as exc:
            return False, str(exc)
        names = [m.get("name", "") for m in data.get("models", [])]
        if any(n == self.model or n.split(":")[0] == self.model for n in names):
            return True, f"Ollama is running and has {self.model}"
        return False, f"Ollama is running but {self.model} is not pulled (have: {', '.join(names) or 'none'})"


class OpenAICompatClient:
    backend = "openai"

    def __init__(self, model: str = "local", host: str = "http://127.0.0.1:8080", *,
                 timeout_s: float = 600.0, temperature: float = 0.0, seed: int = 7, max_tokens: int = 700,
                 allow_remote: bool | None = None):
        ensure_loopback(host, allow_remote)
        self.model, self.host = model, host.rstrip("/")
        self.timeout_s, self.temperature, self.seed, self.max_tokens = timeout_s, temperature, seed, max_tokens

    def chat(self, messages: list[dict], schema: dict | None = None) -> LLMResponse:
        base = {"model": self.model, "messages": messages, "temperature": self.temperature,
                "seed": self.seed, "max_tokens": self.max_tokens, "stream": False}
        formats: list[dict | None] = [None]
        if schema is not None:
            formats = [
                {"type": "json_schema", "json_schema": {"name": "narrative", "schema": schema, "strict": True}},
                {"type": "json_object", "schema": schema},
                None,
            ]
        last_err: LLMError | None = None
        for fmt in formats:
            payload = dict(base)
            if fmt is not None:
                payload["response_format"] = fmt
            t0 = time.perf_counter()
            try:
                data = _request(f"{self.host}/v1/chat/completions", payload, self.timeout_s)
            except LLMError as exc:
                if "HTTP 4" in str(exc) or "HTTP 5" in str(exc):
                    last_err = exc
                    continue  # server does not support this response_format; try the next one
                raise
            dt = time.perf_counter() - t0
            usage = data.get("usage") or {}
            comp = usage.get("completion_tokens")
            text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            # llama.cpp's llama-server adds a "timings" block; other servers only give usage.
            tim = data.get("timings") or {}
            prompt_s = tim["prompt_ms"] / 1000.0 if isinstance(tim.get("prompt_ms"), (int, float)) else None
            gen_s = tim["predicted_ms"] / 1000.0 if isinstance(tim.get("predicted_ms"), (int, float)) else None
            tps = tim.get("predicted_per_second") or ((comp / dt) if comp and dt > 0 else None)
            return LLMResponse(text=text, backend=self.backend, model=self.model, seconds=dt,
                               prompt_tokens=usage.get("prompt_tokens"), completion_tokens=comp,
                               tokens_per_s=tps, prompt_s=prompt_s, gen_s=gen_s)
        raise last_err or LLMError("no response")

    def ping(self) -> tuple[bool, str]:
        try:
            data = _request(f"{self.host}/v1/models", None, 5.0)
        except LLMError as exc:
            return False, str(exc)
        ids = [m.get("id", "") for m in data.get("data", [])]
        return True, f"OpenAI-compatible server is running (models: {', '.join(ids) or 'unknown'})"


def make_client(backend: str, model: str, host: str | None = None, **kw) -> LLMClient:
    if backend == "ollama":
        return OllamaClient(model=model, host=host or "http://127.0.0.1:11434", **kw)
    if backend == "openai":
        return OpenAICompatClient(model=model, host=host or "http://127.0.0.1:8080", **kw)
    raise ValueError(f"unknown backend {backend!r}")
