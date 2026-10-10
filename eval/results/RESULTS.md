# Evaluation results

Generated 2026-10-09 21:28 UTC on `x86_64 Linux, 2 CPUs` from `data/synth` (1000 synthetic events, generator seed 7).

These are synthetic events from the included simulator, not field records.

## Physics core

| Metric | Result |
|---|---|
| Fault type (10 classes) | 100.0% |
| Direction, when decided | 100.0% correct; decided on 99.8% of events |
| Relay verdict | 100.0% |
| Breaker verdict | 100.0% |
| Inception time error | median 0.41 ms, p95 1.51 ms, max 3.69 ms (n=972) |
| Inception time error in samples | median 0.51 samples, p95 0.95 samples, max 1.95 samples (n=972) |
| Trip time error | median 0.41 ms, p95 1.56 ms, max 2.08 ms (n=804) |
| Fault end error | median 0.32 ms, p95 1.45 ms, max 3.80 ms (n=972) |
| Expected operate time vs closed form | median 0.65 cycles, p95 0.87 cycles, max 1.05 cycles (n=804) |
| Steady fault current error | median 0.03 %, p95 1.25 %, max 4.05 % (n=1000) |
| Core time per event (incl. process start) | median 2.7 ms, p95 4.9 ms, max 10.4 ms (n=1000) |
| Events that errored | 0 |

### Verdicts by injected anomaly

| Anomaly | Events | Relay verdict correct | Breaker verdict correct |
|---|---|---|---|
| breaker_failure | 87 | 87/87 | 87/87 |
| failed_to_trip | 61 | 61/61 | 61/61 |
| none | 581 | 581/581 | 581/581 |
| reverse_trip | 72 | 72/72 | 72/72 |
| slow_breaker | 69 | 69/69 | 69/69 |
| slow_relay | 59 | 59/59 | 59/59 |
| wrong_curve | 71 | 71/71 | 71/71 |

### Fault-type confusion (rows = truth)

| truth \ predicted | AG | BG | CG | AB | BC | CA | ABG | BCG | CAG | ABC | other |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **AG** | 98 |  |  |  |  |  |  |  |  |  |  |
| **BG** |  | 95 |  |  |  |  |  |  |  |  |  |
| **CG** |  |  | 89 |  |  |  |  |  |  |  |  |
| **AB** |  |  |  | 101 |  |  |  |  |  |  |  |
| **BC** |  |  |  |  | 114 |  |  |  |  |  |  |
| **CA** |  |  |  |  |  | 84 |  |  |  |  |  |
| **ABG** |  |  |  |  |  |  | 109 |  |  |  |  |
| **BCG** |  |  |  |  |  |  |  | 115 |  |  |  |
| **CAG** |  |  |  |  |  |  |  |  | 90 |  |  |
| **ABC** |  |  |  |  |  |  |  |  |  | 105 |  |

## Validator red team (no model needed)

The template narrative of 1000 events was copied and one known error was injected per copy.
Correct template sentences wrongly rejected: 0 of 7851.

| Injected error | Copies | Caught |
|---|---|---|
| flipped comparison | 895 | 895 (100.0%) |
| flipped direction | 1166 | 1166 (100.0%) |
| flipped verdict | 708 | 708 (100.0%) |
| invented number | 7851 | 7851 (100.0%) |
| number on the wrong subject | 1392 | 1392 (100.0%) |
| spelled-out number | 6952 | 6952 (100.0%) |
| value of a different fact | 6848 | 6804 (99.4%) |
| value of a different fact, no citations | 6848 | 6435 (94.0%) |
| wrong digit | 6952 | 6952 (100.0%) |
| wrong fault type | 1000 | 1000 (100.0%) |
| wrong unit | 6952 | 6952 (100.0%) |
| **all** | 47564 | 47107 (99.0%) |

Missed (first few):

- value of a different fact, no citations: "With the settings on file, 50P1 should have operated after 12.50 ms."
- value of a different fact, no citations: "The fault lasted 29.17 ms (2.50 cycles); local breaker interrupted the current."
- value of a different fact, no citations: "The fault lasted 29.17 ms (2.50 cycles); local breaker interrupted the current."
- value of a different fact, no citations: "The record shows a C-phase-to-ground fault (CG) that began 1204.2 ms before the record trigger."
- value of a different fact, no citations: "The fault lasted 37.50 ms (45.50 cycles); local breaker interrupted the current."
- value of a different fact, no citations: "The record shows a C-A phase-to-phase fault (CA) that began 308.3 ms before the record trigger."
- value of a different fact, no citations: "The fault lasted 33.33 ms (2.87 cycles); local breaker interrupted the current."
- value of a different fact, no citations: "With the settings on file, 51P1 should have operated after 1148.96 ms (U4 curve, time dial 15.00, 11.83 times pickup)."

## Events with a wrong answer

- `ev0221` (breaker_failure/inception_before_record): direction forward->undetermined (abstained)
- `ev0843` (slow_breaker/inception_before_record): direction forward->undetermined (abstained)
