"""Narrator and validator evaluation.

Two parts:

* ``evaluate_narration``: run the narrator (local LLM or template) on events and
  count what the validator kept, rejected and why, plus time per event.
* ``redteam_validator``: no model needed. Take the template narrative of each
  event (correct by construction), inject one known error per copy (wrong digit,
  wrong unit, number moved to the wrong subject, invented number, spelled-out
  number, wrong fault type, flipped direction, flipped verdict) and measure how
  many the validator catches, and how many correct sentences it wrongly rejects.
"""

from __future__ import annotations

import random
import re
import statistics
import time
from collections import Counter
from dataclasses import dataclass

from ..facts import FactSheet
from ..narrator import ESSENTIAL_TOPICS, LLMNarrator, TemplateNarrator
from ..validator import extract_claims, validate

_REASON_KINDS = (
    ("numbers not traceable", "number not produced by the core"),
    ("spelled-out", "spelled-out number"),
    ("fault type", "wrong fault type"),
    ("names phase", "wrong fault type"),
    ("describes a", "wrong fault type"),
    ("but the core found forward", "wrong direction"),
    ("but the core found reverse", "wrong direction"),
    ("verdict", "contradicts a verdict"),
    ("did not trip", "contradicts a verdict"),
    ("tripped, but", "contradicts a verdict"),
    ("slower than expected", "contradicts a verdict"),
    ("faster than expected", "contradicts a verdict"),
    ("time or date", "time/date not in the record"),
    ("incomplete", "incomplete sentence"),
)


def reason_kind(reason: str) -> str:
    for needle, kind in _REASON_KINDS:
        if needle in reason:
            return kind
    return "other"


def skipped_topics(sheet: FactSheet, delivered: list[str]) -> list[str]:
    """Essential topics the model left out, recognised as template sentences in the delivered text."""
    template = TemplateNarrator().sentences(sheet)
    out = []
    for topic, ids in ESSENTIAL_TOPICS:
        sent = next((t for t, cited in template if any(i in cited for i in ids)), None)
        if sent is not None and sent in delivered:
            out.append(topic)
    return out


def evaluate_narration(args, sheets: dict[str, FactSheet], ids: list[str]) -> dict:
    """Narrate each event and summarize what the validator did."""
    from ..llm import make_client
    from ..rag import load_or_build, query_for

    backend = getattr(args, "backend", "ollama")
    model = getattr(args, "model", "llama3.2:3b")
    server = None
    if backend == "template":
        narrator = TemplateNarrator()
    else:
        client = make_client(backend, model, getattr(args, "host", None))
        narrator = LLMNarrator(client)
        server = getattr(client, "server_version", lambda: None)()
    papers = getattr(args, "papers", None)
    index = load_or_build(papers) if papers is not None and papers.is_dir() else None

    per_event = []
    reasons: Counter = Counter()
    secs, tps, gen_tokens, prompt_tokens = [], [], [], []
    proposed = rejected = fallbacks = unavailable = 0
    model_numbers = model_numbers_traced = via_context = delivered_numbers = 0
    skipped: Counter = Counter()
    for eid in ids:
        sheet = sheets[eid]
        t0 = time.perf_counter()
        passages = index.search(query_for(sheet), k=3) if index is not None and sheet.value("fault.detected") else []
        if isinstance(narrator, TemplateNarrator):
            narr = narrator.narrate(sheet)
        else:
            narr = narrator.narrate(sheet, passages)
        dt = time.perf_counter() - t0
        if narr.fallback and narr.llm is None and not isinstance(narrator, TemplateNarrator):
            # The model was not reached: the template was shown. Nothing here measures the
            # model or the validator, so it is counted apart and left out of every rate.
            unavailable += 1
            per_event.append({"id": eid, "seconds": round(dt, 2), "model_unavailable": True,
                              "reason": narr.fallback_reason, "delivered": narr.sentences})
            print(f"  narrated {eid}: model unavailable ({narr.fallback_reason})", flush=True)
            continue
        v = narr.validation
        proposed += len(v.checks)
        rejected += len(v.rejected)
        fallbacks += int(narr.fallback)
        for c in v.rejected:
            for r in c.reasons:
                reasons[reason_kind(r)] += 1
        model_numbers += v.numbers_total
        model_numbers_traced += sum(len(c.numbers) - len(c.unsupported) for c in v.checks)
        d = narr.delivered_validation
        via_context += d.numbers_via_context
        delivered_numbers += d.numbers_delivered
        if not narr.fallback:
            skipped.update(narr.filled)
        secs.append(dt)
        if narr.llm is not None:
            if narr.llm.tokens_per_s:
                tps.append(narr.llm.tokens_per_s)
            if narr.llm.completion_tokens:
                gen_tokens.append(narr.llm.completion_tokens)
            if narr.llm.prompt_tokens:
                prompt_tokens.append(narr.llm.prompt_tokens)
        llm = narr.llm
        per_event.append({
            "id": eid, "seconds": round(dt, 2), "fallback": narr.fallback,
            "rejected": [{"text": c.text, "reasons": c.reasons, "notes": c.notes} for c in v.rejected],
            "proposed": [{"text": t, "fact_ids": ids} for t, ids in narr.proposed],
            "delivered": narr.sentences,
            "filled_from_template": narr.filled,
            "tokens_per_s": llm.tokens_per_s if llm else None,
            "prompt_tokens": llm.prompt_tokens if llm else None,
            "completion_tokens": llm.completion_tokens if llm else None,
            "raw_output": narr.raw_output or None,
        })
        print(f"  narrated {eid}: {len(v.kept)}/{len(v.checks)} kept, {dt:.1f} s"
              + (" (fallback)" if narr.fallback else ""), flush=True)

    n = len(ids) - unavailable
    return {
        "backend": backend,
        "model": model if backend != "template" else "-",
        "server": server,
        "retrieval": f"{len(index.passages)} passages from {len(index.files)} files" if index is not None else "off",
        "events": n,
        "model_unavailable": unavailable,
        "sentences": proposed,
        "rejected": rejected,
        "rejected_rate": rejected / proposed if proposed else 0.0,
        "reasons": dict(reasons.most_common()),
        "model_numbers": model_numbers,
        "model_numbers_traced_rate": model_numbers_traced / model_numbers if model_numbers else 1.0,
        "delivered_numbers": delivered_numbers,
        "delivered_numbers_via_context": via_context,
        "fallbacks": fallbacks,
        "model_skipped": dict(skipped.most_common()),
        "sec_median": statistics.median(secs) if secs else 0.0,
        "sec_max": max(secs) if secs else 0.0,
        "tok_s_median": statistics.median(tps) if tps else 0.0,
        "completion_tokens_median": statistics.median(gen_tokens) if gen_tokens else 0,
        "prompt_tokens_median": statistics.median(prompt_tokens) if prompt_tokens else 0,
        "per_event": per_event,
    }


