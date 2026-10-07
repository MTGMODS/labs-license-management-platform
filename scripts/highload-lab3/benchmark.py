from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class Sample:
    latency_ms: float
    instance: str
    status: int
    error: str | None = None


def make_request(url: str, timeout: float) -> Sample:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            response.read()
            return Sample(
                latency_ms=(time.perf_counter() - started) * 1000,
                instance=response.headers.get("X-Instance-ID", "missing"),
                status=response.status,
            )
    except (urllib.error.URLError, TimeoutError) as exc:
        return Sample(
            latency_ms=(time.perf_counter() - started) * 1000,
            instance="error",
            status=0,
            error=str(exc),
        )


def percentile(values: list[float], percentage: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil((percentage / 100) * len(ordered)) - 1)
    return ordered[index]


def run(url: str, requests: int, concurrency: int, timeout: float, warmup: int) -> dict:
    for _ in range(warmup):
        make_request(url, timeout)

    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        samples = list(pool.map(lambda _: make_request(url, timeout), range(requests)))
    duration = time.perf_counter() - started

    successful = [sample for sample in samples if 200 <= sample.status < 400]
    latencies = [sample.latency_ms for sample in successful]
    distribution = Counter(sample.instance for sample in successful)

    return {
        "url": url,
        "requests": requests,
        "concurrency": concurrency,
        "duration_seconds": round(duration, 3),
        "throughput_rps": round(len(successful) / duration, 2) if duration else 0.0,
        "errors": len(samples) - len(successful),
        "error_rate_percent": round((len(samples) - len(successful)) / len(samples) * 100, 2),
        "latency_ms": {
            "average": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
            "p50": round(percentile(latencies, 50), 2),
            "p95": round(percentile(latencies, 95), 2),
            "p99": round(percentile(latencies, 99), 2),
        },
        "instance_distribution": dict(sorted(distribution.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="High-load Lab 3 balancing benchmark")
    parser.add_argument("--url", default="http://127.0.0.1:18100/health")
    parser.add_argument("--requests", type=int, default=300)
    parser.add_argument("--concurrency", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--warmup", type=int, default=20)
    args = parser.parse_args()

    if args.requests <= 0 or args.concurrency <= 0 or args.warmup < 0:
        parser.error("requests/concurrency must be positive and warmup cannot be negative")

    print(json.dumps(
        run(args.url, args.requests, args.concurrency, args.timeout, args.warmup),
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
