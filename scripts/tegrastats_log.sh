#!/usr/bin/env bash
# Start or stop a tegrastats power log on a Jetson.
#
#   scripts/tegrastats_log.sh start logs/run.log [interval_ms]
#   ... do the work ...
#   scripts/tegrastats_log.sh stop
#   python3 -m rea.evaluation.power logs/run.log --events N --interval-ms 500
#
# Uses only `--interval` and `--logfile` (documented in the Jetson Linux Developer
# Guide, "Tegrastats Utility") and stops the process by PID. Some fields need root;
# set SUDO=sudo if your rails do not show up.
set -euo pipefail
PIDFILE=${PIDFILE:-/tmp/rea_tegrastats.pid}
SUDO=${SUDO:-}

case "${1:-}" in
  start)
    LOG=${2:?usage: $0 start LOGFILE [interval_ms]}
    INTERVAL=${3:-500}
    command -v tegrastats >/dev/null || { echo "tegrastats not found: is this a Jetson?" >&2; exit 1; }
    mkdir -p "$(dirname "$LOG")"
    : > "$LOG"
    $SUDO tegrastats --interval "$INTERVAL" --logfile "$LOG" &
    echo $! > "$PIDFILE"
    echo "tegrastats logging every ${INTERVAL} ms to $LOG (pid $(cat "$PIDFILE"))"
    ;;
  stop)
    if [[ -f "$PIDFILE" ]]; then
      $SUDO kill "$(cat "$PIDFILE")" 2>/dev/null || true
      rm -f "$PIDFILE"
    fi
    $SUDO pkill -x tegrastats 2>/dev/null || true
    echo "tegrastats stopped"
    ;;
  *)
    echo "usage: $0 start LOGFILE [interval_ms] | stop" >&2
    exit 2
    ;;
esac
