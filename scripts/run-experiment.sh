#!/usr/bin/env bash
# run-experiment.sh <experiment> <chaos.yaml> <replicas> <run#> [during_seconds]
#   e.g. scripts/run-experiment.sh podkill chaos/pod-kill.yaml 3 1
# Timeline:  scale+wait ready -> start probes/watchers -> 60s baseline ->
#            apply chaos -> wait <during_seconds> -> delete chaos -> 45s post-baseline
# Output:    results/raw/<experiment>-<N>replica-run<R>/{requests.log,pods.log,events.log,meta.env}
set -euo pipefail
EXP=${1:?experiment name}; YAML=${2:?chaos yaml}; REPLICAS=${3:?replicas}; RUN=${4:?run number}
DURING=${5:-60}
BASELINE=${BASELINE:-60}; POST=${POST:-45}
NS=chaos-lab
ROOT=$(cd "$(dirname "$0")/.." && pwd)
NAME="${EXP}-${REPLICAS}replica-run${RUN}"
DIR="$ROOT/results/raw/$NAME"
mkdir -p "$DIR"
log() { echo "[$(date +%T)] $NAME: $*"; }

PIDS=()
cleanup() {
  kubectl delete -f "$ROOT/$YAML" --ignore-not-found >/dev/null 2>&1 || true
  for p in "${PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done
  pkill -P $$ 2>/dev/null || true
  pkill -f -- "--output-watch-events" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [ "${KEEP_HPA:-0}" != "1" ] && kubectl -n $NS get hpa api >/dev/null 2>&1; then
  echo "HPA is applied; remove it (make hpa-off) or set KEEP_HPA=1 for the HPA experiment" >&2; exit 1
fi

log "scaling api+frontend to $REPLICAS"
kubectl -n $NS scale deploy/api deploy/frontend --replicas="$REPLICAS" >/dev/null
kubectl -n $NS rollout status deploy/api --timeout=180s >/dev/null
kubectl -n $NS rollout status deploy/frontend --timeout=180s >/dev/null
kubectl -n $NS wait --for=condition=Ready pod -l 'app in (api,frontend,db)' --timeout=180s >/dev/null
sleep 5
kubectl -n $NS get pods -o wide > "$DIR/pods-before.txt"

"$ROOT/scripts/measure.sh" "$DIR/requests.log" & PIDS+=($!)
"$ROOT/scripts/watch-pods.sh" "$DIR/pods.log" & PIDS+=($!)
kubectl -n $NS get events --watch-only \
  -o custom-columns='TIME:.lastTimestamp,REASON:.reason,OBJECT:.involvedObject.name,MSG:.message' \
  > "$DIR/events.log" 2>/dev/null & PIDS+=($!)

log "baseline ${BASELINE}s"; sleep "$BASELINE"
CHAOS_START=$(date +%s.%N)
log "applying $YAML"
kubectl apply -f "$ROOT/$YAML" >/dev/null
log "chaos running, waiting ${DURING}s"; sleep "$DURING"
CHAOS_END=$(date +%s.%N)
kubectl delete -f "$ROOT/$YAML" --ignore-not-found >/dev/null
log "chaos deleted, post-baseline ${POST}s"; sleep "$POST"
END=$(date +%s.%N)

kubectl -n $NS get pods -o wide > "$DIR/pods-after.txt"
kubectl -n $NS get hpa -o wide > "$DIR/hpa.txt" 2>/dev/null || true
cat > "$DIR/meta.env" <<META
EXPERIMENT=$EXP
YAML=$YAML
REPLICAS=$REPLICAS
RUN=$RUN
CHAOS_START=$CHAOS_START
CHAOS_END=$CHAOS_END
END=$END
HPA=${KEEP_HPA:-0}
META
log "done -> results/raw/$NAME"
