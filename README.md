# Evaluating Kubernetes self-healing through chaos engineering

Cloud Computing I Project.

This repository contains the code and configuration used to evaluate how Kubernetes' built-in
self-healing (ReplicaSet recovery, liveness/readiness probes, resource limits and the Horizontal
Pod Autoscaler) responds to failures injected with **Chaos Mesh**. A small three-tier app runs on a
local multi-node **kind** cluster and is monitored with **Prometheus and Grafana**.

## Repository contents

```
.
├── kind-config.yaml              # kind cluster: 1 control-plane + 2 workers, host :8080 -> NodePort 30080
├── app/
│   ├── api/                      # Flask + gunicorn API (app.py, Dockerfile, requirements.txt)
│   ├── frontend/                 # nginx frontend (index.html, nginx.conf, Dockerfile)
│   └── docker-compose.yml        # same stack under Docker Compose, for testing before Kubernetes
├── k8s/
│   ├── namespace.yaml            # chaos-lab namespace
│   ├── db.yaml                   # PostgreSQL 16 Deployment + Service
│   ├── api.yaml                  # API Deployment (probes, resource limits) + Service
│   ├── frontend.yaml             # frontend Deployment + NodePort Service (30080)
│   ├── hpa.yaml                  # Horizontal Pod Autoscaler for the API (CPU stress experiment only)
│   └── chaos-dashboard-rbac.yaml # service account for the Chaos Mesh dashboard, limited to chaos-lab
├── monitoring/
│   ├── values.yaml               # kube-prometheus-stack overrides (5s scrape, dashboard sidecar)
│   └── dashboards/chaos-lab.json # custom "Chaos Lab - self-healing" Grafana dashboard
└── scripts/
    ├── fix-inotify.sh            # raises and persists host inotify limits (needed for Chaos Mesh on kind)
    └── measure.sh                # request probe used to measure user-visible impact during experiments
```

### Application (`app/`)
| Component | Description |
|---|---|
| **api** | Flask app served by gunicorn (2 workers) on port 5000. `/api/visit` writes a row to PostgreSQL and returns the visit count and the pod name. `/healthz` is the liveness endpoint (process alive). `/ready` is the readiness endpoint (database reachable, otherwise HTTP 503). DB settings come from environment variables. |
| **frontend** | nginx serving a static page with a *Visit* button. It reverse-proxies `/api/` to `api:5000` (2s connect timeout, 5s read timeout). |
| **db** | Official `postgres:16` image (no custom code). |

