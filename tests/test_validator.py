import pytest

from rea.narrator import TemplateNarrator
from rea.validator import extract_claims, extract_numbers, split_sentences, validate


def ok(sheet, text, cited=(), strict=True, **kw):
    res = validate([(text, list(cited))], sheet, strict=strict, **kw)
    return res.checks[0].ok, res.checks[0]


# ---------------------------------------------------------------- extraction


@pytest.mark.parametrize("text, expected", [
    ("51G1 picked up and 50P1 stayed quiet", []),
    ("the residual current 3I0 and the 52A contact", []),
    ("per IEEE C37.112-2018 and C37.111", []),
    ("an SEL-421 and an SEL-351S", []),
    ("curve U4, threshold Z2F", []),
    ("the 2nd pole and the 3rd cycle", []),
    ("a 3-phase fault", []),
    ("603.13 ms and -0.97 ohm", ["603.13", "-0.97"]),
    ("1,681 A primary", ["1681"]),
    ("between 5-8 cycles", ["5", "8"]),
])
def test_extract_numbers(text, expected):
    assert extract_numbers(text) == expected


def test_units_are_read():
    claims = extract_claims("asserted 603.13 ms (36.19 cycles), 7.00 A, 1.681 kA, 11.68 times pickup, 5.0-cycle limit")
    assert [(c.token, c.unit) for c in claims] == [
        ("603.13", "ms"), ("36.19", "cycles"), ("7.00", "a"), ("1.681", "ka"), ("11.68", "times"), ("5.0", "cycle")]


def test_split_sentences_keeps_decimals_and_abbreviations():
    text = "The relay tripped in 603.13 ms. The breaker cleared vs. the limit in 2.50 cycles! Fine (e.g. normal). Done."
    assert split_sentences(text) == [
        "The relay tripped in 603.13 ms.",
        "The breaker cleared vs. the limit in 2.50 cycles!",
        "Fine (e.g. normal).",
        "Done.",
    ]


# ---------------------------------------------------------------- numbers


@pytest.mark.parametrize("text, good", [
    ("The relay operated 603.13 ms after the fault began.", True),
    ("The relay operated 603.1 ms after the fault began.", True),  # rounding is fine
    ("The relay operated 603 ms after the fault began.", True),
    ("The relay operated 0.603 s after the fault began.", True),  # rescaled unit
    ("The relay operated 603.14 ms after the fault began.", False),  # invented digit
    ("The relay operated 604 ms after the fault began.", False),
    ("The relay operated 36.19 ms after the fault began.", False),  # 36.19 is in cycles
    ("The relay operated 36.19 cycles after the fault began.", True),
])
def test_number_precision_and_units(bg_sheet, text, good):
    assert ok(bg_sheet, text, ["relay.operate_ms", "relay.operate_cycles"])[0] is good


def test_kiloamps(bg_sheet):
    assert ok(bg_sheet, "The largest fault current was 1.681 kA primary.", ["fault.max_phase_rms_primary_a"])[0]


def test_sign_rules(bg_sheet):
    cited = ["assessment.deviation_ms"]
    assert ok(bg_sheet, "Actual minus expected was -2.28 ms.", cited)[0]
    assert ok(bg_sheet, "The relay was 2.28 ms faster than expected.", cited)[0]  # magnitude only
    assert not ok(bg_sheet, "Actual minus expected was +2.28 ms.", cited)[0]  # explicit wrong sign


def test_spelled_out_numbers_are_rejected(bg_sheet):
    good, chk = ok(bg_sheet, "The breaker cleared in about three cycles.", ["breaker.interrupt_cycles"])
    assert not good and "spelled-out" in chk.reasons[0]


