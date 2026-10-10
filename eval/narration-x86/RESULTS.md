# Evaluation results

Generated 2026-10-10 03:12 UTC on `x86_64 Linux, 4 CPUs` from `data/synth` (20 synthetic events, generator seed 7).

These are synthetic events from the included simulator, not field records.

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
| Core time per event (incl. process start) | median 3.5 ms, p95 5.3 ms, max 7.2 ms (n=20) |
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
| Backend / model | Ollama 0.40.2 / `llama3.2:3b` |
| Retrieval (local papers) | off |
| Events narrated by the model | 20 |
| Sentences proposed | 153 |
| Sentences rejected by the validator | 19 (12.4%) |
| Rejection reasons | number not produced by the core 14, other 4, contradicts a verdict 1 |
| Numbers written by the model that trace to a core fact | 89.8% of 137 |
| Events that fell back to the template narrative | 0 of 20 |
| Essential topics the model skipped (template sentence added, flagged in the audit) | fault type in 13, relay operation in 1, relay verdict in 1 |
| Seconds per event (retrieval + narration) | median 45.1, max 63.6 |
| Generation speed | median 8.6 tokens/s; median 294.5 tokens out, 912.0 in |

## Run notes

- Model: `llama3.2:3b` (Q4_K_M, Ollama manifest digest `a80c4f17acd5`) in Ollama 0.40.2 (the
  official `ollama/ollama` image), loopback only, `OLLAMA_NO_CLOUD=1`, `LLAMA_ARG_CACHE_RAM=0`, CPU
  only. ollama.com was not reachable from the build machine, so the model files were copied from a
  Docker Hub image that bundles them; every blob matched its SHA-256 name before use.
- Retrieval was off: the SEL papers could not be downloaded on this machine, so the prompts had
  no BACKGROUND passages. The Jetson run is meant to include them.
- The model often opens with the fault inception relative to the trigger without naming the
  trigger ("Fault detected at -3.07 ms, ..."). The audit counts that as vague but true. The
  validator rejects it because the number is neither cited nor next to words that name its fact;
  these are 9 of the 10 correct sentences it rejected, and the fault type in them is then added
  from the template.
- Two patterns make up 4 of the 6 wrong sentences the validator kept: the 52A opening time quoted
  as the interrupting time (`ev0001`, `ev0003`, where the model cited both facts), and the operate
  time in cycles quoted as the deviation from the expected time (`ev0005`, `ev0018`). Both now
  have validator checks, added after this run (docs/validator.md), as does the "-X ms" opening
  sentence. The kept/rejected columns below are the validator at the time of the run.

## Hand audit of the model's sentences

Every sentence the model proposed was read against the event's fact sheet and labeled by hand:
*wrong* if the fact sheet contradicts it (a number given the wrong meaning, a comparison the
numbers do not support, a time measured from the wrong reference), *ok* otherwise. Vague but
true counts as ok. Labels: `audit.json`.

| Hand label | Validator kept | Validator rejected | Total |
|---|---|---|---|
| ok | 128 | 10 | 138 |
| wrong | 6 | 9 | 15 |

The validator rejected 9 of the 15 wrong sentences and 10 of the 138 correct ones.

Wrong sentences the validator rejected:

- `ev0004` "Relay operated 15.62 ms after fault detection, which is 2.12 ms later than expected." Why wrong: The relay was 3.12 ms later than expected (15.62 ms against 12.50 ms), not 2.12 ms. Validator: numbers not traceable to the physics core: 2.12.
- `ev0004` "Breaker interrupted the fault current 30.21 ms after the relay operated, which is 14.59 ms after the fault detection." Why wrong: 30.21 ms after the trip is right, but the current was interrupted 45.83 ms after the fault began (the fault duration), not 14.59 ms; 14.59 is 30.21 minus 15.62, which measures nothing. Validator: numbers not traceable to the physics core: 14.59.
- `ev0008` "Relay operated 720.83 ms after fault detection, which is 0.86 ms later than expected." Why wrong: Minor: the core's deviation is 0.87 ms. 0.86 comes from subtracting the rounded times (720.83 - 719.97), so the stated value does not match the fact at the precision written. Validator: numbers not traceable to the physics core: 0.86.
- `ev0010` "Fault detected at 0 ms, fault inception was -0.17 ms before the record trigger." Why wrong: "-0.17 ms before the record trigger" is a double negative (the fault began 0.17 ms before the trigger), and the fault was not detected at 0 ms. Validator: a negative time 'before' an event is a double negative.
- `ev0013` "Fault detected at 14.58 ms after the record trigger." Why wrong: The fault began 2.63 ms before the trigger; 14.58 ms is the relay operate time. Validator: numbers not traceable to the physics core: 14.58.
- `ev0013` "Breaker interrupted the fault current 33.33 ms after the fault inception." Why wrong: 33.33 ms is measured from the trip, not from the fault inception (the fault lasted 47.92 ms). Validator: times a breaker event from fault inception; the core measures it from the trip.
- `ev0017` "The relay operated in 14.58 ms, which is longer than the expected 0.87 cycles." Why wrong: 0.87 cycles is the relay's own operate time, not an expected time; no trip was expected for this reverse fault. Validator: compares 14.58 ms with 0.87 cycles: different kinds of quantity.
- `ev0017` "The fault duration was 42.71 ms, which is longer than the expected duration." Why wrong: The core computes no expected fault duration, so "longer than the expected duration" has no basis. Validator: compares with an expected value the core did not compute (only the relay has one).
- `ev0019` "Relay operated 804.17 ms after fault detection, which is 0.92 ms later than expected." Why wrong: Minor: the core's deviation is 0.91 ms. 0.92 comes from subtracting the rounded times (804.17 - 803.25), so the stated value does not match the fact at the precision written. Validator: numbers not traceable to the physics core: 0.92.

