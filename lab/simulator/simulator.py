import argparse
import asyncio
import csv
import json
import random
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

CONFIG_PATH = Path(__file__).parent / "config.json"
OUTPUT_PATH = Path(__file__).parent / "requests.csv"
RUN_MINUTES = 60
SECONDS_PER_MINUTE = 60
REQUEST_TIMEOUT_SECONDS = 60
CSV_COLUMNS = [
    "request_id",
    "scheduled_at",
    "sent_at",
    "operation",
    "intended_event",
    "status_code",
    "client_latency_ms",
]

CUSTOMER_TIERS = ("standard", "premium", "vip")
CATEGORIES = ("electronics", "books", "clothing", "home")

OP_CREATE = "create_order"
OP_GET = "get_order"
OP_SHIP = "ship_order"
OP_CANCEL = "cancel_order"
VALID_OPERATION_WEIGHTS = {OP_CREATE: 0.40, OP_GET: 0.35, OP_SHIP: 0.10, OP_CANCEL: 0.15}

EVENT_VALID = "valid"
EVENT_INVALID_PAYLOAD = "invalid_payload"
EVENT_UNKNOWN_ID = "unknown_id"
EVENT_IMPOSSIBLE_TRANSITION = "impossible_transition"

STATUS_CREATED = "created"
EFFECT_DOUBLE_ERRORS = "double_errors"
UNKNOWN_ORDER_ID = "ord_missing_standard_books_100_00000000"
STATUS_CODE_NO_RESPONSE = 0

ORDER_ACTIONS = {OP_SHIP: "ship", OP_CANCEL: "cancel"}


class OrderPool:
    """Tracks the orders the simulator has created and their last known status."""

    def __init__(self):
        self.orders = {}

    def add(self, order_id, status, category):
        self.orders[order_id] = (status, category)

    def remove(self, order_id):
        self.orders.pop(order_id, None)

    def pick(self, rng, status=None, exclude_status=None):
        candidates = [
            (order_id, category)
            for order_id, (order_status, category) in self.orders.items()
            if (status is None or order_status == status)
            and (exclude_status is None or order_status != exclude_status)
        ]
        return rng.choice(candidates) if candidates else None


class PlannedRequest:
    def __init__(self, operation, event, method, path, body=None, order_id=None):
        self.operation = operation
        self.event = event
        self.method = method
        self.path = path
        self.body = body
        self.order_id = order_id


def load_config():
    return json.loads(CONFIG_PATH.read_text())


def sample_send_minutes(config, rng):
    minutes = []
    while len(minutes) < config["n_requests"]:
        value = rng.gauss(config["mu_min"], config["sigma_min"])
        if 0 <= value <= RUN_MINUTES:
            minutes.append(value)
    return sorted(minutes)


def invalid_probability(config, minute, category):
    multiplier = 1
    for scenario in config.get("scenarios", []):
        in_window = scenario["start_min"] <= minute <= scenario["end_min"]
        matches = scenario.get("category") in (None, category)
        if in_window and matches and scenario["effect"] == EFFECT_DOUBLE_ERRORS:
            multiplier *= 2
    return min(config["invalid_share"] * multiplier, 1.0)


def new_order_body(rng, category=None):
    return {
        "customer_tier": rng.choice(CUSTOMER_TIERS),
        "category": category or rng.choice(CATEGORIES),
        "amount": round(rng.uniform(5, 500), 2),
    }


def corrupt_body(rng, body):
    broken = dict(body)
    defect = rng.choice(["customer_tier", "category", "amount", "missing_amount"])
    if defect == "customer_tier":
        broken["customer_tier"] = "unknown"
    elif defect == "category":
        broken["category"] = "unknown"
    elif defect == "amount":
        broken["amount"] = -1
    else:
        del broken["amount"]
    return broken


def valid_plan(operation, pool, rng):
    if operation == OP_CREATE:
        body = new_order_body(rng)
        return PlannedRequest(OP_CREATE, EVENT_VALID, "POST", "/orders", body), body["category"]
    status = STATUS_CREATED if operation in ORDER_ACTIONS else None
    picked = pool.pick(rng, status=status)
    if picked is None:
        body = new_order_body(rng)
        return PlannedRequest(OP_CREATE, EVENT_VALID, "POST", "/orders", body), body["category"]
    order_id, category = picked
    method, suffix = ("GET", "") if operation == OP_GET else ("POST", f"/{ORDER_ACTIONS[operation]}")
    return PlannedRequest(operation, EVENT_VALID, method, f"/orders/{order_id}{suffix}", order_id=order_id), category


