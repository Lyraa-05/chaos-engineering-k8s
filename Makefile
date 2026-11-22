# Chaos engineering lab: Kubernetes self-healing under Chaos Mesh
# Quick start:  make fix-inotify && make all && make status
CLUSTER   := chaos-lab
NS        := chaos-lab
RUN       ?= 1
REPLICAS  ?= 1

.PHONY: help fix-inotify up down images deploy monitoring metrics-server chaos-install all status \
        compose-test grafana chaos-dashboard chaos-token watch \
        podkill delay stress-cpu stress-mem hpa-on hpa-off stress-cpu-hpa run-all summarise report

help:            ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

fix-inotify:     ## raise + persist inotify limits (needs sudo; fixes Chaos Mesh "too many open files")
	./scripts/fix-inotify.sh

up:              ## create kind cluster (1 control-plane + 2 workers, host :8080 -> NodePort 30080)
	kind create cluster --name $(CLUSTER) --config kind-config.yaml
	kubectl get nodes -o wide

down:            ## delete the kind cluster
	kind delete cluster --name $(CLUSTER)

images:          ## build app images and load them into the kind nodes
	docker build -t chaos-api:v1 app/api
	docker build -t chaos-frontend:v1 app/frontend
	kind load docker-image chaos-api:v1 chaos-frontend:v1 --name $(CLUSTER)

deploy:          ## deploy db, api, frontend into namespace chaos-lab
	kubectl apply -f k8s/namespace.yaml
	kubectl apply -n $(NS) -f k8s/db.yaml -f k8s/api.yaml -f k8s/frontend.yaml
	kubectl -n $(NS) rollout status deploy/db deploy/api deploy/frontend --timeout=180s
	curl -s localhost:8080/api/visit; echo

monitoring:      ## kube-prometheus-stack (5s scrape) + custom Chaos Lab Grafana dashboard
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
	helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
	  -n monitoring --create-namespace -f monitoring/values.yaml --wait --timeout 10m
	kubectl -n monitoring create configmap chaos-lab-dashboard \
	  --from-file=monitoring/dashboards/chaos-lab.json --dry-run=client -o yaml \
	  | kubectl label --local -f - grafana_dashboard=1 -o yaml | kubectl apply -f -

metrics-server:  ## metrics-server (needed by HPA; kind kubelets use self-signed certs)
	helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/ --force-update
	helm upgrade --install metrics-server metrics-server/metrics-server -n kube-system \
	  --set 'args={--kubelet-insecure-tls}' --wait

chaos-install:   ## Chaos Mesh for containerd (kind) + dashboard RBAC
	helm repo add chaos-mesh https://charts.chaos-mesh.org --force-update
	helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh -n chaos-mesh --create-namespace \
	  --set chaosDaemon.runtime=containerd \
	  --set chaosDaemon.socketPath=/run/containerd/containerd.sock \
	  --set controllerManager.replicaCount=1 --wait --timeout 10m
	kubectl apply -f k8s/chaos-dashboard-rbac.yaml

all: up images deploy monitoring metrics-server chaos-install status  ## build everything from scratch

status:          ## show nodes and all pods
	kubectl get nodes
	kubectl get pods -A -o wide

compose-test:    ## run the same app with Docker Compose on :8081
	cd app && docker compose up -d --build && sleep 5 && curl -s localhost:8081/api/visit; echo

grafana:         ## port-forward Grafana to http://localhost:3000 (admin / admin)
	kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80

chaos-dashboard: ## port-forward Chaos Mesh dashboard to http://localhost:2333
	kubectl -n chaos-mesh port-forward svc/chaos-dashboard 2333:2333

chaos-token:     ## print a token for logging in to the Chaos Mesh dashboard
	kubectl -n $(NS) create token account-chaos-lab-manager-fsvkt

watch:           ## live pod view for demos
	kubectl -n $(NS) get pods -o wide -w

podkill:         ## one pod-kill run:   make podkill REPLICAS=3 RUN=2
	./scripts/run-experiment.sh podkill chaos/pod-kill.yaml $(REPLICAS) $(RUN) 60

delay:           ## one delay run:      make delay DELAY=300 REPLICAS=1 RUN=1
	./scripts/run-experiment.sh delay$(DELAY)ms chaos/network-delay-$(DELAY)ms.yaml $(REPLICAS) $(RUN) 150

stress-mem:      ## memory stress run (256MB vs 128Mi limit)
	./scripts/run-experiment.sh stressmem chaos/stress-memory.yaml $(REPLICAS) $(RUN) 150

stress-cpu:      ## CPU stress run without HPA
	./scripts/run-experiment.sh stresscpu chaos/stress-cpu.yaml $(REPLICAS) $(RUN) 210

hpa-on:          ## apply the HPA (only for the HPA experiment)
	kubectl apply -n $(NS) -f k8s/hpa.yaml

hpa-off:         ## remove the HPA and return to 1 replica
	kubectl delete -n $(NS) -f k8s/hpa.yaml --ignore-not-found
	kubectl -n $(NS) scale deploy/api deploy/frontend --replicas=1

stress-cpu-hpa:  ## CPU stress run with HPA (run make hpa-on first, wait ~60s)
	KEEP_HPA=1 ./scripts/run-experiment.sh stresscpu-hpa chaos/stress-cpu.yaml 1 $(RUN) 210

run-all:         ## full experiment matrix (~75 min) then summarise
	./scripts/run-all.sh

summarise:       ## results/raw -> results/summary.csv + results/figures/*.png
	python3 scripts/summarise.py

report:          ## build docs/report/report.docx from report.md (needs pandoc)
	cd docs/report && pandoc report.md -o report.docx --number-sections \
	  --resource-path=.:../..
