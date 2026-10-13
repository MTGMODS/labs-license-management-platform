"""Create reproducible tables/graphs from real Lab 8 avalanche and penetration runs."""
import argparse
import csv
import html
import json
from pathlib import Path


def sql_timeline(run):
    previous = {'elapsed_s': 0, 'calls': 0, 'execution_ms': 0}
    values = []
    for sample in run['database_timeline']:
        duration = sample['elapsed_s'] - previous['elapsed_s']
        if duration > 0:
            values.append({'x': sample['elapsed_s'], 'y': (sample['calls'] - previous['calls']) / duration})
        previous = sample
    return values


def postgres_cpu(run):
    if run.get('postgres_cpu_cgroup'):
        return [{'x': p['elapsed_s'], 'y': p['cpu_pct']} for p in run['postgres_cpu_cgroup']]
    return [{'x': sample['elapsed_s'], 'y': item['cpu_pct']}
            for sample in run['resources'] for item in sample['containers']
            if item['name'] == 'license-postgres-1']


def timeline(series, title, ylabel):
    maximum_x = max((point['x'] for _, points in series for point in points), default=1) or 1
    maximum_y = max((point['y'] for _, points in series for point in points), default=1) or 1
    lines = ['<svg xmlns="http://www.w3.org/2000/svg" width="800" height="340" viewBox="0 0 800 340">',
             '<rect width="100%" height="100%" fill="white"/>',
             f'<text x="55" y="25" font-family="sans-serif" font-size="17">{html.escape(title)}</text>',
             '<path d="M55 55 V275 H755" fill="none" stroke="#94a3b8"/>',
             f'<text x="55" y="45" font-family="sans-serif" font-size="12">{html.escape(ylabel)}; max {maximum_y:.1f}</text>',
             f'<text x="620" y="297" font-family="sans-serif" font-size="12">Час, 0–{maximum_x:.1f} s</text>']
    for index, (label, points) in enumerate(series):
        color = '#ef4444' if index == 0 else '#16a34a'
        coordinates = ' '.join(f'{55+700*p["x"]/maximum_x:.1f},{275-210*p["y"]/maximum_y:.1f}' for p in points)
        lines += [f'<polyline points="{coordinates}" fill="none" stroke="{color}" stroke-width="2"/>',
                  f'<text x="{55+350*index}" y="325" fill="{color}" font-family="sans-serif" font-size="13">{html.escape(label)}</text>']
    return '\n'.join(lines + ['</svg>']) + '\n'


def generate(directory):
    rows = []
    for mode in ('avalanche', 'negative'):
        runs = json.loads((directory / f'{mode}.json').read_text(encoding='utf-8'))
        for run in runs:
            summary = run['summary']
            if summary['error_rate'] != 0 or summary['requests'] < 1:
                raise ValueError('Experiment has failed business responses or no requests')
            rows.append({'mode': mode, 'label': run['label'], 'requests': summary['requests'],
                         'rps': summary['rps'], 'p95_ms': summary['p95_ms'], 'p99_ms': summary['p99_ms'],
                         'error_rate': summary['error_rate'], 'sql_calls': run['database']['calls'],
                         'sql_execution_ms': run['database']['execution_ms'],
                         'peak_sampled_pg_cpu_pct': max((p['y'] for p in postgres_cpu(run)), default=0),
                         'peak_sampled_sql_calls_per_s': max((p['y'] for p in sql_timeline(run)), default=0),
                         'peak_second_p99_ms': max(p['p(99)'] for p in summary['latency_timeline'])})
        if mode in ('avalanche', 'negative'):
            labels = ['Синхронний TTL', 'TTL jitter'] if mode == 'avalanche' else ['Без negative cache', 'Negative cache']
            for suffix, title, ylabel, extract in (
                ('sql-timeline', 'Завершені бізнес SELECT у часі', 'SELECT/s', sql_timeline),
                ('cpu-timeline', 'Утилізація PostgreSQL', 'CPU %', postgres_cpu),
                ('latency-timeline', 'Затримка за односекундними вікнами', 'p99 ms',
                 lambda r: [{'x': p['second'], 'y': p['p(99)']} for p in
                            sorted(r['summary']['latency_timeline'], key=lambda p: p['second'])
                            if p['second'] < 12]),
            ):
                (directory / f'{mode}-{suffix}.svg').write_text(
                    timeline([(label, extract(run)) for label, run in zip(labels, runs)], title, ylabel), encoding='utf-8')
    with (directory / 'comparison.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    columns = ['Режим', 'SELECT', 'RPS', 'p95 ms', 'p99 ms', 'Peak PG CPU %', 'Peak 1s p99 ms']
    text = ['# Вимірювання додаткових сценаріїв №8', '', '| ' + ' | '.join(columns) + ' |',
            '|---|---:|---:|---:|---:|---:|---:|']
    for row in rows:
        text.append(f'| {row["mode"]} / {row["label"]} | {row["sql_calls"]} | {row["rps"]:.2f} | '
                    f'{row["p95_ms"]:.2f} | {row["p99_ms"]:.2f} | {row["peak_sampled_pg_cpu_pct"]:.2f} | '
                    f'{row["peak_second_p99_ms"]:.2f} |')
    text += ['', 'HTTP 404 у negative-сценарії є очікуваною відповіддю, не помилкою.',
             'Обидва negative-прогони мають 5 попередніх SQL lookup; вони не входять у таблицю.',
             'CPU — cgroup usage_usec із кроком близько 100 ms; Docker CPU/RAM samples також у JSON.',
             'Це sampled peak, не безперервний максимум. Одне ядро = 100%. Монітор працює в обох режимах.',
             'SQL timeline має wall-clock runner; latency timeline — k6 clock. Їхній нуль не тотожний.',
             '12-секундний closed-model експеримент не є production SLA чи багатогодинним тестом.']
    (directory / 'metrics.md').write_text('\n'.join(text) + '\n', encoding='utf-8')
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    print(json.dumps(generate(parser.parse_args().directory), indent=2))
