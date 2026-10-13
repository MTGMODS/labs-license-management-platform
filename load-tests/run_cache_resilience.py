"""Real avalanche / negative-cache measurements, only the isolated Lab 8 stack."""
import argparse
import json
import os
import secrets
import subprocess
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from run_stampede import ROOT, PROJECT, compose, sql

BASE = 'http://127.0.0.1:18108'


def request(path, token=None, data=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        response = urlopen(Request(BASE + path, headers=headers,
                                   data=json.dumps(data).encode() if data is not None else None), timeout=60)
    except HTTPError as response:
        return response.code, {k.lower(): v for k, v in response.headers.items()}, json.loads(response.read())
    with response:
        return response.status, {k.lower(): v for k, v in response.headers.items()}, json.loads(response.read())


def redis(*args):
    return compose('exec', '-T', 'redis', 'redis-cli', *map(str, args))


def prepare_dataset():
    # Project and endpoint are constants, reset itself requires LAB5_DEMO=true.
    compose('run', '--rm', '--no-deps', 'cache-tools', 'python', '/load-tests/reset_dataset.py')
    compose('run', '--rm', '--no-deps', 'cache-tools', 'python', '/lab4/seed.py', '--rows', '2000')
    redis('FLUSHDB')


def db_metrics(mode):
    condition = "query LIKE '%lab8_cohort%'" if mode == 'avalanche' else "query ILIKE '%FROM licenses%' AND query ILIKE '%WHERE licenses.id%'"
    return json.loads(sql("SELECT json_build_object('calls',coalesce(sum(calls),0),"
                          "'execution_ms',coalesce(sum(total_exec_time),0)) FROM pg_stat_statements "
                          f"WHERE ({condition}) AND query NOT ILIKE '%pg_stat_statements%'"))


def measure(mode, label, token=None):
    samples, resources, cpu_samples = [], [], []
    stop = threading.Event()
    started = time.monotonic()
    def sample_db():
        while not stop.is_set():
            metric = db_metrics(mode)
            metric['elapsed_s'] = round(time.monotonic() - started, 3)
            samples.append(metric)
            stop.wait(.5)
    def sample_cpu():
        while not stop.is_set():
            result = subprocess.run(['docker', 'stats', '--no-stream', '--format', '{{json .}}'],
                                    capture_output=True, text=True)
            records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
            resources.append({'elapsed_s': round(time.monotonic() - started, 3), 'containers': [
                {'name': r['Name'].replace(PROJECT + '-', ''), 'cpu_pct': float(r['CPUPerc'].strip('%')),
                 'memory': r['MemUsage']} for r in records if r.get('Name', '').startswith(PROJECT)]})
            stop.wait(.5)
    def sample_cgroup_cpu():
        # Docker stats may miss a subsecond avalanche. Read real CPU consumption
        # from the DB container cgroup every ~100ms; /proc/uptime is monotonic.
        script = '''lab_i=0; while [ "$lab_i" -lt 200 ]; do
            lab_usage=$(awk '$1=="usage_usec" {print $2}' /sys/fs/cgroup/cpu.stat)
            lab_time=$(cut -d ' ' -f1 /proc/uptime)
            echo "$lab_time $lab_usage"
            lab_i=$((lab_i+1)); sleep 0.1
        done'''
        process = subprocess.Popen(['docker', 'exec', PROJECT + '-license-postgres-1', 'sh', '-c', script],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        previous = None
        try:
            for line in process.stdout:
                if stop.is_set():
                    break
                clock, usage = map(float, line.split())
                if previous and clock > previous[0]:
                    cpu_samples.append({'elapsed_s': round(time.monotonic() - started, 3),
                                        'cpu_pct': (usage - previous[1]) / 1e6 / (clock - previous[0]) * 100})
                previous = (clock, usage)
        finally:
            process.terminate()
            process.wait(timeout=3)
    threads = [threading.Thread(target=fn, daemon=True) for fn in (sample_db, sample_cpu, sample_cgroup_cpu)]
    for thread in threads:
        thread.start()
    try:
        output = compose('run', '--rm', '--no-deps', '-e', f'EXPERIMENT={mode}',
                         '-e', f'ADMIN_TOKEN={token or ""}', 'k6', 'run', '/tests/cache_resilience.js')
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=5)
    summary = json.loads(next(line for line in output.splitlines() if line.startswith('LAB8_EXPERIMENT=')).split('=', 1)[1])
    if summary['error_rate'] != 0:
        raise AssertionError('Every experiment response must have the expected business status')
    summary['rps'] = summary['requests'] / summary['elapsed_seconds']
    if not cpu_samples or not samples:
        raise AssertionError('CPU and SQL timelines must contain real measurements')
    return {'mode': mode, 'label': label, 'summary': summary, 'database': db_metrics(mode),
            'database_timeline': samples, 'resources': resources,
            'postgres_cpu_cgroup': cpu_samples, 'cpu_sampling_target_seconds': .1}


def avalanche(jitter):
    # Warm distinct real cohorts, then arm their first expiration relative to one
    # shared clock. Refilled entries live 30s, beyond this 12s observation window.
    for bucket in range(24):
        status, _, value = request(f'/api/v1/lab8/aggregate/{bucket}')
        assert status == 200 and value['bucket'] == bucket
    expirations = [6000 + (secrets.randbelow(6001) - 3000 if jitter else 0) for _ in range(24)]
    # One atomic Redis call: shell round-trips must not let early keys expire
    # while the remaining keys are still being armed.
    keys = [f'mtgmods:lab8:cohort:v1:{bucket}' for bucket in range(24)]
    script = """
    local clock = redis.call('TIME')
    local now = clock[1] * 1000 + math.floor(clock[2] / 1000)
    for i,key in ipairs(KEYS) do
        if redis.call('EXISTS',key) == 0 then return redis.error_reply('Cold cohort before arming') end
    end
    for i,key in ipairs(KEYS) do redis.call('PEXPIREAT',key,now + tonumber(ARGV[i])) end
    return #KEYS
    """
    assert redis('EVAL', script, 24, *keys, *expirations) == '24'
    sql('SELECT pg_stat_statements_reset()')
    result = measure('avalanche', 'jitter' if jitter else 'synchronized')
    result.update(expiration_offsets_ms=expirations, keys=24, base_ttl_seconds=6, jitter_seconds=3 if jitter else 0,
                  refill_ttl_seconds=30, amplification=4000)
    assert result['database']['calls'] == 24, 'All 24 cohorts must expire and refill once'
    return result


def negative(enabled):
    env = dict(os.environ, LAB8_PROTECTED='true', LAB8_NEGATIVE_CACHE=str(enabled).lower())
    compose('up', '-d', '--no-deps', '--force-recreate', 'license-service-1', 'license-service-2', 'license-service-3', env=env)
    compose('up', '-d', '--no-deps', '--force-recreate', 'scaling-gateway', env=env)
    token = compose('exec', '-T', 'license-service-1', 'python', '-c',
                    "import jwt; from datetime import datetime,timedelta,timezone; from app.shared.config import settings; "
                    "print(jwt.encode({'sub':'1','role':'ADMIN','type':'access','exp':datetime.now(timezone.utc)+timedelta(hours=1)},settings.JWT_SECRET,algorithm='HS256'))")
    for identifier in range(9999990, 9999995):
        redis('DEL', f'mtgmods:license:absent:v1:{identifier}')
    sql('SELECT pg_stat_statements_reset()')
    for identifier in range(9999990, 9999995):
        status, _, _ = request(f'/api/v1/license/{identifier}', token)
        assert status == 404
    warmup_calls = db_metrics('negative')['calls']
    sql('SELECT pg_stat_statements_reset()')
    result = measure('negative', 'protected' if enabled else 'unprotected', token)
    result.update(warmup_sql_calls=warmup_calls, repeated_missing_ids=5, negative_ttl_seconds=15)
    if enabled:
        assert result['database']['calls'] == 0, 'Warmed absence must avoid all DB lookups within TTL'
        verify_negative(token, result)
    return result


def verify_negative(token, result):
    identifier = int(sql("SELECT last_value + 1 FROM licenses_id_seq"))
    status, _, _ = request(f'/api/v1/license/{identifier}', token)
    assert status == 404
    status, _, _ = request('/api/v1/license/generate', token, {'amount': 3, 'method': 'Stars',
                           'duration_days': 30, 'max_devices': 2, 'reset_limit': 0})
    assert status == 200
    status, _, _ = request(f'/api/v1/license/{identifier}', token)
    assert status == 200, 'Creation must invalidate a previous absence marker'
    missing = 9999989
    assert request(f'/api/v1/license/{missing}', token)[0] == 404
    redis('PEXPIRE', f'mtgmods:license:absent:v1:{missing}', 1)
    time.sleep(.02)
    assert request(f'/api/v1/license/{missing}', token)[1]['x-negative-cache'] == 'MISS'
    compose('stop', 'redis')
    try:
        status, headers, _ = request(f'/api/v1/license/{missing}', token)
        assert status == 404 and headers['x-negative-cache'] == 'BYPASS'
    finally:
        compose('start', 'redis')
    result['verified'] = {'creation_invalidates_404': True, 'ttl_expiration': True, 'redis_outage_fallback': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', choices=['avalanche', 'negative'])
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    directory = args.output_dir.resolve()
    modes = [args.only] if args.only else ['avalanche', 'negative']
    if not directory.is_relative_to(ROOT / 'load-tests/results') or any((directory / f'{mode}.json').exists() for mode in modes):
        parser.error('Use a new output directory inside load-tests/results')
    try:
        for mode in modes:
            print('RUN ' + mode, flush=True)
            prepare_dataset()
            if mode == 'avalanche':
                results = [avalanche(False), avalanche(True)]
            else:
                results = [negative(False), negative(True)]
            directory.mkdir(parents=True, exist_ok=True)
            for result in results:
                result['dataset_initial_licenses'] = 2000
            (directory / f'{mode}.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
            print(json.dumps([{'label': r['label'], 'sql_calls': r['database']['calls'],
                               'requests': r['summary']['requests'], 'p99_ms': r['summary']['p99_ms'],
                               'error_rate': r['summary']['error_rate']} for r in results]), flush=True)
    finally:
        env = dict(os.environ, LAB8_PROTECTED='true', LAB8_NEGATIVE_CACHE='true')
        compose('up', '-d', '--no-deps', '--force-recreate', 'license-service-1', 'license-service-2',
                'license-service-3', 'scaling-gateway', env=env)
