#!/usr/bin/env bash
# Runs the full experiment matrix (~75 min). Edit the lists to cut it down.
# Start/stop a screen recorder yourself if you want footage of a run.
set -euo pipefail
cd "$(dirname "$0")/.."
R=scripts/run-experiment.sh
for n in 1 3; do for r in 1 2 3; do $R podkill chaos/pod-kill.yaml $n $r 60; done; done
for d in 100 300 1000 3000; do $R delay${d}ms chaos/network-delay-${d}ms.yaml 1 1 150; done
$R delay1000ms chaos/network-delay-1000ms.yaml 3 1 150
for n in 1 3; do $R stressmem chaos/stress-memory.yaml $n 1 150; done
for n in 1 3; do $R stresscpu chaos/stress-cpu.yaml $n 1 210; done
kubectl apply -n chaos-lab -f k8s/hpa.yaml
sleep 60   # let metrics-server produce a first reading
KEEP_HPA=1 $R stresscpu-hpa chaos/stress-cpu.yaml 1 1 210
kubectl delete -n chaos-lab -f k8s/hpa.yaml
kubectl -n chaos-lab scale deploy/api deploy/frontend --replicas=1
python3 scripts/summarise.py
