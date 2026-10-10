import json

from rea.evaluation.audit import SECTION, load, main, render, score


def _write(folder, events, labels):
    (folder / "narration.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    (folder / "audit.json").write_text(json.dumps(labels))


EVENTS = [
    {"id": "ev1", "proposed": [{"text": "A.", "fact_ids": []}, {"text": "B.", "fact_ids": []},
                               {"text": "C.", "fact_ids": []}, {"text": "D.", "fact_ids": []}],
     "rejected": [{"text": "B.", "reasons": ["numbers not traceable to the physics core: 1.08"]},
                  {"text": "D.", "reasons": ["a negative time 'before' an event is a double negative"]}]},
    {"id": "ev2", "model_unavailable": True, "delivered": ["template"]},
]
LABELS = [
    {"id": "ev1", "text": "A.", "label": "ok"},
    {"id": "ev1", "text": "B.", "label": "wrong", "note": "1.08 is not the deviation."},
    {"id": "ev1", "text": "C.", "label": "wrong", "note": "Misattributed time."},
    {"id": "ev1", "text": "D.", "label": "ok", "note": "Reads oddly but is right."},
]


def test_confusion_table_and_lists(tmp_path):
    _write(tmp_path, EVENTS, LABELS)
    res = score(*load(tmp_path))
    assert res["table"] == {("ok", "kept"): 1, ("ok", "rejected"): 1, ("wrong", "kept"): 1, ("wrong", "rejected"): 1}
    assert [m["text"] for m in res["missed"]] == ["C."]
    assert [f["text"] for f in res["false_rejects"]] == ["D."]
    assert [c["text"] for c in res["caught"]] == ["B."]
    md = render(res)
    assert "| wrong | 1 | 1 | 2 |" in md and "kept (these reached the report)" in md


def test_unlabeled_sentences_fail_and_section_is_replaced(tmp_path):
    _write(tmp_path, EVENTS, LABELS[:3])
    (tmp_path / "RESULTS.md").write_text("# Results\n\n## Narrator\n\nx\n")
    assert main([str(tmp_path), "--write"]) == 1  # "D." has no label
    _write(tmp_path, EVENTS, LABELS)
    assert main([str(tmp_path), "--write"]) == 0
    text = (tmp_path / "RESULTS.md").read_text()
    assert text.count(SECTION) == 1 and text.startswith("# Results\n\n## Narrator\n\nx\n")