def invalid_plan(plan, category, pool, rng):
    event = rng.choice([EVENT_INVALID_PAYLOAD, EVENT_UNKNOWN_ID, EVENT_IMPOSSIBLE_TRANSITION])
    if event == EVENT_INVALID_PAYLOAD:
        body = corrupt_body(rng, new_order_body(rng, category))
        return PlannedRequest(OP_CREATE, event, "POST", "/orders", body)
    if event == EVENT_UNKNOWN_ID:
        operation = plan.operation if plan.operation != OP_CREATE else OP_GET
        method, suffix = ("GET", "") if operation == OP_GET else ("POST", f"/{ORDER_ACTIONS[operation]}")
        return PlannedRequest(operation, event, method, f"/orders/{UNKNOWN_ORDER_ID}{suffix}")
    finished = pool.pick(rng, exclude_status=STATUS_CREATED)
    if finished is None:
        return invalid_plan_payload(rng, category)
    operation = rng.choice([OP_SHIP, OP_CANCEL])
    return PlannedRequest(
        operation, event, "POST", f"/orders/{finished[0]}/{ORDER_ACTIONS[operation]}"
    )


def invalid_plan_payload(rng, category):
    body = corrupt_body(rng, new_order_body(rng, category))
    return PlannedRequest(OP_CREATE, EVENT_INVALID_PAYLOAD, "POST", "/orders", body)


def build_plan(config, rng, pool, minute):
    operation = rng.choices(
        list(VALID_OPERATION_WEIGHTS), weights=list(VALID_OPERATION_WEIGHTS.values())
    )[0]
    plan, category = valid_plan(operation, pool, rng)
    if rng.random() < invalid_probability(config, minute, category):
        return invalid_plan(plan, category, pool, rng)
    return plan


def update_pool(pool, plan, status_code, payload):
    if plan.event != EVENT_VALID or status_code >= 400 or not isinstance(payload, dict):
        return
    if plan.operation == OP_GET:
        return
    if plan.order_id:
        pool.remove(plan.order_id)
    pool.add(payload["order_id"], payload["status"], payload["category"])


def iso_utc(epoch_seconds):
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat(timespec="milliseconds")


async def send_request(session, config, rng, pool, minute, run_start, speed, rows):
    scheduled = run_start + minute * SECONDS_PER_MINUTE / speed
    await asyncio.sleep(max(0, scheduled - time.time()))
    plan = build_plan(config, rng, pool, minute)
    request_id = str(uuid.uuid4())
    url = config["target_url"].rstrip("/") + plan.path
    sent = time.time()
    started = time.perf_counter()
    status_code, payload = STATUS_CODE_NO_RESPONSE, None
    try:
        async with session.request(
            plan.method, url, json=plan.body, headers={"X-Request-ID": request_id}
        ) as response:
            status_code = response.status
            payload = await response.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        pass
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    update_pool(pool, plan, status_code, payload)
    rows.append(
        {
            "request_id": request_id,
            "scheduled_at": iso_utc(scheduled),
            "sent_at": iso_utc(sent),
            "operation": plan.operation,
            "intended_event": plan.event,
            "status_code": status_code,
            "client_latency_ms": latency_ms,
        }
    )


async def run(config, speed):
    rng = random.Random(config["seed"])
    pool = OrderPool()
    rows = []
    schedule = sample_send_minutes(config, rng)
    run_start = time.time() + 1
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    connector = aiohttp.TCPConnector(limit=0)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        await asyncio.gather(
            *[
                send_request(session, config, rng, pool, minute, run_start, speed, rows)
                for minute in schedule
            ]
        )
    return rows


def write_csv(rows):
    with OUTPUT_PATH.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: row["scheduled_at"]))


def parse_args():
    parser = argparse.ArgumentParser(description="Orders API traffic simulator")
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Time compression factor; 60 runs the hour in one minute (for dry runs only)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    write_csv(asyncio.run(run(load_config(), arguments.speed)))
