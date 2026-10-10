"""End-to-end analysis of one event: core -> retrieval -> narrator -> validator."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from .core_runner import run_core
from .facts import FactSheet
from .llm import make_client
from .narrator import LLMNarrator, Narrative, TemplateNarrator
from .rag import Passage, load_or_build, query_for


@dataclass
class AnalysisReport:
    cfg: Path
    settings: Path | None
    sheet: FactSheet
    narrative: Narrative
    passages: list[Passage]
    timings: dict = field(default_factory=dict)


def analyze_event(
    cfg: Path,
    settings: Path | None = None,
    *,
    backend: str = "ollama",
    model: str = "llama3.2:3b",
    host: str | None = None,
    papers: Path | None = Path("papers"),
    k: int = 3,
    core: str | None = None,
    filter: str = "cosine",
    strict: bool = True,
) -> AnalysisReport:
    t0 = time.perf_counter()
    if Path(cfg).suffix.lower() == ".cev":
        # SEL compressed event report: convert to COMTRADE (plus the relay settings it
        # carries) in a scratch folder that is removed after the core has read it.
        import tempfile

        from .cev import convert

        with tempfile.TemporaryDirectory(prefix="rea-cev-") as tmp:
            converted, generated, _ = convert(cfg, tmp)
            data, core_s = run_core(converted, settings or generated, core=core, filter=filter)
    else:
        data, core_s = run_core(cfg, settings, core=core, filter=filter)
    sheet = FactSheet(data)

    t1 = time.perf_counter()
    passages: list[Passage] = []
    if papers is not None and sheet.value("fault.detected"):
        index = load_or_build(Path(papers))
        if index is not None:
            passages = index.search(query_for(sheet), k=k)
    t2 = time.perf_counter()

    if backend == "template":
        narrative = TemplateNarrator().narrate(sheet)
    else:
        client = make_client(backend, model, host)
        narrative = LLMNarrator(client, strict=strict).narrate(sheet, passages)
    t3 = time.perf_counter()

    timings = {
        "core_s": core_s,
        "core_analyze_ms": float(sheet.meta.get("analyze_ms", 0.0)) + float(sheet.meta.get("parse_ms", 0.0)),
        "retrieval_s": t2 - t1,
        "narration_s": t3 - t2,
        "total_s": t3 - t0,
    }
    if narrative.llm is not None:
        r = narrative.llm
        timings.update(llm_s=r.seconds, tokens_per_s=r.tokens_per_s, completion_tokens=r.completion_tokens,
                       prompt_tokens=r.prompt_tokens, llm_load_s=r.load_s, llm_prompt_s=r.prompt_s,
                       llm_gen_s=r.gen_s)
    return AnalysisReport(cfg=Path(cfg), settings=Path(settings) if settings else None, sheet=sheet,
                          narrative=narrative, passages=passages, timings=timings)
