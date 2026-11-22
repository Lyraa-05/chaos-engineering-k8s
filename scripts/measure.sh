#!/usr/bin/env bash
# measure.sh <outfile> [url] [interval]
# Sends one request every <interval> seconds (default 0.5) to the app, in the
# background so a slow request does not delay the next probe. One line per request:
#   <epoch_seconds_sent> <http_status> <latency_seconds>
# status 000 = connection failed / timed out (curl --max-time 6, nginx read timeout is 5s).
set -u
OUT=${1:?usage: measure.sh <outfile> [url] [interval]}
URL=${2:-http://localhost:8080/api/visit}
INTERVAL=${3:-0.5}
mkdir -p "$(dirname "$OUT")"
: > "$OUT"
trap 'wait; exit 0' INT TERM
while true; do
  (
    ts=$(date +%s.%N)
    r=$(curl -s -o /dev/null --max-time 6 -w '%{http_code} %{time_total}' "$URL")
    echo "$ts ${r:-000 6.0}" >> "$OUT"
  ) &
  sleep "$INTERVAL"
done
