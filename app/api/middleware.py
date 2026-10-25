import time
from uuid import uuid4

import structlog
from prometheus_client import Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

log = structlog.get_logger()

REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path_template", "status"],
)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "path_template"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns request_id, binds log context, records Prometheus metrics."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid4().hex

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed = time.perf_counter() - start
            route = request.scope.get("route")
            path = route.path if route is not None else request.url.path
            REQUESTS.labels(method=request.method, path_template=path, status="500").inc()
            LATENCY.labels(method=request.method, path_template=path).observe(elapsed)
            log.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=round(elapsed * 1000, 2),
            )
            raise

        elapsed = time.perf_counter() - start
        route = request.scope.get("route")
        path = route.path if route is not None else request.url.path

        REQUESTS.labels(
            method=request.method, path_template=path, status=str(response.status_code)
        ).inc()
        LATENCY.labels(method=request.method, path_template=path).observe(elapsed)

        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round(elapsed * 1000, 2),
        )
        response.headers["x-request-id"] = request_id
        return response