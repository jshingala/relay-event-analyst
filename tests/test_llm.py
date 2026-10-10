import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from rea.llm import LLMError, OfflineViolation, OllamaClient, OpenAICompatClient, ensure_loopback, make_client


class _Server:
    """A loopback HTTP server that records requests and answers with canned JSON."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self, body):
                outer.requests.append((self.command, self.path, body))
                code, payload = outer.routes(self.command, self.path, body)
                data = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._answer(None)

            def do_POST(self):
                self._answer(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server():
    made = []

    def make(routes):
        s = _Server(routes)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


def test_remote_hosts_are_refused(monkeypatch):
    monkeypatch.delenv("REA_ALLOW_REMOTE_LLM", raising=False)
    for url in ("http://10.0.0.5:11434", "http://example.com", "https://api.example.com/v1"):
        with pytest.raises(OfflineViolation):
            ensure_loopback(url)
    for url in ("http://127.0.0.1:11434", "http://localhost:8080", "http://[::1]:11434"):
        ensure_loopback(url)
    with pytest.raises(OfflineViolation):
        make_client("ollama", "llama3.2:3b", "http://192.168.1.20:11434")
    monkeypatch.setenv("REA_ALLOW_REMOTE_LLM", "1")
    ensure_loopback("http://10.0.0.5:11434")


def test_ollama_request_and_timings(server, monkeypatch):
    # A proxy in the environment must not capture loopback traffic.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")

    def routes(method, path, body):
        if path == "/api/tags":
            return 200, {"models": [{"name": "llama3.2:3b"}]}
        return 200, {"message": {"role": "assistant", "content": '{"sentences": []}'}, "done": True,
                     "prompt_eval_count": 800, "eval_count": 200, "load_duration": 2_000_000_000,
                     "prompt_eval_duration": 1_000_000_000, "eval_duration": 8_000_000_000}

    s = server(routes)
    c = OllamaClient("llama3.2:3b", s.url)
    assert c.ping() == (True, "Ollama is running and has llama3.2:3b")
    schema = {"type": "object"}
    r = c.chat([{"role": "user", "content": "hi"}], schema=schema)
    method, path, body = s.requests[-1]
    assert path == "/api/chat" and body["format"] == schema and body["stream"] is False
    assert body["options"]["temperature"] == 0.0 and body["options"]["num_predict"] > 0 and body["keep_alive"]
    assert r.tokens_per_s == pytest.approx(25.0) and r.load_s == pytest.approx(2.0) and r.prompt_s == pytest.approx(1.0)


def test_ollama_reports_missing_model(server):
    s = server(lambda m, p, b: (200, {"models": [{"name": "qwen2.5:3b"}]}))
    alive, msg = OllamaClient("llama3.2:3b", s.url).ping()
    assert not alive and "not pulled" in msg


def test_openai_compat_falls_back_to_json_object(server):
    def routes(method, path, body):
        if body and body.get("response_format", {}).get("type") == "json_schema":
            return 400, {"error": "json_schema not supported"}
        return 200, {"choices": [{"message": {"content": '{"sentences": []}'}}],
                     "usage": {"prompt_tokens": 700, "completion_tokens": 150},
                     "timings": {"prompt_ms": 500.0, "predicted_ms": 6000.0, "predicted_per_second": 25.0}}

    s = server(routes)
    r = OpenAICompatClient("local", s.url).chat([{"role": "user", "content": "x"}], schema={"type": "object"})
    kinds = [b["response_format"]["type"] for _, _, b in s.requests]
    assert kinds == ["json_schema", "json_object"]
    assert r.tokens_per_s == 25.0 and r.gen_s == pytest.approx(6.0)


def test_unreachable_server_is_an_llm_error():
    with pytest.raises(LLMError):
        OllamaClient("llama3.2:3b", "http://127.0.0.1:9", timeout_s=2).chat([{"role": "user", "content": "x"}])
