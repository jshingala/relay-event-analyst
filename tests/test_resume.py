import json

import pytest

import rea.llm
from conftest import DEMO
from rea.evaluation.narration import PARTIAL, load_partial
from rea.evaluation.run import main
from rea.llm import LLMError, LLMResponse

REPLY = json.dumps({"sentences": [
    {"text": "The record shows a fault on the line.", "fact_ids": []},
    {"text": "The relay operated 1234.56 ms after inception.", "fact_ids": ["relay.operate_ms"]},
    {"text": "The breaker interrupted the current.", "fact_ids": []},
]})


class FakeClient:
    """Answers every event the same way; raises ``fail`` on call number ``fail_on``."""

    backend = "ollama"
    model = "fake-3b"

    def __init__(self, fail_on: int | None = None, fail: BaseException | None = None):
        self.fail_on, self.fail = fail_on, fail
        self.calls = 0

    def chat(self, messages, schema=None):
        self.calls += 1
        if self.calls == self.fail_on:
            raise self.fail
        return LLMResponse(text=REPLY, backend=self.backend, model=self.model, seconds=1.0,
                           prompt_tokens=800, completion_tokens=200, tokens_per_s=20.0)

    def ping(self):
        return True, "ok"

    def server_version(self):
        return "Fake 1.0"


def _use(monkeypatch, client):
    monkeypatch.setattr(rea.llm, "make_client", lambda *a, **k: client)
    return client


def _run(out, *extra):
    return main(["--data", str(DEMO), "--out", str(out), "--narrate", "--narrate-limit", "5", "--no-redteam",
                 "--papers", str(out / "no-papers"), *extra])


def _narrator(out):
    narr = json.loads((out / "summary.json").read_text())["narrator"]
    for k in ("sec_median", "sec_max", "events_this_run"):
        narr.pop(k)
    return narr


def test_interrupted_run_resumes_and_counts_each_event_once(core_bin, tmp_path, monkeypatch):
    ref = tmp_path / "uninterrupted"
    _use(monkeypatch, FakeClient())
    assert _run(ref) == 0

    out = tmp_path / "run"
    _use(monkeypatch, FakeClient(fail_on=3, fail=RuntimeError("power cut")))
    with pytest.raises(RuntimeError):
        _run(out)
    done = load_partial(out / PARTIAL)
    assert len(done) == 2 and not (out / "narration.jsonl").exists()

    second = _use(monkeypatch, FakeClient())
    assert _run(out, "--resume") == 0
    assert second.calls == 3  # only the events that were not done
    assert not (out / PARTIAL).exists()
    ids = [json.loads(line)["id"] for line in (out / "narration.jsonl").read_text().splitlines()]
    assert len(ids) == len(set(ids)) == 5
    summary = json.loads((out / "summary.json").read_text())["narrator"]
    assert summary["events"] == 5 and summary["events_this_run"] == 3
    assert _narrator(out) == _narrator(ref)


def test_resume_retries_unreachable_events_and_ignores_a_torn_line(core_bin, tmp_path, monkeypatch):
    out = tmp_path / "run"
    # Event 2: the model cannot be reached (counted apart). Event 4: the process dies.
    client = _use(monkeypatch, FakeClient(fail_on=2, fail=LLMError("cannot reach http://127.0.0.1:11434")))
    orig = client.chat

    def chat(messages, schema=None):
        if client.calls == 3:
            client.calls += 1
            raise KeyboardInterrupt
        return orig(messages, schema)

    client.chat = chat
    with pytest.raises(KeyboardInterrupt):
        _run(out)
    lines = (out / PARTIAL).read_text().splitlines()
    assert len(lines) == 3 and json.loads(lines[1])["model_unavailable"]
    with (out / PARTIAL).open("a") as f:
        f.write('{"id": "demo_cag_slow_rel')  # killed mid-write

    second = _use(monkeypatch, FakeClient())
    assert _run(out, "--resume") == 0
    assert second.calls == 3  # the unreachable event and the two never finished
    summary = json.loads((out / "summary.json").read_text())["narrator"]
    assert summary["events"] == 5 and summary["model_unavailable"] == 0


def test_a_fresh_run_discards_an_old_partial_file(core_bin, tmp_path, monkeypatch):
    out = tmp_path / "run"
    out.mkdir()
    (out / PARTIAL).write_text(json.dumps({"id": "demo_bg_51g_normal", "stale": True}) + "\n")
    client = _use(monkeypatch, FakeClient())
    assert _run(out) == 0
    assert client.calls == 5
    first = json.loads((out / "narration.jsonl").read_text().splitlines()[0])
    assert "stale" not in first and first["numbers_total"] >= 1
