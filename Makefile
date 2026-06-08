KAFKA_NS ?= kafka
KAFKA_BOOTSTRAP ?= invest-kafka-kafka-bootstrap.kafka.svc:9092
SR_URL ?= http://schema-registry:8081
STRIMZI_VERSION ?= 1.0.0
CLUSTER ?= invest-flink

ALERT_ID ?=

# Path to a GCP service-account JSON for Vertex AI (Gemini) ADC; defaults to the operator's
# GOOGLE_APPLICATION_CREDENTIALS host env. When empty/missing, 'make secrets' skips the
# gcp-vertex-credentials Secret (non-fatal — Vertex agent stays unauthenticated in dev/CI).
GCP_SA_KEY_FILE ?= $(GOOGLE_APPLICATION_CREDENTIALS)

STRIMZI_DIR := infra/k8s/strimzi
OPERATOR_DIR := $(STRIMZI_DIR)/operator
INJECTORS_DIR := infra/k8s/injectors
FLINK_DIR := services/stream_detection_java/k8s

.DEFAULT_GOAL := help

.PHONY: help operators flink-operator infra-up topics secrets images apps flink schemas \
	wait-kafka wait-sr wait-postgres wait-apps wait-flink wait-infra wait \
	pf-sr pf-pg pf-alert inject-scripts inject-alert inject-tick down teardown-cluster

help:
	@echo "invest_view kind infra operations (context: kind-$(CLUSTER))"
	@echo "This Makefile NEVER creates/deletes the cluster except the explicit 'teardown-cluster'."
	@echo ""
	@echo "Bring-up:"
	@echo "  operators        Install Strimzi $(STRIMZI_VERSION) CRDs + cluster operator into ns $(KAFKA_NS)"
	@echo "  flink-operator   GUARDED helm install of the Flink operator (skips if already installed)"
	@echo "  infra-up         Apply Kafka cluster, topics, Schema Registry, Postgres"
	@echo "  topics           Apply KafkaTopic manifests"
	@echo "  secrets          Create/refresh app Secrets from root .env (values never committed)"
	@echo "  images           Build kis_ingestion + alert_service + tick_persistence + event_pattern_persistence :qa images and kind-load them"
	@echo "  apps             Ensure secrets+topics, apply all 4 workloads, then rollout-restart so freshly kind-loaded :qa images are picked up; depends on secrets+topics"
	@echo "  flink            Apply Flink checkpoint PVC + FlinkDeployments (stream-detection, stream-detection-echo)"
	@echo "  schemas          Register Avro subjects via a temporary schema-registry port-forward"
	@echo ""
	@echo "Waiters:"
	@echo "  wait-kafka wait-sr wait-postgres wait-apps wait-flink"
	@echo "  wait-infra       kafka + schema-registry + postgres"
	@echo "  wait             everything (infra + apps + flink)"
	@echo ""
	@echo "Port-forwards (BLOCKING / run in foreground):"
	@echo "  pf-sr            svc/schema-registry 8081:8081"
	@echo "  pf-pg            svc/postgres 5432:5432"
	@echo "  pf-alert         svc/alert-service 8000:8000"
	@echo ""
	@echo "Synthetic injectors (QA):"
	@echo "  inject-scripts   Create/update ConfigMap qa-injector-scripts"
	@echo "  inject-alert     One-shot Job: publish a synthetic StockAlert (override: make inject-alert ALERT_ID=<uuid>)"
	@echo "  inject-tick      One-shot Job: publish synthetic StockTicks"
	@echo ""
	@echo "Teardown:"
	@echo "  down             Delete apps + flink + infra; KEEPS cluster + operators (safe re-up)"
	@echo "  teardown-cluster DANGER: kind delete cluster --name $(CLUSTER) (explicit; never a dependency)"

