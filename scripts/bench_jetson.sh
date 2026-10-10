#!/usr/bin/env bash
# Measure seconds per event and power on a Jetson, offline, and write the results.
#
#   scripts/bench_jetson.sh                 # 20 narrated events with llama3.2:3b
#   N=50 MODEL=llama3.2:1b scripts/bench_jetson.sh
#
# Writes eval/jetson-<date>/: environment.txt, RESULTS.md, summary.json,
# narration.jsonl, tegrastats_idle.log, tegrastats_run.log, power_idle.json.
# Record the power mode you used: results are only comparable within one mode.
set -euo pipefail
cd "$(dirname "$0")/.."

N=${N:-20}
MODEL=${MODEL:-llama3.2:3b}
INTERVAL=${INTERVAL:-500}
IDLE_S=${IDLE_S:-20}
OUT=${OUT:-eval/jetson-$(date +%Y%m%d-%H%M)}
mkdir -p "$OUT"
export PYTHONPATH=python

echo "== environment"
{
  echo "date: $(date -Is)"
  echo "l4t: $(head -n1 /etc/nv_tegra_release 2>/dev/null || echo 'not a Jetson')"
  echo "model file: $(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
  echo "power mode: $(nvpmodel -q 2>/dev/null | tr '\n' ' ' || echo unknown)"
  echo "kernel: $(uname -r)"
  echo "ollama: $(ollama --version 2>/dev/null || echo 'not found')"
  echo "llm: $MODEL"
  ollama list 2>/dev/null || true
  echo "network: $(python3 -c 'from rea.cli import network_state; print(network_state())')"
} | tee "$OUT/environment.txt"

make core >/dev/null
[[ -f data/synth/manifest.json ]] || make synth EVENTS=200

echo "== warm-up (loads the model; not counted)"
python3 -m rea analyze data/demo/demo_bg_51g_normal.cfg --model "$MODEL" --no-papers --quiet >/dev/null

echo "== idle power baseline (${IDLE_S} s)"
scripts/tegrastats_log.sh start "$OUT/tegrastats_idle.log" "$INTERVAL"
sleep "$IDLE_S"
scripts/tegrastats_log.sh stop
python3 -m rea.evaluation.power "$OUT/tegrastats_idle.log" --interval-ms "$INTERVAL" > "$OUT/power_idle.json" || true

echo "== measured run: $N events, core + retrieval + narration + validation"
scripts/tegrastats_log.sh start "$OUT/tegrastats_run.log" "$INTERVAL"
trap 'scripts/tegrastats_log.sh stop >/dev/null' EXIT
python3 -m rea.evaluation.run --data data/synth --out "$OUT" --limit "$N" \
  --narrate --narrate-limit "$N" --model "$MODEL" \
  --power-log "$OUT/tegrastats_run.log" --power-interval-ms "$INTERVAL"

echo "== idle baseline"
cat "$OUT/power_idle.json"
echo "results: $OUT/RESULTS.md"
