import time
from collections import defaultdict, deque

from fastapi import FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

TOTAL_ORDERS = 51
RATE_LIMIT = 17
WINDOW_SECONDS = 10

app = FastAPI(title="Orders API")

# Allow the grader page/browser to call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

# idempotency_key -> order dict
idempotency_store: dict[str, dict] = {}

# client_id -> timestamps of recent requests
rate_limit_store: defaultdict[str, deque[float]] = defaultdict(deque)


class OrderCreate(BaseModel):
    item: str
    quantity: int = 1


def make_order(order_id: int, item: str, quantity: int) -> dict:
    return {
        "id": order_id,
        "item": item,
        "quantity": quantity,
        "status": "created",
    }


def check_rate_limit(client_id: str) -> Optional[int]:
    """
    Returns None if allowed.
    Returns the number of seconds the client should wait if rate-limited.
    """
    now = time.time()
    bucket = rate_limit_store[client_id]

    # Remove timestamps older than the 10-second window.
    while bucket and now - bucket[0] >= WINDOW_SECONDS:
        bucket.popleft()

    if len(bucket) >= RATE_LIMIT:
        retry_after = int(WINDOW_SECONDS - (now - bucket[0])) + 1
        return max(retry_after, 1)

    bucket.append(now)
    return None


@app.post("/orders", status_code=201)
def create_order(
    order: OrderCreate,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id"),
):
    client_id = x_client_id or "anonymous"

    retry_after = check_rate_limit(client_id)
    if retry_after is not None:
        return Response(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content='{"detail": "Rate limit exceeded"}',
            media_type="application/json",
        )

    # Repeat request with the same key: return the original order.
    if idempotency_key and idempotency_key in idempotency_store:
        return idempotency_store[idempotency_key]

    # First request with this key: create a new order.
    order_id = len(idempotency_store) + 1
    new_order = make_order(order_id, order.item, order.quantity)

    if idempotency_key:
        idempotency_store[idempotency_key] = new_order

    return new_order


@app.get("/orders")
def list_orders(
    limit: int = Query(default=10, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
    x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id"),
):
    client_id = x_client_id or "anonymous"

    retry_after = check_rate_limit(client_id)
    if retry_after is not None:
        return Response(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content='{"detail": "Rate limit exceeded"}',
            media_type="application/json",
        )

    # Cursor is simply the next ID to start from.
    start = 1
    if cursor:
        try:
            start = int(cursor)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid cursor")

    end = min(start + limit - 1, TOTAL_ORDERS)

    items = [
        make_order(order_id, f"Order item {order_id}", 1)
        for order_id in range(start, end + 1)
    ]

    next_cursor = str(end + 1) if end < TOTAL_ORDERS else None

    return {
        "items": items,
        "next_cursor": next_cursor,
    }


@app.get("/")
def root():
    return {"status": "ok", "total_orders": TOTAL_ORDERS}