The images are published on Docker Hub:
- [`lyra200507/chaos-api:v1`](https://hub.docker.com/r/lyra200507/chaos-api)
- [`lyra200507/chaos-frontend:v1`](https://hub.docker.com/r/lyra200507/chaos-frontend)

### Kubernetes manifests (`k8s/`)
| Component | Readiness probe | Liveness probe | Requests -> Limits |
|---|---|---|---|
| api | `/ready` every 2s, failureThreshold 2 | `/healthz` every 5s, failureThreshold 3 | 100m / 64Mi -> 500m / 128Mi |
| frontend | `/` every 2s | none | 50m / 32Mi -> 200m / 64Mi |
| db | `pg_isready` every 5s | none | none |

`hpa.yaml` scales the API between 1 and 4 replicas at 50% CPU. It was only applied for the CPU stress experiment.

### Monitoring (`monitoring/`)
- `values.yaml` lowers the Prometheus scrape interval from the chart's 30s default to **5s**, so short pod-kill outages are visible. It also enables Grafana's dashboard sidecar.
- `dashboards/chaos-lab.json` is a custom dashboard with panels for available replicas, pod readiness, restarts, OOMKilled, CPU vs limit, CPU throttling, memory vs limit and HPA replicas.

### Scripts (`scripts/`)
**`measure.sh <logfile> [url] [interval]`** acts like a user, sending a request to `http://localhost:8080/api/visit` every 0.5s. Each request runs in the background, so slow responses don't reduce the sampling rate. It writes one line per request:

```
1795872345.123456789 200 0.012345 21:38:20.123
<unix time sent>    <HTTP status> <latency (s)> <clock time>
```
Status `000` means no response (connection failed, or no reply within 6s).

**`fix-inotify.sh`** sets `fs.inotify.max_user_watches=524288` and `fs.inotify.max_user_instances=512`, and saves them in `/etc/sysctl.d/99-kind.conf`. Fedora's defaults are too low for a multi-node kind cluster running Prometheus and Chaos Mesh: the Chaos Mesh controller crash-loops with "too many open files".

## Setup

Prerequisites: Docker, kind, kubectl, helm, curl.

```bash
# 0. host inotify limits (once per machine)
./scripts/fix-inotify.sh

# 1. cluster
kind create cluster --name chaos-lab --config kind-config.yaml

# 2. (optional) test the app with Docker Compose on :8081
cd app && docker compose up -d --build && curl -s localhost:8081/api/visit && docker compose down && cd ..

# 3. application (images are pulled from Docker Hub)
kubectl apply -f k8s/namespace.yaml
kubectl apply -n chaos-lab -f k8s/db.yaml -f k8s/api.yaml -f k8s/frontend.yaml
curl -s localhost:8080/api/visit

# 4. Prometheus + Grafana
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install monitoring prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace -f monitoring/values.yaml
kubectl -n monitoring create configmap chaos-lab-dashboard --from-file=monitoring/dashboards/chaos-lab.json
kubectl -n monitoring label configmap chaos-lab-dashboard grafana_dashboard=1
kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80        # http://localhost:3000 (admin / admin)

# 5. metrics-server (for the HPA)
helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/
helm install metrics-server metrics-server/metrics-server -n kube-system \
  --set 'args={--kubelet-insecure-tls}'

# 6. Chaos Mesh (kind uses containerd)
helm repo add chaos-mesh https://charts.chaos-mesh.org
helm install chaos-mesh chaos-mesh/chaos-mesh -n chaos-mesh --create-namespace \
  --set chaosDaemon.runtime=containerd \
  --set chaosDaemon.socketPath=/run/containerd/containerd.sock
kubectl apply -f k8s/chaos-dashboard-rbac.yaml
kubectl -n chaos-lab create token account-chaos-lab-manager-fsvkt        # dashboard login token
kubectl -n chaos-mesh port-forward svc/chaos-dashboard 2333:2333          # http://localhost:2333
```

## Experiments

The experiments were created in the **Chaos Mesh dashboard**, all in namespace `chaos-lab` and targeting label `app: api`:

| Experiment | Dashboard settings | Self-healing mechanism observed |
|---|---|---|
| Pod kill | Pod Fault -> Pod Kill, mode *Random One* | ReplicaSet creates a replacement pod |
| Memory stress | Stress Test -> Memory, 1 worker, 256MB, container `api` (limit 128Mi) | container OOMKilled, kubelet restarts it |
| CPU stress + HPA | Stress Test -> CPU, 2 workers, 90% load, container `api`, with `k8s/hpa.yaml` applied | HPA scales 1 -> 4 replicas, back to 1 after the 5 min stabilisation window |

During each experiment:
```bash
kubectl -n chaos-lab get pods -o wide -w                      # pod state changes
./scripts/measure.sh results/manual/<run-name>.log            # user-visible impact
tail -f results/manual/<run-name>.log
kubectl -n chaos-lab get hpa -w                               # HPA experiment only
```

## Notes
- The DB password `secret` and the Grafana login `admin` / `admin` (set in `monitoring/values.yaml`) are demo values for a throwaway local cluster. Don't reuse them.
- PostgreSQL is a single replica with no persistent volume. Data is lost if its pod restarts.
- `--kubelet-insecure-tls` is only acceptable because kind's kubelets use self-signed certificates in a local lab.
