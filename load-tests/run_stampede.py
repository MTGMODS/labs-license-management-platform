"""Reproducible protected/unprotected cold-key burst in a separate Lab 8 project."""
import argparse
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = 'mtgmods_cache_resilience_lab8'
KEY = 'mtgmods:license:public_stats:v2'
FILES = ['-f', 'docker-compose.scaling.yml', '-f', 'docker-compose.cache.yml',
         '-f', 'docker-compose.load.yml', '-f', 'docker-compose.resilience.yml']


def compose(*args, env=None):
    result = subprocess.run(['docker', 'compose', '-p', PROJECT, *FILES, *args], cwd=ROOT,
                            env=env, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr + '\n' + result.stdout)
    return result.stdout.strip()


def sql(statement):
    return compose('exec', '-T', 'license-postgres', 'psql', '-U', 'mtg_license', '-d', 'licenses', '-Atc', statement)


def run(protected, vus):
    env = dict(os.environ, LAB8_PROTECTED=str(protected).lower())
    compose('up', '-d', '--no-deps', '--force-recreate', 'license-service-1', 'license-service-2', 'license-service-3', env=env)
    time.sleep(3)
    compose('run', '--rm', '--no-deps', 'cache-tools', 'python', '/load-tests/reset_dataset.py', env=env)
    compose('run', '--rm', '--no-deps', 'cache-tools', 'python', '/lab4/seed.py', '--rows', '2000', env=env)
    compose('exec', '-T', 'redis', 'redis-cli', 'FLUSHDB', env=env)
    compose('up', '-d', '--no-deps', '--force-recreate', 'scaling-gateway', env=env)
    sql('SELECT pg_stat_statements_reset()')
    output = compose('run', '--rm', '--no-deps', '-e', f'BURST_VUS={vus}', 'k6', 'run', '/tests/stampede.js', env=env)
    summary = json.loads(next(line for line in output.splitlines() if line.startswith('LAB8_RESULT=')).split('=', 1)[1])
    computations = int(compose('exec', '-T', 'redis', 'redis-cli', 'GET', KEY + ':computations'))
    time.sleep(1)
    # Count actual SELECT statements of this aggregate, not transaction/probe overhead.
    calls = int(sql("SELECT coalesce(sum(calls),0) FROM pg_stat_statements WHERE (query ILIKE '%FROM licenses%' "
                    "OR query ILIKE '%FROM transaction_purchases%') "
                    "AND query NOT ILIKE '%pg_stat_statements%'"))
    summary.update(protected=protected, computations=computations, stats_select_calls=calls,
                   generation_delay_ms=2000)
    # Custom burst metrics exclude setup /health/live, elapsed time includes gate.
    summary['burst_rps_including_gate'] = summary['requests'] / summary['elapsed_seconds']
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--vus', type=int, default=500)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    if not 1 <= args.vus <= 1000:
        parser.error('vus must be between 1 and 1000')
    if args.output_dir:
        directory = args.output_dir.resolve()
        if not directory.is_relative_to(ROOT / 'load-tests/results') or (directory / 'stampede.json').exists():
            parser.error('Use a new output directory inside load-tests/results')
    try:
        results = [run(False, args.vus), run(True, args.vus)]
    finally:
        # Do not leave the demonstration unprotected after an exception/abort.
        env = dict(os.environ, LAB8_PROTECTED='true')
        compose('up', '-d', '--no-deps', '--force-recreate', 'license-service-1', 'license-service-2',
                'license-service-3', 'scaling-gateway', env=env)
    if results[1]['computations'] != 1:
        raise AssertionError('Protected cold burst must compute the aggregate once')
    if results[1]['requests'] != args.vus or results[1]['error_rate'] != 0:
        raise AssertionError('Protected burst must successfully complete every request')
    print(json.dumps(results, indent=2))
    if args.output_dir:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'stampede.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