Wrong sentences the validator kept (these reached the report):

- `ev0001` "Breaker interrupted the fault current 25.00 ms after the trip." Why wrong: 25.00 ms is when the 52A contact opened (breaker.52a_open_after_trip_ms); the current was interrupted 29.17 ms after the trip (breaker.interrupt_ms).
- `ev0003` "Breaker interrupted the fault current 30.21 ms after the trip." Why wrong: 30.21 ms is when the 52A contact opened (breaker.52a_open_after_trip_ms); the current was interrupted 36.46 ms after the trip (breaker.interrupt_ms).
- `ev0005` "Relay operated 12.50 ms after fault inception, 0.75 cycles later than expected." Why wrong: 0.75 cycles is the relay operate time itself (relay.operate_cycles), not how much later than expected it was; the deviation is 3.12 ms (12.50 ms against 9.37 ms expected).
- `ev0006` "The fault direction is forward, indicating the fault current is flowing towards the relay." Why wrong: Forward means the fault is in front of the relay: fault current flows from the bus through the relay out toward the fault. Current flowing toward the relay from the line describes a reverse fault.
- `ev0011` "The relay did not trip as expected, despite the fault being in a reverse direction." Why wrong: The relay correctly did not trip because the fault was reverse and its elements are forward-directional. "Despite" states the opposite relation.
- `ev0018` "Relay operated 258.33 ms after fault inception, 15.50 cycles earlier than expected." Why wrong: 15.50 cycles is the relay operate time itself (relay.operate_cycles), not how much earlier than expected it was; the deviation is -1.01 ms.

Correct sentences the validator rejected:

- `ev0000` "Fault detected at -3.07 ms, fault type is C-A phase-to-phase." Validator: numbers not traceable to the physics core: -3.07. -3.07 ms is the fault inception relative to the record trigger. The sentence gives no reference, so it is vague, but nothing in the fact sheet contradicts it.
- `ev0001` "Fault detected at -3.74 ms, fault type is BC phase-to-phase." Validator: numbers not traceable to the physics core: -3.74. -3.74 ms is the fault inception relative to the record trigger. No reference is given, so it is vague, but nothing contradicts it.
- `ev0002` "Fault detected at -5.30 ms, fault type is three-phase ABC." Validator: numbers not traceable to the physics core: -5.30. -5.30 ms is the fault inception relative to the record trigger. No reference is given, so it is vague, but nothing contradicts it.
- `ev0003` "Fault detected at -0.45 ms, fault type is three-phase ABC." Validator: numbers not traceable to the physics core: -0.45. -0.45 ms is the fault inception relative to the record trigger. No reference is given, so it is vague, but nothing contradicts it.
- `ev0004` "Fault detected at -5.76 ms, fault type is A-B phase-to-phase." Validator: numbers not traceable to the physics core: -5.76. -5.76 ms is the fault inception relative to the record trigger. No reference is given, so it is vague, but nothing contradicts it.
- `ev0005` "Fault detected at -5.43 ms, fault type B-C-to-ground, and ground involved." Validator: numbers not traceable to the physics core: -5.43. -5.43 ms is the fault inception relative to the record trigger. No reference is given, so it is vague, but nothing contradicts it.
- `ev0008` "Fault detected at -0.18 ms, fault type is A-B phase-to-phase." Validator: numbers not traceable to the physics core: -0.18. -0.18 ms is the fault inception relative to the record trigger. No reference is given, so it is vague, but nothing contradicts it.
- `ev0009` "Fault detected at -0.25 ms, fault type is A-B phase-to-phase." Validator: numbers not traceable to the physics core: -0.25. -0.25 ms is the fault inception relative to the record trigger. No reference is given, so it is vague, but nothing contradicts it.
- `ev0017` "The relay tripped, and the breaker performed normally." Validator: says the relay performed correctly, but the core verdict is 'tripped for a fault outside its directional zone'. Both claims are true: the relay tripped and the breaker verdict is normal. "Performed normally" is said of the breaker, not the relay. It leaves out that the trip was outside the directional zone (that verdict was added from the template), but states nothing false.
- `ev0018` "Fault detected at -5.27 ms, fault type C-A-to-ground, and ground involved." Validator: numbers not traceable to the physics core: -5.27. -5.27 ms is the fault inception relative to the record trigger. No reference is given, so it is vague, but nothing contradicts it.