def test_strict_binding(bg_sheet):
    # Cited: fine.
    assert ok(bg_sheet, "The breaker cleared 41.67 ms after the trip.", ["breaker.interrupt_ms"])[1].via == {
        "41.67": "cited"}
    # Not cited, but the sentence talks about the breaker: bound by context.
    good, chk = ok(bg_sheet, "The breaker cleared 41.67 ms after the trip.")
    assert good and chk.via["41.67"] == "context"
    # The breaker's number put on the relay: rejected in strict mode, accepted in lenient mode.
    assert not ok(bg_sheet, "The relay trip output asserted 41.67 ms after the fault began.")[0]
    assert ok(bg_sheet, "The relay trip output asserted 41.67 ms after the fault began.", strict=False)[0]


def test_number_not_in_core_is_rejected_even_when_lenient(bg_sheet):
    good, chk = ok(bg_sheet, "The fault current was 12.4 A.", strict=False)
    assert not good and chk.unsupported == ["12.4"]


def test_clock_times_must_come_from_the_record(bg_sheet):
    assert ok(bg_sheet, "The record started at 02:13:00.596 on the recorder clock.")[0]
    assert not ok(bg_sheet, "The record started at 03:15:00 on the recorder clock.")[0]


def test_incomplete_sentence(bg_sheet):
    assert not ok(bg_sheet, "The relay operated 603.13 ms after", ["relay.operate_ms"], require_terminal=True)[0]


# ---------------------------------------------------------------- categorical claims


@pytest.mark.parametrize("text, good", [
    ("The record shows a B-phase-to-ground fault (BG).", True),
    ("The record shows an A-phase-to-ground fault.", False),
    ("The record shows a phase-to-phase fault.", False),
    ("The record shows an AG fault.", False),
    ("The record shows a single-line-to-ground fault on phase B.", True),
    ("The A-phase-to-ground voltage stayed at 66.2 V.", True),  # a voltage, not a fault claim
])
def test_fault_type(bg_sheet, text, good):
    assert ok(bg_sheet, text, ["fault.type", "fault.va_rms_v"])[0] is good


def test_two_phase_to_ground(cag_slow_sheet):
    assert ok(cag_slow_sheet, "The record shows a C-A-to-ground fault (CAG).")[0]
    assert ok(cag_slow_sheet, "It was a double-line-to-ground fault.")[0]
    assert not ok(cag_slow_sheet, "It was an A-B-to-ground fault.")[0]
    assert not ok(cag_slow_sheet, "It was a phase-to-ground fault.")[0]


def test_direction(bg_sheet, ag_reverse_sheet):
    assert not ok(bg_sheet, "The fault was reverse of the relay.")[0]
    assert ok(bg_sheet, "z2 stayed below the forward threshold Z2F.")[0]
    assert ok(ag_reverse_sheet, "z2 stayed above the reverse threshold, so the fault was reverse.")[0]
    assert not ok(ag_reverse_sheet, "The fault was forward.")[0]


def test_trip_claims(bg_sheet, abc_sheet):
    assert not ok(bg_sheet, "The relay did not trip.")[0]
    assert ok(bg_sheet, "The relay's 50P1 element did not operate.")[0]  # an element, not the relay
    assert not ok(abc_sheet, "The relay tripped quickly.")[0]
    assert ok(abc_sheet, "The relay failed to trip.")[0]


def test_verdict_claims(bg_sheet, cag_slow_sheet, bc_slow_breaker_sheet):
    assert not ok(cag_slow_sheet, "The relay operated as expected.")[0]
    assert ok(cag_slow_sheet, "The relay operated slower than expected.")[0]
    assert not ok(bg_sheet, "The relay operated slower than expected.")[0]  # deviation was negative
    assert not ok(bg_sheet, "This was a misoperation.")[0]
    assert not ok(bc_slow_breaker_sheet, "The breaker opened normally.")[0]
    assert not ok(bg_sheet, "The breaker was slow.")[0]


# ---------------------------------------------------------------- template passes by construction


