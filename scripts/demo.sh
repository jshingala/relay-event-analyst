#!/usr/bin/env bash
# The 60-second demo. Unplug the Ethernet cable (and turn Wi-Fi off) first.
#
#   scripts/demo.sh                    # native: build/core/rea-core + Ollama on this machine
#   REA_DOCKER=1 scripts/demo.sh       # same, with the analyst in the locked-down container
#   EVENT=data/demo/demo_abc_failed_to_trip.cfg scripts/demo.sh
#
# Shot list and narration: docs/demo-script.md
set -euo pipefail
cd "$(dirname "$0")/.."

EVENT=${EVENT:-data/demo/demo_cag_slow_relay.cfg}
MODEL=${MODEL:-llama3.2:3b}
OUT=${OUT:-out}
mkdir -p "$OUT"

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }

if [[ "${REA_DOCKER:-0}" == "1" ]]; then
  REA_UID=$(id -u)
  REA_GID=$(id -g)
  export REA_UID REA_GID
  rea() { docker compose -f docker/compose.yaml run --rm rea "$@"; }
  EV_IN="/events/$(basename "$EVENT")"
  OUT_IN=/out
else
  rea() { PYTHONPATH=python python3 -m rea "$@"; }
  EV_IN="$EVENT"
  OUT_IN="$OUT"
fi

bold "1. Is anything connected?"
if command -v ip >/dev/null; then ip -brief link | grep -v '^lo ' || true; fi
rea doctor --model "$MODEL" || true

bold "2. Analyze a relay event: physics core + local 3B model + validator"
start=$(date +%s.%N)
rea analyze "$EV_IN" --model "$MODEL" --md "$OUT_IN/report.md" --json "$OUT_IN/report.json"
end=$(date +%s.%N)

bold "3. Done in $(python3 -c "print(f'{$end - $start:.1f}')") s with no network. Report: $OUT/report.md"
