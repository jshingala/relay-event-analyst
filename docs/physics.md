# Physics core: what rea-core computes and how

`rea-core analyze event.cfg --settings relay.txt` turns one record into a fact sheet. This page
follows the order of `core/src/analyzer.cpp`. Every fact carries the method string shown in
the report's JSON, so each number can be traced back to one of the steps below.

## 1. Reading the record

* **Format.** COMTRADE (IEEE C37.111, 1991/1999/2013 configuration files) in ASCII, BINARY,
  BINARY32 and FLOAT32. Each sample is converted as `a * raw + b`, and missing-data markers become
  NaN. Sample times come from the rate table, or from the time stamps times `timemult` when the file
  has no fixed rate.
* **Primary or secondary.** The 1999 and 2013 formats give each analog channel CT/VT ratios and a
  P/S flag; values marked primary are divided down to secondary. The 1991 format says neither, so its
  values are reported "as recorded".
* **SEL `.cev` files** go through `python/rea/cev.py` first. Each line's checksum is verified, Relay
  Word bits are unpacked from hex, and when digital elements are recorded at a lower rate than analog
  channels (SAM/CYC_D < SAM/CYC_A) each digital sample covers the rows since the previous one. That
  is how SEL's own converter does it: on SEL's practice event the result matches SEL's COMTRADE
  conversion in all 113,280 digital samples, with analog values within 0.004 A. Values stay primary,
  and the CT and VT ratios from the record go into the channel lines with PS = P.
* **Channels.** IA/IB/IC and VA/VB/VC are found by name, phase and unit, or named in the settings
  (`IA_CH := ...`). The trip output is TRIP, TRP, TRIP1 or OUT101, or `TRIPCH`. The breaker
  a-contact is 52A, or `BKRCH`. A voltage channel that never leaves zero (no VTs wired, common on
  recloser controls) counts as absent.

## 2. Phasors

Two fixed-reference RMS filters. For a steady sinusoid each returns the same complex number at every
sample, so phasors from different channels and different instants can be compared and subtracted.

* **Cosine filter** (default). A full-cycle correlation with a cosine. The quadrature component is
  the same filter's output a quarter cycle earlier, so the window is N + N/4 samples. It rejects
  decaying DC offset far better than the DFT. On the synthetic set, magnitude error at the 95th
  percentile is 1.3% with the cosine filter and 3.5% with the DFT.
* **Full-cycle DFT**, with an N-sample window, for comparison (`--filter dft`).

Symmetrical components use `a = 1∠120°` with the phase rotation measured from the record (step 4).

## 3. Fault inception

* The cycle-to-cycle difference `|x[k] − x[k−N]|` is zero for any steady periodic signal and jumps
  when the network changes. The noise level σ is 1.4826 × the median of that difference over the
  quieter of the record's first and last cycles. When the median is exactly zero, as with whole-ampere
  data from relay event reports, σ is 1.2533 × the mean instead, so the threshold cannot collapse to
  zero.
* The onset is the first run of at least 3 samples above `max(5% of INOM·√2, 8σ)`. It is then walked
  back to where the difference leaves `4σ`, skipping single quiet samples, since the difference of two
  sinusoids can cross zero right after the change.
* **Inception or clearing.** Healthy load is balanced, so unbalance (|I0| + |I2|) that appears marks an
  inception and unbalance that disappears marks a clearing. Balanced disturbances fall back on a rise
  in I1 or a drop in V1. A record that starts inside the fault reports inception as missing, with the
  reason.
* **Breaker closing.** If the disturbance starts within 10 cycles after 52A rises, the report says it
  may be an energization (motor start, inrush) rather than a fault. A switch onto a fault looks the
  same, so the record is still analyzed.

## 4. Phase rotation

The most balanced phasor set in the record (voltages if present, else currents) is dominated either
by the ABC positive sequence or by the ABC negative sequence. More than 80% dominance decides the
rotation. The analysis uses the measured rotation, and a `PHROT` setting that disagrees is reported
as a headline fact: a relay set for the wrong rotation computes positive and negative sequence
swapped. In SEL's SEL-311L webinar event this is the finding that explains the trip.

## 5. Fault type

Classification uses **incremental** currents: fault phasor minus prefault phasor, which removes load.
When the record starts inside the fault and the fault ends within it, the post-fault load is the
reference.

* **Ground.** 3I0 at least 10% of the largest incremental phase current, and at least 5% of INOM.
* **Single phase to ground.** The I2/I0 angle sector names the special phase (A near 0°, B near −120°,
  C near +120° for ABC rotation). The loop that excludes that phase must carry under 35% of the
  largest loop, and the other two loops must be within 25% of it.
* **Two phases to ground.** A dominant phase-to-phase loop names the pair. The phase-to-phase
  differences cancel zero sequence, so ground resistance and zero-sequence infeed cannot shift them.
  When all loops are similar, the I2/I0 sector decides.
* **Phase to phase / three phase.** The largest loop names the pair. If the smallest loop is above 75%
  of the largest, the fault is three phase.
