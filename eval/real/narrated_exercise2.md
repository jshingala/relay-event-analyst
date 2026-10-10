# Event analysis: `Exercise 2.CEV`

Narrator: `ollama:llama3.2:3b`

## What happened

- The fault was detected as a C-A phase-to-phase fault, with no ground involvement, at the start of the record.
- The fault occurred in the forward direction relative to the relay.
- The calculated negative-sequence impedance z2 was below the forward threshold Z2F, indicating a forward fault condition.
- The largest phase fault current was in phase C, with a value of 169 A in the primary system and 0.71 A in the secondary system.
- The residual current 3I0 was 0.02 A.
- The relay tripped after the fault had ended, with a trip time of 348.96 ms and 20.94 cycles.
- The 52A contact opened after the trip, with no fault current left to interrupt.
- The fault current ended due to the current returning to the load before any local trip.
- Phase rotation check: the record shows ACB rotation but the relay is set PHROT := ABC.

## Key facts (physics core)

| Fact | Value |
|---|---|
| `fault.type` | CA |
| `fault.type_description` | C-A phase-to-phase |
| `fault.direction` | forward |
| `fault.z2_ohm` | -26.31 ohm |
| `fault.max_phase` | C |
| `fault.max_phase_rms_a` | 0.71 A |
| `fault.max_phase_rms_primary_a` | 169 A |
| `fault.3i0_a` | 0.02 A |
| `relay.operate_ms` | unknown: fault inception is not in the record |
| `expected.element` | unknown: fault inception is not in the record |
| `assessment.relay` | tripped after the fault had ended |
| `breaker.interrupt_ms` | unknown: current was not interrupted by the local breaker within the record |
| `assessment.breaker` | opened on the late trip, with no fault current left to interrupt |
| `fault.duration_ms` | unknown: fault inception is not in the record |
| `fault.end_cause` | current returned to load before any local trip (self-cleared or cleared by other protection) |

## Could not be determined

- Fault inception: the fault was already present when the record started
- Relay operate time: fault inception is not in the record
- Breaker interrupting time: current was not interrupted by the local breaker within the record
- Fault duration: fault inception is not in the record
- Element expected to trip first: fault inception is not in the record

## Validator

8/8 model sentences passed | 5/5 numbers shown are traced to core facts | model skipped rotation check: added the template sentence

## Background sources

- [P1] 6072_NegSeq_Web.pdf, page 2
- [P2] 6009_DirectionalElement_JBR-AG_20060803_Web.pdf, page 21
- [P3] 6110_Understanding_Web.pdf, page 50

## Core warnings

- INOM not set: current thresholds are scaled to the record's typical current (0.598 A secondary); set INOM to the relay's rating for fixed thresholds
- phase rotation: the record shows ACB but PHROT := ABC; a relay using that setting computes positive and negative sequence swapped
- record starts during a disturbance; treating the fault as present before the record
- fault inception is not in the record; operate time cannot be measured
- record starts inside the fault: incremental quantities use the post-fault load as the reference

## Timing

core 13.9 ms (process 17 ms) | retrieval 32 ms | LLM 125.8 s (load 4.3 s, prompt 1233 tok in 24.3 s, 377 tok at 3.9 tok/s) | total 125.9 s
