# Quote Agent: AI sales quotations on AWS (Terraform)

Enter a client name. The agent resolves the company with Amazon Location Service, estimates the
headcount with an LLM on Amazon Bedrock (or uses your confirmed number), prices the services from
a versioned catalog with deterministic code, renders a PDF, and runs the quote through a full
lifecycle: review, approval, sending, customer acceptance, expiry and an audit trail.

No third-party SaaS: only AWS, a Bedrock LLM and GitHub Actions. Target cost: **~10–30 USD/month**
at ~200 quotes/month (estimate; check the AWS Pricing Calculator for your usage).

```
Cognito (JWT) ─► API Gateway HTTP API ─► Lambda "api" ──► SQS (+DLQ) ──► ECS Fargate Spot worker (0..N)
                         │                    │                              │  Location Places/Routes
       customer link ────┘ (HMAC-signed)      │                              │  Bedrock (estimate + summary)
                                              │                              │  pricing engine (code) ─ S3 catalog
                                              ▼                              ▼
                      DynamoDB (quotes, versions, audit) ◄──────── PDF ─► S3 (quotes bucket)
                         │ stream ─► Lambda "archiver" ─► S3 audit bucket (Object Lock in prod)
                      EventBridge bus ─► CloudWatch Logs (analytics), SNS (failures)
                      EventBridge Scheduler ─► Lambda "scheduler" (reminders, expiry)
                      AWS Budgets ─► alerts + auto-deny Bedrock at 100 %
GitHub Actions (OIDC) ─► tests · checkov · terraform plan/apply · docker push · LLM evals · cost report
```

## Contents

