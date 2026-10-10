"""Number-provenance validator.

Every sentence the narrator proposes is checked before it reaches the user.

Numbers
  * Every numeric claim must equal a value the physics core produced, at the
    precision written: "603 ms" is a correct rounding of 603.13 ms, "604 ms" is not.
  * A unit written after the number must fit the fact's unit. ms<->s, A<->kA and
    V<->kV are rescaled; "36.19 ms" cannot borrow the value of 36.19 cycles.
  * A signed claim must match the sign. An unsigned claim may quote a magnitude
    ("2.28 ms faster" for a deviation of -2.28 ms).
  * Strict mode (the default) also binds each number to a fact. If the sentence cites
    facts of the number's kind (same unit dimension), the number must be one of them.
    Otherwise the sentence must mention what the fact measures (a breaker time in a
    sentence about the breaker). This catches a right number used for the wrong thing
    in most cases, not all, so the key-facts table is always printed next to the text.
  * Spelled-out quantities ("three cycles") are rejected because they cannot be checked.
  * Clock times and dates must appear in one of the core's text facts.

Categorical claims
  * The fault type (code, class and faulted phases), the direction, trip / no trip,
    the relay and breaker verdicts and "slower/faster than expected" must not
    contradict the core.

Identifiers that merely contain digits (51P1, 3I0, 52A, U3, C37.112, SEL-421) are not
claims. Rejected sentences are dropped and listed in the audit.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .facts import FactSheet

# --------------------------------------------------------------------------- tokens

# Masked before scanning: standards, device model numbers and device/contact names.
_IDENTIFIER_SPANS = [
    re.compile(r"\b(?:IEEE|IEC|ANSI|NERC)\s*(?:Std\.?\s*)?[A-Z]?\d+(?:[.\-]\d+)*(?:-\d{2,4})?", re.I),
    re.compile(r"\bC37\.\d+(?:-\d{4})?\b"),
    re.compile(r"\b[A-Z]{2,}-\d+[A-Z]*\b"),
    re.compile(r"\b52[ABab]\b"),  # breaker auxiliary contacts
    re.compile(r"\b\d{2}[A-Z]?(?:/\d{2}[A-Z]?)+\b"),  # ANSI device lists such as 50/51 or 50/51N
    re.compile(r"\b(?:device|ANSI)\s+\d+\b", re.I),
]
_CLOCK = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?\b")
_DATE = re.compile(r"\b\d{1,4}([/-])\d{1,2}\1\d{1,4}\b")
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])([-+−]?)((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)")
_UNIT_AFTER = re.compile(r"(?:\s|-)?([A-Za-z%°ΩΩ×]+)")

# Units a sentence may write after a number: (dimension, scale to the base unit).
_UNITS: dict[str, tuple[str, float]] = {}
for _names, _dim, _scale in (
    (("ms", "msec", "millisecond", "milliseconds"), "time", 1e-3),
    (("s", "sec", "secs", "second", "seconds"), "time", 1.0),
    (("cycle", "cycles", "cyc"), "cycles", 1.0),
    (("a", "amp", "amps", "ampere", "amperes"), "current", 1.0),
    (("ka", "kiloamp", "kiloamps", "kiloampere", "kiloamperes"), "current", 1e3),
    (("v", "volt", "volts"), "voltage", 1.0),
    (("kv", "kilovolt", "kilovolts"), "voltage", 1e3),
    (("ohm", "ohms", "ω", "Ω"), "impedance", 1.0),
    (("hz", "hertz"), "frequency", 1.0),
    (("deg", "degree", "degrees", "°"), "angle", 1.0),
    (("times", "x", "×"), "ratio", 1.0),
    (("%", "percent"), "percent", 1.0),
):
    for _n in _names:
        _UNITS[_n] = (_dim, _scale)

# Units the core writes on facts.
_FACT_UNITS: dict[str, tuple[str, float]] = {
    "ms": ("time", 1e-3), "s": ("time", 1.0), "cycles": ("cycles", 1.0), "A": ("current", 1.0),
    "kA": ("current", 1e3), "V": ("voltage", 1.0), "kV": ("voltage", 1e3), "ohm": ("impedance", 1.0),
    "Hz": ("frequency", 1.0), "deg": ("angle", 1.0), "%": ("percent", 1.0),
}
# Letters glued to a number that still read as a unit ("12A", "3ms", "40%").
_GLUED_UNITS = {"ms", "s", "a", "ka", "v", "kv", "hz", "x", "%", "°", "ω", "Ω", "cyc"}
_ORDINALS = {"st", "nd", "rd", "th"}

_SPELLED = re.compile(
    r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|"
    r"fifty|sixty|hundred|thousand|half|quarter|double|twice|triple)\b[\s-]+"
    r"(cycles?|milli\w*|seconds?|amps?|amperes?|volts?|ohms?|percent|times|kilo\w*)\b",
    re.I,
)

# --------------------------------------------------------------------------- subjects

# What each fact measures, as words a sentence would use when talking about it.
# Longest prefix wins. Used to bind a number the model did not cite, so the words are
# chosen to separate facts that share a unit (inception from the trigger vs from the
# first sample, the expected time vs the deviation, one pole vs another).
_SUBJECTS: list[tuple[str, str]] = sorted([
    ("relay.operate", r"relay|trip|operat"),
    ("relay.trip_ms", r"first sample|record start|start of the record|into the record"),
    ("relay.trip_after_fault_end", r"after the fault|already|ended|cleared|late"),
    ("fault.after_breaker_close", r"breaker clos|closed|closing|energi"),
    ("breaker.close", r"clos"),
    ("relay.trip", r"relay|trip"),
    ("breaker.52a", r"52a|contact|auxiliary|status"),
    ("breaker.interrupt", r"breaker|interrupt|clear|last pole"),
    ("breaker.", r"breaker|pole|interrupt|clear|open"),
    ("expected.m_", r"pickup|multiple|times"),
    ("expected.curve_time", r"constant current|curve time"),
    ("expected.", r"expect|should|predict"),
    ("assessment.deviation", r"deviat|differ|minus|faster|slower|earlier|later|compared|than expected|than predicted"),
    ("assessment.tolerance", r"toleran|allow|margin|within|accept"),
    ("assessment.breaker_limit", r"limit|maximum|allow|accept|bkrmax"),
    ("settings.Z2F", r"z2f|forward threshold"),
    ("settings.Z2R", r"z2r|reverse threshold"),
    ("settings.CTR", r"ct ratio|ctr\b|current transformer"),
    ("settings.50P1", r"50p1|instantaneous"),
    ("settings.51P1", r"51p1|phase (?:time-)?overcurrent"),
    ("settings.51G1", r"51g1|ground (?:time-)?overcurrent"),
    ("settings.", r"setting"),
    ("fault.inception_from_trigger", r"trigger"),
    ("fault.inception", r"first sample|record start|start of the record|into the record"),
    ("fault.duration", r"lasted|lasting|duration|persist"),
    ("fault.z2", r"z2|impedance"),
    ("fault.dz1", r"impedance|z1"),
    ("fault.i2_i0", r"angle"),
    ("fault.max_phase_rms_primary", r"primary|largest|maximum|max\b|highest"),
    ("fault.max_phase", r"largest|maximum|max\b|highest"),
    ("fault.peak", r"peak|instantaneous|offset|asymmetr"),
    ("fault.asymmetry", r"asymmetr|offset|peak"),
    ("fault.3i0", r"3i0|residual|zero[- ]sequence"),
    ("fault.i1", r"positive[- ]sequence|i1\b"),
    ("fault.i2", r"negative[- ]sequence|i2\b"),
    ("fault.steady", r"steady|snapshot"),
    ("fault.", r"current|fault"),
    ("prefault.", r"prefault|pre-fault|load|before the fault"),
    ("record.", r"record|sampl|frequency|nominal|channel|comtrade|length|revision"),
], key=lambda p: -len(p[0]))

# Subjects specific enough to bind a number even when the sentence cites other facts of
# the same kind (a model that cites the operate time but forgets the deviation it also
# quotes). Generic ones ("current", "relay", "expected") are not: there a related value
# could stand in for a cited one.
_SPECIFIC_SUBJECTS = {
    "relay.trip_after_fault_end", "fault.after_breaker_close", "breaker.52a", "expected.curve_time",
    "assessment.deviation", "assessment.tolerance", "settings.Z2F", "settings.Z2R", "settings.CTR",
    "settings.50P1", "settings.51P1", "settings.51G1", "fault.inception_from_trigger", "fault.inception",
    "fault.duration", "fault.z2", "fault.dz1", "fault.i2_i0", "fault.max_phase_rms_primary", "fault.max_phase",
    "fault.3i0", "fault.i1", "fault.i2",
}

# Per-phase facts also need their phase named: "phase A", "A-phase", "pole A", "IA".
_PHASE_FACT = re.compile(r"^(?:fault|prefault)\.([iv])([abc])_rms|^breaker\.clear_([abc])_after")


def subject_pattern(fact_id: str) -> re.Pattern | None:
    for prefix, rx in _SUBJECTS:
        if fact_id.startswith(prefix):
            return re.compile(r"\b(?:" + rx + ")", re.I)
    return None


def is_specific(fact_id: str) -> bool:
    if _PHASE_FACT.match(fact_id):
        return True
    for prefix, _rx in _SUBJECTS:
        if fact_id.startswith(prefix):
            return prefix in _SPECIFIC_SUBJECTS
    return False


def mentions_subject(fact_id: str, text: str) -> bool:
    """True if the sentence talks about what this fact measures."""
    m = _PHASE_FACT.match(fact_id)
    if m:
        letter = (m.group(2) or m.group(3)).upper()
        if not re.search(rf"\b[Pp]hase\s+{letter}\b|\b{letter}[- ](?:phase|pole)\b|\b[Pp]ole\s+{letter}\b|"
                         rf"\b[IV]{letter}\b", text):
            return False
        if m.group(1) == "v" and not re.search(r"voltage", text, re.I):
            return False
        if fact_id.startswith("prefault.") and not re.search(r"prefault|pre-fault|load|before the fault", text, re.I):
            return False
        if fact_id.startswith("breaker.") and not re.search(r"breaker|pole|interrupt|clear", text, re.I):
            return False
        return True
    p = subject_pattern(fact_id)
    return bool(p and p.search(text))


# --------------------------------------------------------------------------- fault types

_PHASES = {
    "AG": ("SLG", "A"), "BG": ("SLG", "B"), "CG": ("SLG", "C"),
    "AB": ("LL", "AB"), "BC": ("LL", "BC"), "CA": ("LL", "AC"),
    "ABG": ("LLG", "AB"), "BCG": ("LLG", "BC"), "CAG": ("LLG", "AC"),
    "ABC": ("3P", "ABC"),
}
_FAULT_CODE_RE = re.compile(r"\b(" + "|".join(sorted(_PHASES, key=len, reverse=True)) + r")\b")
_CLASS_NAMES = {"SLG": "single-phase-to-ground", "LL": "phase-to-phase", "LLG": "two-phase-to-ground",
                "3P": "three-phase"}
_NOT_A_FAULT = r"(?![\s-]+(?:voltages?|loops?|elements?|quantit\w*|impedances?|distance|units?|currents?|measurements?))"
_CLASS_PATTERNS = [
    ("LLG", re.compile(r"\b(?:double[- ]line|two[- ]phase|phase[- ]to[- ]phase|line[- ]to[- ]line|double[- ]phase)"
                       r"[- ]to[- ]ground" + _NOT_A_FAULT + r"|\b(?:LLG|DLG|2LG)\b", re.I)),
    ("SLG", re.compile(r"\b(?:single[- ](?:line|phase)[- ]to[- ]ground|(?:line|phase)[- ]to[- ]ground)"
                       + _NOT_A_FAULT + r"|\b(?:SLG|1LG)\b", re.I)),
    ("LL", re.compile(r"\b(?:line[- ]to[- ]line|phase[- ]to[- ]phase)(?![- ]to[- ]ground)" + _NOT_A_FAULT
                      + r"|\bLL\b", re.I)),
    ("3P", re.compile(r"\b(?:three|3)[- ]phase(?:[- ]to[- ]ground)?\s+(?:short[- ]circuit|fault)|\b(?:LLL|3LG|3PH)\b",
                      re.I)),
]
# Phase-rotation names (PHROT := ABC, "ACB rotation") are not fault types.
_ROTATION = re.compile(r"PHROT\s*:?=\s*A(?:BC|CB)\b|\bA(?:BC|CB)\s+(?:phase\s+)?(?:rotation|sequence)\b|"
                       r"\brotation\s+(?:is\s+|of\s+)?A(?:BC|CB)\b", re.I)
_SLG_PHASE = re.compile(r"(?<![ABC][-–])\b([ABC])[- ]phase[- ]to[- ]ground\b" + _NOT_A_FAULT)
_PAIR = re.compile(r"\b([ABC])[-–]([ABC])\b(?=[- ](?:phase[- ]to[- ]phase|to[- ]ground|phase[- ]to[- ]ground|fault))")

# --------------------------------------------------------------------------- results


@dataclass
class SentenceCheck:
    text: str
    ok: bool
    numbers: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    fact_ids: list[str] = field(default_factory=list)
    bindings: list[tuple[str, str, str]] = field(default_factory=list)  # (claim, fact id, cited|context|any)
    notes: list[str] = field(default_factory=list)

    def bind(self, token: str, fact_id: str, via: str) -> None:
        self.bindings.append((token, fact_id, via))

    @property
    def bound(self) -> dict[str, str]:
        return {t: f for t, f, _ in self.bindings}

    @property
    def via(self) -> dict[str, str]:
        return {t: v for t, _, v in self.bindings}


@dataclass
class ValidationResult:
    checks: list[SentenceCheck]
    strict: bool

    @property
    def kept(self) -> list[SentenceCheck]:
        return [c for c in self.checks if c.ok]

    @property
    def rejected(self) -> list[SentenceCheck]:
        return [c for c in self.checks if not c.ok]

    @property
    def numbers_total(self) -> int:
        return sum(len(c.numbers) for c in self.checks)

    @property
    def numbers_delivered(self) -> int:
        return sum(len(c.numbers) for c in self.kept)

    @property
    def numbers_traced_delivered(self) -> int:
        return sum(len(c.bindings) for c in self.kept)

    @property
    def numbers_via_context(self) -> int:
        return sum(1 for c in self.kept for _, _, v in c.bindings if v == "context")


@dataclass(frozen=True)
class Claim:
    token: str
    value: float
    decimals: int
    signed: bool
    unit: str | None


@dataclass(frozen=True)
class _FactValue:
    fact_id: str
    value: float
    dim: str | None
    scale: float


# --------------------------------------------------------------------------- helpers


def _mask(text: str, patterns: list[re.Pattern]) -> str:
    for rx in patterns:
        text = rx.sub(lambda m: "#" * len(m.group(0)), text)
    return text


def extract_claims(sentence: str) -> list[Claim]:
    """Numeric claims in a sentence, with the unit written after each (if any)."""
    return [c for _, _, c in extract_claims_at(sentence)]


def extract_claims_at(sentence: str) -> list[tuple[int, int, Claim]]:
    """Like extract_claims, with the (start, end) of each number in the sentence."""
    masked = _mask(sentence, _IDENTIFIER_SPANS + [_CLOCK, _DATE])
    claims = []
    for m in _NUMBER.finditer(masked):
        sign, digits = m.group(1), m.group(2)
        start, end = m.start(), m.end()
        after = masked[end:]
        if sign and start > 0 and (masked[start - 1].isalnum()):
            sign = ""  # "SEL-421" leftovers, ranges such as "5-8"
            if masked[start - 1].isalpha():
                continue
        glued = re.match(r"[A-Za-z%°ΩΩ]+", after)
        if glued and glued.group(0).lower() in _ORDINALS:
            continue  # "2nd pole" is a position, not a measurement
        if glued and glued.group(0).lower() not in _GLUED_UNITS and glued.group(0).lower() not in _UNITS:
            continue  # identifier such as 51P1, 3I0, 32Q, 50G
        if re.match(r"-phase|\s+phase-to|-pole|-line|-terminal|-bus", after, re.I):
            continue  # "3-phase", "2-pole" name things, they do not measure them
        unit = None
        u = _UNIT_AFTER.match(after)
        if u and u.group(1).lower() in _UNITS:
            unit = u.group(1).lower()
        signed = sign in ("-", "+", "−")
        value = float(digits.replace(",", ""))
        if sign in ("-", "−"):
            value = -value
        token = ("-" if value < 0 else "") + digits.replace(",", "")
        claims.append((start, end, Claim(token=token, value=value,
                                         decimals=len(digits.split(".", 1)[1]) if "." in digits else 0,
                                         signed=signed, unit=unit)))
    return claims


def extract_numbers(sentence: str) -> list[str]:
    """Numeric claims as written (sign kept, thousands separators removed)."""
    return [c.token for c in extract_claims(sentence)]


def _fact_values(sheet: FactSheet) -> list[_FactValue]:
    out: list[_FactValue] = []
    for f in sheet.facts:
        if f.kind == "number" and f.value is not None:
            dim, scale = _FACT_UNITS.get(f.unit, (None, 1.0))
            out.append(_FactValue(f.id, float(f.value), dim, scale))
        elif f.kind == "text" and f.value:
            masked = _mask(str(f.value), [_CLOCK, _DATE])
            for t in re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", masked):
                out.append(_FactValue(f.id, float(t), None, 1.0))
    return out


def _fact_dim(f) -> str:
    return _FACT_UNITS.get(f.unit, ("none", 1.0))[0]


def _close(claim: Claim, v: float) -> bool:
    tol = 0.5 * 10.0 ** (-claim.decimals) + 1e-9
    if claim.signed:
        return abs(v - claim.value) <= tol
    return abs(abs(v) - claim.value) <= tol


def candidates(claim: Claim, values: list[_FactValue]) -> list[str]:
    """Fact ids whose value the claim reproduces, at its precision and in its unit."""
    out: list[str] = []
    for fv in values:
        if claim.unit is None:
            ok = _close(claim, fv.value)
        else:
            dim, scale = _UNITS[claim.unit]
            if fv.dim is None:
                ok = dim == "ratio" and _close(claim, fv.value)
            elif fv.dim == dim:
                ok = _close(claim, fv.value * fv.scale / scale)
            else:
                ok = False
        if ok and fv.fact_id not in out:
            out.append(fv.fact_id)
    return out


def split_sentences(text: str) -> list[str]:
    """Split a paragraph into sentences without breaking decimals or common abbreviations."""
    parts = re.split(r"(?<=[.!?])[\"')\]]?\s+(?=[A-Z(\"'])", text.strip())
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if out and re.search(r"\b(?:e\.g|i\.e|vs|approx|Fig|No|Std|Eq|Ref|ca)\.$", out[-1]):
            out[-1] = out[-1] + " " + p
        else:
            out.append(p)
    return out


# --------------------------------------------------------------------------- categorical checks


def _fault_type_problems(text: str, fault_type: str) -> list[str]:
    problems = []
    text = _ROTATION.sub(" ", text)
    cls, phases = _PHASES[fault_type]
    other = [c for c in _FAULT_CODE_RE.findall(text) if c != fault_type]
    if other:
        problems.append(f"names fault type {other[0]} but the core found {fault_type}")
    rest = text
    for name, rx in _CLASS_PATTERNS:
        m = rx.search(rest)
        if m:
            if name != cls:
                problems.append(f"describes a {_CLASS_NAMES[name]} fault but the core found {fault_type} "
                                f"({_CLASS_NAMES[cls]})")
                break
            rest = rest[:m.start()] + "#" * (m.end() - m.start()) + rest[m.end():]
    for m in _SLG_PHASE.finditer(text):
        if cls == "SLG" and m.group(1) != phases:
            problems.append(f"names phase {m.group(1)} but the core found {fault_type}")
    for m in _PAIR.finditer(text):
        pair = "".join(sorted({m.group(1), m.group(2)}))
        if cls in ("LL", "LLG") and pair != phases:
            problems.append(f"names phases {m.group(1)}-{m.group(2)} but the core found {fault_type}")
    return problems


def _verdict_problems(text: str, sheet: FactSheet) -> list[str]:
    problems = []
    tripped = sheet.value("relay.tripped")
    if tripped is None and (sheet.value("relay.operate_ms") is not None or sheet.value("relay.trip_ms") is not None):
        tripped = True
    if tripped is True and re.search(r"\brelay\b(?:(?!element|function|zone|instantaneous|5[01])[^.;]){0,40}?"
                                     r"\b(?:did not|didn't|failed to|never|does not)\s+(?:trip|operate)", text, re.I):
        problems.append("says the relay did not trip, but the core saw the trip output assert")
    if tripped is False and re.search(r"\brelay\s+(?:tripped|operated)\b", text, re.I):
        problems.append("says the relay tripped, but the trip output never asserted")

    verdict = sheet.value("assessment.relay")
    good = ("operated as expected", "correctly did not trip")
    if verdict and verdict not in good and verdict != "undetermined":
        if re.search(r"\brelay\b[^.;]{0,30}?\b(?:operated|performed|tripped|worked|responded|behaved)\s+"
                     r"(?:as expected|correctly|properly|normally|as it should)\b", text, re.I):
            problems.append(f"says the relay performed correctly, but the core verdict is '{verdict}'")
    if verdict == "operated as expected" and re.search(
            r"\b(?:misoperat\w*|too slow|too fast|failed to trip|outside (?:its|the) (?:directional )?zone)\b",
            text, re.I):
        problems.append("claims a misoperation, but the core verdict is 'operated as expected'")

    # Only the relay has an expected time; a breaker or a duration compared with
    # "the expected" one is a comparison the core never made.
    cmp_rx = (r"\b(slower|later|faster|earlier|quicker|longer|shorter)\s+than\s+(?:the\s+)?(?:expected|predicted|"
              r"the curve|(?:the|its) settings?)")
    about_relay = re.search(r"\b(?:relay|operat\w*|trip\w*)\b", text, re.I)
    if re.search(cmp_rx, text, re.I) and not about_relay:
        problems.append("compares with an expected value the core did not compute (only the relay has one)")
    dev = sheet.value("assessment.deviation_ms")
    if isinstance(dev, (int, float)) and dev != 0 and about_relay:
        for m in re.finditer(cmp_rx, text, re.I):
            word = m.group(1).lower()
            if dev < 0 and word in ("slower", "later", "longer"):
                problems.append("says the relay was slower than expected, but it operated earlier")
            if dev > 0 and word in ("faster", "earlier", "quicker", "shorter"):
                problems.append("says the relay was faster than expected, but it operated later")

    # z2 against the thresholds, whatever the sentence puts in front of "below"/"above".
    z2, z2f, z2r = sheet.value("fault.z2_ohm"), sheet.value("settings.Z2F"), sheet.value("settings.Z2R")
    if all(isinstance(x, (int, float)) for x in (z2, z2f, z2r)):
        for m in re.finditer(r"\b(below|less than|under|above|greater than|over|exceed\w*)\s+the\s+(forward|reverse)"
                             r"\s+threshold", text, re.I):
            limit = z2f if m.group(2).lower() == "forward" else z2r
            under = m.group(1).lower() in ("below", "less than", "under")
            if (under and not z2 < limit) or (not under and not z2 > limit):
                problems.append(f"says z2 is {m.group(1).lower()} the {m.group(2).lower()} threshold, but z2 = {z2} "
                                f"and the threshold is {limit}")
    # Breaker times run from the trip, not from inception; and there is no expected breaker time.
    if re.search(r"\b(?:breaker|interrupt\w*|52a)\b", text, re.I):
        if re.search(r"\bafter (?:the )?(?:fault )?inception\b|\bafter the fault (?:began|started)\b", text, re.I) and \
                not re.search(r"\brelay\b", text, re.I):
            problems.append("times a breaker event from fault inception; the core measures it from the trip")
        if re.search(r"\bexpected (?:interrupt\w*|clearing|breaker|opening)\b", text, re.I):
            problems.append("mentions an expected breaker time, which the core does not compute")
    # "-3.74 ms before the trigger" says the opposite of what it means.
    if re.search(r"(?<![\w.])[-\u2212]\d+(?:\.\d+)?\s*(?:ms|s|cycles?)\s+before\b", text, re.I):
        problems.append("a negative time 'before' an event is a double negative")

    if sheet.value("settings.DIR") == "non-directional" and re.search(
            r"\bdirectional (?:control|supervision)\b[^.;]{0,20}?\b(?:was |is |were )?(?:applied|enabled|active|on)\b|"
            r"\bwith directional (?:control|supervision)\b(?! (?:off|disabled|not))", text, re.I):
        problems.append("says directional control was applied, but the setting is non-directional")

    bv = sheet.value("assessment.breaker")
    if bv == "normal" and re.search(r"\bbreaker\b[^.;]{0,60}?\b(?:slow|slower|too slow|failed|"
                                    r"did not (?:open|interrupt|clear))\b", text, re.I):
        problems.append("calls the breaker slow or failed, but the core verdict is 'normal'")
    if bv == "slow" and re.search(r"\bbreaker\b[^.;]{0,60}?\b(?:normal(?:ly)?|as expected|on time|fast|quickly)\b",
                                  text, re.I):
        problems.append("calls the breaker normal, but the core verdict is 'slow'")
    return problems


_COMPARE = re.compile(
    r"\b(?:(faster|quicker|earlier|shorter|less|lower|smaller|below|under)|"
    r"(slower|later|longer|more|higher|larger|greater|above|over|exceed\w*))\b(?:\s+than)?", re.I)


def _comparison_problems(text: str, claims_at: list[tuple[int, int, Claim]]) -> list[str]:
    """'X ms, faster than the expected Y ms' must have X < Y (and so on), when both numbers
    are in the sentence with comparable units and nothing but words between them."""
    problems = []
    for m in _COMPARE.finditer(text):
        before = [c for c in claims_at if c[1] <= m.start() and m.start() - c[1] <= 60]
        after = [c for c in claims_at if c[0] >= m.end() and c[0] - m.end() <= 40]
        if not before or not after:
            continue
        a, b = before[-1][2], after[0][2]
        between = text[before[-1][1]:after[0][0]]
        if re.fullmatch(r"\s*(?:ms|msec|s|sec|seconds?|cycles?|cyc|a|ka|v|kv|ohms?|%|hz|degrees?)?\s*",
                        text[before[-1][1]:m.start()], re.I):
            continue  # "0.87 ms later than 719.97 ms": the first number is the difference
        if re.search(r"[;:]|\band\b|\bwith\b", between[: m.start() - before[-1][1]]):
            continue  # the first number belongs to another clause
        da = _UNITS.get(a.unit or "", (None, 1.0))
        db = _UNITS.get(b.unit or "", (None, 1.0))
        if a.unit and b.unit and da[0] != db[0]:
            problems.append(f"compares {a.token} {a.unit} with {b.token} {b.unit}: different kinds of quantity")
            continue
        va, vb = a.value * da[1], b.value * db[1]
        less = m.group(1) is not None
        if (less and not va < vb) or (not less and not va > vb):
            word = (m.group(1) or m.group(2)).lower()
            problems.append(f"says {a.token} is {word} than {b.token}, which the numbers contradict")
    return problems


_NEGATIVE = re.compile(r"\bnegative\b(?![- ]sequence)", re.I)
_POSITIVE = re.compile(r"\bpositive\b(?![- ]sequence)", re.I)


def _sign_word_problems(text: str, chk: SentenceCheck, sheet: FactSheet) -> list[str]:
    """'1399.56 ms (a negative value ...)' when the fact is positive, and the reverse."""
    problems = []
    for token, fid, _ in chk.bindings:
        f = sheet.get(fid)
        if f is None or f.kind != "number" or not isinstance(f.value, (int, float)) or f.value == 0:
            continue
        if f.value > 0 and _NEGATIVE.search(text):
            problems.append(f"calls {fid} negative, but the core value is positive")
        elif f.value < 0 and _POSITIVE.search(text):
            problems.append(f"calls {fid} positive, but the core value is negative")
    return problems[:1]


# --------------------------------------------------------------------------- entry point


def validate(sentences: list[tuple[str, list[str]]], sheet: FactSheet, strict: bool = True,
             require_terminal: bool = False) -> ValidationResult:
    """Check (sentence, cited fact ids) pairs against the fact sheet."""
    values = _fact_values(sheet)
    texts = [str(f.value) for f in sheet.facts if f.kind == "text" and f.value]
    fault_type = sheet.value("fault.type")
    direction = sheet.value("fault.direction")
    checks = []
    for text, cited in sentences:
        text = text.strip()
        if not text:
            continue
        known = [c for c in cited if c in sheet]
        chk = SentenceCheck(text=text, ok=True, fact_ids=known)
        unknown = [c for c in cited if c not in sheet]
        if unknown:
            chk.notes.append("ignored unknown fact ids: " + ", ".join(unknown))

        cited_dims = {_fact_dim(sheet.get(c)) for c in known if sheet.get(c).kind == "number"}
        for claim in extract_claims(text):
            chk.numbers.append(claim.token)
            cands = candidates(claim, values)
            if not cands:
                chk.unsupported.append(claim.token)
                continue
            if not strict:
                chk.bind(claim.token, cands[0], "any")
                continue
            cited_hits = [c for c in cands if c in known]
            if cited_hits:
                # Two cited facts can share a value (3I0 equals the faulted phase current
                # when there is no load): give each claim a cited fact not used yet.
                used = {f for _, f, _ in chk.bindings}
                chk.bind(claim.token, next((c for c in cited_hits if c not in used), cited_hits[0]), "cited")
                continue
            # The sentence cites facts of this kind (same dimension) but not this one. A
            # related value could be standing in for a cited one (the expected time quoted
            # as the deviation), so only a specific subject may bind it (a deviation named
            # as a deviation, a phase-B current next to "phase B").
            dim = "any" if claim.unit is None else _UNITS[claim.unit][0]
            dim = "none" if dim == "ratio" else dim
            if (dim == "any" and cited_dims) or dim in cited_dims:
                hit = next((c for c in cands if is_specific(c) and mentions_subject(c, text)), None)
                if hit is not None:
                    chk.bind(claim.token, hit, "context")
                    continue
                chk.unsupported.append(claim.token)
                chk.notes.append(f"{claim.token} equals {cands[0]}, which the sentence does not cite, while it "
                                 "cites other facts of the same kind")
                continue
            hit = next((c for c in cands if mentions_subject(c, text)), None)
            if hit is not None:
                chk.bind(claim.token, hit, "context")
                continue
            chk.unsupported.append(claim.token)
            chk.notes.append(f"{claim.token} equals {cands[0]} but the sentence neither cites nor mentions it")
        if chk.unsupported:
            chk.reasons.append("numbers not traceable to the physics core: " + ", ".join(chk.unsupported))
        if strict:
            # One cited value quoted twice while another cited value of the same kind is
            # never quoted: one of the two numbers is standing in for the wrong fact.
            uses = Counter(f for _, f, via in chk.bindings if via == "cited")
            for fid, n in uses.items():
                dim = _fact_dim(sheet.get(fid))
                unused = [c for c in known if sheet.get(c).kind == "number" and c not in uses
                          and _fact_dim(sheet.get(c)) == dim]
                if n > 1 and unused:
                    chk.reasons.append(f"quotes {fid} {n} times but never the cited {unused[0]}")
                    break

        for rx in (_CLOCK, _DATE):
            for m in rx.finditer(_mask(text, _IDENTIFIER_SPANS)):
                if not any(m.group(0) in t for t in texts):
                    chk.reasons.append(f"time or date {m.group(0)} does not appear in the record")

        spelled = _SPELLED.findall(text)
        if spelled:
            chk.reasons.append("spelled-out quantity cannot be verified: " + ", ".join(" ".join(s) for s in spelled))
        if isinstance(fault_type, str) and fault_type in _PHASES:
            chk.reasons += _fault_type_problems(text, fault_type)
        # Threshold and element names (Z2F, "forward threshold") are not direction claims.
        plain = re.sub(r"\b(forward|reverse)\s+(threshold|reach|setting|element|zone|limit)s?\b", "", text,
                       flags=re.I)
        # "the directional supervision setting was forward" describes DIR, not the fault.
        plain = re.sub(r"\b(?:setting|supervision|DIR)\b[^.;,]{0,25}?\b(?:forward|reverse)\b", "", plain,
                       flags=re.I)
        if direction in ("forward", "reverse"):
            opposite = "reverse" if direction == "forward" else "forward"
            if re.search(rf"\b{opposite}\b", plain, re.I):
                chk.reasons.append(f"says {opposite} but the core found {direction}")
        elif re.search(r"\b(?:forward|reverse)\b", plain, re.I) and not re.search(
                r"could not|cannot|can't|unknown|undetermined|not (?:be )?determined|no signal|abstain", text, re.I):
            chk.reasons.append("states a fault direction, but the core could not determine one")
        # Only the overcurrent elements' DIR setting is non-directional, never the fault.
        if re.search(r"\bnon-?directional\s+(?:fault|phase|direction|event)\b", text, re.I) or (
                re.search(r"\bfault\b[^.;]*\b(?:was|is|occurred)\b[^.;]*\bnon-?directional\b", text, re.I)
                and not re.search(r"element|setting|overcurrent|supervision|\bDIR\b|\b5[01][PG]?\d?\b", text, re.I)):
            chk.reasons.append("calls the fault non-directional; only the DIR setting of the overcurrent elements is")
        chk.reasons += _verdict_problems(text, sheet)
        chk.reasons += _comparison_problems(text, extract_claims_at(text))
        chk.reasons += _sign_word_problems(text, chk, sheet)
        if require_terminal and not re.search(r"[.!?][\"')\]]?$", text):
            chk.reasons.append("incomplete sentence")
        chk.ok = not chk.reasons
        checks.append(chk)
    return ValidationResult(checks=checks, strict=strict)