# --------------------------------------------------------------------------- red team


@dataclass
class Mutation:
    kind: str
    text: str
    cited: list[str]


def _num_spans(text: str) -> list[re.Match]:
    """Spans of numbers the validator treats as claims."""
    claims = {c.token.lstrip("-") for c in extract_claims(text)}
    return [m for m in re.finditer(r"\d+(?:\.\d+)?", text) if m.group(0) in claims
            and not (m.start() > 0 and (text[m.start() - 1].isalpha() or text[m.start() - 1] in "._"))
            and not (m.end() < len(text) and (text[m.end()].isalnum() and text[m.end()] not in "ms"))]


def _bump_digit(token: str, rng: random.Random) -> str:
    digits = [i for i, ch in enumerate(token) if ch.isdigit()]
    i = digits[-1]
    d = int(token[i])
    new = (d + rng.choice([1, 2, 3, 4, 5, 6, 7, 8, 9])) % 10
    return token[:i] + str(new) + token[i + 1:]


_TYPE_SWAP = {"AG": ("BG", "B-phase-to-ground"), "BG": ("CG", "C-phase-to-ground"),
              "CG": ("AG", "A-phase-to-ground"), "AB": ("BC", "B-C phase-to-phase"),
              "BC": ("CA", "C-A phase-to-phase"), "CA": ("AB", "A-B phase-to-phase"),
              "ABG": ("BCG", "B-C-to-ground"), "BCG": ("CAG", "C-A-to-ground"),
              "CAG": ("ABG", "A-B-to-ground"), "ABC": ("AG", "A-phase-to-ground")}


