"""Orchestrate k6 on the isolated Lab 5 project. No production targets allowed."""
import argparse
import json
import os
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = 'mtgmods_load_baseline_lab5'
FILES = ['-f', 'docker-compose.scaling.yml', '-f', 'docker-compose.cache.yml', '-f', 'docker-compose.load.yml']


def peak_cpu(samples):
    peaks = {}
    for sample in samples:
        for item in sample['containers']:
            name = item['name'].split('-run-')[0]
            peaks[name] = max(peaks.get(name, 0), item['cpu_pct'])
    return peaks


def compose(*args, env=None):
    result = subprocess.run(['docker', 'compose', '-p', PROJECT, *FILES, *args], cwd=ROOT,
                            env=env, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr + '\n' + result.stdout)  # Never print the JWT-bearing command.
    return result.stdout.strip()


def sql(statement):
    return compose('exec', '-T', 'license-postgres', 'psql', '-U', 'mtg_license', '-d', 'licenses', '-Atc', statement)


def database_metrics():
    return json.loads(sql("SELECT json_build_object('calls', coalesce(sum(calls),0), "
                          "'execution_ms', coalesce(sum(total_exec_time),0)) FROM pg_stat_statements "
                          "WHERE query NOT ILIKE '%pg_stat_statements%' AND query <> 'SELECT $1'"))


def run(label, workload, instances, enabled, levels, seconds, warmup):
    env = dict(os.environ, LAB4_CACHE_ENABLED=str(enabled).lower())
    env['LAB5_UPSTREAM_CONFIG'] = ('./deploy/horizontal-scaling/upstream-one-instance.conf' if instances == 1
                                 else './deploy/horizontal-scaling/upstream-round-robin.conf')
    compose('up', '-d', '--no-deps', '--force-recreate', 'license-service-1', 'license-service-2',
            'license-service-3', env=env)
    # Keep total SUT resource limits identical; vary only the gateway's active pool.
    time.sleep(3)
    compose('up', '-d', '--no-deps', '--force-recreate', 'scaling-gateway', env=env)
    compose('run', '--rm', '--no-deps', 'cache-tools', 'python', '/load-tests/reset_dataset.py', env=env)
    compose('run', '--rm', '--no-deps', 'cache-tools', 'python', '/lab4/seed.py', '--rows', '2000', env=env)
    compose('exec', '-T', 'redis', 'redis-cli', 'FLUSHDB', env=env)
    token = compose('exec', '-T', 'license-service-1', 'python', '-c',
                    "import jwt; from datetime import datetime,timedelta,timezone; from app.shared.config import settings; "
                    "print(jwt.encode({'sub':'1','role':'ADMIN','type':'access','exp':datetime.now(timezone.utc)+timedelta(hours=1)},settings.JWT_SECRET,algorithm='HS256'))", env=env)
    sql('SELECT pg_stat_statements_reset()')
    before = database_metrics()
    stop = threading.Event()
    samples = []
    def monitor():
        while not stop.is_set():
            result = subprocess.run(['docker', 'stats', '--no-stream', '--format', '{{json .}}'],
                                    capture_output=True, text=True)
            records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
            samples.append({'elapsed_s': round(time.monotonic() - started, 2),
                            'containers': [{'name': r['Name'].replace(PROJECT + '-', ''),
                                            'cpu_pct': float(r['CPUPerc'].strip('%')), 'memory': r['MemUsage']}
                                           for r in records if r.get('Name', '').startswith(PROJECT)]})
            stop.wait(1)
    started = time.monotonic()
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    try:
        output = compose('run', '--rm', '--no-deps', '-e', f'WORKLOAD={workload}', '-e', f'ADMIN_TOKEN={token}',
                         '-e', f'LEVELS={levels}', '-e', f'STAGE_SECONDS={seconds}', '-e', f'WARMUP_SECONDS={warmup}',
                         'k6', env=env)
    finally:
        stop.set()
        thread.join(timeout=5)
    line = next(line for line in output.splitlines() if line.startswith('LAB5_RESULT='))
    summary = json.loads(line.split('=', 1)[1])
    time.sleep(1)
    after = database_metrics()
    pool = json.loads(compose('exec', '-T', 'license-service-1', 'python', '-c',
                             "import json; from app.shared.database import engine; "
                             "print(json.dumps({'pool_size':engine.pool.size(),'max_overflow':engine.pool._max_overflow,'pool_timeout_seconds':engine.pool.timeout()}))"))
    return {'label': label, 'instances': instances, 'cache_enabled': enabled, 'summary': summary,
            'dataset_initial_licenses': 2000, 'connection_pool_per_instance': pool,
            'resource_peak_cpu_pct': peak_cpu(samples),
            'database': {k: after[k] - before[k] for k in before}, 'resources': samples}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--levels', default='1,10,25,50,100,200')
    parser.add_argument('--stage-seconds', type=int, default=8)
    parser.add_argument('--warmup-seconds', type=int, default=5)
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--only', choices=['read', 'write', 'workflow'])
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    try:
        vus = [int(value) for value in args.levels.split(',')]
    except ValueError:
        parser.error('levels must be comma-separated integers')
    if not vus or any(v < 1 or v > 2000 for v in vus) or vus != sorted(set(vus)):
        parser.error('levels must be unique ascending VUs between 1 and 2000')
    if args.stage_seconds < 1 or args.warmup_seconds < 1:
        parser.error('stage and warmup durations must be positive')
    configurations = [('read_three_cached', 'read', 3, True), ('write_three', 'write', 3, True),
                      ('workflow_three', 'workflow', 3, True), ('read_one_cached', 'read', 1, True),
                      ('read_three_uncached', 'read', 3, False)]
    if args.smoke:
        configurations = configurations[:1]
    if args.only:
        configurations = [c for c in configurations if c[1] == args.only]
    if args.output_dir:
        directory = args.output_dir.resolve()
        if not directory.is_relative_to(ROOT / 'load-tests' / 'results'):
            parser.error('Output must be inside load-tests/results')
        if any((directory / f'{c[0]}.json').exists() for c in configurations):
            parser.error('Results already exist; choose a new output directory')
    for label, workload, instances, enabled in configurations:
        print('RUN ' + label, flush=True)
        result = run(label, workload, instances, enabled, args.levels, args.stage_seconds, args.warmup_seconds)
        if args.output_dir:
            directory = args.output_dir.resolve()
            if not directory.is_relative_to(ROOT / 'load-tests' / 'results'):
                raise ValueError('Output must be inside load-tests/results')
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / f'{label}.json'
            if destination.exists():
                raise FileExistsError('Results already exist; choose a new output directory')
            destination.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print('LAB5_RUN=' + json.dumps({k: v for k, v in result.items() if k != 'resources'}), flush=True)
