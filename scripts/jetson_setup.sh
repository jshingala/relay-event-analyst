#!/usr/bin/env bash
# One-time setup on a Jetson (JetPack 6 / Ubuntu 22.04) while it is still online.
# After this, everything runs with the network cable unplugged.
#
#   scripts/jetson_setup.sh            # asks before installing Ollama
#   YES=1 scripts/jetson_setup.sh      # no prompts
set -euo pipefail
cd "$(dirname "$0")/.."
MODEL=${MODEL:-llama3.2:3b}

ask() { [[ "${YES:-0}" == "1" ]] && return 0; read -r -p "$1 [y/N] " a; [[ "$a" =~ ^[Yy] ]]; }

echo "== device"
head -n1 /etc/nv_tegra_release 2>/dev/null || echo "warning: /etc/nv_tegra_release not found (not a Jetson?)"
nvpmodel -q 2>/dev/null || true

echo "== build tools"
sudo apt-get update
sudo apt-get install -y --no-install-recommends cmake g++ make python3-pip python3-numpy

echo "== physics core and tests"
make core
(cd build/core && ctest --output-on-failure)

echo "== Python tests"
python3 -m pip install --user pytest pypdf
PYTHONPATH=python python3 -m pytest tests

echo "== Ollama (GPU build for Jetson comes from the official installer)"
if ! command -v ollama >/dev/null; then
  if ask "Install Ollama with the official script from ollama.com?"; then
    curl -fsSL https://ollama.com/install.sh | sh
  else
    echo "skipped; see https://ollama.com/download/linux"
  fi
fi
if command -v ollama >/dev/null && command -v systemctl >/dev/null && systemctl cat ollama >/dev/null 2>&1; then
  echo "== Ollama service settings (loopback only, no cloud features, bounded memory)"
  sudo mkdir -p /etc/systemd/system/ollama.service.d
  sudo tee /etc/systemd/system/ollama.service.d/rea.conf >/dev/null <<'CONF'
[Service]
# Loopback only. rea refuses a non-loopback model address anyway.
Environment="OLLAMA_HOST=127.0.0.1:11434"
# No cloud models or account features: nothing leaves the device.
Environment="OLLAMA_NO_CLOUD=1"
# Keep the model loaded between events (rea also asks for this per request).
Environment="OLLAMA_KEEP_ALIVE=30m"
# Ollama 0.40.2 runs llama-server, whose prompt cache defaults to 8 GiB of RAM. On an
# 8 GB board that got the runner killed. 0 turns the cache off; older versions ignore it.
Environment="LLAMA_ARG_CACHE_RAM=0"
CONF
  sudo systemctl daemon-reload
  sudo systemctl restart ollama
  sleep 3
fi
if command -v ollama >/dev/null; then
  ollama pull "$MODEL"
  ollama list
fi

echo "== demo data and papers"
[[ -f data/demo/manifest.json ]] || PYTHONPATH=python python3 -m rea.synth.cli --demo --out data/demo
[[ -f data/synth/manifest.json ]] || make synth EVENTS=200
ls papers/*.pdf >/dev/null 2>&1 && PYTHONPATH=python python3 -m rea index-papers \
  || echo "no PDFs in papers/ yet: see papers/README.md (retrieval is optional)"

echo "== check"
PYTHONPATH=python python3 -m rea doctor --model "$MODEL" || true
echo "Next: unplug the network and run scripts/demo.sh"
