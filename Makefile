# Relay Event Analyst. Works from a fresh clone without installing the Python package.
PY      ?= python3
JOBS    ?= $(shell nproc 2>/dev/null || echo 2)
BUILD   ?= build/core
RUN     := PYTHONPATH=python $(PY) -m
EVENTS  ?= 1000
SEED    ?= 7
MODEL   ?= llama3.2:3b

.PHONY: all core test test-core test-py synth demo-data eval eval-narrate real demo doctor docker clean help

all: core

help:
	@echo "make core          build the C++ physics core (build/core/rea-core)"
	@echo "make test          C++ unit tests + Python tests"
	@echo "make synth         generate $(EVENTS) labeled synthetic events (seed $(SEED)) into data/synth"
	@echo "make eval          score the core on data/synth, red-team the validator -> eval/results/RESULTS.md"
	@echo "make eval-narrate  also narrate 20 events with the local model ($(MODEL)); RESUME=1 continues"
	@echo "make real          SEL webinar events vs. the relays' own summaries (downloads once)"
	@echo "make demo          the 60-second offline demo (scripts/demo.sh)"
	@echo "make docker        build the container image"

core:
	cmake -S core -B $(BUILD) -DCMAKE_BUILD_TYPE=Release -DREA_WERROR=ON
	cmake --build $(BUILD) -j $(JOBS)

test: test-core test-py

test-core: core
	cd $(BUILD) && ctest --output-on-failure -j $(JOBS)

test-py: core
	PYTHONPATH=python $(PY) -m pytest tests

synth:
	$(RUN) rea.synth.cli --out data/synth --n $(EVENTS) --seed $(SEED)

demo-data:
	$(RUN) rea.synth.cli --demo --out data/demo

eval: core
	$(RUN) rea.evaluation.run --data data/synth --out eval/results

eval-narrate: core
	$(RUN) rea.evaluation.run --data data/synth --out eval/narration --limit 20 --narrate --narrate-limit 20 --model $(MODEL) \
	  $(if $(RESUME),--resume)

real: core
	$(RUN) rea.evaluation.real_events $(if $(wildcard data/real/sel_webinar_2020),,--fetch)

demo: core
	scripts/demo.sh

doctor: core
	$(RUN) rea doctor --model $(MODEL)

docker:
	docker build -f docker/Dockerfile -t relay-event-analyst:0.1.0 .

clean:
	rm -rf build eval/results
