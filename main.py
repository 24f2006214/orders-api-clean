import time
import uuid
from collections import defaultdict, deque
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

TOTAL_ORDERS = 51
RATE_LIMIT = 17
WINDOW_SECONDS = 10.0

app = FastAPI(title="Orders API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

idempotency_store: dict[str, dict[str, Any]] = {}
rate_limit_store: defaultdict[str, deque[float]] = defaultdict(deque)


def check_rate_limit(client_id: Optional[str]) -> Optional[int]:
    # Requests without X-Client-Id are not rate-limited.
    if not client_id:
        return None

    now = time.monotonic()
    bucket = rate_limit_store[client_id]

    while bucket and now - bucket[0] >= WINDOW_SECONDS:
        bucket.popleft()

    if len(bucket) >= RATE_LIMIT:
        retry_after = max(
            1,
            int(WINDOW_SECONDS - (now - bucket[0]) + 0.999),
        )
        return retry_after

    bucket.append(now)
    return None


def rate_limited_response(retry_after: int) -> Response:
    return Response(
        status_code=429,
        content='{"detail":"rate limit exceeded"}',
        media_type="application/json",
        headers={"Retry-After": str(retry_after)},
    )


@app.get("/")
async def root():
    return {"status": "ok", "total_orders": TOTAL_ORDERS}


@app.post("/orders", status_code=201)
async def create_order(
    request: Request,
    idempotency_key: Optional[str] = Header(
        default=None,
        alias="Idempotency-Key",
    ),
    x_client_id: Optional[str] = Header(
        default=None,
        alias="X-Client-Id",
    ),
):
    retry_after = check_rate_limit(x_client_id)

    if retry_after is not None:
        return rate_limited_response(retry_after)

    if idempotency_key and idempotency_key in idempotency_store:
        return idempotency_store[idempotency_key]

    try:
        body = await request.json()
    except Exception:
        body = {}

    order = {
        "id": str(uuid.uuid4()),
    }

    if isinstance(body, dict):
        for key, value in body.items():
            if key != "id":
                order[key] = value

    if idempotency_key:
        idempotency_store[idempotency_key] = order

    return order


@app.get("/orders")
async def list_orders(
    limit: int = Query(default=10, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
    x_client_id: Optional[str] = Header(
        default=None,
        alias="X-Client-Id",
    ),
):
    retry_after = check_rate_limit(x_client_id)

    if retry_after is not None:
        return rate_limited_response(retry_after)

    start = 1

    if cursor:
        try:
            start = int(cursor)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid cursor",
            )

    if start < 1 or start > TOTAL_ORDERS + 1:
        raise HTTPException(
            status_code=400,
            detail="Invalid cursor",
        )

    end = min(start + limit - 1, TOTAL_ORDERS)

    if start > TOTAL_ORDERS:
        items = []
        next_cursor = None
    else:
        items = [
            {
                "id": order_id,
                "item": f"Order item {order_id}",
                "quantity": 1,
                "status": "catalog",
            }
            for order_id in range(start, end + 1)
        ]
        next_cursor = str(end + 1) if end < TOTAL_ORDERS else None

    return {
        "items": items,
        "next_cursor": next_cursor,
    }
