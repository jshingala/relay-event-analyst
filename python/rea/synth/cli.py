"""rea-synth: write synthetic COMTRADE events with ground-truth labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .network import FAULT_TYPES
from .scenario import ANOMALIES, VARIANTS, build_event

# Curated events for the demo and the README walkthrough: (id, seed, kind, anomaly, forward)
DEMO_EVENTS = (
    ("demo_bg_51g_normal", 11, "BG", "none", True),
    ("demo_bc_slow_breaker", 12, "BC", "slow_breaker", True),
    ("demo_ag_reverse_restrained", 13, "AG", "none", False),
    ("demo_cag_slow_relay", 14, "CAG", "slow_relay", True),
    ("demo_abc_failed_to_trip", 15, "ABC", "failed_to_trip", True),
)


def generate(out: Path, n: int, seed: int, anomaly: str | None = None, kind: str | None = None,
             variant: str | None = None) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    manifest = []
    for i in range(n):
        ev = build_event(rng, f"ev{i:04d}", out, anomaly=anomaly, kind=kind, variant=variant)
        manifest.append({"id": ev.event_id, "cfg": ev.cfg_path.name, "settings": ev.settings_path.name,
                         "labels": ev.labels_path.name})
    (out / "manifest.json").write_text(json.dumps({"seed": seed, "events": manifest}, indent=2), encoding="utf-8")
    return manifest


def generate_demo(out: Path) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for event_id, seed, kind, anomaly, forward in DEMO_EVENTS:
        rng = np.random.default_rng(seed)
        ev = build_event(rng, event_id, out, anomaly=anomaly, kind=kind, variant="normal", forward=forward)
        manifest.append({"id": ev.event_id, "cfg": ev.cfg_path.name, "settings": ev.settings_path.name,
                         "labels": ev.labels_path.name})
    (out / "manifest.json").write_text(json.dumps({"seed": None, "events": manifest}, indent=2), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("data/synth"))
    ap.add_argument("--n", type=int, default=200, help="number of events")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--anomaly", choices=ANOMALIES)
    ap.add_argument("--kind", choices=FAULT_TYPES)
    ap.add_argument("--variant", choices=VARIANTS)
    ap.add_argument("--demo", action="store_true", help="write the five curated demo events instead")
    args = ap.parse_args(argv)
    if args.demo:
        events = generate_demo(args.out)
    else:
        events = generate(args.out, args.n, args.seed, args.anomaly, args.kind, args.variant)
    print(f"wrote {len(events)} events to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
