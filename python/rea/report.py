"""Render an AnalysisReport for the terminal, as Markdown, or as JSON."""

from __future__ import annotations

import json
import textwrap

from .pipeline import AnalysisReport

_HEADLINE_IDS = (
    "fault.type", "fault.type_description", "fault.direction", "fault.z2_ohm", "fault.max_phase",
    "fault.max_phase_rms_a", "fault.max_phase_rms_primary_a", "fault.3i0_a", "relay.operate_ms",
    "relay.operate_cycles", "expected.element", "expected.operate_ms", "assessment.deviation_ms",
    "assessment.tolerance_ms", "assessment.relay", "breaker.interrupt_ms", "breaker.interrupt_cycles",
    "assessment.breaker", "fault.duration_ms", "fault.end_cause",
)


def _audit_line(r: AnalysisReport) -> str:
    n = r.narrative
    v = n.validation
    if not v.checks:
        return "no sentences"
    who = "model" if n.llm is not None else "template"
    parts = [f"{len(v.kept)}/{len(v.checks)} {who} sentences passed"]
    d = n.delivered_validation
    traced = f"{d.numbers_traced_delivered}/{d.numbers_delivered} numbers shown are traced to core facts"
    if d.numbers_via_context:
        traced += f" ({d.numbers_via_context} bound by context, the rest by citation)"
    parts.append(traced)
    if n.fallback:
        parts.append("fallback: " + n.fallback_reason)
    if n.filled:
        parts.append("model skipped " + ", ".join(n.filled) + ": added the template sentence")
    return " | ".join(parts)


def _timing_line(r: AnalysisReport) -> str:
    t = r.timings
    s = f"core {t['core_analyze_ms']:.1f} ms (process {1000 * t['core_s']:.0f} ms) | retrieval {1000 * t['retrieval_s']:.0f} ms"
    if "llm_s" in t:
        s += f" | LLM {t['llm_s']:.1f} s"
        extra = []
        if t.get("llm_load_s"):
            extra.append(f"load {t['llm_load_s']:.1f} s")
        if t.get("llm_prompt_s") is not None and t.get("prompt_tokens"):
            extra.append(f"prompt {t['prompt_tokens']} tok in {t['llm_prompt_s']:.1f} s")
        if t.get("tokens_per_s") and t.get("completion_tokens"):
            extra.append(f"{t['completion_tokens']} tok at {t['tokens_per_s']:.1f} tok/s")
        if extra:
            s += " (" + ", ".join(extra) + ")"
    s += f" | total {t['total_s']:.1f} s"
    return s


def render_text(r: AnalysisReport, show_facts: bool = True, width: int = 92) -> str:
    out = []
    bar = "=" * width
    out.append(bar)
    out.append(f"Relay Event Analyst  |  {r.cfg.name}  |  narrator: {r.narrative.narrator}")
    out.append(bar)
    out.append("WHAT HAPPENED")
    for s in r.narrative.sentences:
        out.append(textwrap.fill(s, width=width, initial_indent="  ", subsequent_indent="  "))
    if show_facts:
        out.append("")
        out.append("KEY FACTS (computed by the physics core)")
        for fid in _HEADLINE_IDS:
            f = r.sheet.get(fid)
            if f is None:
                continue
            val = f"UNKNOWN - {f.reason}" if f.is_missing else f.render()
            out.append(f"  {fid:<34} {val}")
    missing = [f for f in r.sheet.missing() if f.priority == 1]
    if missing:
        out.append("")
        out.append("COULD NOT BE DETERMINED")
        for f in missing:
            out.append(f"  {f.label}: {f.reason}")
    rejected = r.narrative.validation.rejected
    out.append("")
    out.append("VALIDATOR  " + _audit_line(r))
    for c in rejected[:6]:
        why = c.reasons + [n for n in c.notes if "neither cites" in n]
        out.append(textwrap.fill(f"rejected: \"{c.text}\" -> {'; '.join(why)}", width=width,
                                 initial_indent="  ", subsequent_indent="    "))
    if len(rejected) > 6:
        out.append(f"  ... and {len(rejected) - 6} more (see --json)")
    if r.passages:
        out.append("")
        out.append("BACKGROUND SOURCES (local files, concepts only)")
        for p in r.passages:
            out.append(f"  [{p.tag}] {p.source}, page {p.page}")
    if r.sheet.warnings:
        out.append("")
        out.append("CORE WARNINGS")
        for w in r.sheet.warnings:
            out.append(textwrap.fill(w, width=width, initial_indent="  - ", subsequent_indent="    "))
    out.append("")
    out.append("TIMING  " + _timing_line(r))
    out.append(bar)
    return "\n".join(out)


def render_markdown(r: AnalysisReport) -> str:
    md = [f"# Event analysis: `{r.cfg.name}`", "", f"Narrator: `{r.narrative.narrator}`", "", "## What happened", ""]
    md += [f"- {s}" for s in r.narrative.sentences]
    md += ["", "## Key facts (physics core)", "", "| Fact | Value |", "|---|---|"]
    for fid in _HEADLINE_IDS:
        f = r.sheet.get(fid)
        if f is not None:
            md.append(f"| `{fid}` | {('unknown: ' + f.reason) if f.is_missing else f.render()} |")
    missing = [f for f in r.sheet.missing() if f.priority == 1]
    if missing:
        md += ["", "## Could not be determined", ""]
        md += [f"- {f.label}: {f.reason}" for f in missing]
    md += ["", "## Validator", "", _audit_line(r)]
    for c in r.narrative.validation.rejected:
        md.append(f"- rejected: \"{c.text}\": {'; '.join(c.reasons)}")
    if r.passages:
        md += ["", "## Background sources", ""]
        md += [f"- [{p.tag}] {p.source}, page {p.page}" for p in r.passages]
    if r.sheet.warnings:
        md += ["", "## Core warnings", ""]
        md += [f"- {w}" for w in r.sheet.warnings]
    md += ["", "## Timing", "", _timing_line(r), ""]
    return "\n".join(md)


def to_json(r: AnalysisReport) -> str:
    v = r.narrative.validation
    return json.dumps({
        "cfg": str(r.cfg),
        "settings": str(r.settings) if r.settings else None,
        "narrator": r.narrative.narrator,
        "fallback": r.narrative.fallback,
        "fallback_reason": r.narrative.fallback_reason,
        "filled_from_template": r.narrative.filled,
        "narrative": r.narrative.sentences,
        "validation": [
            {"text": c.text, "ok": c.ok, "numbers": c.numbers, "unsupported": c.unsupported,
             "reasons": c.reasons, "fact_ids": c.fact_ids, "bindings": c.bindings, "notes": c.notes}
            for c in v.checks
        ],
        "raw_model_output": r.narrative.raw_output or None,
        "passages": [{"tag": p.tag, "source": p.source, "page": p.page, "score": p.score} for p in r.passages],
        "timings": r.timings,
        "facts": r.sheet.raw,
    }, indent=2)
