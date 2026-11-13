"""Prometheus counters. Labels use route templates, not raw identifiers."""

from prometheus_client import Counter, Histogram, generate_latest

REQUESTS = Counter(
    "sentinel_http_requests_total",
    "HTTP responses served by the API",
    ("method", "path", "status"),
)
LATENCY = Histogram(
    "sentinel_http_request_seconds",
    "HTTP request latency",
    ("method", "path"),
)
PROPOSALS = Counter(
    "sentinel_proposals_total",
    "Operation proposals by outcome",
    ("outcome",),
)


def observe_request(method: str, path: str, status: int, seconds: float) -> None:
    REQUESTS.labels(method, path, str(status)).inc()
    LATENCY.labels(method, path).observe(seconds)


def record_proposal(outcome: str) -> None:
    PROPOSALS.labels(outcome).inc()


def render_metrics() -> bytes:
    return generate_latest()
