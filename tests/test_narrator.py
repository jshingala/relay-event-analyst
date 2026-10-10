import json

from rea.llm import LLMError, LLMResponse
from rea.narrator import LLMNarrator, TemplateNarrator, expand_sentences, parse_model_output
from rea.prompts import SYSTEM_PROMPT, build_user_prompt, narrative_schema, select_facts


class FakeClient:
    backend = "fake"
    model = "fake-3b"

    def __init__(self, reply: str | Exception):
        self.reply = reply
        self.calls = []

    def chat(self, messages, schema=None):
        self.calls.append((messages, schema))
        if isinstance(self.reply, Exception):
            raise self.reply
        return LLMResponse(text=self.reply, backend=self.backend, model=self.model, seconds=1.0,
                           prompt_tokens=800, completion_tokens=200, tokens_per_s=20.0)

    def ping(self):
        return True, "ok"


def reply(*sentences):
    return json.dumps({"sentences": [{"text": t, "fact_ids": ids} for t, ids in sentences]})


GOOD = reply(
    ("The record shows a B-phase-to-ground fault (BG).", ["fault.type"]),
    ("The fault was forward of the relay.", ["fault.direction"]),
    ("The relay operated 603.13 ms after inception, against 605.40 ms expected from the settings.",
     ["relay.operate_ms", "expected.operate_ms"]),
    ("The relay operated as expected.", ["assessment.relay"]),
    ("The breaker interrupted the current 41.67 ms after the trip.", ["breaker.interrupt_ms"]),
)


def test_parse_json_and_code_fence():
    items, structured = parse_model_output("```json\n" + GOOD + "\n```")
    assert structured and len(items) == 5 and items[0][1] == ["fault.type"]


def test_parse_salvages_truncated_json():
    cut = GOOD[: GOOD.index("The breaker") - 30]  # token limit hit mid-answer
    items, structured = parse_model_output(cut)
    assert structured and [t for t, _ in items][:3] == [
        "The record shows a B-phase-to-ground fault (BG).", "The fault was forward of the relay.",
        "The relay operated 603.13 ms after inception, against 605.40 ms expected from the settings."]


def test_parse_plain_prose():
    items, structured = parse_model_output("The relay tripped. The breaker opened.")
    assert not structured and items == [("The relay tripped.", []), ("The breaker opened.", [])]


def test_expand_splits_paragraph_items_and_keeps_citations():
    out = expand_sentences([("The relay tripped. The breaker opened in 41.67 ms.", ["breaker.interrupt_ms"])])
    assert out == [("The relay tripped.", ["breaker.interrupt_ms"]),
                   ("The breaker opened in 41.67 ms.", ["breaker.interrupt_ms"])]


def test_llm_narrator_keeps_valid_sentences(bg_sheet):
    client = FakeClient(GOOD)
    nar = LLMNarrator(client).narrate(bg_sheet)
    assert not nar.fallback
    assert len(nar.sentences) == 5
    assert nar.validation.rejected == []
    messages, schema = client.calls[0]
    assert messages[0]["content"] == SYSTEM_PROMPT
    enum = schema["properties"]["sentences"]["items"]["properties"]["fact_ids"]["items"]["enum"]
    assert "relay.operate_ms" in enum and all(bg_sheet.get(f) is not None for f in enum)


def test_llm_narrator_drops_a_bad_sentence(bg_sheet):
    bad = json.loads(GOOD)
    bad["sentences"].append({"text": "The fault current peaked at 12.4 kA.", "fact_ids": ["fault.peak_a"]})
    nar = LLMNarrator(FakeClient(json.dumps(bad))).narrate(bg_sheet)
    assert not nar.fallback and len(nar.sentences) == 5
    assert [c.unsupported for c in nar.validation.rejected] == [["12.4"]]


def test_llm_narrator_falls_back_when_most_sentences_fail(bg_sheet):
    junk = reply(("The relay operated in 12 ms.", []), ("The fault lasted 3 s.", []), ("It was an AG fault.", []),
                 ("The relay operated as expected.", ["assessment.relay"]))
    nar = LLMNarrator(FakeClient(junk)).narrate(bg_sheet)
    assert nar.fallback and "template" in nar.fallback_reason
    assert nar.sentences == TemplateNarrator().narrate(bg_sheet).sentences
    assert nar.delivered_validation.rejected == []  # what the user sees is clean
    assert len(nar.validation.rejected) == 3  # what the model said is still audited


def test_llm_narrator_survives_a_dead_server(bg_sheet):
    nar = LLMNarrator(FakeClient(LLMError("cannot reach http://127.0.0.1:11434"))).narrate(bg_sheet)
    assert nar.fallback and "unavailable" in nar.fallback_reason and nar.sentences


def test_prompt_lists_unknown_facts_with_reasons(abc_sheet):
    facts = select_facts(abc_sheet)
    prompt = build_user_prompt(abc_sheet, facts, [])
    assert "UNKNOWN:" in prompt
    missing = [f for f in facts if f.is_missing]
    assert missing and all(f"- {f.id}: {f.reason}" in prompt for f in missing)
    schema = narrative_schema([f.id for f in facts if not f.is_missing])
    enum = schema["properties"]["sentences"]["items"]["properties"]["fact_ids"]["items"]["enum"]
    assert not set(enum) & {f.id for f in missing}


def test_select_facts_keeps_all_headline_facts_in_story_order(bg_sheet):
    ids = [f.id for f in select_facts(bg_sheet)]
    assert all(f.id in ids for f in bg_sheet.facts if f.priority == 1)
    assert ids.index("fault.type") < ids.index("fault.direction") < ids.index("relay.operate_ms") \
        < ids.index("assessment.relay") < ids.index("breaker.interrupt_ms") < ids.index("fault.duration_ms")


def test_skipped_essentials_are_added_from_the_template(abc_sheet):
    # Mirrors a real 3B answer that never named the fault type.
    answer = reply(
        ("The fault was detected 2.96 ms before the record trigger.", ["fault.inception_from_trigger_ms"]),
        ("The fault occurred in the forward direction.", ["fault.direction"]),
        ("The maximum phase fault current was 23.16 A in phase A.", ["fault.max_phase_rms_a", "fault.max_phase"]),
        ("The relay did not operate within the expected time of 11.46 ms.", ["expected.operate_ms"]),
        ("The relay failed to trip.", ["assessment.relay"]),
    )
    nar = LLMNarrator(FakeClient(answer)).narrate(abc_sheet)
    assert not nar.fallback and nar.filled == ["fault type"]
    assert nar.sentences[0].startswith("The record shows a three-phase fault (ABC)")
    assert nar.delivered_validation.rejected == []


def test_nothing_is_added_when_the_model_covers_everything(bg_sheet):
    nar = LLMNarrator(FakeClient(GOOD)).narrate(bg_sheet)
    assert nar.filled == [] and nar.delivered is None
