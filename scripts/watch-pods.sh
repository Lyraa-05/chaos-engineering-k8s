#!/usr/bin/env bash
# watch-pods.sh <outfile>
# Timestamped pod watch for the chaos-lab namespace (sub-second resolution,
# unlike Kubernetes events which only have 1s timestamps). One line per change:
#   <epoch> <EVENT> <pod> <ready> <phase> <restarts> <node>
set -u
OUT=${1:?usage: watch-pods.sh <outfile>}
: > "$OUT"
kubectl -n chaos-lab get pods -w --output-watch-events --no-headers \
  -o custom-columns='EV:.type,NAME:.object.metadata.name,READY:.object.status.containerStatuses[*].ready,PHASE:.object.status.phase,RESTARTS:.object.status.containerStatuses[*].restartCount,NODE:.object.spec.nodeName,REASON:.object.status.containerStatuses[*].lastState.terminated.reason' \
  | while read -r line; do echo "$(date +%s.%N) $line"; done >> "$OUT"
