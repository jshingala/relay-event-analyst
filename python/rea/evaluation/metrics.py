"""Per-event scoring of the core's fact sheet against generator labels."""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..facts import FactSheet


def _num(sheet: FactSheet, fact_id: str) -> float | None:
    v = sheet.value(fact_id)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


@dataclass
class EventScore:
    event_id: str
    fault_type_true: str
    fault_type_pred: str | None
    direction_true: str
    direction_pred: str | None
    anomaly: str
    variant: str
    samples_per_cycle: int
    sample_period_ms: float
    inception_err_ms: float | None = None
    trip_err_ms: float | None = None
    fault_end_err_ms: float | None = None
    expected_err_ms: float | None = None
    expected_err_pct: float | None = None
    expected_element_true: str | None = None
    expected_element_pred: str | None = None
    magnitude_err_pct: float | None = None
    relay_verdict_true: str = ""
    relay_verdict_pred: str = ""
    breaker_verdict_true: str = ""
    breaker_verdict_pred: str = ""
    core_wall_ms: float = 0.0
    core_analyze_ms: float = 0.0
    error: str | None = None

    @property
    def type_ok(self) -> bool:
        return self.fault_type_pred == self.fault_type_true

    @property
    def direction_ok(self) -> bool:
        return self.direction_pred == self.direction_true

    @property
    def relay_ok(self) -> bool:
        return self.relay_verdict_pred == self.relay_verdict_true

    @property
    def breaker_ok(self) -> bool:
        if self.breaker_verdict_true == "failed":
            return self.breaker_verdict_pred.startswith("failed")
        return self.breaker_verdict_pred == self.breaker_verdict_true


def score_event(sheet: FactSheet, labels: dict, wall_s: float) -> EventScore:
    rec = labels["record"]
    s = EventScore(
        event_id=labels["id"],
        fault_type_true=labels["fault_type"],
        fault_type_pred=sheet.value("fault.type"),
        direction_true=labels["direction"],
        direction_pred=sheet.value("fault.direction"),
        anomaly=labels["anomaly"],
        variant=labels["variant"],
        samples_per_cycle=rec["samples_per_cycle"],
        sample_period_ms=1000.0 / rec["sample_rate_hz"],
        relay_verdict_true=labels["expected_relay_verdict"],
        relay_verdict_pred=sheet.value("assessment.relay", ""),
        breaker_verdict_true=labels["expected_breaker_verdict"],
        breaker_verdict_pred=sheet.value("assessment.breaker", ""),
        core_wall_ms=wall_s * 1000.0,
        core_analyze_ms=float(sheet.meta.get("analyze_ms", 0.0)) + float(sheet.meta.get("parse_ms", 0.0)),
    )
    inc = _num(sheet, "fault.inception_ms")
    if inc is not None and labels["inception_ms"] is not None:
        s.inception_err_ms = inc - labels["inception_ms"]
    trip = _num(sheet, "relay.trip_ms")
    if trip is not None and labels["trip_ms"] is not None:
        s.trip_err_ms = trip - labels["trip_ms"]
    dur = _num(sheet, "fault.duration_ms")
    if dur is not None and inc is not None and labels["fault_end_ms"] is not None:
        s.fault_end_err_ms = inc + dur - labels["fault_end_ms"]
    exp_true = labels.get("expected_closed_form")
    exp_pred = _num(sheet, "expected.operate_ms")
    s.expected_element_pred = sheet.value("expected.element")
    if exp_true:
        s.expected_element_true = exp_true["element"]
        if exp_pred is not None and labels["inception_in_record"]:
            s.expected_err_ms = exp_pred - exp_true["ms"]
            if exp_true["ms"] > 0:
                s.expected_err_pct = 100.0 * s.expected_err_ms / exp_true["ms"]
    mag = _num(sheet, "fault.max_phase_rms_a")
    true_mag = labels["steady_secondary"]["i_max_a"]
    if mag is not None and true_mag > 0:
        s.magnitude_err_pct = 100.0 * (mag - true_mag) / true_mag
    return s


def _stats(values: list[float]) -> dict:
    vals = [abs(v) for v in values if v is not None and math.isfinite(v)]
    if not vals:
        return {"n": 0}
    vals.sort()
    p95 = vals[min(len(vals) - 1, int(math.ceil(0.95 * len(vals))) - 1)]
    return {"n": len(vals), "median": statistics.median(vals), "p95": p95, "max": vals[-1]}