def mutations(sentences: list[tuple[str, list[str]]], sheet: FactSheet, rng: random.Random) -> list[Mutation]:
    out: list[Mutation] = []
    ft = sheet.value("fault.type")
    desc = sheet.value("fault.type_description")
    direction = sheet.value("fault.direction")
    for text, cited in sentences:
        spans = _num_spans(text)
        if spans:
            m = rng.choice(spans)
            out.append(Mutation("wrong digit", text[:m.start()] + _bump_digit(m.group(0), rng) + text[m.end():], cited))
            # A unit the fact does not have.
            for m in spans:
                after = text[m.end():]
                um = re.match(r"\s(ms|cycles|A|ohm)\b", after)
                if um:
                    swap = {"ms": "cycles", "cycles": "ms", "A": "V", "ohm": "A"}[um.group(1)]
                    out.append(Mutation("wrong unit", text[:m.end()] + " " + swap + after[len(um.group(0)):], cited))
                    break
        # Invented number appended to a correct sentence.
        out.append(Mutation("invented number", text.rstrip(".") + ", about 17.3% above normal.", cited))
        if spans:
            out.append(Mutation("spelled-out number", re.sub(r"\d+(?:\.\d+)?\s+cycles", "three cycles", text, count=1)
                                if re.search(r"\d+(?:\.\d+)?\s+cycles", text) else
                                text.rstrip(".") + ", roughly two cycles.", cited))
        if ft in _TYPE_SWAP and (ft in text or (desc and desc in text)):
            code, words = _TYPE_SWAP[ft]
            t2 = re.sub(rf"\b{ft}\b", code, text)
            t2 = t2.replace(desc, words) if desc else t2
            out.append(Mutation("wrong fault type", t2, cited))
        if direction in ("forward", "reverse") and re.search(rf"\b{direction}\b", text):
            other = "reverse" if direction == "forward" else "forward"
            t2 = re.sub(rf"\b{direction}\b", other, text)
            t2 = t2.replace("in front of the relay", "behind the relay") if direction == "forward" else \
                t2.replace("behind the relay", "in front of the relay")
            out.append(Mutation("flipped direction", t2, cited))
        if " below the threshold " in text or " above the threshold " in text:
            flipped = (text.replace(" below the threshold ", " above the threshold ") if " below the threshold " in text
                       else text.replace(" above the threshold ", " below the threshold "))
            out.append(Mutation("flipped comparison", flipped, cited))
        if "operated as expected" in text:
            out.append(Mutation("flipped verdict", text.replace("operated as expected", "failed to trip"), cited))
        elif re.search(r"slower than|faster than", text):
            out.append(Mutation("flipped verdict", re.sub(r"operated (slower|faster) than its settings predict",
                                                          "operated as expected", text), cited))
    # A cited number replaced by the value of a different fact with the same unit. This
    # is the hardest case: the sentence still talks about something the new value could
    # describe, so binding by context may accept it.
    numeric = [f for f in sheet.numbers() if f.unit]
    for text, cited in sentences:
        for m in _num_spans(text):
            owner = next((sheet.get(c) for c in cited
                          if sheet.get(c) is not None and sheet.get(c).kind == "number"
                          and sheet.get(c).display.lstrip("-") == m.group(0)), None)
            if owner is None:
                continue
            others = [f for f in numeric if f.unit == owner.unit and f.id not in cited
                      and f.display.lstrip("-") != m.group(0)]
            if others:
                g = rng.choice(others)
                swapped = text[:m.start()] + g.display.lstrip("-") + text[m.end():]
                out.append(Mutation("value of a different fact", swapped, cited))
                # Same error from a narrator that cites nothing: only context binding is left.
                out.append(Mutation("value of a different fact, no citations", swapped, []))
            break
    # Number moved to the wrong subject: the relay operate time replaced by the breaker time.
    op = sheet.get("relay.operate_ms")
    br = sheet.get("breaker.interrupt_ms")
    if op is not None and br is not None and not op.is_missing and not br.is_missing:
        out.append(Mutation("number on the wrong subject",
                            f"The relay trip output asserted {br.render()} after the fault began.",
                            ["relay.operate_ms"]))
        out.append(Mutation("number on the wrong subject",
                            f"The relay trip output asserted {br.render()} after the fault began.", []))
    return out


def redteam_validator(sheets: dict[str, FactSheet], ids: list[str], seed: int = 11) -> dict:
    rng = random.Random(seed)
    tmpl = TemplateNarrator()
    caught: Counter = Counter()
    total: Counter = Counter()
    clean_total = clean_rejected = 0
    misses: list[dict] = []
    for eid in ids:
        sheet = sheets[eid]
        sents = tmpl.sentences(sheet)
        base = validate(sents, sheet, strict=True)
        clean_total += len(base.checks)
        clean_rejected += len(base.rejected)
        originals = {t for t, _ in sents}
        for mu in mutations(sents, sheet, rng):
            if mu.text in originals:
                continue  # the substitution did not change anything
            res = validate([(mu.text, mu.cited)], sheet, strict=True)
            total[mu.kind] += 1
            if res.rejected:
                caught[mu.kind] += 1
            elif len(misses) < 25:
                misses.append({"id": eid, "kind": mu.kind, "text": mu.text})
    kinds = sorted(total)
    return {
        "events": len(ids),
        "clean_sentences": clean_total,
        "clean_rejected": clean_rejected,
        "by_kind": {k: {"injected": total[k], "caught": caught[k], "rate": caught[k] / total[k]} for k in kinds},
        "injected": sum(total.values()),
        "caught": sum(caught.values()),
        "misses": misses,
    }
