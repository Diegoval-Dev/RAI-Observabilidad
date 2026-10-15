import json
import os
import random
import re
import time
import uuid

import functions_framework

FAILURE_RATE = float(os.environ.get("FAILURE_RATE", "0"))
EXTRA_LATENCY_MS = int(os.environ.get("EXTRA_LATENCY_MS", "0"))

OUTCOME_SUCCESS = "success"
OUTCOME_CLIENT_ERROR = "client_error"
OUTCOME_SERVER_ERROR = "server_error"

STATUS_CREATED = "created"
STATUS_SHIPPED = "shipped"
STATUS_CANCELLED = "cancelled"

CUSTOMER_TIERS = ("standard", "premium", "vip")
CATEGORIES = ("electronics", "books", "clothing", "home")

ORDER_ID_PREFIX = "ord"
ORDER_ID_SEPARATOR = "_"
ORDER_ID_PARTS = 6
ORDER_ID_PATTERN = re.compile(r"^ord_[a-z]+_[a-z]+_[a-z]+_\d+_[0-9a-f]{8}$")

ROUTE_CREATE = "POST /orders"
ROUTE_GET = "GET /orders/{id}"
ROUTE_SHIP = "POST /orders/{id}/ship"
ROUTE_CANCEL = "POST /orders/{id}/cancel"
ROUTE_UNKNOWN = "UNKNOWN"

ORDER_PATH_PATTERN = re.compile(r"^/orders(?:/([^/]+)(?:/(ship|cancel))?)?/?$")


class ApiError(Exception):
    def __init__(self, status_code, error_type):
        super().__init__(error_type)
        self.status_code = status_code
        self.error_type = error_type


class Order:
    """Order state is encoded in the order ID so the API stays stateless across instances."""

    def __init__(self, status, customer_tier, category, amount_cents, nonce):
        self.status = status
        self.customer_tier = customer_tier
        self.category = category
        self.amount_cents = amount_cents
        self.nonce = nonce

    @property
    def order_id(self):
        return ORDER_ID_SEPARATOR.join(
            [
                ORDER_ID_PREFIX,
                self.status,
                self.customer_tier,
                self.category,
                str(self.amount_cents),
                self.nonce,
            ]
        )

    @property
    def business(self):
        return {
            "customer_tier": self.customer_tier,
            "category": self.category,
            "amount": self.amount_cents / 100,
        }

    def with_status(self, status):
        return Order(status, self.customer_tier, self.category, self.amount_cents, self.nonce)

    def to_response(self):
        return {"order_id": self.order_id, "status": self.status, **self.business}

    @classmethod
    def parse(cls, order_id):
        if not ORDER_ID_PATTERN.match(order_id):
            raise ApiError(404, "order_not_found")
        _, status, tier, category, amount_cents, nonce = order_id.split(ORDER_ID_SEPARATOR)
        valid = (
            status in (STATUS_CREATED, STATUS_SHIPPED, STATUS_CANCELLED)
            and tier in CUSTOMER_TIERS
            and category in CATEGORIES
        )
        if not valid:
            raise ApiError(404, "order_not_found")
        return cls(status, tier, category, int(amount_cents), nonce)


def outcome_for(status_code):
    if status_code >= 500:
        return OUTCOME_SERVER_ERROR
    if status_code >= 400:
        return OUTCOME_CLIENT_ERROR
    return OUTCOME_SUCCESS


def severity_for(status_code):
    if status_code >= 500:
        return "ERROR"
    if status_code >= 400:
        return "WARNING"
    return "INFO"


def log_request(request_id, endpoint, status_code, error_type, latency_ms, business):
    print(
        json.dumps(
            {
                "severity": severity_for(status_code),
                "message": "request_completed",
                "request_id": request_id,
                "endpoint": endpoint,
                "status_code": status_code,
                "outcome": outcome_for(status_code),
                "error_type": error_type,
                "latency_ms": latency_ms,
                **business,
            }
        ),
        flush=True,
    )


def parse_payload(request):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError(400, "invalid_json")
    return payload


def validate_new_order(payload):
    tier = payload.get("customer_tier")
    category = payload.get("category")
    amount = payload.get("amount")
    is_number = isinstance(amount, (int, float)) and not isinstance(amount, bool)
    if tier not in CUSTOMER_TIERS:
        raise ApiError(400, "invalid_customer_tier")
    if category not in CATEGORIES:
        raise ApiError(400, "invalid_category")
    if not is_number or amount <= 0:
        raise ApiError(400, "invalid_amount")
    return tier, category, round(amount * 100)


def create_order(request):
    tier, category, amount_cents = validate_new_order(parse_payload(request))
    order = Order(STATUS_CREATED, tier, category, amount_cents, uuid.uuid4().hex[:8])
    return 201, order.to_response(), order.business


def get_order(order_id):
    order = Order.parse(order_id)
    return 200, order.to_response(), order.business


def transition_order(order_id, allowed_from, new_status):
    order = Order.parse(order_id)
    if order.status != allowed_from:
        raise ApiError(409, f"invalid_transition_from_{order.status}")
    updated = order.with_status(new_status)
    return 200, updated.to_response(), updated.business


def resolve_route(request):
    match = ORDER_PATH_PATTERN.match(request.path)
    if not match:
        return ROUTE_UNKNOWN, None
    order_id, action = match.groups()
    key = (request.method, order_id is not None, action)
    routes = {
        ("POST", False, None): ROUTE_CREATE,
        ("GET", True, None): ROUTE_GET,
        ("POST", True, "ship"): ROUTE_SHIP,
        ("POST", True, "cancel"): ROUTE_CANCEL,
    }
    return routes.get(key, ROUTE_UNKNOWN), order_id


def business_from_order_id(order_id):
    try:
        return Order.parse(order_id).business
    except ApiError:
        return {}


def business_from_payload(request):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return {}
    known = {
        "customer_tier": payload.get("customer_tier") if payload.get("customer_tier") in CUSTOMER_TIERS else None,
        "category": payload.get("category") if payload.get("category") in CATEGORIES else None,
    }
    return {key: value for key, value in known.items() if value is not None}


def dispatch(route, request, order_id):
    if route == ROUTE_CREATE:
        return create_order(request)
    if route == ROUTE_GET:
        return get_order(order_id)
    if route == ROUTE_SHIP:
        return transition_order(order_id, STATUS_CREATED, STATUS_SHIPPED)
    if route == ROUTE_CANCEL:
        return transition_order(order_id, STATUS_CREATED, STATUS_CANCELLED)
    raise ApiError(404, "route_not_found")


def apply_simulated_faults():
    if EXTRA_LATENCY_MS:
        time.sleep(EXTRA_LATENCY_MS / 1000)
    if random.random() < FAILURE_RATE:
        raise ApiError(500, "simulated_failure")


@functions_framework.http
def handle(request):
    started = time.perf_counter()
    request_id = request.headers.get("X-Request-ID", "")
    route, order_id = resolve_route(request)
    if route == ROUTE_CREATE:
        business = business_from_payload(request)
    else:
        business = business_from_order_id(order_id) if order_id else {}
    error_type = None

    try:
        apply_simulated_faults()
        status_code, body, business = dispatch(route, request, order_id)
    except ApiError as error:
        status_code, error_type = error.status_code, error.error_type
        body = {"error": error.error_type}

    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    log_request(request_id, route, status_code, error_type, latency_ms, business)
    return json.dumps(body), status_code, {"Content-Type": "application/json"}
