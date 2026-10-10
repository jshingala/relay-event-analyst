# Evaluation results

Generated 2026-10-09 21:17 UTC on `x86_64 Linux` from `data/synth` (20 synthetic events, generator seed 7).

These are synthetic events from the included simulator, not field records.

> **Read this first.** The narrator table below is the run as recorded (2026-10-09 21:17 UTC, the
> validator at that time). Two things changed after it: one event (`ev0015`) never reached the
> model, because the Ollama runner was killed for lack of memory (fixed with `LLAMA_ARG_CACHE_RAM=0`,
> see docs/architecture.md), and its 8 template sentences were counted as model sentences, a counting
> bug fixed since. And the validator gained the checks listed in docs/validator.md. The section
> "Re-validation and hand audit" at the end has the numbers for the final validator on the same
> stored model output. A fresh run is still to do.

## Physics core

| Metric | Result |
|---|---|
| Fault type (10 classes) | 100.0% |
| Direction, when decided | 100.0% correct; decided on 100.0% of events |
| Relay verdict | 100.0% |
| Breaker verdict | 100.0% |
| Inception time error | median 0.41 ms, p95 1.24 ms, max 1.24 ms (n=20) |
| Inception time error in samples | median 0.50 samples, p95 0.99 samples, max 1.19 samples (n=20) |
| Trip time error | median 0.41 ms, p95 1.69 ms, max 1.69 ms (n=18) |
| Fault end error | median 0.27 ms, p95 1.35 ms, max 1.38 ms (n=20) |
| Expected operate time vs closed form | median 0.61 cycles, p95 0.80 cycles, max 0.80 cycles (n=18) |
| Steady fault current error | median 0.09 %, p95 1.29 %, max 1.40 % (n=20) |
| Core time per event (incl. process start) | median 2.5 ms, p95 5.1 ms, max 5.4 ms (n=20) |
| Events that errored | 0 |

### Verdicts by injected anomaly

| Anomaly | Events | Relay verdict correct | Breaker verdict correct |
|---|---|---|---|
| breaker_failure | 1 | 1/1 | 1/1 |
| failed_to_trip | 1 | 1/1 | 1/1 |
| none | 13 | 13/13 | 13/13 |
| reverse_trip | 1 | 1/1 | 1/1 |
| slow_breaker | 1 | 1/1 | 1/1 |
| slow_relay | 1 | 1/1 | 1/1 |
| wrong_curve | 2 | 2/2 | 2/2 |

### Fault-type confusion (rows = truth)

| truth \ predicted | AG | BG | CG | AB | BC | CA | ABG | BCG | CAG | ABC | other |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **AG** | 1 |  |  |  |  |  |  |  |  |  |  |
| **BG** |  | 1 |  |  |  |  |  |  |  |  |  |
| **CG** |  |  | 2 |  |  |  |  |  |  |  |  |
| **AB** |  |  |  | 3 |  |  |  |  |  |  |  |
| **BC** |  |  |  |  | 1 |  |  |  |  |  |  |
| **CA** |  |  |  |  |  | 3 |  |  |  |  |  |
| **ABG** |  |  |  |  |  |  | 1 |  |  |  |  |
| **BCG** |  |  |  |  |  |  |  | 2 |  |  |  |
| **CAG** |  |  |  |  |  |  |  |  | 2 |  |  |
| **ABC** |  |  |  |  |  |  |  |  |  | 4 |  |

## Narrator (local LLM + validator)

| Metric | Result |
|---|---|
| Backend / model | ollama / `llama3.2:3b` |
| Events narrated | 20 |
| Sentences proposed | 157 |
| Sentences rejected by the validator | 9 (5.7%) |
| Rejection reasons | number not produced by the core 8, wrong direction 1 |
| Numbers written by the model that trace to a core fact | 95.8% of 189 |
| Events that fell back to the template narrative | 1 of 20 |
| Essential topics the model skipped (template sentence added, flagged in the audit) | fault type in 9, relay operation in 2 |
| Seconds per event (retrieval + narration) | median 105.8, max 141.6 |
| Generation speed | median 3.7 tokens/s; median 366 tokens out, 1395 in |

## Re-validation and hand audit

The 149 sentences the model wrote for the 19 events it narrated (stored in `narration.jsonl`) were
checked again with the final validator, and every one was read by hand against the event's fact
sheet. A sentence is *wrong* if the fact sheet contradicts it; vague but true counts as correct.

| Hand label | Validator kept | Validator rejected | Total |
|---|---|---|---|
| correct | 136 | 0 | 136 |
| wrong | 2 | 11 | 13 |

Wrong sentences the validator rejects:

- `ev0001`, `ev0011`, `ev0012`, `ev0013`: "Fault detected at -3.74 ms before the record trigger" and the
  same pattern. A negative time "before" the trigger says the opposite of what happened.
- `ev0001`: "3I0 is 0.02 A ..., below the 51P1 pickup (0.84 A)". 0.84 A is the 51G1 pickup.
- `ev0013`: "operated 1.08 ms later than expected". The deviation is 2.08 ms.
- `ev0003`: "-1.09 ohms, which is less than the 51P1 pickup of 3.35 amperes". Ohms compared with amperes.
- `ev0004`, `ev0012`: "below the forward threshold ... and above the reverse threshold". z2 is below both.
- `ev0005`: "interrupted the current at 29.17 ms, which is 1.75 cycles after the fault inception". Breaker
  times run from the trip.
- `ev0008`: "37.50 ms after the fault inception, ... 2.25 cycles after the expected interrupt time". Wrong
  reference, and the core computes no expected breaker time.

Wrong sentences the validator keeps (these reached the report):

- `ev0004`: "The fault ended when the local breaker interrupted the current 25.00 ms after the trip, which
  opened the 52A contact." 25.00 ms is when 52A opened; the current was interrupted at 30.21 ms. Both
  numbers are breaker times measured from the trip, so this swap is the kind the red team's "value of
  a different fact" row measures.
- `ev0007`: "The fault current ended when the local 52A breaker stayed closed, interrupting the current."
  Other breakers interrupted it; the local breaker stayed closed.

No correct sentence was rejected. Summary: 13 of 149 model sentences (8.7%) were wrong; the validator
removed 11 of them.

