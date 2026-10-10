"""Read SEL Compressed ASCII event reports (.CEV) and write them as COMTRADE.

A .CEV file is comma-separated text. Every line ends with a checksum: four hex digits
holding the byte sum, modulo 65536, of everything before it (including the comma).

    "FID",...                                  header names, then values
    "MONTH","DAY","YEAR","HOUR","MIN","SEC","MSEC"   trigger time, then values
    "FREQ","SAM/CYC_A","SAM/CYC_D",...,"EVENT",...   event summary, then values
    "IA","IB",...,"TRIG","<Relay Word bit names>"    channel labels
    131,-206,..., ,"8000...",                  one row per analog sample: '>' in the TRIG
                                               column marks the trigger row; the hex string
                                               packs the digital bits MSB first, four per
                                               digit, in label order (blank = unchanged)
    "SETTINGS"                                 relay settings text (optional)

Values are primary (A, kV). The converter keeps them primary and writes the CT and VT
ratios into the COMTRADE channel lines with PS = P, so rea-core can work in secondary
units, and writes a settings file for rea-core from the relay settings it finds.
Standard library only.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

INVALID = -99999.0


class CevError(ValueError):
    pass


@dataclass
class CevEvent:
    header: dict[str, str]
    trigger_time: datetime
    summary: dict[str, str]
    analog_labels: list[str]
    analog: list[list[float]]  # [channel][sample]
    digital_names: list[str]
    digital: list[list[int]]  # [channel][sample]
    trigger_index: int
    settings: dict[str, str] = field(default_factory=dict)
    bad_checksums: int = 0

    @property
    def samples_per_cycle(self) -> int:
        return int(float(self.summary.get("SAM/CYC_A", "0")))

    @property
    def nominal_hz(self) -> float:
        for key in ("NFREQ",):
            if self.summary.get(key):
                return float(self.summary[key])
            if self.settings.get(key):
                return float(self.settings[key])
        f = float(self.summary.get("FREQ", "60") or 60)
        return 50.0 if abs(f - 50.0) < abs(f - 60.0) else 60.0

    @property
    def relay(self) -> str:
        fid = self.header.get("FID", "")
        return fid.split("=", 1)[1].split("-R")[0] if "=" in fid else fid


def _fields(line: str) -> list[str]:
    return next(csv.reader(io.StringIO(line), skipinitialspace=False))


def checksum_ok(line: str) -> bool:
    i = line.rfind(',"')
    if i < 0 or not line.endswith('"'):
        return False
    try:
        want = int(line[i + 2:-1], 16)
    except ValueError:
        return False
    return sum(line[: i + 1].encode("latin-1")) % 65536 == want


def _num(s: str) -> float:
    s = s.strip()
    if not s or set(s) <= {"$"}:
        return INVALID
    try:
        return float(s)
    except ValueError:
        return INVALID


def _parse_settings(lines: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in lines:
        for name, value in re.findall(r"([A-Z][A-Z0-9_]*)\s*:?=\s*(\S*)", line):
            out.setdefault(name, value.strip('"'))
    return out


def read_cev(path: Path | str) -> CevEvent:
    raw = Path(path).read_bytes()
    end = raw.find(b"\x00")  # some files carry a binary trailer after the text
    text = raw[: end if end > 0 else len(raw)].decode("latin-1")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 8 or not lines[0].startswith('"FID"'):
        raise CevError("not an SEL compressed event report (first line must start with \"FID\")")
    bad = 0

    def row(i: int) -> list[str]:
        nonlocal bad
        if not checksum_ok(lines[i]):
            bad += 1
        return _fields(lines[i])[:-1]  # drop the checksum

    header = dict(zip(row(0), row(1)))
    t = dict(zip(row(2), row(3)))
    try:
        trig = datetime(int(t["YEAR"]), int(t["MONTH"]), int(t["DAY"]), int(t["HOUR"]), int(t["MIN"]),
                        int(t["SEC"])) + timedelta(milliseconds=int(t["MSEC"]))
    except (KeyError, ValueError) as exc:
        raise CevError(f"bad trigger time: {exc}") from exc
    summary = {k.strip(): v.strip() for k, v in zip(row(4), row(5))}

    labels = row(6)
    if "TRIG" not in labels:
        raise CevError("channel label row has no TRIG column")
    ti = labels.index("TRIG")
    analog_labels = labels[:ti]
    digital_names = labels[ti + 1].split() if len(labels) > ti + 1 else []

    analog: list[list[float]] = [[] for _ in analog_labels]
    states: list[list[int] | None] = []  # per row; None where the row has no digital sample
    trigger_index = -1
    i = 7
    while i < len(lines) and re.match(r"\s*[-+0-9.]", lines[i]):
        f = row(i)
        for c in range(len(analog_labels)):
            analog[c].append(_num(f[c]) if c < len(f) else INVALID)
        if len(f) > ti and ">" in f[ti]:
            trigger_index = i - 7
        hexs = f[ti + 1].strip() if len(f) > ti + 1 else ""
        if hexs:
            bits = bin(int(hexs, 16))[2:].zfill(4 * len(hexs))
            states.append([int(bits[k]) if k < len(bits) else 0 for k in range(len(digital_names))])
        else:
            states.append(None)
        i += 1
    if not analog or not analog[0]:
        raise CevError("no sample rows found")
    # Digital elements can be recorded at a lower rate than analog channels (SAM/CYC_D).
    # A digital sample covers the rows since the previous one, as in SEL's own COMTRADE
    # conversion; after the last digital sample the state is held.
    filled: list[list[int]] = [None] * len(states)  # type: ignore[list-item]
    nxt = next((st for st in reversed(states) if st is not None), [0] * len(digital_names))
    for r in range(len(states) - 1, -1, -1):
        if states[r] is not None:
            nxt = states[r]
        filled[r] = nxt
    digital = [[filled[r][c] for r in range(len(filled))] for c in range(len(digital_names))]

    settings: dict[str, str] = {}
    rest = lines[i:]
    if rest and rest[0].startswith('"SETTINGS"'):
        settings = _parse_settings(rest[1:])
    return CevEvent(header=header, trigger_time=trig, summary=summary, analog_labels=analog_labels,
                    analog=analog, digital_names=digital_names, digital=digital,
                    trigger_index=max(trigger_index, 0), settings=settings, bad_checksums=bad)


# --------------------------------------------------------------------------- COMTRADE


def _unit(label: str) -> tuple[str, str]:
    """(channel id, unit) from an SEL label such as 'VA(kV)' or 'IA(A)'."""
    m = re.match(r"^(.*?)\((kV|V|A|kA|Hz|C)\)$", label)
    if m:
        return m.group(1), m.group(2)
    if label == "FREQ":
        return label, "Hz"
    if label == "VDC":
        return label, "V"
    if label.startswith("V"):
        return label, "kV"
    if label.startswith("I"):
        return label, "A"
    return label, ""


def _ratio(name: str, event: CevEvent) -> float | None:
    s, st = event.summary, event.settings

    def val(*keys: str) -> float | None:
        for k in keys:
            for src in (s, st):
                v = src.get(k)
                try:
                    if v is not None and float(v) > 0:
                        return float(v)
                except ValueError:
                    pass
        return None

    if name.startswith("I"):
        if name in ("IN",):
            return val("CTR_IN", "CTRN")
        if name in ("IG",):
            return val("CTR_IG", "CTR_IA", "CTR")
        return val(f"CTR_{name}", "CTR_IA", "CTR")
    if name.startswith("V") and name not in ("VDC",):
        if name == "VS":
            return val("PTRS", "PTR")
        return val(f"PTR_{name}", "PTR_VAB", "PTR")
    return None


def _fmt_time(ts: datetime) -> str:
    return ts.strftime("%d/%m/%Y,%H:%M:%S.") + f"{ts.microsecond:06d}"


def write_comtrade(event: CevEvent, out_dir: Path | str, name: str) -> Path:
    """Write <name>.cfg/.dat/.hdr (COMTRADE 1999, ASCII). Channels with no valid samples
    are left out. Returns the .cfg path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    spc = event.samples_per_cycle
    if spc <= 0:
        raise CevError("SAM/CYC_A missing")
    f_nom = event.nominal_hz
    fs = spc * f_nom
    n = len(event.analog[0])
    keep = [c for c, ch in enumerate(event.analog) if any(v != INVALID for v in ch)]
    dig = [c for c, nm in enumerate(event.digital_names) if nm != "*"]

    lines = [f"{event.header.get('FID', 'SEL')},{event.relay},1999", f"{len(keep) + len(dig)},{len(keep)}A,{len(dig)}D"]
    scales = []
    for k, c in enumerate(keep, start=1):
        cid, unit = _unit(event.analog_labels[c])
        ph = cid[1] if len(cid) >= 2 and cid[0] in "IV" and cid[1] in "ABC" and len(cid) <= 3 else ""
        vals = [v for v in event.analog[c] if v != INVALID]
        lo, hi = min(vals), max(vals)
        a = max(hi - lo, 1e-6) / 60000.0
        b = (hi + lo) / 2.0
        scales.append((a, b))
        ratio = _ratio(cid, event)
        primary, secondary = (ratio, 1.0) if ratio else (1.0, 1.0)
        lines.append(f"{k},{event.analog_labels[c]},{ph},,{unit},{a:.9g},{b:.9g},0,-32767,32767,"
                     f"{primary:g},{secondary:g},P")
    for k, c in enumerate(dig, start=1):
        lines.append(f"{k},{event.digital_names[c]},,,0")
    lines += [f"{f_nom:g}", "1", f"{fs:g},{n}"]
    start = event.trigger_time - timedelta(seconds=event.trigger_index / fs)
    lines += [_fmt_time(start), _fmt_time(event.trigger_time), "ASCII", "1"]
    cfg = out / f"{name}.cfg"
    cfg.write_text("\r\n".join(lines) + "\r\n", encoding="latin-1")

    rows = []
    dt_us = 1e6 / fs
    for i in range(n):
        vals = []
        for (a, b), c in zip(scales, keep):
            v = event.analog[c][i]
            vals.append("99999" if v == INVALID else str(int(round((v - b) / a))))
        rows.append(",".join([str(i + 1), str(int(round(i * dt_us)))] + vals + [str(event.digital[c][i]) for c in dig]))
    (out / f"{name}.dat").write_text("\r\n".join(rows) + "\r\n", encoding="latin-1")

    hdr = [f"Converted from an SEL compressed event report by rea ({event.relay}).",
           "Event summary as reported by the relay:"]
    hdr += [f"  {k} = {v}" for k, v in event.summary.items()]
    (out / f"{name}.hdr").write_text("\n".join(hdr) + "\n", encoding="latin-1")
    return cfg


def rea_settings(event: CevEvent) -> str:
    """A rea-core settings file from the relay settings in the event (what can be mapped)."""
    st = event.settings
    out = [f"# from the settings stored in the {event.relay} event report"]
    for key in ("Z1MAG", "Z1ANG", "PHROT", "NFREQ"):
        if st.get(key):
            out.append(f"{key} := {st[key]}")
    for key in ("Z2F", "Z2R"):
        try:
            float(st.get(key, "x"))
            out.append(f"{key} := {st[key]}")
        except ValueError:
            pass
    if st.get("E32", "").upper() == "AUTO":
        out.append("# E32 = AUTO: the relay computes Z2F/Z2R itself; rea uses its defaults from Z1MAG")
    return "\n".join(out) + "\n"


def convert(path: Path | str, out_dir: Path | str, name: str | None = None) -> tuple[Path, Path, CevEvent]:
    """CEV -> (cfg path, settings path, parsed event)."""
    ev = read_cev(path)
    stem = name or re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(path).stem)
    cfg = write_comtrade(ev, out_dir, stem)
    settings = Path(out_dir) / f"{stem}.settings.txt"
    settings.write_text(rea_settings(ev), encoding="utf-8")
    return cfg, settings, ev
