# Orders API observability lab

Full spec: [../docs/laboratorio.md](../docs/laboratorio.md).

## API

| Operation | Request | Success | Errors |
|-----------|---------|---------|--------|
| Create | `POST /orders` `{customer_tier, category, amount}` | 201 | 400 |
| Get | `GET /orders/{id}` | 200 | 404 |
| Ship | `POST /orders/{id}/ship` | 200 | 404, 409 |
| Cancel | `POST /orders/{id}/cancel` | 200 | 404, 409 |

Any request can also return 500 (`FAILURE_RATE`) and is slowed by `EXTRA_LATENCY_MS`.

The API is stateless: the order ID encodes its status (`ord_<status>_<tier>_<category>_<cents>_<nonce>`) and a ship or cancel returns the order with its new ID. This keeps results consistent when Cloud Run functions scales to several instances without adding a database.

## Run order

1. `gcloud auth application-default login`
2. Set `project_id` in `infra/terraform.tfvars`.
3. From `infra/`: `terraform init && terraform apply` (retry once after a minute if the build fails). This also creates the log-based metrics in `infra/monitoring.tf`.
4. Put the `function_url` output in `simulator/config.json` as `target_url`.
5. From `simulator/`: `pip install -r requirements.txt && python simulator.py` (runs for one hour).
6. Build the dashboard in Cloud Monitoring with native metrics, the log-based metrics, SQL charts from `queries.sql` and a logs panel.
7. `terraform destroy` when finished.

## Local check

```bash
cd backend && pip install -r requirements.txt
functions-framework --target handle --port 8080
```

In another terminal set `target_url` to `http://localhost:8080` and run `python simulator.py --speed 600` for a compressed dry run (`--speed` is only for testing; the real lab runs at speed 1).
