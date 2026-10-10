# Real events: SEL's event-analysis webinar files

Source: SEL on-demand webinar *Introduction to Event Analysis Using Actual Field Cases* (https://selinc.com/events/on-demand-webinar/131921/),
"Example Event Files". The files are SEL's and are not in this repository; `rea-real --fetch` downloads them.

Field events have no ground-truth labels. Each row compares what rea-core found with what the relay itself
recorded in the same file (its event type, targets and PHROT setting).

| Event | Relay | Relay says | rea-core fault type | Rotation check | Inception / end | Trip | rea-core verdict |
|---|---|---|---|---|---|---|---|
| Exercise 1 - Motor / Exercise 1 CEV_R_1.CEV | SEL-710 | ER Trigger | BCG | matches the PHROT setting | starts -15.63 ms from trigger; fault current still present at the end of the record | no trip | undetermined |
| Exercise 1 - Motor / Exercise 1 CEV_R_2.CEV | SEL-710 | ER Trigger | ABC | matches the PHROT setting | starts -134.38 ms from trigger, lasts 23.37 cycles | 14.06 cycles | undetermined |
| Exercise 1 - Motor / Exercise 1 CEV_R_3.CEV | SEL-710 | ER Trigger | ABC | matches the PHROT setting | starts -48.96 ms from trigger, lasts 18.87 cycles | 8.94 cycles | undetermined |
| Exercise 2 - Distance / Exercise 2.CEV | SEL-311L-1 | CA T; targets TIME ZONE2 | CA | the record shows ACB rotation but the relay is set PHROT := ABC | fault present at record start; current returned to load before any local trip (self-cleared or cleared by other protection) | 20.94 cycles after the fault ended | tripped after the fault had ended |
| Practise Event / Case 1.cev | SEL-351R-2 | CA; targets 11000000 10000000 | CA | matches the PHROT setting | starts -14.58 ms from trigger, lasts 1.31 cycles | no trip | undetermined |
| Practise Event / Case 1_from_cev.cfg | SEL-351R-2 | CA; targets 11000000 10000000 | CA | matches the PHROT setting | starts -14.58 ms from trigger, lasts 1.31 cycles | no trip | undetermined |

## Notes per event

### Exercise 1 - Motor / Exercise 1 CEV_R_1.CEV

- Source: SEL compressed event (.cev), converted by rea; relay SEL-710; relay's event type `ER Trigger`, PHROT `ABC`.
- Relay's own event summary currents (A primary): IA(A) 1382.6, IB(A) 1461.3, IC(A) 1385.9, IG(A) 358.52
- Fault type in words: B-C-to-ground
- Fault direction relative to the relay: undetermined
- Largest phase fault current (primary RMS): 891 A
- Disturbance began after the breaker closed: 71.87 ms
- Breaker performance verdict: not applicable
- core warning: INOM not set: current thresholds are scaled to the record's typical current (11.5 A secondary); set INOM to the relay's rating for fixed thresholds
- core warning: the disturbance began just after the breaker closed: it may be an energization (motor start, inrush) rather than a fault; a switch onto a fault would look the same
- core warning: direction: z2 between Z2F and Z2R

### Exercise 1 - Motor / Exercise 1 CEV_R_2.CEV

- Source: SEL compressed event (.cev), converted by rea; relay SEL-710; relay's event type `ER Trigger`, PHROT `ABC`.
- Relay's own event summary currents (A primary): IA(A) 1524.1, IB(A) 1362.9, IC(A) 1374.2, IG(A) 6.14
- Fault type in words: three-phase
- Fault direction relative to the relay: forward
- Largest phase fault current (primary RMS): 938 A
- Breaker performance verdict: slow
- core warning: INOM not set: current thresholds are scaled to the record's typical current (0.097 A secondary); set INOM to the relay's rating for fixed thresholds

### Exercise 1 - Motor / Exercise 1 CEV_R_3.CEV

- Source: SEL compressed event (.cev), converted by rea; relay SEL-710; relay's event type `ER Trigger`, PHROT `ABC`.
- Relay's own event summary currents (A primary): IA(A) 1143.4, IB(A) 1514.0, IC(A) 1081.6, IG(A) 6.80
- Fault type in words: three-phase
- Fault direction relative to the relay: forward
- Largest phase fault current (primary RMS): 946 A
- Disturbance began after the breaker closed: 71.87 ms
- Breaker performance verdict: slow
- core warning: INOM not set: current thresholds are scaled to the record's typical current (0.132 A secondary); set INOM to the relay's rating for fixed thresholds
- core warning: the disturbance began just after the breaker closed: it may be an energization (motor start, inrush) rather than a fault; a switch onto a fault would look the same

### Exercise 2 - Distance / Exercise 2.CEV

- Source: SEL compressed event (.cev), converted by rea; relay SEL-311L-1; relay's event type `CA T`, PHROT `ABC`.
- Relay's own event summary currents (A primary): IA 204, IB 204, IC 204, IG 5, 3I2 614
- Rotation check from the relay's summary: the relay's own summary lists balanced phase currents (IA, IB, IC = 204, 204, 204 A) with 3I2 = 614 A, 3.01 times the phase current. Balanced currents have no negative sequence; three times the phase current is what the relay computes when its rotation setting is the reverse of the actual rotation.
- Fault type in words: C-A phase-to-phase
- Fault direction relative to the relay: forward
- Largest phase fault current (primary RMS): 169 A
- Trip asserted after the fault had ended: 348.96 ms
- Breaker performance verdict: opened on the late trip, with no fault current left to interrupt
- core warning: INOM not set: current thresholds are scaled to the record's typical current (0.598 A secondary); set INOM to the relay's rating for fixed thresholds
- core warning: phase rotation: the record shows ACB but PHROT := ABC; a relay using that setting computes positive and negative sequence swapped
- core warning: record starts during a disturbance; treating the fault as present before the record
- core warning: fault inception is not in the record; operate time cannot be measured
- core warning: record starts inside the fault: incremental quantities use the post-fault load as the reference

### Practise Event / Case 1.cev

- Source: SEL compressed event (.cev), converted by rea; relay SEL-351R-2; relay's event type `CA`, PHROT `ACB`.
- Relay's own event summary currents (A primary): IA 489, IB 159, IC 437, IG 2, 3I2 590
- Fault type in words: C-A phase-to-phase
- Fault direction: unknown (the voltage channels carry no signal (VTs not connected?))
- Largest phase fault current (primary RMS): 331 A
- Breaker performance verdict: not applicable
- core warning: the voltage channels carry no signal (VTs not connected?): direction cannot be determined
- core warning: INOM not set: current thresholds are scaled to the record's typical current (0.0859 A secondary); set INOM to the relay's rating for fixed thresholds
- core warning: fault lasted 1.3 cycles, shorter than the phasor window: magnitudes come from a partial window and are underestimated

### Practise Event / Case 1_from_cev.cfg

- Source: COMTRADE 1991 as converted by SEL (values as recorded); relay SEL-351R-2; relay's event type `CA`, PHROT `ACB`.
- Fault type in words: C-A phase-to-phase
- Fault direction: unknown (the voltage channels carry no signal (VTs not connected?))
- Breaker performance verdict: not applicable
- core warning: the voltage channels carry no signal (VTs not connected?): direction cannot be determined
- core warning: INOM not set: current thresholds are scaled to the record's typical current (85.9 A as recorded); set INOM to the relay's rating for fixed thresholds
- core warning: fault lasted 1.3 cycles, shorter than the phasor window: magnitudes come from a partial window and are underestimated

## Reading the results

- The motor-relay records (SEL-710) are motor starts and trips, not short circuits. rea-core is a
  feeder/line fault analyzer with no motor model, so it reports the starting current as a balanced or
  ground disturbance; it does flag when the disturbance follows a breaker close.
- Magnitudes from faults shorter than the phasor window are underestimated and say so.
- The relay's event summary gives a second rotation check that does not depend on rea-core: with
  balanced phase currents its 3I2 should be near zero, and near three times the phase current when
  the relay's rotation setting is the reverse of the actual rotation.