* **ACB rotation.** Swap the B and C labels, classify, and swap the answer back.
* **Short faults.** A fault shorter than the phasor window has no full-window phasor. Its end is found
  by comparing each sample with the same point of the last prefault cycle (superimposed current).
  Classification then uses the partial-window phasor that deviates most from prefault, and the
  magnitudes are flagged as underestimated.

## 6. Direction

* **Negative-sequence impedance (32Q style):**
  `z2 = Re[V2 · conj(I2 · 1∠Z1ANG)] / |I2|²`. Forward if `z2 < Z2F`, reverse if `z2 > Z2R`,
  undetermined in between. Used when |I2| ≥ 5% of INOM and |I2|/|I1| ≥ 0.1. A forward fault measures
  minus the source impedance behind the relay, so z2 is negative.
* **Balanced faults** use the sign of the incremental positive-sequence impedance
  `Re[ΔV1 · conj(ΔI1 · 1∠Z1ANG)] / |ΔI1|²`.
* Without settings: Z2F = 0.5 × Z1MAG and Z2R = Z2F + 0.1 Ω. The fact's method says so.

## 7. Timing

* **Relay operate time** = first rising edge of the trip output − inception.
* **Breaker.** A pole counts as interrupted at the first full cycle with |i| below
  `max(2% of INOM·√2, 6σ)` after it carried current. Only poles that carried more than 0.1 × INOM are
  timed. Interrupting time = last pole − trip. The pole order and the 52A edge are also reported.
* **Fault end without local interruption.** A clean step after a quiet half cycle, voltages first,
  accepted only if the currents then settle near prefault, or, without prefault, if unbalance
  collapses. Short faults use the superimposed comparison from step 5.
* **Trip after the fault ended.** A trip edge more than half a cycle after the fault ended found no
  fault to clear, whatever element issued it. It is reported with the delay and gets its own verdict.

## 8. Expected operate time and verdicts

The measured phasor magnitudes are replayed, sample by sample, through the configured elements.

* **50P1:** the largest phase magnitude ≥ pickup, plus the definite delay `50P1D`.
* **51P1 / 51G1:** the IEEE C37.112 integral form. The element operates when
  `∫ dt / tp(M(t)) = 1`, with `tp(M) = TD · (A + B / (M^p − 1))` and `M = I / pickup`. Below pickup,
  electromechanical reset (`51xRS := Y`) unwinds at `tr = TD · R / (1 − M²)` (C4: `1 − M`). If the
  current stops before the integral reaches 1, the prediction is extrapolated at the steady fault
  current and flagged.
* **Directional supervision** (`DIR := F/R`) blocks the elements for a fault in the other direction.

Curve coefficients, from SEL's *PowerSystemProtection IEC 61131 Library for ACSELERATOR RTAC
Projects* (Tables 1 and 2):

| Curve | Name | A | B | p | R (reset) |
|---|---|---|---|---|---|
| U1 | U.S. Moderately Inverse | 0.0226 | 0.0104 | 0.02 | 1.08 |
| U2 | U.S. Inverse | 0.180 | 5.95 | 2 | 5.95 |
| U3 | U.S. Very Inverse | 0.0963 | 3.88 | 2 | 3.88 |
| U4 | U.S. Extremely Inverse | 0.0352 | 5.67 | 2 | 5.67 |
| U5 | U.S. Short-Time Inverse | 0.00262 | 0.00342 | 0.02 | 0.323 |
| C1 | IEC Standard Inverse | 0 | 0.14 | 0.02 | 13.5 |
| C2 | IEC Very Inverse | 0 | 13.5 | 1 | 47.3 |
| C3 | IEC Extremely Inverse | 0 | 80.0 | 2 | 80.0 |
| C4 | IEC Long-Time Inverse | 0 | 120.0 | 1 | 120.0 |
| C5 | IEC Short-Time Inverse | 0 | 0.05 | 0.04 | 4.85 |

**Relay verdict.** The deviation is the actual operate time minus the expected one. The tolerance is
`TOLCYC` cycles plus `TOLPCT` percent of the expected time (defaults 1.5 cycles and 5%). The verdicts
are: operated as expected, slower than expected, faster than expected, failed to trip, correctly did
not trip, tripped for a fault outside its directional zone, tripped although no trip was expected,
tripped after the fault had ended, and undetermined.

**Breaker verdict.** Interrupting time against `BKRMAX` (default 5 cycles). It reports failure when
other breakers cleared the fault, notes when 52A never opened, and notes when the breaker opened on a
late trip with no fault current left.

## 9. Ground truth for the synthetic set

`python/rea/synth/` solves the sequence networks of a two-source line for each fault type. The fault
is a star of resistances to a common node, with an admittance matrix of
`diag(g) − g gᵀ / (Σg + g_ground)`. The simulator writes waveforms with continuous currents (decaying
DC offset at inception), and lets each breaker pole clear at its next current zero. It then computes
the expected relay behaviour in closed form. Labels record the true inception, trip, per-pole
clearing, fault end, steady magnitudes and verdicts.

## 10. Thresholds without settings

All current thresholds scale with INOM. If `INOM` is not set, they scale to the record's typical
current instead (the median over cycles of the largest phase RMS, normally the load), and the report
says so. Set `INOM` to the relay's rating to get fixed thresholds.
