"""Parse a tegrastats log and summarize board power.

tegrastats prints one line per interval. Power rails appear as
``NAME current/average`` with or without an ``mW`` suffix depending on the
JetPack release, for example::

    ... VDD_IN 5123mW/4998mW VDD_CPU_GPU_CV 1234mW/1100mW VDD_SOC 1400mW/1390mW
    ... POM_5V_IN 3816/3816 POM_5V_GPU 0/0 POM_5V_CPU 816/816

The board input rail (``VDD_IN`` on Orin, ``POM_5V_IN`` on older modules) is
the total power drawn by the module; the other rails are subsets of it.
Only the instantaneous (first) value of each pair is used.
"""

from __future__ import annotations

import re
import statistics
from pathlib import Path

_RAIL = re.compile(r"\b([A-Z][A-Z0-9_]+)\s+(\d+)(?:mW)?/(\d+)(?:mW)?\b")
_TOTAL_RAILS = ("VDD_IN", "POM_5V_IN", "VIN_SYS_5V0")


def parse_line(line: str) -> dict[str, float]:
    """Instantaneous power per rail in watts."""
    return {name: int(cur) / 1000.0 for name, cur, _avg in _RAIL.findall(line)}


def read_log(path: Path) -> list[dict[str, float]]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        rails = parse_line(line)
        if rails:
            rows.append(rails)
    return rows


def total_rail(rows: list[dict[str, float]]) -> str | None:
    for name in _TOTAL_RAILS:
        if any(name in r for r in rows):
            return name
    return None


def summarize_power(path: Path, n_events: int | None = None, interval_ms: float = 1000.0,
                    seconds: float | None = None) -> dict | None:
    """Mean/max board power, energy and (if n_events is given) joules per event.

    Energy is integrated as samples x interval. Pass the tegrastats --interval used.
    """
    rows = read_log(path)
    rail = total_rail(rows)
    if rail is None:
        return None
    watts = [r[rail] for r in rows if rail in r]
    if not watts:
        return None
    duration_s = seconds if seconds is not None else len(watts) * interval_ms / 1000.0
    energy_j = statistics.fmean(watts) * duration_s
    rails = sorted({k for r in rows for k in r})
    out = {
        "rail": rail,
        "samples": len(watts),
        "interval_ms": interval_ms,
        "duration_s": round(duration_s, 2),
        "mean_w": round(statistics.fmean(watts), 3),
        "median_w": round(statistics.median(watts), 3),
        "max_w": round(max(watts), 3),
        "min_w": round(min(watts), 3),
        "energy_j": round(energy_j, 1),
        "joules_per_event": round(energy_j / n_events, 2) if n_events else None,
        "rails": {name: round(statistics.fmean([r[name] for r in rows if name in r]), 3) for name in rails},
    }
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Summarize a tegrastats log (watts).")
    ap.add_argument("log", type=Path)
    ap.add_argument("--events", type=int, default=None, help="events processed during the log, for J/event")
    ap.add_argument("--interval-ms", type=float, default=1000.0, help="tegrastats --interval used")
    args = ap.parse_args(argv)
    s = summarize_power(args.log, args.events, args.interval_ms)
    if s is None:
        print("no power rails found in the log")
        return 1
    print(json.dumps(s, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
