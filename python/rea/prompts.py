"""Prompt and output schema for the narrator.

The schema restricts `fact_ids` to the ids actually given to the model, so a
grammar-constrained decoder (Ollama `format`, llama.cpp `json_schema`) cannot
cite a fact that does not exist.
"""

from __future__ import annotations

from .facts import Fact, FactSheet
from .rag import Passage

SYSTEM_PROMPT = """You explain protective-relay event records to a protection engineer.
The FACTS were computed by a physics engine from the waveform record. Rules:
1. Use only the FACTS. Copy each number exactly as written, with its unit.
2. Never calculate: no sums, differences, ratios, percentages or rounding.
3. Write numbers as digits, never as words.
4. For each item under UNKNOWN, say that it could not be determined and why. Never guess it.
5. BACKGROUND passages explain concepts only. Never take numbers from them.
6. Each item in "sentences" is ONE short sentence of at most 30 words. Its "fact_ids" lists the id of
   every fact whose value the sentence uses.
Write 5 to 8 sentences, in this order: what happened and when, the fault direction, the fault current,
the relay operate time against the expected time from the settings, the verdict, the breaker, how the
fault ended.
Answer only with JSON of the form {"sentences": [{"text": "...", "fact_ids": ["..."]}]}."""

# Supporting facts worth giving the model in addition to all priority-1 facts.
_SUPPORTING_PREFIXES = (
    "settings.51P1", "settings.51G1", "settings.50P1P", "settings.DIR", "settings.Z2F", "settings.Z2R",
    "expected.", "assessment.tolerance_ms", "fault.z2_ohm", "fault.dz1_ohm", "fault.3i0_a",
    "fault.ground_involved", "breaker.first_pole", "breaker.52a_open_after_trip_ms", "fault.inception_from_trigger_ms",
)

# Facts are shown to the model in the order the explanation should follow.
_TOPIC_ORDER = (
    "fault.detected", "fault.type", "fault.inception", "fault.ground_involved", "fault.after_breaker_close",
    "assessment.rotation", "record.rotation", "settings.PHROT",
    "fault.direction", "fault.z2", "fault.dz1", "settings.Z2", "settings.DIR",
    "fault.max_phase", "fault.3i0",
    "relay.",
    "expected.element", "expected.operate", "expected.", "settings.5",
    "assessment.relay", "assessment.deviation", "assessment.tolerance",
    "breaker.", "assessment.breaker",
    "fault.duration", "fault.end_cause",
)


def _topic(f: Fact) -> int:
    for i, prefix in enumerate(_TOPIC_ORDER):
        if f.id.startswith(prefix):
            return i
    return len(_TOPIC_ORDER)


def select_facts(sheet: FactSheet, max_facts: int = 32) -> list[Fact]:
    chosen = [f for f in sheet.facts if f.priority == 1]
    for f in sheet.facts:
        if len(chosen) >= max_facts:
            break
        if f.priority == 2 and f.id.startswith(_SUPPORTING_PREFIXES) and f not in chosen:
            chosen.append(f)
    return sorted(chosen, key=_topic)  # stable: keeps the core's order within a topic


def narrative_schema(fact_ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "sentences": {
                "type": "array",
                "minItems": 3,
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "maxLength": 280},
                        "fact_ids": {"type": "array", "items": {"type": "string", "enum": fact_ids}},
                    },
                    "required": ["text", "fact_ids"],
                },
            }
        },
        "required": ["sentences"],
    }


# Generic schema (used when the fact list is not known yet, e.g. in docs and tests).
NARRATIVE_SCHEMA = narrative_schema(["fact.id"])


# Reading aids for facts whose sign or meaning small models tend to get backwards.
_NOTES = {
    "fault.z2_ohm": "below Z2F means forward, above Z2R means reverse",
    "fault.max_phase_rms_primary_a": "primary amperes",
    "fault.max_phase_rms_a": "secondary amperes",
    "relay.trip_after_fault_end_ms": "the trip came after the fault current had already gone",
}


def _note(f: Fact) -> str:
    """Reading aid for one fact; for signed times it states what this value's sign means."""
    if f.id == "fault.inception_from_trigger_ms" and isinstance(f.value, (int, float)):
        return "the fault began before the trigger" if f.value < 0 else "the fault began after the trigger"
    if f.id == "assessment.deviation_ms" and isinstance(f.value, (int, float)):
        return ("the relay operated later than expected" if f.value > 0
                else "the relay operated earlier than expected")
    return _NOTES.get(f.id, "")


def build_user_prompt(sheet: FactSheet, facts: list[Fact], passages: list[Passage]) -> str:
    known = [f for f in facts if not f.is_missing]
    unknown = [f for f in facts if f.is_missing]
    lines = ["FACTS:"]
    for f in known:
        note = f"; {_note(f)}" if _note(f) else ""
        lines.append(f"- {f.id} = {f.render()}  ({f.label}{note})")
    if unknown:
        lines.append("")
        lines.append("UNKNOWN:")
        for f in unknown:
            lines.append(f"- {f.id}: {f.reason}")
    if passages:
        lines.append("")
        lines.append("BACKGROUND (concepts only):")
        for p in passages:
            lines.append(f"[{p.tag}] ({p.source}, page {p.page}) {p.text[:600]}")
    lines.append("")
    lines.append("Write the explanation now as JSON.")
    return "\n".join(lines)
