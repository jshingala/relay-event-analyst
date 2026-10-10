"""Integration tests that need the compiled physics core (skipped otherwise)."""

import json

import pytest

from conftest import DEMO
from rea.cli import main as rea_main
from rea.core_runner import run_core
from rea.evaluation.metrics import score_event, summarize
from rea.evaluation.narration import redteam_validator
from rea.facts import FactSheet
from rea.pipeline import analyze_event
from rea.synth.cli import generate

DEMO_IDS = ["demo_bg_51g_normal", "demo_bc_slow_breaker", "demo_ag_reverse_restrained", "demo_cag_slow_relay",
            "demo_abc_failed_to_trip"]


@pytest.mark.parametrize("event", DEMO_IDS)
def test_demo_events_match_their_labels(core_bin, event):
    labels = json.loads((DEMO / f"{event}.labels.json").read_text())
    data, wall = run_core(DEMO / f"{event}.cfg", DEMO / f"{event}.settings.txt", core=core_bin)
    s = score_event(FactSheet(data), labels, wall)
    assert s.type_ok and s.direction_ok and s.relay_ok and s.breaker_ok


def test_generated_events_round_trip(core_bin, tmp_path):
    manifest = generate(tmp_path, n=16, seed=123, variant="normal")
    scores = []
    for ev in manifest:
        labels = json.loads((tmp_path / ev["labels"]).read_text())
        data, wall = run_core(tmp_path / ev["cfg"], tmp_path / ev["settings"], core=core_bin)
        scores.append(score_event(FactSheet(data), labels, wall))
    summary = summarize(scores)
    assert summary.n_errors == 0
    assert summary.fault_type_accuracy == 1.0
    assert summary.relay_verdict_accuracy == 1.0 and summary.breaker_verdict_accuracy == 1.0
    assert summary.trip_abs_err_ms["max"] < 2.5  # trip edge within ~1 sample at the slowest rate
    assert summary.inception_abs_err_samples["max"] <= 2.0


def test_pipeline_with_template_narrator(core_bin):
    r = analyze_event(DEMO / "demo_cag_slow_relay.cfg", DEMO / "demo_cag_slow_relay.settings.txt",
                      backend="template", papers=None, core=str(core_bin))
    assert r.sheet.value("assessment.relay") == "slower than expected"
    assert any("slower than its settings predict" in s for s in r.narrative.sentences)
    assert r.narrative.validation.rejected == []


def test_cli_analyze_and_json(core_bin, tmp_path, capsys):
    out = tmp_path / "r.json"
    code = rea_main(["analyze", str(DEMO / "demo_bg_51g_normal.cfg"), "--backend", "template", "--no-papers",
                     "--json", str(out), "--core", str(core_bin)])
    assert code == 0
    text = capsys.readouterr().out
    assert "WHAT HAPPENED" in text and "KEY FACTS" in text and "VALIDATOR" in text
    report = json.loads(out.read_text())
    assert report["facts"]["schema"] == "rea.facts/1" and report["narrative"]


def test_cli_refuses_remote_model(core_bin, capsys):
    code = rea_main(["analyze", str(DEMO / "demo_bg_51g_normal.cfg"), "--backend", "ollama",
                     "--host", "http://203.0.113.7:11434", "--no-papers", "--core", str(core_bin)])
    assert code == 3 and "refusing" in capsys.readouterr().err


def test_missing_facts_are_reported(core_bin, capsys):
    rea_main(["analyze", str(DEMO / "demo_abc_failed_to_trip.cfg"), "--backend", "template", "--no-papers",
              "--core", str(core_bin)])
    out = capsys.readouterr().out
    assert "COULD NOT BE DETERMINED" in out


def test_validator_red_team_on_demo_events(core_bin):
    sheets = {}
    for e in DEMO_IDS:
        data, _ = run_core(DEMO / f"{e}.cfg", DEMO / f"{e}.settings.txt", core=core_bin)
        sheets[e] = FactSheet(data)
    rt = redteam_validator(sheets, DEMO_IDS)
    assert rt["clean_rejected"] == 0
    assert rt["caught"] / rt["injected"] >= 0.95, rt["misses"]
