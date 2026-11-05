"""Small authenticated HTTP load test using only the Python standard library."""

import concurrent.futures
import json
import math
import os
import statistics
import threading
import time
import urllib.error
import urllib.request
from argparse import ArgumentParser


def request_json(url, *, data=None, token=None):
    body = None if data is None else json.dumps(data).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read()
        return response.status, json.loads(payload) if payload else None


def percentile(samples, value):
    ordered = sorted(samples)
    index = min(math.ceil(value * len(ordered)) - 1, len(ordered) - 1)
    return ordered[max(index, 0)]


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--path", default="/api/v1/reports/summary/")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--duration", type=int, default=30)
    args = parser.parse_args()
    if args.workers < 1 or args.duration < 1:
        parser.error("--workers and --duration must be positive.")

    username = os.environ.get("LOAD_TEST_USERNAME")
    password = os.environ.get("LOAD_TEST_PASSWORD")
    if not username or not password:
        parser.error("Set LOAD_TEST_USERNAME and LOAD_TEST_PASSWORD in the environment.")

    auth_status, tokens = request_json(
        f"{args.base_url.rstrip('/')}/api/v1/auth/token/",
        data={"username": username, "password": password},
    )
    if auth_status != 200 or not tokens or "access" not in tokens:
        raise RuntimeError(f"JWT token request failed with status {auth_status}.")

    token = tokens["access"]
    endpoint = f"{args.base_url.rstrip('/')}/{args.path.lstrip('/')}"
    stop_at = time.monotonic() + args.duration
    lock = threading.Lock()
    latencies = []
    failures = []

    def worker():
        while time.monotonic() < stop_at:
            started = time.perf_counter()
            try:
                status_code, _ = request_json(endpoint, token=token)
                elapsed = (time.perf_counter() - started) * 1000
                with lock:
                    latencies.append(elapsed)
                    if status_code < 200 or status_code >= 300:
                        failures.append(status_code)
            except urllib.error.HTTPError as exc:
                with lock:
                    failures.append(exc.code)
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                with lock:
                    failures.append(type(exc).__name__)

    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(worker) for _ in range(args.workers)]
        for future in futures:
            future.result()
    elapsed = time.monotonic() - started

    if not latencies:
        raise RuntimeError(f"No successful HTTP responses; failures: {failures[:10]}")

    print(
        json.dumps(
            {
                "endpoint": endpoint,
                "workers": args.workers,
                "duration_seconds": round(elapsed, 2),
                "requests": len(latencies),
                "failures": len(failures),
                "requests_per_second": round(len(latencies) / elapsed, 2),
                "latency_ms": {
                    "median": round(statistics.median(latencies), 2),
                    "p95": round(percentile(latencies, 0.95), 2),
                    "p99": round(percentile(latencies, 0.99), 2),
                },
                "failure_samples": failures[:10],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
