"""Closed-form relay model used to create ground truth.

Deliberately independent from the C++ core: the generator predicts trip times
with the constant-current curve equation, while the core replays the measured
phasors through the IEEE C37.112 integral. Agreement between the two is one of
the evaluation metrics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

# SEL U.S. and IEC curves: tp = TD * (A + B / (M^p - 1))
CURVES: dict[str, tuple[float, float, float]] = {
    "U1": (0.0226, 0.0104, 0.02),
    "U2": (0.180, 5.95, 2.0),
    "U3": (0.0963, 3.88, 2.0),
    "U4": (0.0352, 5.67, 2.0),
    "U5": (0.00262, 0.00342, 0.02),
    "C1": (0.0, 0.14, 0.02),
    "C2": (0.0, 13.5, 1.0),
    "C3": (0.0, 80.0, 2.0),
    "C4": (0.0, 120.0, 1.0),
    "C5": (0.0, 0.05, 0.04),
}


def curve_time(curve: str, td: float, m: float) -> float:
    if m <= 1.0:
        return math.inf
    a, b, p = CURVES[curve]
    return td * (a + b / (m**p - 1.0))


@dataclass(frozen=True)
class Toc:
    pickup: float  # secondary amps
    curve: str
    td: float


@dataclass(frozen=True)
class RelaySettings:
    ctr: float
    z1mag: float
    z1ang: float
    z2f: float
    z2r: float
    p50: float | None = None  # secondary amps, None = OFF
    d50_cycles: float = 0.0
    toc_p: Toc | None = None
    toc_g: Toc | None = None
    directional: bool = True
    inom: float = 5.0
    vnom: float = 66.4
    bkrmax_cycles: float = 5.0
    tol_cycles: float = 1.5
    tol_pct: float = 5.0
    trip_channel: str | None = None

    def with_changes(self, **kw) -> "RelaySettings":
        return replace(self, **kw)

    def to_text(self, title: str = "") -> str:
        """Settings sheet in the `NAME := value` style the core reads."""
        rows = []
        if title:
            rows.append(f"# {title}")
        rows.append(f"INOM   := {self.inom:.2f}    VNOM   := {self.vnom:.2f}    CTR    := {self.ctr:g}")
        rows.append(f"Z1MAG  := {self.z1mag:.2f}    Z1ANG  := {self.z1ang:.2f}    Z2F    := {self.z2f:.2f}    Z2R    := {self.z2r:.2f}")
        rows.append(f"DIR    := {'F' if self.directional else 'N'}")
        rows.append(f"50P1P  := {'OFF' if self.p50 is None else f'{self.p50:.2f}'}    50P1D  := {self.d50_cycles:.2f}")
        for name, toc in (("51P1", self.toc_p), ("51G1", self.toc_g)):
            if toc is None:
                rows.append(f"{name}P := OFF")
            else:
                rows.append(f"{name}P := {toc.pickup:.2f}    {name}C := {toc.curve}    {name}TD := {toc.td:.2f}    {name}RS := N")
        rows.append(f"TOLCYC := {self.tol_cycles:.2f}    TOLPCT := {self.tol_pct:.2f}    BKRMAX := {self.bkrmax_cycles:.2f}")
        if self.trip_channel:
            rows.append(f"TRIPCH := {self.trip_channel}")
        return "\n".join(rows) + "\n"


def predict(
    settings: RelaySettings, i_max: float, three_i0: float, forward: bool, f_nom: float
) -> tuple[str | None, float | None]:
    """First element to operate and its closed-form time (s from inception)."""
    if settings.directional and not forward:
        return None, None
    candidates: list[tuple[str, float]] = []
    if settings.p50 is not None and i_max >= settings.p50:
        candidates.append(("50P1", settings.d50_cycles / f_nom))
    if settings.toc_p is not None:
        t = curve_time(settings.toc_p.curve, settings.toc_p.td, i_max / settings.toc_p.pickup)
        if math.isfinite(t):
            candidates.append(("51P1", t))
    if settings.toc_g is not None:
        t = curve_time(settings.toc_g.curve, settings.toc_g.td, three_i0 / settings.toc_g.pickup)
        if math.isfinite(t):
            candidates.append(("51G1", t))
    if not candidates:
        return None, None
    return min(candidates, key=lambda c: c[1])
