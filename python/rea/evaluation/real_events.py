"""Real relay events: SEL's example files from its event-analysis webinar.

SEL publishes the event files used in the on-demand webinar "Introduction to Event
Analysis Using Actual Field Cases" (selinc.com/events/on-demand-webinar/131921/,
link "Example Event Files"). They are SEL's files, so they are downloaded to this
device on request and never committed:

    rea-real --fetch            # once, while online: download and unpack into data/real/
    rea-real                    # offline: convert, analyze, write eval/real/REAL_EVENTS.md

There are no ground-truth labels for field events. The comparison is with what the
relay itself recorded: its event type, its targets and the settings stored in the
record (phase rotation in particular).
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ..cev import convert, read_cev
from ..core_runner import CoreError, run_core
from ..facts import FactSheet

SEL_WEBINAR_ZIP = "https://selinc.com/api/download/132097/id/145550/0/"
SEL_WEBINAR_PAGE = "https://selinc.com/events/on-demand-webinar/131921/"
FOLDER = "sel_webinar_2020"


def fetch(data_dir: Path) -> Path:
    """Download and unpack the example events (one-time, needs the network)."""
    dest = data_dir / FOLDER
    if dest.is_dir() and any(dest.rglob("*.[cC][eE][vV]")):
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    archive = data_dir / "sel_webinar_event_files.zip"
    if not archive.exists():
        req = urllib.request.Request(SEL_WEBINAR_ZIP, headers={"User-Agent": "relay-event-analyst"})
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (fixed https URL)
            archive.write_bytes(resp.read())
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            target = (dest / info.filename).resolve()
            if not str(target).startswith(str(dest.resolve()) + os.sep):
                raise ValueError(f"unsafe path in archive: {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info))
    return dest


@dataclass
class RealResult:
    name: str
    source: str
    relay: str
    relay_event: str
    relay_targets: str
    relay_phrot: str
    sheet: FactSheet
    relay_currents: str = ""
    summary_check: str | None = None


def _v(sheet: FactSheet, fid: str) -> str:
    f = sheet.get(fid)
    if f is None:
        return "-"
    return f"unknown ({f.reason})" if f.is_missing else f.render()


def _summary_currents(summary: dict[str, str]) -> str:
    keys = [k for k in ("IA", "IB", "IC", "IA(A)", "IB(A)", "IC(A)", "IG", "IG(A)", "3I2") if summary.get(k)]
    return ", ".join(f"{k} {summary[k]}" for k in keys)


def summary_rotation_check(summary: dict[str, str]) -> str | None:
    """A second rotation check from the relay's own numbers. Balanced phase currents have no
    negative sequence, so the relay's 3I2 should be near zero. If it is near three times the
    phase current, the relay computed I2 with the rotation reversed: with A-C-B currents and
    the A-B-C formula, I2 = (IA + a^2 IB + a IC) / 3 adds three equal phasors."""
    try:
        phases = [float(summary[k]) for k in ("IA", "IB", "IC")]
        i2x3 = float(summary["3I2"])
    except (KeyError, ValueError):
        return None
    if min(phases) <= 0 or max(phases) > 1.1 * min(phases):
        return None  # not balanced: 3I2 is expected
    ratio = i2x3 / (sum(phases) / 3)
    if not 2.7 <= ratio <= 3.3:
        return None
    return (f"the relay's own summary lists balanced phase currents (IA, IB, IC = {', '.join(summary[k] for k in ('IA', 'IB', 'IC'))} A) "
            f"with 3I2 = {summary['3I2']} A, {ratio:.2f} times the phase current. Balanced currents have no negative "
            "sequence; three times the phase current is what the relay computes when its rotation setting is the "
            "reverse of the actual rotation.")


def analyze_all(src: Path, work: Path, core: str | None = None) -> list[RealResult]:
    out: list[RealResult] = []
    for cev in sorted(src.rglob("*"), key=lambda p: str(p).lower()):
        if cev.suffix.lower() != ".cev":
            continue
        ev = read_cev(cev)
        stem = cev.stem.replace(" ", "_")
        cfg, settings, _ = convert(cev, work, stem)
        data, _ = run_core(cfg, settings, core=core)
        out.append(RealResult(
            name=f"{cev.parent.name} / {cev.name}", source="SEL compressed event (.cev), converted by rea",
            relay=ev.relay, relay_event=ev.summary.get("EVENT", "-"),
            relay_targets=ev.summary.get("TARGETS", "-") or "-", relay_phrot=ev.settings.get("PHROT", "-"),
            sheet=FactSheet(data), relay_currents=_summary_currents(ev.summary),
            summary_check=summary_rotation_check(ev.summary)))
    # SEL's own COMTRADE conversion of the practice event, analyzed as delivered.
    for cfg in sorted(src.rglob("*.cfg")):
        settings = work / "sel_comtrade.settings.txt"
        twin = next(iter(cfg.parent.glob("*.cev")), None)
        phrot = read_cev(twin).settings.get("PHROT", "") if twin else ""
        settings.write_text(f"PHROT := {phrot}\n" if phrot else "", encoding="utf-8")
        data, _ = run_core(cfg, settings, core=core)
        sheet = FactSheet(data)
        out.append(RealResult(
            name=f"{cfg.parent.name} / {cfg.name}", source="COMTRADE 1991 as converted by SEL (values as recorded)",
            relay=(read_cev(twin).relay if twin else "-"), relay_event=(read_cev(twin).summary.get("EVENT", "-") if twin else "-"),
            relay_targets=(read_cev(twin).summary.get("TARGETS", "-") if twin else "-"), relay_phrot=phrot or "-",
            sheet=sheet))
    return out


def render(results: list[RealResult]) -> str:
    md = [
        "# Real events: SEL's event-analysis webinar files",
        "",
        f"Source: SEL on-demand webinar *Introduction to Event Analysis Using Actual Field Cases* ({SEL_WEBINAR_PAGE}),",
        "\"Example Event Files\". The files are SEL's and are not in this repository; `rea-real --fetch` downloads them.",
        "",
        "Field events have no ground-truth labels. Each row compares what rea-core found with what the relay itself",
        "recorded in the same file (its event type, targets and PHROT setting).",
        "",
        "| Event | Relay | Relay says | rea-core fault type | Rotation check | Inception / end | Trip | rea-core verdict |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        s = r.sheet
        says = r.relay_event + (f"; targets {r.relay_targets}" if r.relay_targets not in ("-", "") else "")
        ftype = _v(s, "fault.type")
        rot = s.get("assessment.rotation")
        rot_txt = rot.render() if rot is not None and not rot.is_missing else f"{_v(s, 'record.rotation')} (no PHROT to compare)"
        inc = s.get("fault.inception_from_trigger_ms")
        timing = (f"starts {inc.render()} from trigger" if inc is not None and not inc.is_missing else "fault present at record start")
        if s.get("fault.duration_cycles") is not None and not s.get("fault.duration_cycles").is_missing:
            timing += f", lasts {_v(s, 'fault.duration_cycles')}"
        elif s.value("fault.end_cause"):
            timing += f"; {s.value('fault.end_cause')}"
        trip = "no trip" if s.value("relay.tripped") is False else (
            f"{_v(s, 'relay.trip_after_fault_end_cycles')} after the fault ended"
            if s.get("relay.trip_after_fault_end_cycles") is not None else _v(s, "relay.operate_cycles"))
        md.append(f"| {r.name} | {r.relay} | {says} | {ftype} | {rot_txt} | {timing} | {trip} | {_v(s, 'assessment.relay')} |")
    md += ["", "## Notes per event", ""]
    for r in results:
        s = r.sheet
        md.append(f"### {r.name}")
        md.append("")
        md.append(f"- Source: {r.source}; relay {r.relay}; relay's event type `{r.relay_event}`, PHROT `{r.relay_phrot}`.")
        if r.relay_currents:
            md.append(f"- Relay's own event summary currents (A primary): {r.relay_currents}")
        if r.summary_check:
            md.append(f"- Rotation check from the relay's summary: {r.summary_check}")
        for fid in ("fault.type_description", "fault.direction", "fault.max_phase_rms_primary_a", "fault.after_breaker_close_ms",
                    "relay.trip_after_fault_end_ms", "assessment.breaker"):
            f = s.get(fid)
            if f is not None:
                md.append(f"- {f.label}: {'unknown (' + f.reason + ')' if f.is_missing else f.render()}")
        for w in s.warnings:
            md.append(f"- core warning: {w}")
        md.append("")
    md += [
        "## Reading the results",
        "",
        "- The motor-relay records (SEL-710) are motor starts and trips, not short circuits. rea-core is a",
        "  feeder/line fault analyzer with no motor model, so it reports the starting current as a balanced or",
        "  ground disturbance; it does flag when the disturbance follows a breaker close.",
        "- Magnitudes from faults shorter than the phasor window are underestimated and say so.",
        "- The relay's event summary gives a second rotation check that does not depend on rea-core: with",
        "  balanced phase currents its 3I2 should be near zero, and near three times the phase current when",
        "  the relay's rotation setting is the reverse of the actual rotation.",
        "",
    ]
    return "\n".join(md)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=Path("data/real"))
    ap.add_argument("--out", type=Path, default=Path("eval/real"))
    ap.add_argument("--core", default=None)
    ap.add_argument("--fetch", action="store_true", help="download SEL's example files first (needs the network)")
    args = ap.parse_args(argv)
    src = args.data / FOLDER
    if args.fetch:
        src = fetch(args.data)
    if not src.is_dir():
        print(f"no events in {src}: run once with --fetch while online")
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        results = analyze_all(src, args.out / "converted", args.core)
    except CoreError as exc:
        print(f"rea-real: {exc}")
        return 2
    report = render(results)
    (args.out / "REAL_EVENTS.md").write_text(report, encoding="utf-8")
    (args.out / "real_events.json").write_text(json.dumps(
        [{"event": r.name, "relay": r.relay, "relay_event": r.relay_event, "relay_targets": r.relay_targets,
          "relay_phrot": r.relay_phrot, "facts": r.sheet.raw} for r in results], indent=1), encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