@pytest.mark.parametrize("name", ["bg_sheet", "ag_reverse_sheet", "abc_sheet", "cag_slow_sheet",
                                  "bc_slow_breaker_sheet"])
def test_template_narrative_always_passes(name, request):
    sheet = request.getfixturevalue(name)
    nar = TemplateNarrator().narrate(sheet)
    assert nar.validation.rejected == [], [c.reasons for c in nar.validation.rejected]
    assert len(nar.sentences) >= 5
    assert nar.validation.numbers_traced_delivered == nar.validation.numbers_delivered


def test_citing_a_same_kind_fact_requires_the_cited_value(bg_sheet):
    text = "The relay operated 603.13 ms after inception, against 605.40 ms expected from the settings."
    # Cites one time fact but quotes a second, uncited time: rejected.
    good, chk = ok(bg_sheet, text, ["relay.operate_ms"])
    assert not good and chk.unsupported == ["605.40"]
    # Cites both: fine. Cites nothing: bound by context ("operated", "expected").
    assert ok(bg_sheet, text, ["relay.operate_ms", "expected.operate_ms"])[0]
    good, chk = ok(bg_sheet, text, [])
    assert good and set(chk.via.values()) == {"context"}


def test_two_cited_facts_with_the_same_value(bg_sheet):
    # A sentence quoting the same value twice must cite two facts that both have it.
    text = "The largest fault current was 7.00 A on phase B, and 7.00 A again on phase B."
    good, chk = ok(bg_sheet, text, ["fault.max_phase_rms_a", "fault.ib_rms_a"])
    assert good and sorted(f for _, f, _ in chk.bindings) == ["fault.ib_rms_a", "fault.max_phase_rms_a"]
    # The same value twice while another cited current (3I0 = 9.11 A) is never quoted.
    good, chk = ok(bg_sheet, text, ["fault.max_phase_rms_a", "fault.3i0_a"])
    assert not good and "never the cited fault.3i0_a" in chk.reasons[-1]


@pytest.mark.parametrize("text, good", [
    # Real 3B outputs that the first validator let through (see eval/narration-x86).
    ("The relay operated in 603.13 ms, faster than the expected 605.40 ms.", True),
    ("The relay operated in 605.40 ms, faster than the expected 603.13 ms.", False),
    ("The breaker interrupted the current in 41.67 ms, which is slower than the expected time.", False),
    ("The fault duration was 644.79 ms, which is slower than the expected time.", False),
    ("z2 was -0.97 ohm, below the threshold Z2F of 1.22 ohm.", True),
    ("z2 was 1.22 ohm, below the threshold Z2F of -0.97 ohm.", False),
    ("The fault current of 7.00 A was higher than the 3I0 of 9.11 A.", False),
])
def test_comparisons_must_agree_with_the_numbers(bg_sheet, text, good):
    assert ok(bg_sheet, text, strict=False)[0] is good


def test_relay_compared_with_than_the_expected(bg_sheet):
    # deviation is -2.28 ms: the relay was faster, so "slower than the expected" is wrong
    assert not ok(bg_sheet, "The relay operated slower than the expected time.")[0]
    assert ok(bg_sheet, "The relay operated faster than the expected time.")[0]


def test_sign_words_must_match_the_value(cag_slow_sheet, bg_sheet):
    # Real 3B output on real Ollama: the deviation is +1399.56 ms.
    bad = ("The relay performance verdict was slower than expected, and the actual minus expected operate time "
           "was 1399.56 ms (negative value indicating faster relay).")
    assert not ok(cag_slow_sheet, bad, ["assessment.relay", "assessment.deviation_ms"])[0]
    good = "The negative-sequence directional impedance z2 was -0.97 ohm."
    assert ok(bg_sheet, good, ["fault.z2_ohm"])[0]


