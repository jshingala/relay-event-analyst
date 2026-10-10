"""Random, reproducible fault events with ground-truth labels.

Each event is written as ``<id>.cfg`` + ``<id>.dat`` (COMTRADE 1999), the relay
settings on file (``<id>.settings.txt``) and the answers (``<id>.labels.json``).

Anomalies make the "field" relay or breaker behave differently from the
settings on file, so the analyzer's expected-vs-actual verdicts can be scored:

    slow_relay       field time dial 1.8-3x the setting on file
    wrong_curve      field curve differs from the curve on file
    slow_breaker     contacts part 6.5-9 cycles after trip (limit: 5)
    breaker_failure  breaker never opens; upstream breakers clear the fault
    reverse_trip     settings say forward-directional, field relay is not
    failed_to_trip   relay never trips; upstream backup clears the fault
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from .comtrade_writer import AnalogSpec, write_comtrade
from .network import FAULT_TYPES, FaultSpec, SystemModel, sequence, solve
from .relay_model import CURVES, RelaySettings, Toc, curve_time, predict
from .waveform import Timeline, synthesize

F_NOM = 60.0
ANOMALIES = ("none", "slow_relay", "wrong_curve", "slow_breaker", "breaker_failure", "reverse_trip", "failed_to_trip")
ANOMALY_WEIGHTS = (0.55, 0.075, 0.075, 0.075, 0.075, 0.075, 0.075)
VARIANTS = ("normal", "no_trip_channel", "inception_before_record")
VARIANT_WEIGHTS = (0.92, 0.04, 0.04)
U_CURVES = ("U1", "U2", "U3", "U4", "U5")
C_CURVES = ("C1", "C2", "C3", "C5")


class Retry(Exception):
    """The drawn parameters do not make a usable event; draw again."""


@dataclass
class Event:
    event_id: str
    cfg_path: Path
    settings_path: Path
    labels_path: Path
    labels: dict


def _polar(mag: float, ang_deg: float) -> complex:
    return complex(mag * math.cos(math.radians(ang_deg)), mag * math.sin(math.radians(ang_deg)))


def random_system(rng: np.random.Generator) -> SystemModel:
    length_km = rng.uniform(20.0, 60.0)
    zl1 = complex(0.05, 0.40) * length_km
    zs1 = _polar(rng.uniform(2.0, 6.0), 85.0)
    zr1 = _polar(rng.uniform(4.0, 15.0), 83.0)
    return SystemModel(
        zs1=zs1,
        zs0=_polar(abs(zs1) * rng.uniform(1.0, 3.0), 80.0),
        zr1=zr1,
        zr0=_polar(abs(zr1) * rng.uniform(1.0, 3.0), 78.0),
        zl1=zl1,
        zl0=_polar(3.0 * abs(zl1), rng.uniform(72.0, 78.0)),
        load_angle_deg=rng.uniform(-12.0, 12.0),
        f_hz=F_NOM,
    )


def random_fault(rng: np.random.Generator, kind: str, forward: bool) -> FaultSpec:
    loc = rng.uniform(0.05, 0.9)
    if kind in ("AG", "BG", "CG"):
        rg = rng.uniform(0.0, 12.0) if rng.random() < 0.8 else rng.uniform(12.0, 30.0)
        return FaultSpec(kind, forward, loc, r_phase=0.0, r_ground=rg)
    if kind in ("AB", "BC", "CA"):
        return FaultSpec(kind, forward, loc, r_phase=rng.uniform(0.0, 1.5))
    if kind in ("ABG", "BCG", "CAG"):
        return FaultSpec(kind, forward, loc, r_phase=rng.uniform(0.0, 0.5), r_ground=rng.uniform(0.0, 5.0))
    return FaultSpec(kind, forward, loc, r_phase=rng.uniform(0.0, 1.0))


def _td_for(curve: str, m: float, target_s: float) -> float | None:
    """Time dial giving `target_s` at multiple `m`, clipped to the usual range."""
    base = curve_time(curve, 1.0, m)
    if not math.isfinite(base) or base <= 0.0:
        return None
    lo, hi = (0.5, 15.0) if curve.startswith("U") else (0.05, 1.0)
    return float(min(max(target_s / base, lo), hi))


def random_settings(
    rng: np.random.Generator, system: SystemModel, i_max: float, three_i0: float, load: float, allow_50: bool
) -> RelaySettings:
    z1mag = abs(system.zl1) * system.ctr / system.ptr
    z1ang = math.degrees(math.atan2(system.zl1.imag, system.zl1.real))
    z2f = 0.5 * z1mag

    p51 = max(1.6 * load, 1.5) * rng.uniform(1.0, 1.3)
    toc_p = None
    curve = str(rng.choice(U_CURVES + C_CURVES, p=[0.12, 0.14, 0.2, 0.2, 0.06, 0.12, 0.06, 0.06, 0.04]))
    if i_max / p51 > 1.3:
        td = _td_for(curve, i_max / p51, rng.uniform(0.15, 1.6))
        if td is not None:
            toc_p = Toc(round(p51, 2), curve, round(td, 2))
    if toc_p is None:  # still set the element so the sheet is realistic; it just will not operate
        toc_p = Toc(round(p51, 2), curve, 2.0 if curve.startswith("U") else 0.2)

    toc_g = None
    p51g = rng.uniform(0.5, 1.5)
    curve_g = str(rng.choice(("U2", "U3", "U4", "C1", "C3")))
    if three_i0 / p51g > 1.3:
        td = _td_for(curve_g, three_i0 / p51g, rng.uniform(0.15, 1.6))
        if td is not None:
            toc_g = Toc(round(p51g, 2), curve_g, round(td, 2))
    if toc_g is None:
        toc_g = Toc(round(p51g, 2), curve_g, 2.0 if curve_g.startswith("U") else 0.2)

    p50 = None
    if allow_50 and rng.random() < 0.35 and i_max >= 2.5 * p51:
        p50 = round(rng.uniform(0.45, 0.75) * i_max, 2)
    return RelaySettings(
        ctr=system.ctr,
        z1mag=round(z1mag, 2),
        z1ang=round(z1ang, 2),
        z2f=round(z2f, 2),
        z2r=round(z2f + 0.1, 2),
        p50=p50,
        toc_p=toc_p,
        toc_g=toc_g,
        directional=bool(rng.random() < 0.85),
    )


def tolerance_s(expected_s: float, s: RelaySettings) -> float:
    return s.tol_cycles / F_NOM + s.tol_pct / 100.0 * expected_s


def _different_curve(rng: np.random.Generator, curve: str) -> str:
    family = U_CURVES if curve.startswith("U") else C_CURVES
    return str(rng.choice([c for c in family if c != curve]))


def build_event(
    rng: np.random.Generator,
    event_id: str,
    out_dir: Path,
    *,
    anomaly: str | None = None,
    kind: str | None = None,
    variant: str | None = None,
    forward: bool | None = None,
) -> Event:
    for _attempt in range(200):
        try:
            return _build_event_once(rng, event_id, out_dir, anomaly, kind, variant, forward)
        except Retry:
            continue
    raise RuntimeError(f"could not build a valid event for {event_id}")


def _build_event_once(rng, event_id, out_dir, anomaly, kind, variant, forward) -> Event:
    anomaly = anomaly or str(rng.choice(ANOMALIES, p=ANOMALY_WEIGHTS))
    variant = variant or str(rng.choice(VARIANTS, p=VARIANT_WEIGHTS))
    kind = kind or str(rng.choice(FAULT_TYPES))
    if forward is None:
        forward = anomaly != "reverse_trip" and not (anomaly == "none" and rng.random() < 0.2)
    if anomaly == "reverse_trip":
        forward = False

    system = random_system(rng)
    spec = random_fault(rng, kind, forward)
    sol = solve(system, spec)
    ctr, ptr = system.ctr, system.ptr
    i_flt_sec = sol.i_flt / ctr
    v_flt_sec = sol.v_flt / ptr
    i_max = float(np.max(np.abs(i_flt_sec)))
    three_i0 = float(abs(np.sum(i_flt_sec)))
    load = float(np.max(np.abs(sol.i_pre / ctr)))
    # A relay only records what it can see: skip faults whose incremental current is
    # below the analyzer's documented sensitivity (0.1 x INOM) plus a margin.
    if float(np.max(np.abs((sol.i_flt - sol.i_pre) / ctr))) < 0.6:
        raise Retry

    allow_50 = anomaly not in ("slow_relay", "wrong_curve") and variant != "inception_before_record"
    file_s = random_settings(rng, system, i_max, three_i0, load, allow_50)
    if anomaly == "reverse_trip":
        file_s = file_s.with_changes(directional=True)
    file_elem, file_t = predict(file_s, i_max, three_i0, forward, F_NOM)

    field_s = file_s
    if anomaly == "slow_relay":
        k = rng.uniform(1.8, 3.0)
        field_s = file_s.with_changes(
            toc_p=Toc(file_s.toc_p.pickup, file_s.toc_p.curve, file_s.toc_p.td * k),
            toc_g=Toc(file_s.toc_g.pickup, file_s.toc_g.curve, file_s.toc_g.td * k),
        )
    elif anomaly == "wrong_curve":
        field_s = file_s.with_changes(
            toc_p=Toc(file_s.toc_p.pickup, _different_curve(rng, file_s.toc_p.curve), file_s.toc_p.td),
            toc_g=Toc(file_s.toc_g.pickup, _different_curve(rng, file_s.toc_g.curve), file_s.toc_g.td),
        )
    elif anomaly == "reverse_trip":
        field_s = file_s.with_changes(directional=False)
    field_elem, field_t = predict(field_s, i_max, three_i0, forward, F_NOM)

    # Reject draws that would not exercise the intended behaviour.
    trips_expected = file_elem is not None
    if anomaly in ("none", "slow_relay", "wrong_curve", "slow_breaker", "breaker_failure", "failed_to_trip"):
        if forward and not trips_expected:
            raise Retry
    if anomaly == "reverse_trip" and field_elem is None:
        raise Retry
    for t in (file_t, field_t):
        if t is not None and not (0.0 <= t <= 2.5):
            raise Retry
    if anomaly in ("slow_relay", "wrong_curve"):
        if field_t is None or file_t is None or abs(field_t - file_t) < 3.0 * tolerance_s(file_t, file_s) + 2.0 / F_NOM:
            raise Retry
    if variant == "inception_before_record" and (field_elem in (None, "50P1") or anomaly in ("failed_to_trip", "reverse_trip")):
        raise Retry

    cyc = 1.0 / F_NOM
    relay_trips = field_elem is not None and anomaly != "failed_to_trip"
    t_trip_rel = None
    if relay_trips:
        delay = (0.75 if field_elem == "50P1" else 0.5) + rng.uniform(0.0, 0.25)
        t_trip_rel = field_t + delay * cyc

    pre = rng.uniform(3.0, 8.0) * cyc
    t0 = pre if variant != "inception_before_record" else -rng.uniform(1.0, 3.0) * cyc
    if variant == "inception_before_record" and t_trip_rel is not None and t0 + t_trip_rel < 2.0 * cyc:
        raise Retry

    t_trip = t0 + t_trip_rel if t_trip_rel is not None else None
    t_contacts = t_removed = None
    removal = "return_to_load"
    if relay_trips:
        if anomaly == "breaker_failure":
            t_removed = t_trip + rng.uniform(9.0, 14.0) * cyc
            removal = "deenergize"
        elif anomaly == "slow_breaker":
            t_contacts = t_trip + rng.uniform(6.5, 9.0) * cyc
        else:
            t_contacts = t_trip + rng.uniform(1.3, 2.2) * cyc
    elif anomaly == "failed_to_trip":
        t_removed = t0 + max(1.5 * file_t + 0.1, 0.4) + rng.uniform(0.0, 0.2)
        removal = "deenergize"
    else:  # reverse fault, correctly restrained: the adjacent circuit clears it
        t_removed = t0 + rng.uniform(3.0, 8.0) * cyc

    last = max(x for x in (t0 + 2 * cyc, t_trip, t_contacts, t_removed) if x is not None)
    duration = last + rng.uniform(6.0, 10.0) * cyc
    if duration > 4.0:
        raise Retry

    spc = int(rng.choice((8, 16, 32), p=(0.2, 0.4, 0.4)))
    fs = spc * F_NOM
    f_actual = F_NOM if rng.random() < 0.7 else F_NOM + rng.uniform(-0.05, 0.05)
    tl = Timeline(fs=fs, f_actual=f_actual, duration_s=duration, t_fault=t0, t_trip=t_trip,
                  t_contacts=t_contacts, t_removed=t_removed, removal=removal)
    wf = synthesize(sol, tl)

    noise = rng.uniform(0.0005, 0.003)
    i_sec = wf.i / ctr + rng.normal(0.0, noise * 5.0 * math.sqrt(2.0), wf.i.shape)
    v_sec = wf.v / ptr + rng.normal(0.0, noise * 66.4 * math.sqrt(2.0), wf.v.shape)

    names = ("IA", "IB", "IC", "VA", "VB", "VC") if rng.random() < 0.6 else ("IAW", "IBW", "ICW", "VAY", "VBY", "VCY")
    primary_style = rng.random() < 0.2
    analogs = []
    for p in range(3):
        if primary_style:
            analogs.append(AnalogSpec(names[p], "ABC"[p], "LINE", "kA", i_sec[p] * ctr / 1000.0, 1200, 5, "P"))
        else:
            analogs.append(AnalogSpec(names[p], "ABC"[p], "LINE", "A", i_sec[p], 1200, 5, "S"))
    for p in range(3):
        if primary_style:
            analogs.append(AnalogSpec(names[3 + p], "ABC"[p], "BUS", "kV", v_sec[p] * ptr / 1000.0, 115000, 115, "P"))
        else:
            analogs.append(AnalogSpec(names[3 + p], "ABC"[p], "BUS", "V", v_sec[p], 115000, 115, "S"))

    trip_name = "TRIP" if rng.random() < 0.7 else "OUT101"
    digitals = [] if variant == "no_trip_channel" else [(trip_name, wf.trip)]
    digitals.append(("52A", wf.bkr_52a))
    if trip_name == "OUT101" and rng.random() < 0.5:
        file_s = file_s.with_changes(trip_channel="OUT101")

    start = datetime(2026, 9, 14, 8, 0, 0) + timedelta(minutes=int(rng.integers(0, 60 * 24 * 20)),
                                                       microseconds=int(rng.integers(0, 10**6)))
    trigger_offset = (t0 if t0 > 0 else 0.0) + rng.uniform(0.0, 0.5) * cyc
    trigger = start + timedelta(microseconds=round(trigger_offset * 1e6))
    fmt = "BINARY" if rng.random() < 0.7 else "ASCII"
    stem = out_dir / event_id
    cfg_path, _dat = write_comtrade(stem, station="SYNTH-S", device=f"REL-{event_id}", analogs=analogs,
                                    digitals=digitals, fs=fs, f_nom=F_NOM, start=start, trigger=trigger, fmt=fmt)
    settings_path = stem.with_suffix(".settings.txt")
    settings_path.write_text(file_s.to_text(f"settings on file for {event_id}"), encoding="ascii")

    # ------------------------------------------------------------ ground truth
    ms = lambda x: None if x is None else round(1000.0 * x, 4)  # noqa: E731
    # Like the analyzer, only poles carrying at least 0.1 x INOM count for clearing times.
    carried = [abs(i_flt_sec[p]) >= 0.1 * file_s.inom for p in range(3)]
    local_clear = [c for p, c in enumerate(wf.pole_clear_s) if c is not None and carried[p]]
    removal_clear = [c for p, c in enumerate(wf.removal_clear_s) if c is not None and carried[p]]
    if local_clear:
        end_cause, fault_end = "local_breaker", max(local_clear)
    elif removal == "deenergize":
        end_cause, fault_end = "other_breakers", max(removal_clear)
    else:
        end_cause, fault_end = "returned_to_load", t_removed

    has_trip_channel = variant != "no_trip_channel"
    inception_in_record = variant != "inception_before_record"
    if not has_trip_channel or not inception_in_record:
        relay_verdict = "undetermined"
    elif anomaly == "slow_relay":
        relay_verdict = "slower than expected"
    elif anomaly == "wrong_curve":
        relay_verdict = "slower than expected" if field_t > file_t else "faster than expected"
    elif anomaly == "reverse_trip":
        relay_verdict = "tripped for a fault outside its directional zone"
    elif anomaly == "failed_to_trip":
        relay_verdict = "failed to trip"
    else:
        relay_verdict = "operated as expected" if trips_expected else "correctly did not trip"

    if not has_trip_channel:
        breaker_verdict = "undetermined"
    elif not relay_trips:
        breaker_verdict = "not applicable"
    elif anomaly == "breaker_failure":
        breaker_verdict = "failed"
    elif anomaly == "slow_breaker":
        breaker_verdict = "slow"
    else:
        breaker_verdict = "normal"

    seq_i = sequence(i_flt_sec)
    labels = {
        "id": event_id,
        "fault_type": kind,
        "direction": "forward" if forward else "reverse",
        "location_pu": round(spec.location, 4),
        "r_phase_ohm": round(spec.r_phase, 3),
        "r_ground_ohm": round(spec.r_ground, 3),
        "anomaly": anomaly,
        "variant": variant,
        "inception_in_record": inception_in_record,
        "inception_ms": ms(t0) if inception_in_record else None,
        "trip_ms": ms(t_trip) if (relay_trips and has_trip_channel) else None,
        "relay_tripped": relay_trips,
        "pole_clear_ms": [ms(c) for c in wf.pole_clear_s],
        "removal_clear_ms": [ms(c) for c in wf.removal_clear_s],
        "fault_end_ms": ms(fault_end),
        "end_cause": end_cause,
        "expected_closed_form": None if file_elem is None else {"element": file_elem, "ms": ms(file_t)},
        "field_closed_form": None if field_elem is None else {"element": field_elem, "ms": ms(field_t)},
        "expected_relay_verdict": relay_verdict,
        "expected_breaker_verdict": breaker_verdict,
        "steady_secondary": {
            "i_rms_a": [round(float(abs(x)), 4) for x in i_flt_sec],
            "v_rms_v": [round(float(abs(x)), 3) for x in v_flt_sec],
            "i_max_a": round(i_max, 4),
            "three_i0_a": round(three_i0, 4),
            "i2_a": round(float(abs(seq_i[2])), 4),
        },
        "record": {
            "samples_per_cycle": spc,
            "sample_rate_hz": fs,
            "f_actual_hz": round(f_actual, 4),
            "format": fmt,
            "primary_scaling": primary_style,
            "channel_names": list(names),
            "trip_channel": trip_name if has_trip_channel else None,
            "duration_ms": ms(duration),
            "noise_fraction": round(noise, 5),
            "dc_tau_ms": round(sol.tau_s * 1000.0, 3),
        },
        "settings_file": settings_path.name,
    }
    labels_path = stem.with_suffix(".labels.json")
    labels_path.write_text(json.dumps(labels, indent=2), encoding="utf-8")
    return Event(event_id, cfg_path, settings_path, labels_path, labels)
