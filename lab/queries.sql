-- Log Analytics queries for project lab-observabilidad-030120 (see the log_analytics_view output).
-- Fields written by the backend live under json_payload.

-- 1. Requests per minute by outcome
SELECT
  TIMESTAMP_TRUNC(timestamp, MINUTE) AS minute,
  JSON_VALUE(json_payload.outcome) AS outcome,
  COUNT(*) AS requests
FROM `lab-observabilidad-030120.global._Default._Default`
WHERE JSON_VALUE(json_payload.message) = 'request_completed'
GROUP BY minute, outcome
ORDER BY minute;

-- 2. Error rate per minute
SELECT
  TIMESTAMP_TRUNC(timestamp, MINUTE) AS minute,
  COUNTIF(CAST(JSON_VALUE(json_payload.status_code) AS INT64) >= 400) / COUNT(*) AS error_rate
FROM `lab-observabilidad-030120.global._Default._Default`
WHERE JSON_VALUE(json_payload.message) = 'request_completed'
GROUP BY minute
ORDER BY minute;

-- 3. Errors by category per minute (shows the double_errors scenario on electronics)
SELECT
  TIMESTAMP_TRUNC(timestamp, MINUTE) AS minute,
  JSON_VALUE(json_payload.category) AS category,
  COUNTIF(CAST(JSON_VALUE(json_payload.status_code) AS INT64) BETWEEN 400 AND 499) AS client_errors
FROM `lab-observabilidad-030120.global._Default._Default`
WHERE JSON_VALUE(json_payload.message) = 'request_completed'
GROUP BY minute, category
ORDER BY minute;

-- 4. Latency percentiles by endpoint
SELECT
  JSON_VALUE(json_payload.endpoint) AS endpoint,
  APPROX_QUANTILES(CAST(JSON_VALUE(json_payload.latency_ms) AS FLOAT64), 100)[OFFSET(50)] AS p50_ms,
  APPROX_QUANTILES(CAST(JSON_VALUE(json_payload.latency_ms) AS FLOAT64), 100)[OFFSET(95)] AS p95_ms,
  APPROX_QUANTILES(CAST(JSON_VALUE(json_payload.latency_ms) AS FLOAT64), 100)[OFFSET(99)] AS p99_ms
FROM `lab-observabilidad-030120.global._Default._Default`
WHERE JSON_VALUE(json_payload.message) = 'request_completed'
GROUP BY endpoint;

-- 5. Errors by type
SELECT
  JSON_VALUE(json_payload.error_type) AS error_type,
  JSON_VALUE(json_payload.status_code) AS status_code,
  COUNT(*) AS occurrences
FROM `lab-observabilidad-030120.global._Default._Default`
WHERE JSON_VALUE(json_payload.message) = 'request_completed'
  AND CAST(JSON_VALUE(json_payload.status_code) AS INT64) >= 400
GROUP BY error_type, status_code
ORDER BY occurrences DESC;

-- 6. Revenue and order volume by customer tier (business view)
SELECT
  JSON_VALUE(json_payload.customer_tier) AS customer_tier,
  COUNT(*) AS orders,
  SUM(CAST(JSON_VALUE(json_payload.amount) AS FLOAT64)) AS total_amount
FROM `lab-observabilidad-030120.global._Default._Default`
WHERE JSON_VALUE(json_payload.endpoint) = 'POST /orders'
  AND CAST(JSON_VALUE(json_payload.status_code) AS INT64) = 201
GROUP BY customer_tier;