@dataclass
class Summary:
    n_events: int
    n_errors: int
    fault_type_accuracy: float
    direction_accuracy: float
    direction_accuracy_decided: float
    direction_coverage: float
    relay_verdict_accuracy: float
    breaker_verdict_accuracy: float
    inception_abs_err_ms: dict
    inception_abs_err_samples: dict
    trip_abs_err_ms: dict
    fault_end_abs_err_ms: dict
    expected_vs_closed_form_ms: dict
    expected_vs_closed_form_cycles: dict
    magnitude_abs_err_pct: dict
    core_wall_ms: dict
    confusion: dict = field(default_factory=dict)
    relay_confusion: dict = field(default_factory=dict)
    breaker_confusion: dict = field(default_factory=dict)
    per_anomaly: dict = field(default_factory=dict)
    failures: list = field(default_factory=list)


def summarize(scores: list[EventScore]) -> Summary:
    ok = [s for s in scores if s.error is None]
    n = len(ok) or 1
    confusion: dict = defaultdict(Counter)
    relay_conf: dict = defaultdict(Counter)
    breaker_conf: dict = defaultdict(Counter)
    per_anomaly: dict = defaultdict(lambda: {"n": 0, "relay_ok": 0, "breaker_ok": 0})
    failures = []
    for s in ok:
        confusion[s.fault_type_true][s.fault_type_pred or "none"] += 1
        relay_conf[s.relay_verdict_true][s.relay_verdict_pred] += 1
        breaker_conf[s.breaker_verdict_true][s.breaker_verdict_pred] += 1
        a = per_anomaly[s.anomaly]
        a["n"] += 1
        a["relay_ok"] += int(s.relay_ok)
        a["breaker_ok"] += int(s.breaker_ok)
        problems = []
        if not s.type_ok:
            problems.append(f"type {s.fault_type_true}->{s.fault_type_pred}")
        if not s.direction_ok:
            kind = "abstained" if s.direction_pred not in ("forward", "reverse") else "wrong"
            problems.append(f"direction {s.direction_true}->{s.direction_pred} ({kind})")
        if not s.relay_ok:
            problems.append(f"relay '{s.relay_verdict_true}'->'{s.relay_verdict_pred}'")
        if not s.breaker_ok:
            problems.append(f"breaker '{s.breaker_verdict_true}'->'{s.breaker_verdict_pred}'")
        if problems:
            failures.append({"id": s.event_id, "anomaly": s.anomaly, "variant": s.variant, "problems": problems})
    for s in scores:
        if s.error is not None:
            failures.append({"id": s.event_id, "problems": [f"error: {s.error}"]})

    def cycles(ms_list: list[float]) -> list[float]:
        return [m * 60.0 / 1000.0 for m in ms_list]

    expected_err = [s.expected_err_ms for s in ok if s.expected_err_ms is not None]
    decided = [s for s in ok if s.direction_pred in ("forward", "reverse")]
    return Summary(
        n_events=len(scores),
        n_errors=len(scores) - len(ok),
        fault_type_accuracy=sum(s.type_ok for s in ok) / n,
        direction_accuracy=sum(s.direction_ok for s in ok) / n,
        direction_accuracy_decided=sum(s.direction_ok for s in decided) / max(len(decided), 1),
        direction_coverage=len(decided) / n,
        relay_verdict_accuracy=sum(s.relay_ok for s in ok) / n,
        breaker_verdict_accuracy=sum(s.breaker_ok for s in ok) / n,
        inception_abs_err_ms=_stats([s.inception_err_ms for s in ok if s.inception_err_ms is not None]),
        inception_abs_err_samples=_stats(
            [s.inception_err_ms / s.sample_period_ms for s in ok if s.inception_err_ms is not None]
        ),
        trip_abs_err_ms=_stats([s.trip_err_ms for s in ok if s.trip_err_ms is not None]),
        fault_end_abs_err_ms=_stats([s.fault_end_err_ms for s in ok if s.fault_end_err_ms is not None]),
        expected_vs_closed_form_ms=_stats(expected_err),
        expected_vs_closed_form_cycles=_stats(cycles(expected_err)),
        magnitude_abs_err_pct=_stats([s.magnitude_err_pct for s in ok if s.magnitude_err_pct is not None]),
        core_wall_ms=_stats([s.core_wall_ms for s in ok]),
        confusion={k: dict(v) for k, v in confusion.items()},
        relay_confusion={k: dict(v) for k, v in relay_conf.items()},
        breaker_confusion={k: dict(v) for k, v in breaker_conf.items()},
        per_anomaly=dict(per_anomaly),
        failures=failures,
    )
