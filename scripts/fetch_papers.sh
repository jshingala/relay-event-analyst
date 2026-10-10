#!/usr/bin/env bash
# Download technical papers into papers/ (git-ignored), then index them.
#
#   scripts/fetch_papers.sh                      # the SEL papers listed in papers/sel-papers.txt
#   scripts/fetch_papers.sh my-list.txt          # your own list: one URL per line, '#' comments
#
# Respect the publisher's terms. The PDFs stay on this device and are never committed.
set -euo pipefail
cd "$(dirname "$0")/.."
LIST=${1:-papers/sel-papers.txt}
[[ -f "$LIST" ]] || { echo "no $LIST: add one PDF URL per line" >&2; exit 1; }

while IFS= read -r line; do
  url=${line%%#*}
  url=${url//[[:space:]]/}
  [[ -z "$url" ]] && continue
  name=$(basename "${url%/}")
  name=${name%%\?*}
  [[ "$name" == *.pdf ]] || name="sel-$name.pdf"
  if [[ -f "papers/$name" ]]; then
    echo "have  $name"
  else
    echo "fetch $name"
    curl -fsSL --retry 2 -o "papers/$name" "$url" || { echo "  failed: $url" >&2; rm -f "papers/$name"; }
  fi
done < "$LIST"

PYTHONPATH=python python3 -m rea index-papers
