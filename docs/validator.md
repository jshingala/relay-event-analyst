# The validator

Rule: **a sentence reaches the user only if every number in it is a number the physics core
produced.** The checks below implement that rule and a few categorical cross-checks.
Code: `python/rea/validator.py`. Tests: `tests/test_validator.py`. Red team:
`python/rea/evaluation/narration.py`.

## Numbers

1. **Extraction.** Digits with optional sign and thousands separators. Not claims: identifiers such
   as 51P1, 3I0, 52A, U4, Z2F, C37.112, IEEE 1547 and SEL-421; ordinals (2nd); names such as
   "3-phase". Clock times and dates are checked separately: they must appear in one of the record's
   text facts.
2. **Value at the written precision.** "603 ms" is a valid rounding of 603.13 ms. "604 ms" and
   "603.14 ms" are not. The tolerance is half a unit in the last written digit.
3. **Unit.** A unit written after the number must have the same dimension as the fact's unit (time,
   cycles, current, voltage, impedance, frequency, angle, ratio, percent). ms↔s, A↔kA and V↔kV are
   rescaled. "36.19 ms" cannot borrow 36.19 cycles; "0.603 s" can be 603.13 ms.
4. **Sign.** "-2.28 ms" must match the sign. "2.28 ms faster" may quote the magnitude of -2.28 ms.
5. **Binding (strict mode, the default).**
   * If the sentence cites a fact with this value, it is bound to that fact. When two cited facts
     share a value, each number takes a cited fact not used yet.
   * If the sentence cites facts of the same dimension but none has this value, it is rejected: a
     related value cannot stand in for a cited one. Examples are the expected time quoted as the
     deviation, or another pole's clearing time.
   * If nothing of that dimension is cited, the number may bind by context. The sentence must mention
     what the fact measures (a breaker time next to "breaker", a phase-B current next to "phase B").
   * A cited value quoted twice while another cited value of the same kind is never quoted is
     rejected.
   * A negative time written right after "detected / began / started / occurred at" ("Fault
     detected at -3.07 ms") binds to `fault.inception_from_trigger_ms` when it equals it: a start
     time before zero only exists on the trigger's time axis. An unsigned value stays ambiguous
     (from the trigger or from the first sample?) and needs a citation or the word "trigger".
6. **Spelled-out quantities** ("three cycles") are rejected because they cannot be checked.

`--lenient` keeps steps 1-4 and 6 and drops the binding.

## Categorical claims

* **Fault type.** A different code (AG ... ABC), a different class ("phase-to-phase" for a ground
  fault) or a different faulted phase ("A-phase-to-ground" for BG). A phrase like
  "A-phase-to-ground voltage" is a quantity, not a fault claim. Rotation names (PHROT := ABC, "ACB
  rotation") are not fault types.
* **Direction.** The opposite direction word. "Forward threshold" and similar threshold or element
  names are not claims.
* **Relay.** "Did not trip" when the trip output asserted, or "tripped" when it did not. "Operated as
  expected" when the verdict says otherwise (but "the relay tripped, and the breaker performed
  normally" is about the breaker). "Misoperation" when the verdict is as expected.
  "Slower/faster than expected" with the wrong sign of the deviation.
* **Breaker.** "Slow" when the breaker verdict is normal, or "normal" when it is slow.
* **Who ended the fault current.** When `fault.end_cause` says other breakers interrupted it, or
  that it returned to load before any local trip, a sentence in which the local breaker interrupts
  or clears the current is rejected ("the local 52A breaker stayed closed, interrupting the
  current"). When the local breaker interrupted it, crediting other breakers, self-clearing or a
  return to load is rejected. Negated statements ("did not interrupt") are left alone.
* **Comparisons.** "13.02 ms, faster than the expected 9.37 ms" is rejected because the numbers say
  otherwise. The same goes for slower, longer, higher, lower, below, above and exceeds, whenever both
  numbers sit in the sentence. Comparing quantities of different kinds ("-1.09 ohms, which is less
  than the pickup of 3.35 amperes") is rejected. Only the relay has an expected time, so a breaker
  time or a fault duration "slower than expected" is rejected too.
* **The difference from the expected time.** A number written directly before "later / earlier /
  slower / faster than expected" (or "than its settings", "than the curve") is the deviation, so it
  must equal `assessment.deviation_ms` in magnitude. "12.50 ms after inception, 0.75 cycles later
  than expected" is rejected when 0.75 cycles is the operate time itself. "X ms, earlier than the
  expected Y ms" is left to the comparison rule above.
* **z2 against the thresholds.** "Below the forward threshold" or "above the reverse threshold" must
  agree with the measured z2 and the Z2F/Z2R settings.
* **What a time is measured from.** Breaker times are measured from the trip, so "the breaker
  interrupted the current 37.50 ms after the fault inception" is rejected, and so is any "expected"
  breaker time (the core does not compute one).
* **Which breaker time.** The 52A contact opening and the current interruption are different
  times, both measured from the trip. A time in a clause about interrupting or clearing that is the
  52A opening time is rejected ("the breaker interrupted the current 25.00 ms after the trip" when
  25.00 ms is `breaker.52a_open_after_trip_ms` and the interruption took 30.21 ms), unless the same
  value is also the interrupting time or the clearing time of a pole the clause names. The reverse
  (the interrupting time given as the 52A opening) is rejected too. Clauses are split at commas,
  semicolons, brackets and "and / but / while / which / when".
* **Signs in words.** Calling a positive value negative, or the reverse, is rejected.
  "Negative-sequence" is not a sign word. "-3.74 ms before the trigger" is a double negative and is
  rejected.
* **Incomplete sentences** (model output cut off by the token limit).

Most of these checks were added after reading every sentence Llama 3.2 3B wrote in the narration
eval and marking the wrong ones by hand. Comparisons in the wrong direction and times quoted
against the wrong reference were the most common errors that got past the first version. The
audit is in `eval/narration-x86/RESULTS.md`.

## What it cannot do

It checks numbers and the claims above. It cannot check every relation a sentence states. "The
breaker opened 603.13 ms after the fault" cites a real number for a wrong relation if the model
cited `relay.operate_ms` for it. That is why the report always prints the key-facts table next to
the narrative, and why the model must cite: in the red team, a number swapped for a related fact of
the same kind passes 0.6% of the time with citations and 6.0% without.

## Red team (`rea-eval`, no model needed)

For each event the template narrative (correct by construction) is copied, and one error is
injected per copy: wrong digit, wrong unit, invented number, spelled-out number, wrong fault type,
flipped direction, flipped verdict, flipped comparison ("below" <-> "above" the threshold), a
number moved to the wrong subject, the value of a different fact (with the original citations),
and the same without citations. On 1,000 events: 47,564 injected errors, 99.0% caught, and 0 of
7,851 correct sentences rejected. Every kind is caught 100% of the time except the value of a
different fact (99.4% with citations, 94.0% without). Details are in `eval/results/RESULTS.md`.
