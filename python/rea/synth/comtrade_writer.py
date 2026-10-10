"""Minimal IEEE C37.111-1999 writer (CFG + ASCII or 16-bit BINARY DAT).

This is an independent implementation from the C++ reader, so a round trip
through both actually tests the reader against the standard; tests also read
the files with the third-party ``comtrade`` package when it is installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np


@dataclass
class AnalogSpec:
    id: str
    phase: str
    ccbm: str
    unit: str
    data: np.ndarray  # engineering values in `unit`
    primary: float = 1.0
    secondary: float = 1.0
    ps: str = "S"


def format_time(ts: datetime) -> str:
    return f"{ts.day:02d}/{ts.month:02d}/{ts.year:04d},{ts.hour:02d}:{ts.minute:02d}:{ts.second:02d}.{ts.microsecond:06d}"


def _quantize(x: np.ndarray) -> tuple[float, np.ndarray]:
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    a = peak / 32000.0 if peak > 0.0 else 1e-6
    raw = np.clip(np.round(x / a), -32767, 32767).astype(np.int16)
    return a, raw


def write_comtrade(
    stem: Path,
    *,
    station: str,
    device: str,
    analogs: list[AnalogSpec],
    digitals: list[tuple[str, np.ndarray]],
    fs: float,
    f_nom: float,
    start: datetime,
    trigger: datetime,
    fmt: str = "BINARY",
) -> tuple[Path, Path]:
    fmt = fmt.upper()
    if fmt not in ("ASCII", "BINARY"):
        raise ValueError("fmt must be ASCII or BINARY")
    k_total = len(analogs[0].data)
    scaled = [_quantize(np.asarray(a.data, dtype=float)) for a in analogs]

    lines = [f"{station},{device},1999", f"{len(analogs) + len(digitals)},{len(analogs)}A,{len(digitals)}D"]
    for n, (spec, (a, _raw)) in enumerate(zip(analogs, scaled), start=1):
        lines.append(
            f"{n},{spec.id},{spec.phase},{spec.ccbm},{spec.unit},{a:.10g},0,0,-32767,32767,"
            f"{spec.primary:g},{spec.secondary:g},{spec.ps}"
        )
    for n, (name, _bits) in enumerate(digitals, start=1):
        lines.append(f"{n},{name},,,0")
    lines += [f"{f_nom:g}", "1", f"{fs:g},{k_total}", format_time(start), format_time(trigger), fmt, "1"]

    stem.parent.mkdir(parents=True, exist_ok=True)
    cfg_path = stem.with_suffix(".cfg")
    dat_path = stem.with_suffix(".dat")
    cfg_path.write_text("\r\n".join(lines) + "\r\n", encoding="ascii")

    stamps = np.round(np.arange(k_total) * 1e6 / fs).astype(np.uint32)
    if fmt == "ASCII":
        raw_cols = [r for _a, r in scaled]
        bit_cols = [np.asarray(b, dtype=np.uint8) for _n, b in digitals]
        rows = []
        for k in range(k_total):
            fields = [str(k + 1), str(int(stamps[k]))]
            fields += [str(int(c[k])) for c in raw_cols]
            fields += [str(int(b[k])) for b in bit_cols]
            rows.append(",".join(fields))
        dat_path.write_text("\r\n".join(rows) + "\r\n", encoding="ascii")
    else:
        n_words = (len(digitals) + 15) // 16
        fields = [("n", "<u4"), ("ts", "<u4"), ("a", "<i2", (len(analogs),))]
        if n_words:
            fields.append(("d", "<u2", (n_words,)))
        rec = np.zeros(k_total, dtype=np.dtype(fields))
        rec["n"] = np.arange(1, k_total + 1)
        rec["ts"] = stamps
        rec["a"] = np.stack([r for _a, r in scaled], axis=1)
        if n_words:
            words = np.zeros((k_total, n_words), dtype=np.uint16)
            for c, (_name, bits) in enumerate(digitals):
                words[:, c // 16] |= (np.asarray(bits, dtype=np.uint16) & 1) << (c % 16)
            rec["d"] = words
        dat_path.write_bytes(rec.tobytes())
    return cfg_path, dat_path
