# Event analysis: `Case 1.cev`

Narrator: `ollama:llama3.2:3b`

## What happened

- Fault detected at -14.58 ms relative to the record trigger.
- Fault type is C-A phase-to-phase.
- Residual current 3I0 is 0.00 A (secondary RMS).
- Fault occurred in non-directional phase A.
- Fault current ended due to current returned to load before any local trip.
- Relay performance verdict is undetermined and breaker performance verdict is not applicable.
- Fault duration is 21.87 ms and 1.31 cycles.
- The relay did not trip during this record.

## Key facts (physics core)

| Fact | Value |
|---|---|
| `fault.type` | CA |
| `fault.type_description` | C-A phase-to-phase |
| `fault.direction` | unknown: the voltage channels carry no signal (VTs not connected?) |
| `fault.max_phase` | A |
| `fault.max_phase_rms_a` | 0.33 A |
| `fault.max_phase_rms_primary_a` | 331 A |
| `fault.3i0_a` | 0.00 A |
| `expected.element` | unknown: no 50P1/51P1/51G1 settings were provided |
| `assessment.relay` | undetermined |
| `breaker.interrupt_ms` | unknown: no local trip and no local interruption |
| `assessment.breaker` | not applicable |
| `fault.duration_ms` | 21.87 ms |
| `fault.end_cause` | current returned to load before any local trip (self-cleared or cleared by other protection) |

## Could not be determined

- Fault direction: the voltage channels carry no signal (VTs not connected?)
- Breaker interrupting time: no local trip and no local interruption
- Element expected to trip first: no 50P1/51P1/51G1 settings were provided

## Validator

7/8 model sentences passed | 4/4 numbers shown are traced to core facts | model skipped relay operation: added the template sentence
- rejected: "Maximum phase fault current is 331 A (primary RMS), 0.33 A (secondary RMS), and 1.3 A (C-phase).": numbers not traceable to the physics core: 1.3

## Background sources

- [P1] 6110_Understanding_Web.pdf, page 32
- [P2] 6002_AnalysisEventReports_WPRC-1989_Web.pdf, page 6
- [P3] 6002_AnalysisEventReports_WPRC-1989_Web.pdf, page 13

## Core warnings

- the voltage channels carry no signal (VTs not connected?): direction cannot be determined
- INOM not set: current thresholds are scaled to the record's typical current (0.0859 A secondary); set INOM to the relay's rating for fixed thresholds
- fault lasted 1.3 cycles, shorter than the phasor window: magnitudes come from a partial window and are underestimated

## Timing

core 5.4 ms (process 8 ms) | retrieval 31 ms | LLM 91.5 s (load 0.0 s, prompt 1083 tok in 16.7 s, 299 tok at 4.0 tok/s) | total 91.5 s