def test_directional_setting_claims():
    # Real 3B output on the SEL-311L event, whose DIR setting is non-directional.
    import json

    from conftest import FIXTURES
    from rea.facts import FactSheet

    data = json.loads((FIXTURES / "facts_demo_abc_failed_to_trip.json").read_text())
    for f in data["facts"]:
        if f["id"] == "settings.DIR":
            f["value"] = f["display"] = "non-directional"
    sheet = FactSheet(data)
    assert not ok(sheet, "The relay settings indicated thresholds, with directional control applied.")[0]
    assert ok(sheet, "The overcurrent elements were non-directional.")[0]


@pytest.mark.parametrize("text, good", [
    # Real outputs from Llama 3.2 3B on Ollama that the validator must accept.
    ("The relay operated 603.13 ms after the fault inception, which is 2.28 ms earlier than the expected "
     "operate time of 605.40 ms.", True),
    ("The fault direction was forward, consistent with the directional supervision of the 50/51 elements.", True),
])
def test_differences_and_device_numbers_are_not_flagged(bg_sheet, text, good):
    assert ok(bg_sheet, text, ["relay.operate_ms", "expected.operate_ms", "assessment.deviation_ms",
                               "fault.direction"])[0] is good


def test_direction_word_about_the_dir_setting(ag_reverse_sheet):
    # Real 3B output: the fault is reverse, the DIR setting is forward. Both statements are true.
    text = "Fault direction was reverse, but the directional supervision setting was forward."
    assert ok(ag_reverse_sheet, text, ["fault.direction", "settings.DIR"])[0]
    assert not ok(ag_reverse_sheet, "The fault was forward of the relay.")[0]


@pytest.mark.parametrize("text, good", [
    # Errors from the real-Ollama narration audit that the validator must reject.
    ("The fault was in the forward direction, which is below the forward threshold of 1.22 ohm and above "
     "the reverse threshold of 1.32 ohm.", False),
    ("The negative-sequence impedance was -0.97 ohm, which is less than the 51G1 pickup of 0.78 A.", False),
    ("The breaker interrupted the current at 41.67 ms, which is 2.50 cycles after the fault inception.", False),
    ("The breaker interrupted the current 41.67 ms after the trip, 2.50 cycles after the expected interrupt time.",
     False),
    ("Fault detected at -3.56 ms before the record trigger.", False),
    # and the correct versions
    ("z2 was -0.97 ohm, below the forward threshold of 1.22 ohm.", True),
    ("The breaker interrupted the current 41.67 ms (2.50 cycles) after the trip.", True),
    ("The fault began 3.56 ms before the record trigger.", True),
])
def test_audit_errors(bg_sheet, text, good):
    assert ok(bg_sheet, text, strict=False)[0] is good


def test_direction_claims_when_the_core_could_not_decide():
    import json

    from conftest import FIXTURES
    from rea.facts import FactSheet

    data = json.loads((FIXTURES / "facts_demo_bg_51g_normal.json").read_text())
    for f in data["facts"]:
        if f["id"] == "fault.direction":
            f.clear()
            f.update({"id": "fault.direction", "kind": "missing", "label": "Fault direction", "priority": 1,
                      "reason": "the voltage channels carry no signal (VTs not connected?)"})
        if f["id"] == "settings.DIR":
            f["value"] = f["display"] = "non-directional"
    sheet = FactSheet(data)
    assert not ok(sheet, "The fault occurred in the forward direction.")[0]
    assert ok(sheet, "The fault direction could not be determined, so forward or reverse is unknown.")[0]
    assert ok(sheet, "z2 is compared with the forward threshold.")[0]
    # Real 3B output on SEL's Case 1 event: the DIR setting and the largest phase merged into one claim.
    assert not ok(sheet, "Fault occurred in non-directional phase A.", ["settings.DIR", "fault.max_phase"])[0]
    assert not ok(sheet, "The fault was non-directional.")[0]
    assert ok(sheet, "The overcurrent elements were non-directional.")[0]
    assert ok(sheet, "The fault direction is unknown, and the overcurrent elements are non-directional.")[0]


