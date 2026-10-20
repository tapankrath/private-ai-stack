# Private local AI stack. Start with `make advise`.
PROFILE ?= profile.yaml
OUT     ?= generated
PY      ?= python3
COMPOSE  = docker compose --env-file $(OUT)/.env -f docker-compose.yml -f $(OUT)/docker-compose.override.yml

.PHONY: help install advise wizard detect explain secrets intake pull pull-native up down egress-test test ui-token ingest eval backup restore-drill

help:
	@echo "make wizard       answer a few questions -> profile.yaml"
	@echo "make intake       same, as a chat with a LOCAL model (needs Ollama running)"
	@echo "make advise       recommend the smallest setup and generate config ($(OUT)/)"
	@echo "make secrets      create local secrets (never committed)"
	@echo "make pull         download ONLY the recommended models (the only online step)"
	@echo "make up           start only the recommended services"
	@echo "make egress-test  prove containers cannot reach the internet"
	@echo "make ui-token     token for the Gateway web UI (https://localhost:8443/gateway/ui)"
	@echo "make ingest       index config/collections.yaml folders once (the worker also watches)"
	@echo "make test         run advisor tests"

install:
	$(PY) -m pip install -e ".[dev]"

wizard:
	$(PY) -m advisor wizard --output $(PROFILE)

advise:
	@test -f $(PROFILE) || (echo "No $(PROFILE). Run 'make wizard' or copy one from profiles/." && exit 1)
	$(PY) -m advisor run --profile $(PROFILE) --out $(OUT)
	@cp $(OUT)/.env .env
	@cp $(OUT)/.env.gateway .env.gateway

detect:
	$(PY) -m advisor detect

explain:
	$(PY) -m advisor explain --out $(OUT)

intake:
	$(PY) -m advisor intake --output $(PROFILE)

secrets:
	@mkdir -p secrets && chmod 700 secrets
	@test -f secrets/postgres_password   || (openssl rand -hex 24 > secrets/postgres_password)
	@test -f secrets/gateway_db_password || (openssl rand -hex 24 > secrets/gateway_db_password)
	@test -f secrets/webui_secret_key    || (openssl rand -hex 32 > secrets/webui_secret_key)
	@test -f secrets/gateway_key         || (openssl rand -hex 32 > secrets/gateway_key)
	@test -f .env.secrets || ( \
	  echo "OPENAI_API_KEY=$$(cat secrets/gateway_key)" > .env.secrets; \
	  echo "DATABASE_URL=postgresql://assistant:$$(cat secrets/postgres_password)@postgres:5432/assistant" >> .env.secrets; \
	  echo "GATEWAY_DATABASE_URL=postgresql://gateway_app:$$(cat secrets/gateway_db_password)@postgres:5432/gateway" >> .env.secrets; \
	  echo "GATEWAY_DATABASE_ADMIN_URL=postgresql://assistant:$$(cat secrets/postgres_password)@postgres:5432/gateway" >> .env.secrets; \
	  echo "AUTHENTIK_SECRET_KEY=$$(openssl rand -hex 50)" >> .env.secrets; \
	  echo "AUTHENTIK_POSTGRESQL__PASSWORD=$$(cat secrets/postgres_password)" >> .env.secrets; \
	  chmod 600 .env.secrets )
	@chmod 600 secrets/*
	@echo "Secrets created in ./secrets and .env.secrets (never committed)."

pull:
	@grep -q '^# AIR-GAPPED' $(OUT)/models-ollama.txt && { echo "Air-gapped profile: import models from an offline drive instead."; exit 1; } || true
	$(COMPOSE) --profile pull run --rm model-pull

pull-native:   # macOS: Ollama runs on the host, not in Docker
	grep -v '^#' $(OUT)/models-ollama.txt | grep -v '^$$' | xargs -n1 ollama pull

up:
	$(COMPOSE) up -d
	@echo "Open https://localhost:8443 (local certificate; accept it once)."

down:
	$(COMPOSE) down

ui-token:      # personal access token for the Gateway web UI: make ui-token USER=owner
	$(COMPOSE) exec gateway python -m gateway token --user $(or $(USER_ID),owner)

ingest:        # index the folders in config/collections.yaml once
	$(COMPOSE) run --rm ingest-worker python -m gateway ingest --config /config/collections.yaml

eval:          # golden-set evaluation: make eval GOLDEN=config/golden.yaml MODEL="qwen3:8b + Recall"
	$(COMPOSE) exec gateway python -m gateway eval --golden /pins/../$(GOLDEN) --model "$(MODEL)" --url http://localhost:8080

backup:        # encrypted restic backup with rotation (RESTIC_REPOSITORY, RESTIC_PASSWORD_FILE)
	sudo -E ./scripts/backup.sh

restore-drill: # restore the latest backup into a scratch folder and verify it
	./scripts/restore-drill.sh

egress-test:
	./scripts/egress-test.sh

test:
	$(PY) -m pytest -q