operators:
	kubectl get ns $(KAFKA_NS) >/dev/null 2>&1 || kubectl create ns $(KAFKA_NS)
	kubectl apply -f $(OPERATOR_DIR)/strimzi-crds-$(STRIMZI_VERSION).yaml
	kubectl -n $(KAFKA_NS) apply -f $(OPERATOR_DIR)/strimzi-cluster-operator-$(STRIMZI_VERSION).yaml
	kubectl -n $(KAFKA_NS) rollout status deploy/strimzi-cluster-operator --timeout=300s

flink-operator:
	@if helm status flink-kubernetes-operator >/dev/null 2>&1; then \
		echo "flink-kubernetes-operator already installed; skipping (guarded target)"; \
	else \
		echo "installing flink-kubernetes-operator 1.14.0 (cert-manager is a prerequisite)..."; \
		helm repo list 2>/dev/null | grep -q '^flink-operator-repo' || helm repo add flink-operator-repo https://archive.apache.org/dist/flink/flink-kubernetes-operator-1.14.0/; \
		helm repo update flink-operator-repo >/dev/null; \
		kubectl get crd certificates.cert-manager.io >/dev/null 2>&1 || { kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.13.3/cert-manager.yaml; kubectl wait --for=condition=Available --timeout=120s -n cert-manager deployment/cert-manager-webhook; }; \
		helm install flink-kubernetes-operator flink-operator-repo/flink-kubernetes-operator --version 1.14.0 -n default; \
		kubectl wait --for=condition=Available --timeout=180s deployment/flink-kubernetes-operator; \
	fi

infra-up:
	kubectl apply -f $(STRIMZI_DIR)/kafka-cluster.yaml
	kubectl apply -f $(STRIMZI_DIR)/topics.yaml
	kubectl apply -f infra/k8s/schema-registry/schema-registry.yaml
	kubectl apply -f infra/k8s/postgres/postgres.yaml

topics:
	kubectl apply -f $(STRIMZI_DIR)/topics.yaml

secrets:
	@test -f .env || { echo "ERROR: .env not found at repo root (required for KIS credentials)"; exit 1; }
	@KIS_APP_KEY="$$(grep '^KIS_APP_KEY=' .env | cut -d= -f2-)"; \
	KIS_APP_SECRET="$$(grep '^KIS_APP_SECRET=' .env | cut -d= -f2-)"; \
	test -n "$$KIS_APP_KEY"    || { echo "ERROR: KIS_APP_KEY missing in .env"; exit 1; }; \
	test -n "$$KIS_APP_SECRET" || { echo "ERROR: KIS_APP_SECRET missing in .env"; exit 1; }; \
	kubectl create secret generic kis-credentials \
		--from-literal=KIS_APP_KEY="$$KIS_APP_KEY" \
		--from-literal=KIS_APP_SECRET="$$KIS_APP_SECRET" \
		--dry-run=client -o yaml | kubectl apply -f -; \
	kubectl create secret generic alert-service-secrets \
		--from-literal=ALERT_SERVICE_DATABASE_URL='postgresql+asyncpg://postgres:postgres@postgres:5432/invest_view' \
		--from-literal=ALERT_SERVICE_JWT_SECRET='dev-secret-change-me' \
		--dry-run=client -o yaml | kubectl apply -f -; \
	kubectl create secret generic tick-persistence-secrets \
		--from-literal=TICK_PERSISTENCE_DATABASE_URL='postgresql+asyncpg://postgres:postgres@postgres:5432/invest_view' \
		--dry-run=client -o yaml | kubectl apply -f -; \
	kubectl create secret generic event-pattern-persistence-secrets \
		--from-literal=EVENT_PATTERN_PERSISTENCE_DATABASE_URL='postgresql+asyncpg://postgres:postgres@postgres:5432/invest_view' \
		--dry-run=client -o yaml | kubectl apply -f -; \
	if [ -n "$(GCP_SA_KEY_FILE)" ] && [ -f "$(GCP_SA_KEY_FILE)" ]; then \
		kubectl create secret generic gcp-vertex-credentials \
			--from-file=key.json="$(GCP_SA_KEY_FILE)" \
			--dry-run=client -o yaml | kubectl apply -f - ; \
	else \
		echo "  (skip) GCP_SA_KEY_FILE not set/found — gcp-vertex-credentials secret not created (Vertex agent will be unauthenticated)"; \
	fi