# ---------------------------------------------------------------- breaker times


@pytest.mark.parametrize("text, cited, good", [
    # bg_sheet: 52A opened 37.50 ms after the trip; the last pole cleared at 41.67 ms.
    ("Breaker interrupted the fault current 37.50 ms after the trip.",
     ["breaker.interrupt_ms", "breaker.52a_open_after_trip_ms"], False),
    ("The fault ended when the breaker interrupted the current 37.50 ms after the trip, which opened the 52A contact.",
     ["breaker.52a_open_after_trip_ms"], False),
    ("Breaker interrupted the fault current 41.67 ms after the trip.", ["breaker.interrupt_ms"], True),
    ("The 52A contact opened 37.50 ms after the trip, and the breaker interrupted the current 41.67 ms after it.",
     ["breaker.52a_open_after_trip_ms", "breaker.interrupt_ms"], True),
    ("The 52A contact opened 41.67 ms after the trip.", ["breaker.52a_open_after_trip_ms", "breaker.interrupt_ms"],
     False),
    ("The 52A contact opened 37.50 ms after the trip.", ["breaker.52a_open_after_trip_ms"], True),
    # 37.50 ms is also when phase B cleared: fine when the clause names the pole.
    ("Phase B cleared 37.50 ms after the trip.", ["breaker.52a_open_after_trip_ms"], True),
])
def test_52a_opening_is_not_the_interrupting_time(bg_sheet, text, cited, good):
    assert ok(bg_sheet, text, cited)[0] is good


@pytest.mark.parametrize("text, cited, good", [
    # bg_sheet: operated at 603.13 ms (36.19 cycles), expected 605.40 ms, deviation -2.28 ms.
    ("The relay operated 2.28 ms earlier than expected.", ["assessment.deviation_ms"], True),
    ("The relay operated 2.28 ms faster than its settings predict.", ["assessment.deviation_ms"], True),
    ("Relay operated 603.13 ms after fault inception, 36.19 cycles earlier than expected.",
     ["relay.operate_ms", "expected.operate_ms", "relay.operate_cycles"], False),
    ("The relay operated 605.40 ms later than expected.", ["expected.operate_ms"], False),
    # The difference written next to the expected time is checked by the comparison rule instead.
    ("The relay operated 603.13 ms after inception, earlier than the expected 605.40 ms.",
     ["relay.operate_ms", "expected.operate_ms"], True),
])
def test_a_difference_from_the_expected_time_must_be_the_deviation(bg_sheet, text, cited, good):
    assert ok(bg_sheet, text, cited)[0] is good


@pytest.mark.parametrize("text, good", [
    # abc_sheet: the current was interrupted by other breakers while the local 52A stayed closed.
    ("The fault current ended when the local 52A breaker stayed closed, interrupting the current.", False),
    ("The local breaker interrupted the fault current.", False),
    ("The breaker cleared the fault.", False),
    ("The fault was cleared by other breakers while the local breaker stayed closed.", True),
    ("The local breaker did not interrupt the current.", True),
    ("The breaker never cleared the fault; other breakers interrupted the current.", True),
    ("Breaker verdict: not applicable.", True),
])
def test_local_breaker_claims_follow_the_end_cause(abc_sheet, text, good):
    assert ok(abc_sheet, text, ["fault.end_cause"])[0] is good


@pytest.mark.parametrize("text, good", [
    # bg_sheet: the local breaker interrupted the current.
    ("The fault was cleared by other breakers.", False),
    ("The fault self-cleared before any local trip.", False),
    ("The current returned to load before the breaker opened.", False),
    ("The local breaker interrupted the current.", True),
    ("The fault was not cleared by other breakers.", True),
])
def test_other_breakers_claims_follow_the_end_cause(bg_sheet, text, good):
    assert ok(bg_sheet, text, ["fault.end_cause"])[0] is good
