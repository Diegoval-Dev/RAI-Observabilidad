---
name: gcp-observability-lab
description: Use when working on the serverless observable API lab on GCP - Cloud Run functions backend with structured JSON logs, Terraform infra, traffic simulator, log-based metrics, Log Analytics SQL and a Cloud Monitoring dashboard. Full spec in docs/laboratorio.md.
---

# GCP Observability Lab

Full lab spec: `docs/laboratorio.md`. Read it before changing any file.

## Layout

- `infra/`: Terraform (google >= 6.0, archive >= 2.4) deploying a Cloud Run function (gen2) plus Log Analytics on the `_Default` bucket.
- `backend/main.py`: Python HTTP API, entry point `handle`, at least 3 operations.
- `simulator/simulator.py` and `config.json`: one-hour load generator, writes `requests.csv`.

## Backend contract

- Status codes: 400 invalid payload, 404 missing resource, 409 impossible state change, 500 simulated failure.
- Env vars: `FAILURE_RATE` (0-1 probability of 500), `EXTRA_LATENCY_MS`.
- One JSON line on stdout per finished request with: `severity`, `message="request_completed"`, `request_id` (from `X-Request-ID`), `endpoint`, `status_code`, `outcome` (`success|client_error|server_error`), `error_type`, `latency_ms`, plus at least two business attributes.
- Cloud Logging parses stdout JSON into `jsonPayload`; keep `severity` uppercase (INFO, WARNING, ERROR).

## Simulator contract

- Send times drawn from N(mu_min, sigma_min), truncated to [0, 60] minutes, fired concurrently without waiting for the previous request.
- `invalid_share` of requests are intentional errors (invalid payload, unknown ID, impossible transition).
- Scenarios from config (e.g. `double_errors` for a category between `start_min` and `end_min`).
- Unique `X-Request-ID` per request; `requests.csv` has one row per request: request_id, scheduled time, sent time, operation, intended event, response code, client latency.

## Workflow

1. `gcloud auth application-default login`
2. From `infra/`: fill `terraform.tfvars`, `terraform init`, `terraform apply` (if the build fails, wait a minute and retry).
3. Put `function_url` output into `simulator/config.json` as `target_url`.
4. Create log-based metrics BEFORE running the simulator (they do not count past logs).
5. Run the simulator, then build one Monitoring dashboard: native function metrics, log-based metrics, Log Analytics SQL charts, one logs panel.
6. `terraform destroy` when done. Enabling Log Analytics on `_Default` is permanent.

## Log Analytics

- Table: `` `PROJECT_ID.global._Default._Default` ``.
- Fields live under `json_payload`; use `JSON_VALUE(json_payload.endpoint)` and `SAFE_CAST(JSON_VALUE(json_payload.latency_ms) AS FLOAT64)` or equivalent.
- Example: error rate per minute by endpoint, p95 latency by category, errors by `error_type`.

## Related skills

Use `cloud-logging-query-generation`, `cloud-logging-configuration-basics`, `cloud-monitoring-chart-generation`, `cloud-monitoring-metric-selection`, `cloud-monitoring-promql-query`, `cloud-run-basics`, `gcloud` and `google-cloud-recipe-auth` for the GCP details, and `brainstorming`, `writing-plans`, `test-driven-development`, `verification-before-completion` for the process.

## Cost and safety

- `allow_unauthenticated = true` makes the function public; destroy the stack after class.
- Never commit real project IDs, service account keys or `.build/`, `.terraform/`, `*.tfstate`.