images:
	docker build -f services/kis_ingestion/Dockerfile -t kis_ingestion:qa .
	docker build -f services/alert_service/Dockerfile -t alert_service:qa .
	docker build -f services/tick_persistence/Dockerfile -t tick_persistence:qa .
	docker build -f services/event_pattern_persistence/Dockerfile -t event_pattern_persistence:qa .
	kind load docker-image kis_ingestion:qa --name $(CLUSTER)
	kind load docker-image alert_service:qa --name $(CLUSTER)
	kind load docker-image tick_persistence:qa --name $(CLUSTER)
	kind load docker-image event_pattern_persistence:qa --name $(CLUSTER)

apps: secrets topics
	kubectl apply -f infra/k8s/alert-service-configmap.yaml
	kubectl apply -f infra/k8s/alert-service-service.yaml
	kubectl apply -f infra/k8s/alert-service-deployment.yaml
	kubectl apply -f infra/k8s/kis-ingestion-deployment.yaml
	kubectl apply -f infra/k8s/event-pattern-persistence-configmap.yaml
	kubectl apply -f infra/k8s/event-pattern-persistence-deployment.yaml
	kubectl apply -f infra/k8s/tick-persistence-configmap.yaml
	kubectl apply -f infra/k8s/tick-persistence-deployment.yaml
	@echo "force-restart so freshly kind-loaded :qa images are picked up (imagePullPolicy: Never + same tag => apply is a no-op)"
	kubectl rollout restart deploy/alert-service deploy/kis-ingestion deploy/event-pattern-persistence deploy/tick-persistence
	kubectl rollout status deploy/alert-service --timeout=300s
	kubectl rollout status deploy/event-pattern-persistence --timeout=300s
	kubectl rollout status deploy/tick-persistence --timeout=300s
	kubectl rollout status deploy/kis-ingestion --timeout=300s

flink:
	kubectl apply -f $(FLINK_DIR)/flink-checkpoint-pvc.yaml
	kubectl apply -f $(FLINK_DIR)/flinkdeployment.yaml
	kubectl apply -f $(FLINK_DIR)/flinkdeployment-echo.yaml

