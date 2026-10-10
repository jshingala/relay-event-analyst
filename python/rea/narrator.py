"""Narrators turn a fact sheet into plain-language sentences.

* TemplateNarrator: deterministic sentences built only from fact displays. It is the
  fallback and the baseline; it passes the validator by construction.
* LLMNarrator: asks a local model (Ollama or an OpenAI-compatible llama.cpp server)
  for sentences that cite fact ids, then validates every sentence.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from .facts import Fact, FactSheet
from .llm import LLMClient, LLMError, LLMResponse
from .prompts import SYSTEM_PROMPT, build_user_prompt, narrative_schema, select_facts
from .rag import Passage
from .validator import ValidationResult, split_sentences, validate

Sentence = tuple[str, list[str]]  # (text, cited fact ids)

_VERDICT_PHRASES = {
    "operated as expected": "operated as expected",
    "slower than expected": "operated slower than its settings predict",
    "faster than expected": "operated faster than its settings predict",
    "failed to trip": "failed to trip",
    "correctly did not trip": "correctly did not trip",
    "tripped for a fault outside its directional zone": "tripped for a fault outside its directional zone",
    "tripped although no trip was expected": "tripped although no trip was expected",
    "undetermined": "could not be judged from this record",
    "tripped after the fault had ended": "tripped after the fault had already ended",
}


@dataclass
class Narrative:
    sentences: list[str]
    narrator: str
    validation: ValidationResult  # checks on what the narrator proposed
    fallback: bool = False
    fallback_reason: str = ""
    llm: LLMResponse | None = None
    raw_output: str = ""
    seconds: float = 0.0
    proposed: list[Sentence] = field(default_factory=list)
    delivered: ValidationResult | None = None  # checks on what the user sees (template ones after a fallback)
    filled: list[str] = field(default_factory=list)  # essential topics the model skipped, added from the template

    @property
    def delivered_validation(self) -> ValidationResult:
        return self.delivered if self.delivered is not None else self.validation


# Every narrative must state these; if the model leaves one out, the template's
# sentence for it is added (and the audit says so).
ESSENTIAL_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fault type", ("fault.type", "fault.type_description")),
    ("relay operation", ("relay.operate_ms", "relay.operate_cycles", "relay.tripped")),
    ("relay verdict", ("assessment.relay",)),
    ("rotation check", ("assessment.rotation",)),
    ("late trip", ("relay.trip_after_fault_end_ms", "relay.trip_after_fault_end_cycles")),
)


def _q(f: Fact | None) -> str:
    return f.render() if f is not None else "unknown"


class TemplateNarrator:
    name = "template"

    def sentences(self, sheet: FactSheet) -> list[Sentence]:
        g = sheet.get
        out: list[Sentence] = []
        if sheet.value("fault.detected") is False or g("fault.type") is None or g("fault.type").is_missing:
            reason = g("fault.type").reason if g("fault.type") is not None else "no fault detected"
            out.append((f"No fault could be identified in this record: {reason}.", ["fault.type"]))
            return out

        desc = sheet.value("fault.type_description")
        article = "an" if str(desc).startswith("A") else "a"
        s = f"The record shows {article} {desc} fault ({sheet.value('fault.type')})"
        ids = ["fault.type_description", "fault.type"]
        trig = g("fault.inception_from_trigger_ms")
        if trig is not None and not trig.is_missing:
            when = "before" if float(trig.value) < 0 else "after"
            s += f" that began {trig.display.lstrip('-')} {trig.unit} {when} the record trigger"
            ids.append("fault.inception_from_trigger_ms")
        elif g("fault.inception_ms") is not None and g("fault.inception_ms").is_missing:
            s += ", already in progress when the record started"
        out.append((s + ".", ids))

        rot = g("assessment.rotation")
        if rot is not None and rot.priority == 1:
            out.append((f"Phase rotation check: {rot.value}.", ["assessment.rotation"]))
        after_close = g("fault.after_breaker_close_ms")
        if after_close is not None:
            out.append((f"The disturbance began {_q(after_close)} after the breaker closed, so it may be an "
                        "energization (motor start or inrush) rather than a fault.", ["fault.after_breaker_close_ms"]))

        d = g("fault.direction")
        if d is not None and not d.is_missing:
            where = {"forward": "in front of the relay (forward)", "reverse": "behind the relay (reverse)"}.get(
                d.value, d.value)
            if g("fault.z2_ohm") is not None and g("settings.Z2F") is not None and d.value in ("forward", "reverse"):
                thr_id = "settings.Z2F" if d.value == "forward" else "settings.Z2R"
                rel = "below" if d.value == "forward" else "above"
                out.append((
                    f"The fault was {where}: the negative-sequence directional impedance z2 was "
                    f"{_q(g('fault.z2_ohm'))}, {rel} the threshold {thr_id.split('.')[1]} of {_q(g(thr_id))}.",
                    ["fault.direction", "fault.z2_ohm", thr_id],
                ))
            elif g("fault.dz1_ohm") is not None:
                out.append((
                    f"The fault was {where}, from the incremental positive-sequence impedance of "
                    f"{_q(g('fault.dz1_ohm'))}.",
                    ["fault.direction", "fault.dz1_ohm"],
                ))
            else:
                out.append((f"The fault direction is {d.value}.", ["fault.direction"]))
        elif d is not None:
            out.append((f"The fault direction could not be determined: {d.reason}.", ["fault.direction"]))

        mx = g("fault.max_phase_rms_a")
        basis = "as recorded" if mx is not None and "as recorded" in mx.label else "secondary"
        s = (f"The largest fault current was {_q(mx)} {basis} on phase "
             f"{sheet.value('fault.max_phase')}")
        ids = ["fault.max_phase_rms_a", "fault.max_phase"]
        if g("fault.max_phase_rms_primary_a") is not None:
            s += f" ({_q(g('fault.max_phase_rms_primary_a'))} primary)"
            ids.append("fault.max_phase_rms_primary_a")
        if sheet.value("fault.ground_involved") and g("fault.3i0_a") is not None:
            s += f", with a residual current 3I0 of {_q(g('fault.3i0_a'))}"
            ids.append("fault.3i0_a")
        out.append((s + ".", ids))

        op = g("relay.operate_ms")
        if op is not None and not op.is_missing:
            out.append((
                f"The relay trip output asserted {_q(op)} ({_q(g('relay.operate_cycles'))}) after the fault began.",
                ["relay.operate_ms", "relay.operate_cycles"],
            ))
        elif op is not None:
            out.append((f"The relay operate time could not be measured: {op.reason}.", ["relay.operate_ms"]))
        elif sheet.value("relay.tripped") is False:
            out.append(("The relay did not trip during this record.", ["relay.tripped"]))
        late = g("relay.trip_after_fault_end_ms")
        if late is not None:
            out.append((f"The relay trip output asserted {_q(late)} ({_q(g('relay.trip_after_fault_end_cycles'))}) "
                        "after the fault had already ended.",
                        ["relay.trip_after_fault_end_ms", "relay.trip_after_fault_end_cycles"]))

        el = sheet.value("expected.element")
        if el and el != "none":
            s = f"With the settings on file, {el} should have operated after {_q(g('expected.operate_ms'))}"
            ids = ["expected.element", "expected.operate_ms"]
            if el.startswith("51") and g(f"settings.{el}C") is not None and g("expected.m_51" + el[2].lower()) is not None:
                tag = "51" + el[2].lower()
                s += (f" ({sheet.value(f'settings.{el}C')} curve, time dial {_q(g(f'settings.{el}TD'))}, "
                      f"{_q(g('expected.m_' + tag))} times pickup)")
                ids += [f"settings.{el}C", f"settings.{el}TD", "expected.m_" + tag]
            out.append((s + ".", ids))
        elif g("expected.reason") is not None:
            out.append((f"No trip was expected: {sheet.value('expected.reason')}.", ["expected.reason"]))
        elif g("expected.element") is not None and g("expected.element").is_missing:
            out.append((f"The expected operate time could not be computed: {g('expected.element').reason}.",
                        ["expected.element"]))

        verdict = sheet.value("assessment.relay")
        if verdict:
            s = f"Verdict: the relay {_VERDICT_PHRASES.get(verdict, verdict)}"
            ids = ["assessment.relay"]
            if g("assessment.deviation_ms") is not None:
                s += (f" (actual minus expected {_q(g('assessment.deviation_ms'))}, allowed "
                      f"{_q(g('assessment.tolerance_ms'))})")
                ids += ["assessment.deviation_ms", "assessment.tolerance_ms"]
            out.append((s + ".", ids))

        intr = g("breaker.interrupt_ms")
        bv = sheet.value("assessment.breaker")
        if intr is not None and not intr.is_missing:
            out.append((
                f"The breaker cleared its last pole {_q(intr)} ({_q(g('breaker.interrupt_cycles'))}) after the trip, "
                f"phase {sheet.value('breaker.first_pole')} first; breaker verdict: {bv}.",
                ["breaker.interrupt_ms", "breaker.interrupt_cycles", "breaker.first_pole", "assessment.breaker"],
            ))
        elif bv and bv != "not applicable":
            out.append((f"Breaker verdict: {bv}.", ["assessment.breaker"]))

        dur = g("fault.duration_ms")
        if dur is not None and not dur.is_missing:
            out.append((
                f"The fault lasted {_q(dur)} ({_q(g('fault.duration_cycles'))}); {sheet.value('fault.end_cause')}.",
                ["fault.duration_ms", "fault.duration_cycles", "fault.end_cause"],
            ))
        elif sheet.value("fault.end_cause"):
            out.append((f"How the fault ended: {sheet.value('fault.end_cause')}.", ["fault.end_cause"]))
        return out

    def narrate(self, sheet: FactSheet) -> Narrative:
        t0 = time.perf_counter()
        sents = self.sentences(sheet)
        result = validate(sents, sheet, strict=True)
        return Narrative(
            sentences=[c.text for c in result.kept],
            narrator=self.name,
            validation=result,
            seconds=time.perf_counter() - t0,
            proposed=sents,
        )


_ITEM = re.compile(r'\{\s*"text"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"fact_ids"\s*:\s*\[([^\]]*)\]\s*\}')


def parse_model_output(text: str) -> tuple[list[Sentence], bool]:
    """Return (items, structured).

    Items come from the JSON answer; if the JSON was cut off (token limit), every
    complete item is salvaged. Plain prose falls back to sentence splitting with no
    citations.
    """
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
        items = data.get("sentences", []) if isinstance(data, dict) else data
        out: list[Sentence] = []
        for it in items:
            if isinstance(it, dict) and isinstance(it.get("text"), str):
                ids = it.get("fact_ids") or []
                out.append((it["text"], [str(i) for i in ids if isinstance(i, (str, int))]))
            elif isinstance(it, str):
                out.append((it, []))
        if out:
            return out, True
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass
    salvaged: list[Sentence] = []
    for m in _ITEM.finditer(cleaned):
        try:
            sentence = json.loads('"' + m.group(1) + '"')
        except json.JSONDecodeError:
            continue
        salvaged.append((sentence, re.findall(r'"([^"]+)"', m.group(2))))
    if salvaged:
        return salvaged, True
    return [(p, []) for p in split_sentences(cleaned)], False


def expand_sentences(items: list[Sentence]) -> list[Sentence]:
    """One check per sentence: an item holding several sentences is split, and each
    piece keeps the item's citations."""
    out: list[Sentence] = []
    for text, ids in items:
        for piece in split_sentences(text):
            out.append((piece, list(ids)))
    return out


