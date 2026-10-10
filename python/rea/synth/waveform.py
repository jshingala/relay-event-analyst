"""Time-domain waveforms for one event, built from fault phasors.

* Currents are continuous at inception (inductive network), so each phase gets a
  decaying DC term equal to the jump between the prefault and fault waveforms.
* Each breaker pole interrupts at the first current zero after its contacts part,
  so poles clear at different instants.
* A fault removed by other equipment either returns the relay to load current
  (adjacent circuit cleared) or de-energizes the bus (upstream breakers opened).

Simplification: when one pole clears, the remaining poles keep their fault
waveforms (no current redistribution).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .network import FaultSolution


@dataclass(frozen=True)
class Timeline:
    fs: float
    f_actual: float
    duration_s: float
    t_fault: float  # inception, seconds from the first sample (negative: before the record)
    t_trip: float | None = None
    t_trip_reset: float | None = None
    t_contacts: float | None = None  # local breaker contacts part
    t_removed: float | None = None  # fault removed by other equipment
    removal: str = "return_to_load"  # or "deenergize"
    aux_delay_s: float = 0.002  # 52A auxiliary contact lag
    min_arc_s: float = 0.0005


@dataclass
class Waveforms:
    t: np.ndarray
    i: np.ndarray  # (3, K) primary amps
    v: np.ndarray  # (3, K) primary volts (line-to-neutral)
    trip: np.ndarray
    bkr_52a: np.ndarray
    pole_clear_s: list[float | None] = field(default_factory=lambda: [None, None, None])
    removal_clear_s: list[float | None] = field(default_factory=lambda: [None, None, None])


def _wave(p: complex, t: np.ndarray | float, w: float) -> np.ndarray:
    return math.sqrt(2.0) * abs(p) * np.cos(w * np.asarray(t) + np.angle(p))


def _first_zero(fn, t_from: float, w: float) -> float:
    """Time of the first sign change of fn(t) after t_from (searched over one cycle)."""
    period = 2.0 * math.pi / w
    grid = np.linspace(t_from, t_from + 1.05 * period, 4001)
    vals = fn(grid)
    sign = np.signbit(vals)
    idx = np.nonzero(sign[1:] != sign[:-1])[0]
    if idx.size == 0:
        return float(grid[-1])
    k = int(idx[0])
    t0, t1, y0, y1 = grid[k], grid[k + 1], vals[k], vals[k + 1]
    return float(t0 - y0 * (t1 - t0) / (y1 - y0)) if y1 != y0 else float(t0)


def synthesize(sol: FaultSolution, tl: Timeline) -> Waveforms:
    k_total = int(round(tl.duration_s * tl.fs))
    t = np.arange(k_total) / tl.fs
    w = 2.0 * math.pi * tl.f_actual
    tau = sol.tau_s
    t0 = tl.t_fault

    i = np.zeros((3, k_total))
    v = np.zeros((3, k_total))
    out = Waveforms(t=t, i=i, v=v, trip=np.zeros(k_total, np.uint8), bkr_52a=np.ones(k_total, np.uint8))

    for p in range(3):
        ip, ifl = sol.i_pre[p], sol.i_flt[p]
        vp, vf = sol.v_pre[p], sol.v_flt[p]
        dc0 = float(_wave(ip, t0, w) - _wave(ifl, t0, w))

        def fault_current(tt: np.ndarray, ifl=ifl, dc0=dc0) -> np.ndarray:
            tt = np.asarray(tt)
            return _wave(ifl, tt, w) + dc0 * np.exp(-np.clip(tt - t0, 0.0, None) / tau)

        cur = np.where(t < t0, _wave(ip, t, w), fault_current(t))
        vol = np.where(t < t0, _wave(vp, t, w), _wave(vf, t, w))

        if tl.t_removed is not None:
            tr = tl.t_removed
            if tl.removal == "return_to_load":
                jump = float(fault_current(np.array([tr]))[0] - _wave(ip, tr, w))
                after = _wave(ip, t, w) + jump * np.exp(-np.clip(t - tr, 0.0, None) / tau)
                cur = np.where(t >= tr, after, cur)
                vol = np.where(t >= tr, _wave(vp, t, w), vol)
                out.removal_clear_s[p] = tr
            else:  # de-energized by upstream breakers: pole clears at its next zero
                tz = _first_zero(fault_current, tr + tl.min_arc_s, w)
                cur = np.where(t >= tz, 0.0, cur)
                vol = np.where(t >= tr, 0.0, vol)
                out.removal_clear_s[p] = tz

        if tl.t_contacts is not None:
            tz = _first_zero(fault_current, tl.t_contacts + tl.min_arc_s, w)
            cur = np.where(t >= tz, 0.0, cur)
            out.pole_clear_s[p] = tz

        i[p] = cur
        v[p] = vol

    if tl.t_contacts is not None:
        last = max(c for c in out.pole_clear_s if c is not None)
        for p in range(3):  # bus-side VTs: voltage recovers once the faulted line is disconnected
            v[p] = np.where(t >= last, _wave(sol.v_pre[p], t, w), v[p])
        out.bkr_52a = (t < tl.t_contacts + tl.aux_delay_s).astype(np.uint8)

    if tl.t_trip is not None:
        reset = tl.t_trip_reset if tl.t_trip_reset is not None else np.inf
        out.trip = ((t >= tl.t_trip) & (t < reset)).astype(np.uint8)
    return out
