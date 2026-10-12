"""Live Lab 4 verification; only touches the isolated lab Compose project.

This is a functional/cold-hot comparison, not the k6 performance baseline of Lab 5.
"""
import argparse
import json
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT = "mtgmods_distributed_cache_lab4"
KEY = "mtgmods:license:public_stats:v2"


def compose(*args):
    result = subprocess.run(["docker", "compose", "-p", PROJECT,
                             "-f", "docker-compose.scaling.yml", "-f", "docker-compose.cache.yml", *args],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def request(base, path, method="GET", data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                                 headers=headers, method=method)
    started = time.perf_counter()
    try:
        response = urllib.request.urlopen(req, timeout=100)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = json.loads(response.read())
        return {"code": response.status, "cache": response.headers.get("X-Cache"),
                "instance": response.headers.get("X-Instance-ID"),
                "invalidation": response.headers.get("X-Cache-Invalidation"),
                "ms": round((time.perf_counter() - started) * 1000, 3)}, body


def verify(base, samples):
    # Safe built-in URL guard: no production load or remote target.
    if base not in {"http://127.0.0.1:18104", "http://localhost:18104"}:
        raise ValueError("This script only targets the local Lab 4 gateway on port 18104")
    url = "/api/v1/license/stats/public"
    compose("exec", "-T", "redis", "redis-cli", "DEL", KEY)
    cold, first = request(base, url)
    hot, second = request(base, url)
    assert cold["code"] == hot["code"] == 200
    assert cold["cache"] == "MISS" and hot["cache"] == "HIT"
    assert first == second
    assert cold["instance"] != hot["instance"], (cold, hot)
    token = compose("exec", "-T", "license-service-1", "python", "-c",
                    "import jwt; from datetime import datetime,timedelta,timezone; "
                    "from app.shared.config import settings; "
                    "print(jwt.encode({'sub':'1','role':'ADMIN','type':'access',"
                    "'exp':datetime.now(timezone.utc)+timedelta(minutes=15)},settings.JWT_SECRET,algorithm='HS256'))")
    old_total = first["subscriptions"]["overview"]["total_money"]
    mutation, created = request(base, "/api/v1/license/generate", "POST",
                                {"duration_days": 30, "amount": 3, "method": "Stars",
                                 "max_devices": 2, "reset_limit": 0}, token)
    assert mutation["code"] == 200 and mutation["invalidation"] == "INVALIDATED"
    key = created["data"]["key"]
    _, found = request(base, "/api/v1/license/find?key=" + key, token=token)
    license_id = found["data"][0]["id"]
    try:
        activated, _ = request(base, f"/api/v1/license/{license_id}", "PATCH",
                               {"user_id": 999999, "status": "ACTIVE"}, token)
        assert activated["code"] == 200 and activated["invalidation"] == "INVALIDATED"
        fresh, body = request(base, url)
        assert fresh["cache"] == "MISS"
        assert body["subscriptions"]["overview"]["total_money"] == old_total + 3
        _, _ = request(base, url)
        updated, _ = request(base, f"/api/v1/license/{license_id}", "PATCH", {"amount": 7}, token)
        assert updated["code"] == 200
        after_patch, body = request(base, url)
        assert after_patch["cache"] == "MISS"
        assert body["subscriptions"]["overview"]["total_money"] == old_total + 7
    finally:
        deleted, _ = request(base, f"/api/v1/license/{license_id}", "DELETE", token=token)
        assert deleted["code"] == 200
    after_delete, body = request(base, url)
    assert after_delete["cache"] == "MISS" and body["subscriptions"]["overview"]["total_money"] == old_total
    # Real TTL expiration without waiting 5 minutes.
    compose("exec", "-T", "redis", "redis-cli", "EXPIRE", KEY, "1")
    time.sleep(1.2)
    expired, _ = request(base, url)
    assert expired["cache"] == "MISS"
    # Restore in finally even when a check fails.
    try:
        compose("stop", "redis")
        fallback, body = request(base, url)
        assert fallback["code"] == 200 and fallback["cache"] == "BYPASS"
        assert body["subscriptions"]["overview"]["total_money"] == old_total
        rejected, _ = request(base, "/api/v1/license/generate", "POST", {}, token)
        assert rejected["code"] == 503
    finally:
        compose("start", "redis")
    for _ in range(30):
        recovered, _ = request(base, url)
        if recovered["cache"] != "BYPASS":
            break
        time.sleep(0.2)
    assert recovered["cache"] in {"MISS", "HIT"}
    cold_samples, hot_samples = [], []
    for _ in range(samples):
        compose("exec", "-T", "redis", "redis-cli", "DEL", KEY)
        result, _ = request(base, url)
        assert result["cache"] == "MISS"
        cold_samples.append(result["ms"])
        result, _ = request(base, url)
        assert result["cache"] == "HIT"
        hot_samples.append(result["ms"])
    return {"project": PROJECT, "base_url": base,
            "checks": {"cold": cold, "hot_cross_instance": hot, "after_create_activate": fresh,
                       "after_patch": after_patch, "after_delete": after_delete, "expired": expired,
                       "redis_down": fallback, "write_while_redis_down": rejected, "recovered": recovered},
            "timings": {"samples_per_mode": samples, "cold_ms": cold_samples, "hot_ms": hot_samples,
                        "cold_median_ms": round(statistics.median(cold_samples), 3),
                        "hot_median_ms": round(statistics.median(hot_samples), 3)}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18104")
    parser.add_argument("--samples", type=int, default=10)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("samples must be positive")
    print(json.dumps(verify(args.base_url, args.samples), indent=2))