# Ways a sentence can state each verdict (the validator has already rejected contradictions).
_VERDICT_WORDS = {
    "operated as expected": r"as expected|correctly|properly|within (?:the )?(?:toleran|allowed)",
    "slower than expected": r"slower than|too slow|\blate\b",
    "faster than expected": r"faster than|too fast|\bearly\b",
    "failed to trip": r"failed to trip|did not trip|didn't trip|did not operate|never tripped",
    "correctly did not trip": r"correctly did not|did not trip|did not operate|restrained|no trip",
    "tripped for a fault outside its directional zone": r"outside|misoperat|should not have tripped",
    "tripped although no trip was expected": r"no trip was expected|should not have tripped|misoperat|unexpected",
    "undetermined": r"could not be (?:judged|determined)|undetermined",
    "tripped after the fault had ended": r"after the fault|already (?:ended|cleared|gone)|no fault (?:was )?(?:left|present)",
}


def _mentions_topic(sheet: FactSheet, topic: str, fact_ids: tuple[str, ...], check) -> bool:
    if any(f in check.fact_ids for f in fact_ids) or any(f in check.bound.values() for f in fact_ids):
        return True
    text = check.text
    if topic == "fault type":
        code, desc = sheet.value("fault.type"), sheet.value("fault.type_description")
        return bool((code and re.search(rf"\b{re.escape(str(code))}\b", text))
                    or (desc and str(desc).lower() in text.lower()))
    if topic == "relay operation":
        return bool(re.search(r"\brelay\b.*\b(trip|tripped|operate|operated|did not)\b", text, re.I))
    if topic == "rotation check":
        return bool(re.search(r"rotation|PHROT", text, re.I))
    if topic == "late trip":
        return bool(re.search(r"after the fault|already (?:ended|cleared|gone)", text, re.I))
    if topic == "relay verdict":
        rx = _VERDICT_WORDS.get(str(sheet.value("assessment.relay") or ""))
        return bool(rx and re.search(rx, text, re.I))
    return False


