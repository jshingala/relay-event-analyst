"""rea: analyze a relay event offline.

    rea analyze event.cfg --settings relay.txt              # local LLM via Ollama
    rea analyze event.cev                                   # SEL compressed event report
    rea analyze event.cfg --backend template                # no LLM at all
    rea analyze event.cfg --backend openai --host http://127.0.0.1:8080   # llama.cpp server
    rea convert-cev event.cev --out folder/                 # .cev -> COMTRADE + settings
    rea index-papers                                        # (re)build the BM25 index of papers/
    rea doctor                                              # check core, model, papers, network
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .core_runner import CoreError, find_core
from .llm import LLMError, make_client


def _cmd_analyze(args: argparse.Namespace) -> int:
    from .pipeline import analyze_event
    from .report import render_markdown, render_text, to_json

    settings = args.settings
    if settings is None:
        guess = Path(args.cfg).with_suffix(".settings.txt")
        settings = guess if guess.exists() else None
    if not Path(args.cfg).exists():
        print(f"rea: {args.cfg} not found", file=sys.stderr)
        return 2
    try:
        report = analyze_event(
            Path(args.cfg), settings, backend=args.backend, model=args.model, host=args.host,
            papers=None if args.no_papers else Path(args.papers), k=args.k, core=args.core,
            filter=args.filter, strict=not args.lenient,
        )
    except CoreError as exc:
        print(f"rea: physics core failed: {exc}", file=sys.stderr)
        return 2
    except LLMError as exc:
        print(f"rea: {exc}", file=sys.stderr)
        return 3
    print(render_text(report, show_facts=not args.quiet))
    if args.json:
        Path(args.json).write_text(to_json(report), encoding="utf-8")
    if args.md:
        Path(args.md).write_text(render_markdown(report), encoding="utf-8")
    return 0


def _cmd_convert_cev(args: argparse.Namespace) -> int:
    from .cev import CevError, convert

    try:
        cfg, settings, ev = convert(args.cev, args.out)
    except (CevError, OSError) as exc:
        print(f"rea: {exc}", file=sys.stderr)
        return 2
    print(f"{ev.relay}: {len(ev.analog[0])} samples, event {ev.summary.get('EVENT', '?')!r} -> {cfg} (+ {settings.name})")
    if ev.bad_checksums:
        print(f"warning: {ev.bad_checksums} line(s) failed their checksum", file=sys.stderr)
    return 0


def _cmd_index(args: argparse.Namespace) -> int:
    from .rag import build_index, index_path

    papers = Path(args.papers)
    if not papers.is_dir():
        print(f"rea: {papers} does not exist", file=sys.stderr)
        return 1
    idx = build_index(papers)
    print(f"indexed {len(idx.files)} files into {len(idx.passages)} passages ({index_path(papers)})")
    return 0


def network_state() -> str:
    """Describe connectivity without sending a packet: links with carrier and the default route."""
    net = Path("/sys/class/net")
    if not net.is_dir():
        return "unknown (not Linux)"
    links = []
    for iface in sorted(p.name for p in net.iterdir() if p.name != "lo"):
        try:
            if (net / iface / "carrier").read_text().strip() == "1":
                links.append(iface)
        except OSError:
            pass  # interface down
    default = []
    try:
        for line in Path("/proc/net/route").read_text().splitlines()[1:]:
            parts = line.split()
            if len(parts) > 2 and parts[1] == "00000000":
                default.append(parts[0])
    except OSError:
        pass
    if not links:
        stale = f" (a stale default route via {', '.join(default)} remains)" if default else ""
        return "OFFLINE: no network interface has a link" + stale
    return (f"link up on {', '.join(links)}; default route via {', '.join(default) or 'none'} "
            "(rea sends nothing either way)")


def _cmd_doctor(args: argparse.Namespace) -> int:
    ok = True
    try:
        core = find_core(args.core)
        print(f"[ok]   physics core: {core}")
    except CoreError as exc:
        ok = False
        print(f"[fail] physics core: {exc}")
    if args.backend == "template":
        print("[ok]   narrator: template (no LLM)")
    else:
        try:
            alive, msg = make_client(args.backend, args.model, args.host).ping()
            print(f"[{'ok' if alive else 'fail'}]   {'' if alive else ' '}narrator: {msg}".replace("]    ", "]   "))
            ok = ok and alive
        except LLMError as exc:
            ok = False
            print(f"[fail] narrator: {exc}")
    papers = Path(args.papers)
    from .rag import NOT_PAPERS

    files = [p for p in papers.glob("*") if p.suffix.lower() in (".pdf", ".txt", ".md")
             and p.name.lower() not in NOT_PAPERS] if papers.is_dir() else []
    print(f"[{'ok' if files else 'info'}]   papers: {len(files)} file(s) in {papers}" + ("" if files else " (retrieval disabled)"))
    print(f"[info] network: {network_state()}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rea", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"rea {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    env = os.environ.get

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--backend", choices=("ollama", "openai", "template"), default=env("REA_BACKEND", "ollama"))
        p.add_argument("--model", default=env("REA_MODEL", "llama3.2:3b"))
        p.add_argument("--host", default=env("REA_LLM_HOST"), help="LLM server URL (loopback only)")
        p.add_argument("--papers", default=env("REA_PAPERS", "papers"), help="folder of local papers for retrieval")
        p.add_argument("--core", default=None, help="path to rea-core (or set REA_CORE)")

    a = sub.add_parser("analyze", help="analyze one event: COMTRADE .cfg or SEL compressed .cev")
    a.add_argument("cfg", help="event file (.cfg with its .dat, or .cev)")
    a.add_argument("--settings", default=None, help="relay settings file (default: <event>.settings.txt)")
    a.add_argument("--filter", choices=("cosine", "dft"), default="cosine")
    a.add_argument("--k", type=int, default=3, help="background passages to retrieve")
    a.add_argument("--no-papers", action="store_true")
    a.add_argument("--lenient", action="store_true", help="numbers may match any fact, not only cited ones")
    a.add_argument("--quiet", action="store_true", help="hide the key-facts table")
    a.add_argument("--json", default=None, help="write the full report as JSON")
    a.add_argument("--md", default=None, help="write the report as Markdown")
    common(a)
    a.set_defaults(func=_cmd_analyze)

    c = sub.add_parser("convert-cev", help="convert an SEL compressed event report (.cev) to COMTRADE")
    c.add_argument("cev")
    c.add_argument("--out", default=".", help="output folder")
    c.set_defaults(func=_cmd_convert_cev)

    i = sub.add_parser("index-papers", help="build the retrieval index for papers/")
    i.add_argument("--papers", default=env("REA_PAPERS", "papers"))
    i.set_defaults(func=_cmd_index)

    d = sub.add_parser("doctor", help="check the core, the local model, papers and network")
    common(d)
    d.set_defaults(func=_cmd_doctor)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
