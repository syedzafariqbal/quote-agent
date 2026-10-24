# Run from the repo root. On Windows use WSL2 (recommended) or Git Bash.
SHELL := /bin/bash
ENV   ?= dev
TF_DIR := infra/envs/$(ENV)
REGION ?= eu-central-1
PY := .venv/bin/python
TAG ?= $(shell git rev-parse --short=12 HEAD 2>/dev/null || echo local)

.PHONY: venv test lint fmt pdf pdf-docker tf-check init plan apply destroy ecr-login build push outputs env evals

venv:            ## create .venv with dev dependencies
	python3.12 -m venv .venv || python3 -m venv .venv
	$(PY) -m pip install -U pip
	$(PY) -m pip install -r app/requirements-dev.txt

test:            ## unit + integration tests (moto, no AWS needed)
	cd app && ../$(PY) -m pytest -q

lint:
	.venv/bin/ruff check app/src app/tests scripts evals --config app/pyproject.toml

fmt:
	.venv/bin/ruff format app/src app/tests scripts evals --config app/pyproject.toml
	terraform fmt -recursive infra

pdf:             ## render out/quote.pdf locally (needs WeasyPrint system libs)
	$(PY) scripts/local_quote.py "Acme Logistics GmbH" --employees 120 --term 24 --travel-km 180

pdf-docker:      ## same, inside the worker image (no local system libs needed)
	docker build -t quote-agent-worker:local app
	docker run --rm --user "$$(id -u):$$(id -g)" -v "$$PWD:/repo" -w /repo -e PYTHONPATH=/app quote-agent-worker:local \
	  python scripts/local_quote.py "Acme Logistics GmbH" --employees 120 --term 24 --lang de

tf-check:
	terraform fmt -recursive -check infra
	cd $(TF_DIR) && terraform init -backend=false -input=false >/dev/null && terraform validate

init:
	cd $(TF_DIR) && terraform init -backend-config=backend.hcl

plan:
	cd $(TF_DIR) && terraform plan -var="worker_image_tag=$(TAG)" -out=tfplan

apply:
	cd $(TF_DIR) && terraform apply tfplan

destroy:
	cd $(TF_DIR) && terraform destroy

outputs:
	cd $(TF_DIR) && terraform output

ecr-login:
	aws ecr get-login-password --region $(REGION) | docker login --username AWS --password-stdin \
	  $$(cd $(TF_DIR) && terraform output -raw ecr_repository_url | cut -d/ -f1)

build:
	docker build --platform linux/amd64 -t quote-agent-worker:$(TAG) app

push: ecr-login build   ## build and push the worker image tagged with the git SHA
	REPO=$$(cd $(TF_DIR) && terraform output -raw ecr_repository_url); \
	docker tag quote-agent-worker:$(TAG) $$REPO:$(TAG) && docker push $$REPO:$(TAG)

env:             ## write .env from terraform outputs (for running the worker / evals locally)
	./scripts/write_env.sh $(ENV)

evals:
	$(PY) evals/run_evals.py