def fill_essentials(sheet: FactSheet, kept: list, template: list[Sentence]) -> tuple[list[str], list[str]]:
    """Add the template sentence for each essential topic the kept model sentences skip.

    Returns (sentences, topics filled)."""
    out = [c.text for c in kept]
    filled: list[str] = []
    for topic, ids in ESSENTIAL_TOPICS:
        if sheet.get(ids[0]) is None and all(sheet.get(i) is None for i in ids):
            continue
        if topic == "rotation check" and sheet.get(ids[0]).priority != 1:
            continue  # only a mismatch is essential
        if any(_mentions_topic(sheet, topic, ids, c) for c in kept):
            continue
        sent = next((t for t, cited in template if any(i in cited for i in ids)), None)
        if sent is None:
            continue
        filled.append(topic)
        if topic == "fault type":
            out.insert(0, sent)
        else:
            out.append(sent)
    return out, filled


class LLMNarrator:
    def __init__(self, client: LLMClient, *, max_facts: int = 32, min_kept: int = 3,
                 max_reject_rate: float = 0.5, strict: bool = True):
        self.client = client
        self.max_facts = max_facts
        self.min_kept = min_kept
        self.max_reject_rate = max_reject_rate
        self.strict = strict
        self.name = f"{client.backend}:{client.model}"

    def narrate(self, sheet: FactSheet, passages: list[Passage] | None = None) -> Narrative:
        t0 = time.perf_counter()
        facts = select_facts(sheet, self.max_facts)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(sheet, facts, passages or [])},
        ]
        template = TemplateNarrator()
        try:
            resp = self.client.chat(messages, schema=narrative_schema([f.id for f in facts if not f.is_missing]))
        except LLMError as exc:
            nar = template.narrate(sheet)
            nar.fallback, nar.fallback_reason = True, f"local model unavailable: {exc}"
            nar.seconds = time.perf_counter() - t0
            return nar
        items, _structured = parse_model_output(resp.text)
        proposed = expand_sentences(items)
        result = validate(proposed, sheet, strict=self.strict, require_terminal=True)
        kept = [c.text for c in result.kept]
        reject_rate = len(result.rejected) / max(len(result.checks), 1)
        nar = Narrative(sentences=kept, narrator=self.name, validation=result, llm=resp,
                        raw_output=resp.text, proposed=proposed)
        if len(kept) < self.min_kept or reject_rate > self.max_reject_rate:
            fb = template.narrate(sheet)
            nar.sentences = fb.sentences
            nar.delivered = fb.validation
            nar.fallback = True
            nar.fallback_reason = (f"only {len(kept)} of {len(result.checks)} model sentences passed validation; "
                                   "showing the template narrative instead")
        else:
            tmpl = template.sentences(sheet)
            nar.sentences, nar.filled = fill_essentials(sheet, result.kept, tmpl)
            if nar.filled:
                added = [(t, ids) for t, ids in tmpl if t in nar.sentences]
                nar.delivered = validate([(c.text, c.fact_ids) for c in result.kept] + added, sheet,
                                         strict=self.strict)
        nar.seconds = time.perf_counter() - t0
        return nar
