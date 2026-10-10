"""rea-eval: score the physics core (and optionally the narrator) on a labeled set.

    rea-eval --data data/synth                     # core only
    rea-eval --data data/synth --narrate --limit 20   # plus local-LLM narration stats
    rea-eval ... --power-log logs/tegrastats.log    # attach Jetson power numbers
    rea-eval ... --narrate --resume                 # continue an interrupted narration run

Narration writes each event to <out>/narration.partial.jsonl as soon as it is done;
--resume skips the events already there. The partial file is removed once the final
narration.jsonl, summary.json and RESULTS.md are written.

The validator red team (injected errors, no model needed) runs by default.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import platform
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from ..core_runner import CoreError, find_core, run_core
from ..facts import FactSheet
from .metrics import EventScore, score_event, summarize


def evaluate_core(data_dir: Path, core: str | None, filt: str, limit: int | None) -> tuple[list[EventScore], dict]:
    manifest = json.loads((data_dir / "manifest.json").read_text())
    events = manifest["events"][:limit] if limit else manifest["events"]
    scores: list[EventScore] = []
    sheets: dict = {}
    for ev in events:
        labels = json.loads((data_dir / ev["labels"]).read_text())
        try:
            data, wall = run_core(data_dir / ev["cfg"], data_dir / ev["settings"], core=core, filter=filt)
            sheet = FactSheet(data)
            sheets[ev["id"]] = sheet
            scores.append(score_event(sheet, labels, wall))
        except CoreError as exc:
            s = EventScore(ev["id"], labels["fault_type"], None, labels["direction"], None, labels["anomaly"],
                           labels["variant"], labels["record"]["samples_per_cycle"], 0.0, error=str(exc))
            scores.append(s)
    return scores, sheets


def _pct(x: float) -> str:
    return f"{100.0 * x:.1f}%"


def _fmt_stats(d: dict, unit: str, digits: int = 2) -> str:
    if not d.get("n"):
        return "n/a"
    return f"median {d['median']:.{digits}f} {unit}, p95 {d['p95']:.{digits}f} {unit}, max {d['max']:.{digits}f} {unit} (n={d['n']})"


def render_markdown(summary, meta: dict, narr: dict | None, power: dict | None,
                    redteam: dict | None = None) -> str:
    s = summary
    lines = [
        "# Evaluation results",
        "",
        f"Generated {meta['generated_utc']} on `{meta['host']}` from `{meta['data']}` "
        f"({s.n_events} synthetic events, generator seed {meta.get('seed')}).",
        "",
        "These are synthetic events from the included simulator, not field records.",
        "",
        "## Physics core",
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| Fault type (10 classes) | {_pct(s.fault_type_accuracy)} |",
        f"| Direction, when decided | {_pct(s.direction_accuracy_decided)} correct; decided on {_pct(s.direction_coverage)} of events |",
        f"| Relay verdict | {_pct(s.relay_verdict_accuracy)} |",
        f"| Breaker verdict | {_pct(s.breaker_verdict_accuracy)} |",
        f"| Inception time error | {_fmt_stats(s.inception_abs_err_ms, 'ms')} |",
        f"| Inception time error in samples | {_fmt_stats(s.inception_abs_err_samples, 'samples')} |",
        f"| Trip time error | {_fmt_stats(s.trip_abs_err_ms, 'ms')} |",
        f"| Fault end error | {_fmt_stats(s.fault_end_abs_err_ms, 'ms')} |",
        f"| Expected operate time vs closed form | {_fmt_stats(s.expected_vs_closed_form_cycles, 'cycles')} |",
        f"| Steady fault current error | {_fmt_stats(s.magnitude_abs_err_pct, '%')} |",
        f"| Core time per event (incl. process start) | {_fmt_stats(s.core_wall_ms, 'ms', 1)} |",
        f"| Events that errored | {s.n_errors} |",
        "",
        "### Verdicts by injected anomaly",
        "",
        "| Anomaly | Events | Relay verdict correct | Breaker verdict correct |",
        "|---|---|---|---|",
    ]
    for name, a in sorted(s.per_anomaly.items()):
        lines.append(f"| {name} | {a['n']} | {a['relay_ok']}/{a['n']} | {a['breaker_ok']}/{a['n']} |")
    lines += ["", "### Fault-type confusion (rows = truth)", ""]
    types = ["AG", "BG", "CG", "AB", "BC", "CA", "ABG", "BCG", "CAG", "ABC"]
    lines.append("| truth \\ predicted | " + " | ".join(types) + " | other |")
    lines.append("|---" * (len(types) + 2) + "|")
    for t in types:
        row = s.confusion.get(t, {})
        other = sum(v for k, v in row.items() if k not in types)
        lines.append(f"| **{t}** | " + " | ".join(str(row.get(p, 0) or "") for p in types) + f" | {other or ''} |")
    if redteam:
        rt = redteam
        lines += [
            "",
            "## Validator red team (no model needed)",
            "",
            f"The template narrative of {rt['events']} events was copied and one known error was injected per copy.",
            f"Correct template sentences wrongly rejected: {rt['clean_rejected']} of {rt['clean_sentences']}.",
            "",
            "| Injected error | Copies | Caught |",
            "|---|---|---|",
        ]
        for kind, k in rt["by_kind"].items():
            lines.append(f"| {kind} | {k['injected']} | {k['caught']} ({_pct(k['rate'])}) |")
        lines.append(f"| **all** | {rt['injected']} | {rt['caught']} ({_pct(rt['caught'] / max(rt['injected'], 1))}) |")
        if rt["misses"]:
            lines += ["", "Missed (first few):", ""]
            lines += [f"- {m['kind']}: \"{m['text']}\"" for m in rt["misses"][:8]]
    if narr:
        reasons = ", ".join(f"{k} {v}" for k, v in narr["reasons"].items()) or "none"
        skipped = ", ".join(f"{k} in {v}" for k, v in narr.get("model_skipped", {}).items()) or "none"
        lines += [
            "",
            "## Narrator (local LLM + validator)",
            "",
            "| Metric | Result |",
            "|---|---|",
            f"| Backend / model | {narr.get('server') or narr['backend']} / `{narr['model']}` |",
            f"| Retrieval (local papers) | {narr.get('retrieval', '-')} |",
            f"| Events narrated by the model | {narr['events']} |",
        ]
        if narr.get("model_unavailable"):
            lines.append(f"| Events where the model could not be reached (template shown, not counted) | "
                         f"{narr['model_unavailable']} |")
        lines += [
            f"| Sentences proposed | {narr['sentences']} |",
            f"| Sentences rejected by the validator | {narr['rejected']} ({_pct(narr['rejected_rate'])}) |",
            f"| Rejection reasons | {reasons} |",
            f"| Numbers written by the model that trace to a core fact | {_pct(narr['model_numbers_traced_rate'])} of {narr['model_numbers']} |",
            f"| Events that fell back to the template narrative | {narr['fallbacks']} of {narr['events']} |",
            f"| Essential topics the model skipped (template sentence added, flagged in the audit) | {skipped} |",
            f"| Seconds per event (retrieval + narration) | median {narr['sec_median']:.1f}, max {narr['sec_max']:.1f} |",
            f"| Generation speed | median {narr['tok_s_median']:.1f} tokens/s; median {narr['completion_tokens_median']} tokens out, {narr['prompt_tokens_median']} in |",
        ]
    if power:
        lines += [
            "",
            "## Power (tegrastats)",
            "",
            f"- Board input power ({power['rail']}) during the run: mean {power['mean_w']:.2f} W, "
            f"max {power['max_w']:.2f} W over {power['samples']} samples ({power['duration_s']:.0f} s).",
        ]
        if power.get("joules_per_event") is not None:
            lines.append(f"- Energy per event: {power['joules_per_event']:.1f} J")
    if s.failures:
        lines += ["", "## Events with a wrong answer", ""]
        for f in s.failures[:40]:
            lines.append(f"- `{f['id']}` ({f.get('anomaly', '?')}/{f.get('variant', '?')}): " + "; ".join(f["problems"]))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=Path("data/synth"))
    ap.add_argument("--out", type=Path, default=Path("eval/results"))
    ap.add_argument("--core", default=None, help="path to rea-core (default: build/core/rea-core)")
    ap.add_argument("--filter", choices=("cosine", "dft"), default="cosine")
    ap.add_argument("--limit", type=int, default=None, help="only the first N events")
    ap.add_argument("--narrate", action="store_true", help="also run the narrator on --narrate-limit events")
    ap.add_argument("--narrate-limit", type=int, default=20)
    ap.add_argument("--backend", default="ollama", choices=("ollama", "openai", "template"))
    ap.add_argument("--model", default="llama3.2:3b")
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("--papers", type=Path, default=Path("papers"))
    ap.add_argument("--power-log", type=Path, default=None, help="tegrastats log captured during this run")
    ap.add_argument("--power-interval-ms", type=float, default=1000.0, help="tegrastats --interval used")
    ap.add_argument("--no-redteam", action="store_true", help="skip the validator red team")
    ap.add_argument("--resume", action="store_true",
                    help="continue an interrupted --narrate run from <out>/narration.partial.jsonl")
    args = ap.parse_args(argv)

    find_core(args.core)  # fail early with a clear message
    t_start = time.time()
    scores, sheets = evaluate_core(args.data, args.core, args.filter, args.limit)
    summary = summarize(scores)

    redteam = None
    if not args.no_redteam:
        from .narration import redteam_validator

        redteam = redteam_validator(sheets, list(sheets))

    narr = None
    if args.narrate:
        from .narration import PARTIAL, evaluate_narration

        narr = evaluate_narration(args, sheets, list(sheets)[: args.narrate_limit],
                                  partial=args.out / PARTIAL, resume=args.resume)
    t_end = time.time()

    power = None
    if args.power_log:
        from .power import summarize_power

        # The power log covers this process only, so a resumed run divides by the events it narrated.
        n_power = narr["events_this_run"] if narr else len(scores)
        power = summarize_power(args.power_log, n_events=n_power, interval_ms=args.power_interval_ms)

    manifest = json.loads((args.data / "manifest.json").read_text())
    meta = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "host": f"{platform.machine()} {platform.system()}, {os.cpu_count()} CPUs",
        "data": str(args.data),
        "seed": manifest.get("seed"),
        "filter": args.filter,
        "wall_s": round(t_end - t_start, 2),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    narr_events = narr.pop("per_event", []) if narr else []
    (args.out / "summary.json").write_text(
        json.dumps({"meta": meta, "core": dataclasses.asdict(summary), "narrator": narr, "power": power,
                    "validator_redteam": redteam}, indent=2)
    )
    (args.out / "events.json").write_text(
        json.dumps([dataclasses.asdict(s) for s in scores], indent=1)
    )
    if narr_events:
        (args.out / "narration.jsonl").write_text("\n".join(json.dumps(e) for e in narr_events) + "\n")
    report = render_markdown(summary, meta, narr, power, redteam)
    (args.out / "RESULTS.md").write_text(report)
    if narr is not None:
        from .narration import PARTIAL

        (args.out / PARTIAL).unlink(missing_ok=True)
    print(report)
    return 0 if summary.n_errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
