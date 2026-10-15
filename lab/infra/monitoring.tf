locals {
  request_log_filter = <<-EOT
    resource.type="cloud_run_revision"
    resource.labels.service_name="${var.function_name}"
    jsonPayload.message="request_completed"
  EOT
}

# Log-based metrics only count logs written after they exist,
# so they must be created before running the simulator.
resource "google_logging_metric" "requests_by_outcome" {
  name        = "${var.function_name}/requests_by_outcome"
  description = "Completed requests by endpoint, outcome and category"
  filter      = local.request_log_filter

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"

    labels {
      key         = "endpoint"
      value_type  = "STRING"
      description = "Normalized route"
    }
    labels {
      key         = "outcome"
      value_type  = "STRING"
      description = "success, client_error or server_error"
    }
    labels {
      key         = "category"
      value_type  = "STRING"
      description = "Order category"
    }
  }

  label_extractors = {
    endpoint = "EXTRACT(jsonPayload.endpoint)"
    outcome  = "EXTRACT(jsonPayload.outcome)"
    category = "EXTRACT(jsonPayload.category)"
  }

  depends_on = [google_project_service.apis]
}

resource "google_logging_metric" "errors_by_type" {
  name        = "${var.function_name}/errors_by_type"
  description = "Failed requests by error type and status code"
  filter      = "${local.request_log_filter}\njsonPayload.status_code>=400"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"

    labels {
      key         = "error_type"
      value_type  = "STRING"
      description = "Machine readable error type"
    }
    labels {
      key         = "status_code"
      value_type  = "STRING"
      description = "HTTP status code"
    }
  }

  label_extractors = {
    error_type  = "EXTRACT(jsonPayload.error_type)"
    status_code = "EXTRACT(jsonPayload.status_code)"
  }

  depends_on = [google_project_service.apis]
}

resource "google_logging_metric" "request_latency_ms" {
  name        = "${var.function_name}/request_latency_ms"
  description = "Backend latency as measured by the application"
  filter      = local.request_log_filter

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "ms"

    labels {
      key         = "endpoint"
      value_type  = "STRING"
      description = "Normalized route"
    }
  }

  value_extractor = "EXTRACT(jsonPayload.latency_ms)"

  label_extractors = {
    endpoint = "EXTRACT(jsonPayload.endpoint)"
  }

  bucket_options {
    exponential_buckets {
      num_finite_buckets = 20
      growth_factor      = 1.5
      scale              = 5
    }
  }

  depends_on = [google_project_service.apis]
}