1. [Repository layout](#1-repository-layout)
2. [Install the tools](#2-install-the-tools)
3. [Configure VS Code](#3-configure-vs-code)
4. [Run everything locally first (no AWS)](#4-run-everything-locally-first-no-aws)
5. [Prepare the AWS account](#5-prepare-the-aws-account)
6. [Terraform step 1: bootstrap (state + GitHub OIDC)](#6-terraform-step-1-bootstrap)
7. [Terraform step 2: deploy the dev stack](#7-terraform-step-2-deploy-the-dev-stack)
8. [Create users and walk through the lifecycle](#8-create-users-and-walk-through-the-lifecycle)
9. [GitHub Actions CI/CD](#9-github-actions-cicd)
10. [Operating the system](#10-operating-the-system)
11. [Quote lifecycle reference](#11-quote-lifecycle-reference)
12. [Cost notes and known limits](#12-cost-notes-and-known-limits)
13. [Next steps](#13-next-steps)

---

## 1. Repository layout

```
quote-agent/
├── app/
│   ├── Dockerfile                  worker image (Python 3.12 + WeasyPrint)
│   ├── pyproject.toml              ruff + pytest config
│   ├── requirements-*.txt
│   ├── src/
│   │   ├── quote_core/             shared domain code
│   │   │   ├── catalog.py          load + validate the price list
│   │   │   ├── pricing.py          deterministic pricing engine (tiers, bundles, VAT, approval level)
│   │   │   ├── lifecycle.py        state machine + DynamoDB conditional writes + audit
│   │   │   ├── estimator.py        headcount: user > actual > cache > Bedrock (forced tool call)
│   │   │   ├── location.py         Location Service Places v2 / Routes v2
│   │   │   ├── writer.py           short PDF summary (small model, no numbers)
│   │   │   ├── pdf.py + templates/ HTML → PDF (EN/DE)
│   │   │   ├── signing.py          HMAC customer links
│   │   │   └── notify.py, events.py, serde.py, config.py
│   │   ├── worker/                 ECS worker: SQS loop + agent pipeline
│   │   └── handlers/               Lambdas: api, scheduler, audit_archiver
│   └── tests/                      pytest (moto) – no AWS account needed
├── catalog/catalog.v2026-10.json   the price list
├── evals/                          golden set + LLM eval runner
├── infra/
│   ├── bootstrap/                  state bucket + GitHub OIDC role (run once)
│   ├── modules/                    network, data, messaging, auth, api, worker, observability
│   └── envs/dev/                   root module for the dev environment
├── scripts/                        local PDF, users, tokens, .env, DLQ redrive
├── .github/workflows/              ci, deploy, evals, cost-control
├── .vscode/                        extensions, settings, launch, tasks
├── requests.http                   REST Client walkthrough of the whole lifecycle
└── Makefile
```

## 2. Install the tools

| Tool | Version | Notes |
|---|---|---|
| VS Code | latest | |
| Git | any | |
| Python | 3.12 | matches the Lambda runtime and the container |
| Docker Desktop | latest | builds the worker image |
| Terraform | ≥ 1.10 | 1.10+ is needed for S3-native state locking |
| AWS CLI | v2 | |
| GNU make | any | preinstalled on macOS/Linux |

**Windows:** use **WSL2 (Ubuntu)** and the VS Code *WSL* extension. Clone the repo inside WSL
(`~/code/quote-agent`), then `code .` from the WSL terminal. Everything below then works unchanged.
(Native Windows works for Terraform, but WeasyPrint, `make` and the shell scripts are painful there.)

macOS (Homebrew):

```bash
brew install python@3.12 terraform awscli
brew install pango                       # WeasyPrint system library, for local PDF rendering
```

Ubuntu / WSL:

```bash
sudo apt update && sudo apt install -y python3.12 python3.12-venv make unzip \
  libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core
# Terraform: https://developer.hashicorp.com/terraform/install (apt repo)
# AWS CLI v2: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html
```

Check:

```bash
python3.12 --version && terraform -version && aws --version && docker --version
```

## 3. Configure VS Code

1. **Open the folder**: `File → Open Folder… → quote-agent` (WSL: `code .` inside WSL).
2. **Install the recommended extensions**: VS Code shows *"This workspace has extension
   recommendations"* → **Install All**. (Or `Ctrl/Cmd+Shift+P → Extensions: Show Recommended
   Extensions`.) They come from `.vscode/extensions.json`:
   - HashiCorp Terraform (syntax, validation, format on save)
   - Python, Pylance, Python Debugger, Ruff (lint + format on save)
   - Docker, AWS Toolkit, REST Client, GitHub Actions, YAML, EditorConfig, PDF viewer
3. **Create the virtual environment** in the integrated terminal (`` Ctrl+` ``):

   ```bash
   make venv
   ```

4. **Select the interpreter**: `Ctrl/Cmd+Shift+P → Python: Select Interpreter → ./.venv/bin/python`
   (already the default via `.vscode/settings.json`).
5. **Run the tests from the Testing panel** (beaker icon): it discovers `app/tests` automatically.
   Or run the task: `Ctrl/Cmd+Shift+P → Tasks: Run Test Task`.
6. **Tasks** (`Terminal → Run Task…`): `test`, `lint`, `pdf: local sample`, `tf: fmt + validate`,
   `tf: plan (dev)`, `tf: apply (dev)`, `docker: build + push worker`, `cognito: get token`.
7. **Debug configurations** (Run and Debug view, `F5`):
   - *Render quote PDF locally (no AWS)*: set breakpoints in `pricing.py` or `pdf.py`.
   - *Worker against dev AWS*: runs the real SQS loop on your laptop, using `.env`.
   - *Run LLM evals*, *Debug current test file*.
8. **AWS Toolkit**: click the AWS icon → *Connect* → choose your SSO profile (step 5). You can then
   browse the DynamoDB table, S3 buckets, Lambda logs and ECS service from the sidebar.
9. **Terraform language server**: open any `.tf` file; it indexes the modules. Saving formats the
   file. After `terraform init`, hover/complete works for AWS resources.

## 4. Run everything locally first (no AWS)

```bash
make test          # 41 tests: pricing, state machine, DynamoDB conditional writes, pipeline, full API lifecycle (moto)
make lint
make pdf           # → out/quote.pdf  (open it in VS Code with the PDF extension)
# no local WeasyPrint libs? render inside the worker container instead:
make pdf-docker
```

Try variations:

```bash
.venv/bin/python scripts/local_quote.py "Müller Maschinenbau GmbH" --employees 320 \
  --lang de --country AT --vat-id ATU12345678 --term 36 --discount 12
```

The JSON printed next to the PDF shows `approval` (none/manager/finance), driven by the catalog
rules. Edit `catalog/catalog.v2026-10.json` and re-run to see pricing change; the validator rejects
gaps between tiers, unknown SKUs and broken `requires`/`excludes` references.

## 5. Prepare the AWS account

A **dedicated AWS account** for this project is strongly recommended (clean budget, easy teardown).

### 5.1 CLI credentials (IAM Identity Center / SSO)

```bash
aws configure sso
#   SSO session name: quote-agent
#   SSO start URL:    https://<your-org>.awsapps.com/start
#   SSO region:       eu-central-1
#   pick the account + an admin permission set
#   CLI default region: eu-central-1
#   CLI profile name:  quote-agent-dev
aws sso login --profile quote-agent-dev
export AWS_PROFILE=quote-agent-dev
aws sts get-caller-identity
```

(Without Identity Center, `aws configure --profile quote-agent-dev` with an IAM user's access key
also works for the one-time bootstrap. Do not create long-lived keys for CI; it uses OIDC.)

### 5.2 Bedrock model access

1. Console → **Amazon Bedrock** (region **eu-central-1**) → *Model catalog*.
2. Pick a capable model for estimation and a small one for the summary text. For Anthropic models
   you may be asked once to submit a short use-case form.
3. Copy the **model ID or the cross-region inference profile ID** (often starting with `eu.`).
   Test it in the *Playground*, or:

   ```bash
   aws bedrock-runtime converse --model-id <ID> \
     --messages '[{"role":"user","content":[{"text":"Say OK"}]}]'
   ```

### 5.3 SES (email)

1. Console → **Amazon SES** → *Identities* → *Create identity* → Email address → your sender address.
   (Terraform also creates it if `sender_email` is set; you still click the verification email.)
2. SES starts in the **sandbox**: recipients must be verified too. Verify your own test addresses
   (rep, manager, "customer"). Request production access when you go live.
3. Email is optional: leave `sender_email = ""` and the system logs instead of emailing; the
   `send` API response still contains the customer link.

### 5.4 Location Service

Nothing to create: Places v2 and Routes v2 are called directly and authorised by IAM.

## 6. Terraform step 1: bootstrap

Creates the S3 state bucket (versioned, encrypted, with S3-native locking) and the IAM role GitHub
Actions assumes through OIDC. Run once per account, with your admin profile.

```bash
cd infra/bootstrap
cp terraform.tfvars.example terraform.tfvars     # set github_owner / github_repo
terraform init
terraform apply
terraform output                                 # note state_bucket and gha_role_arn
```

The bootstrap state is local (`infra/bootstrap/terraform.tfstate`, git-ignored). Keep a copy safe,
or migrate it into the new bucket later with a `backend "s3"` block and `terraform init -migrate-state`.

If the account already has a GitHub OIDC provider, set `create_github_oidc_provider = false`.

## 7. Terraform step 2: deploy the dev stack

```bash
cd infra/envs/dev
cp backend.hcl.example backend.hcl               # bucket = <state_bucket output>
cp terraform.tfvars.example terraform.tfvars     # emails + Bedrock model IDs
terraform init -backend-config=backend.hcl
```

**First deploy (chicken-and-egg with the image):** the ECR repository must exist before you can push
the worker image. The service starts at 0 tasks, so a placeholder tag is fine on the first apply.

```bash
terraform plan  -out=tfplan                      # worker_image_tag defaults to "bootstrap"
terraform apply tfplan                           # ~5 min; creates everything
cd ../../..
make push                                        # builds app/Dockerfile, tags with git SHA, pushes
make plan && make apply                          # points the task definition at that SHA
```

(Or let GitHub Actions do all of this on push to `main`, section 9.)

Outputs you will use:

```bash
make outputs
# api_endpoint, user_pool_id, app_client_id, ecr_repository_url, queue_url, dlq_url, table_name, dashboard ...
```

**Confirm the SNS subscription** email ("AWS Notification – Subscription Confirmation") so budget
and DLQ alerts reach you.

What Terraform created, by module:

| Module | Resources |
|---|---|
| network | VPC, 2 public subnets, IGW, S3 + DynamoDB gateway endpoints (free), worker SG (egress 443 only). **No NAT gateway.** |
| data | catalog / quotes / audit buckets (encrypted, TLS-only, lifecycle), catalog upload, DynamoDB single table + GSI + stream + TTL |
| messaging | SQS queue + DLQ, EventBridge bus + rules (→ Logs, failures → SNS), Scheduler group, SNS alerts |
| auth | Cognito user pool, app client, groups SalesRep / Manager / Finance / Admin |
| api | 3 Lambdas (arm64), HTTP API + JWT authorizer, public routes, SSM link secret, scheduler role, stream mapping |
| worker | ECR, ECS cluster, Fargate Spot service (0..3), backlog-based step scaling, least-privilege task role |
| observability | Budget (50/80/100 % + forecast), auto-deny-Bedrock budget action, DLQ + Lambda alarms, dashboard |

## 8. Create users and walk through the lifecycle

```bash
./scripts/create_user.sh rep@example.com SalesRep
./scripts/create_user.sh manager@example.com Manager
./scripts/get_token.sh rep@example.com           # prints an ID token (valid 60 min)
```

In VS Code:

1. Put `api_endpoint` and the token into `.vscode/settings.json` →
   `rest-client.environmentVariables.dev`.
2. Open `requests.http`, switch environment with `Ctrl/Cmd+Alt+E` → `dev`.
3. Click **Send Request** on *Create a quote* → copy `quoteId` into `@quoteId`.
4. Poll *Get quote*. The first request after the system was idle waits for a worker cold start
   (SQS metrics + Fargate start: typically a few minutes; see section 12).
5. Follow the blocks: confirm (if `NEEDS_REVIEW`), revise, approve with a **manager** token, send,
   open `customerLink` in a browser, accept, record the actual headcount, read the audit trail.

The same with curl:

```bash
API=$(cd infra/envs/dev && terraform output -raw api_endpoint)
TOKEN=$(./scripts/get_token.sh rep@example.com)
curl -s -X POST "$API/quotes" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"clientName":"Acme Logistics GmbH","country":"DE","termMonths":24}'
```

## 9. GitHub Actions CI/CD

1. Create the repository and push:

   ```bash
   git init && git add . && git commit -m "Quote agent" && git branch -M main
   git remote add origin git@github.com:<owner>/quote-agent.git && git push -u origin main
   ```

2. **Settings → Environments → New environment `dev`** (optionally add yourself as required reviewer
   to approve deploys).
3. **Settings → Secrets and variables → Actions → Variables** (no secrets needed thanks to OIDC):

   | Variable | Value |
   |---|---|
   | `AWS_ROLE_ARN` | `gha_role_arn` output of the bootstrap |
   | `AWS_REGION` | `eu-central-1` |
   | `TF_STATE_BUCKET` | `state_bucket` output of the bootstrap |
   | `ALERT_EMAIL`, `SENDER_EMAIL`, `APPROVER_EMAIL` | as in tfvars |
   | `ESTIMATOR_MODEL_ID`, `WRITER_MODEL_ID` | Bedrock IDs |
   | `EVAL_MODEL_ID` | optional cheaper model for evals |
   | `NIGHTLY_TEARDOWN` | `true` to destroy dev every night at 21:00 UTC |

4. Workflows:

   | Workflow | Trigger | What it does |
   |---|---|---|
   | `ci.yml` | PR, push | ruff, pytest, sample PDF artifact, `terraform fmt`/`validate`, Checkov, `terraform plan` summary on PRs |
   | `deploy.yml` | push to `main` | ensure ECR → build + push image (tag = SHA) → `terraform apply` → smoke test (expects 401 without token) |
   | `evals.yml` | PR touching the estimator or golden set | runs `evals/run_evals.py` against Bedrock, fails under 85 % |
   | `cost-control.yml` | weekly / nightly | month-to-date cost by service in the job summary; optional dev teardown |

Pin third-party actions to commit SHAs for production use.

## 10. Operating the system

- **Logs**: CloudWatch → `/ecs/quote-agent-dev-worker`, `/aws/lambda/quote-agent-dev-api`, and
  `/aws/events/quote-agent-dev-quotes` (every lifecycle event). Logs Insights example:

  ```
  fields @timestamp, `detail-type`, detail.quoteId, detail.actor
  | sort @timestamp desc | limit 50
  ```

- **Dashboard**: CloudWatch → Dashboards → `quote-agent-dev-quotes`.
- **Failed quotes**: after 3 attempts a quote moves to `FAILED` and the message goes to the DLQ (alarm
  emails you). Fix the cause, then `POST /quotes/{id}/revise` with `{}` to retry, or
  `./scripts/redrive_dlq.sh`.
- **New price list**: add `catalog/catalog.v2026-11.json`, set `catalog_file` in tfvars, apply. Old
  quotes keep their `catalog_version`; only new versions use the new list.
- **Run the worker locally** against dev (debugging): `make env` writes `.env`, then F5 *Worker
  against dev AWS*. It competes with ECS for messages, which is fine in dev.
- **Budget hit**: at 100 % the budget action attaches `quote-agent-dev-deny-bedrock` to the worker
  role; quotes fail instead of spending. Detach the policy (IAM console) to resume.
- **Teardown**: `make destroy` (dev buckets/ECR are force-deleted; Cognito users are lost).

## 11. Quote lifecycle reference

```
REQUESTED → PROCESSING ─┬─► NEEDS_REVIEW ──(confirm)──► PROCESSING
                        ├─► DRAFT ─────────────(send)──────────────► SENT ─► VIEWED ─► ACCEPTED
                        ├─► PENDING_APPROVAL ─► APPROVED ─(send)──►  │         │    ─► REJECTED
                        │        └─(request changes)─► DRAFT          └─────────┴──► EXPIRED (scheduler)
                        └─► FAILED (after 3 attempts)
revise: NEEDS_REVIEW | DRAFT | PENDING_APPROVAL | APPROVED | SENT | VIEWED | FAILED → PROCESSING → new version
cancel: any open state → CANCELLED
```

| Rule | Where it is enforced |
|---|---|
| Only listed `(from, to)` pairs, only by allowed roles | `lifecycle.TRANSITIONS` + `check_transition()` |
| No lost updates / double processing | DynamoDB `ConditionExpression: status = :from` inside a transaction with the audit write |
| Every change audited | audit item in the same transaction; stream copies it to the audit bucket |
| Versions are immutable | `V#00n` items written with `attribute_not_exists`; PDF SHA-256 stored |
| Old links stop working after a revision | previous version gets `supersededBy`; public page returns 410 |
| Approval level from discount and net total | `pricing.approval_level()` + catalog `approval` rules |
| Approvers cannot approve their own quote | `handlers/api.py: approve()` |
| DRAFT can be sent only if no approval is needed | `send_quote()` guard |
| Expiry and reminders | one-time EventBridge Scheduler entries; each re-checks status and version |
| Customer acceptance evidence | name, email, IP, user agent, time, version, PDF hash stored on the quote and in the audit |

Customer acceptance here is a simple electronic acceptance, not a qualified electronic signature.
Check with legal that it suffices for your contract values. VAT handling is simplified: domestic VAT
unless an EU B2B reverse charge applies (VAT ID format check only, no VIES lookup).

## 12. Cost notes and known limits

- **Scale to zero means cold starts.** SQS publishes metrics about once a minute (slower when a queue
  was idle), then Fargate needs ~1 minute to start. Expect the first quote after idle to take a few
  minutes. Set `worker_min_tasks = 1` to keep one Spot task warm (a few USD/month).
- **Fargate Spot** can be interrupted; the worker finishes the in-flight message within the 2-minute
  notice, and SQS retries anything unfinished.
- **No NAT gateway**: tasks use public IPs with an inbound-deny security group. If your policy demands
  private subnets, add a NAT instance or interface endpoints (each endpoint costs ~7–8 USD/month/AZ).
- **Headcount quality**: without paid data, the LLM is a prior, not a source. Low confidence routes to
  `NEEDS_REVIEW`; recorded actuals override future estimates for the same company. Replace
  `evals/golden.jsonl` with companies whose headcount you have verified.
- **Bedrock model IDs** change over time; always copy them from your console.
- **Biggest cost drivers**: Bedrock tokens (bounded by caching + small summary model), CloudWatch logs
  (14-day retention), Fargate time (scale to zero).

## 13. Next steps

- Static frontend (S3 + CloudFront) using Cognito hosted UI; then disable `USER_PASSWORD_AUTH`.
- Step Functions orchestration with `waitForTaskToken` approvals (replace the DB-only approval gate).
- WebSocket progress updates, bulk CSV upload, SES email intake.
- `envs/prod` (copy `envs/dev`, set `dev_mode = false`, `enable_object_lock = true`) in a separate account.
- Athena over exported events for win-rate, discount-vs-conversion and estimate-accuracy reports.