schemas:
	@echo "registering Avro subjects via temporary schema-registry port-forward (localhost:18081)..."
	@( kubectl port-forward svc/schema-registry 18081:8081 >/tmp/invest-schemas-pf.log 2>&1 & \
	   pf_pid=$$!; \
	   trap 'kill $$pf_pid 2>/dev/null || true' EXIT; \
	   sleep 4; \
	   uv run --project services/kis_ingestion python scripts/register_schemas.py --registry-url http://localhost:18081 --subject stock-ticks-value --schema-file schemas/stock-ticks.avsc; \
	   uv run --project services/kis_ingestion python scripts/register_schemas.py --registry-url http://localhost:18081 --subject stock-alerts-value --schema-file schemas/stock-alerts.avsc; \
	   uv run --project services/kis_ingestion python scripts/register_schemas.py --registry-url http://localhost:18081 --subject stock-patterns-value --schema-file schemas/stock-patterns.avsc )

wait-kafka:
	kubectl -n $(KAFKA_NS) wait kafka/invest-kafka --for=condition=Ready --timeout=600s

wait-sr:
	kubectl rollout status deploy/schema-registry --timeout=180s

wait-postgres:
	kubectl rollout status statefulset/postgres --timeout=180s

wait-apps:
	kubectl rollout status deploy/alert-service --timeout=300s
	kubectl rollout status deploy/kis-ingestion --timeout=300s
	kubectl rollout status deploy/event-pattern-persistence --timeout=300s
	kubectl rollout status deploy/tick-persistence --timeout=300s

wait-flink:
	kubectl wait --for=jsonpath='{.status.jobStatus.state}'=RUNNING flinkdeployment/stream-detection flinkdeployment/stream-detection-echo --timeout=600s

wait-infra: wait-kafka wait-sr wait-postgres
	@echo "wait-infra: kafka + schema-registry + postgres are ready"

wait: wait-infra wait-apps wait-flink
	@echo "wait: full stack ready (infra + apps + flink)"

pf-sr:
	kubectl port-forward svc/schema-registry 8081:8081

pf-pg:
	kubectl port-forward svc/postgres 5432:5432

pf-alert:
	kubectl port-forward svc/alert-service 8000:8000

inject-scripts:
	kubectl create configmap qa-injector-scripts \
		--from-file=scripts/fake_alert_generator.py \
		--from-file=scripts/fake_tick_generator.py \
		--dry-run=client -o yaml | kubectl apply -f -

inject-alert: inject-scripts
	@aid="$(ALERT_ID)"; \
	if [ -z "$$aid" ]; then aid="$$(uuidgen | tr 'A-Z' 'a-z')"; fi; \
	echo "inject-alert: ALERT_ID=$$aid"; \
	kubectl delete job alert-injector --ignore-not-found; \
	sed "s/__ALERT_ID__/$$aid/" $(INJECTORS_DIR)/alert-injector-job.yaml | kubectl apply -f -; \
	kubectl wait --for=condition=complete job/alert-injector --timeout=120s \
		|| { echo "=== alert-injector logs (did not complete) ==="; kubectl logs job/alert-injector; exit 1; }; \
	echo "=== alert-injector logs ==="; kubectl logs job/alert-injector

inject-tick: inject-scripts
	kubectl delete job tick-injector --ignore-not-found
	kubectl apply -f $(INJECTORS_DIR)/tick-injector-job.yaml
	@kubectl wait --for=condition=complete job/tick-injector --timeout=120s \
		|| { echo "=== tick-injector logs (did not complete) ==="; kubectl logs job/tick-injector; exit 1; }
	@echo "=== tick-injector logs ==="
	kubectl logs job/tick-injector

down:
	-kubectl delete -f infra/k8s/tick-persistence-deployment.yaml --ignore-not-found
	-kubectl delete -f infra/k8s/tick-persistence-configmap.yaml --ignore-not-found
	-kubectl delete -f infra/k8s/event-pattern-persistence-deployment.yaml --ignore-not-found
	-kubectl delete -f infra/k8s/event-pattern-persistence-configmap.yaml --ignore-not-found
	-kubectl delete -f infra/k8s/kis-ingestion-deployment.yaml --ignore-not-found
	-kubectl delete -f infra/k8s/alert-service-deployment.yaml --ignore-not-found
	-kubectl delete -f infra/k8s/alert-service-service.yaml --ignore-not-found
	-kubectl delete -f infra/k8s/alert-service-configmap.yaml --ignore-not-found
	-kubectl delete -f $(FLINK_DIR)/flinkdeployment.yaml --ignore-not-found
	-kubectl delete -f $(FLINK_DIR)/flinkdeployment-echo.yaml --ignore-not-found
	-kubectl delete -f infra/k8s/schema-registry/schema-registry.yaml --ignore-not-found
	-kubectl delete -f infra/k8s/postgres/postgres.yaml --ignore-not-found
	-kubectl delete -f $(STRIMZI_DIR)/topics.yaml --ignore-not-found
	-kubectl delete -f $(STRIMZI_DIR)/kafka-cluster.yaml --ignore-not-found

teardown-cluster:
	@echo "DANGER: deleting kind cluster '$(CLUSTER)' destroys EVERYTHING (cluster, operators, data)."
	kind delete cluster --name $(CLUSTER)
