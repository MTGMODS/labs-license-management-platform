"""Generate the baseline passport, CSV metric matrices and native SVG charts."""
import argparse
import csv
import html
import json
from pathlib import Path


def classify(rows, p95_limit=500, p99_limit=1000, error_limit=0.01):
    stable = [r for r in rows if r['p95_ms'] <= p95_limit and r['p99_ms'] <= p99_limit
              and r['error_rate'] < error_limit and r['requests'] > 0]
    knee = None
    for previous, current in zip(rows, rows[1:]):
        if (current['rps'] <= previous['rps'] * 1.2
                and current['p99_ms'] >= previous['p99_ms'] * 1.5):
            knee = [previous['vus'], current['vus']]
            break
    return {'stable': max(stable, key=lambda r: r['rps']) if stable else None,
            'saturation_interval_vus': knee, 'slo': {'p95_ms': p95_limit, 'p99_ms': p99_limit,
                                                   'error_rate_below': error_limit}}


def svg_chart(rows, metrics, title):
    width, height = 720, 320
    left, top, plotw, ploth = 70, 45, 610, 210
    maximum = max((r[m] for r in rows for m in metrics), default=1) or 1
    colors = ['#0ea5e9', '#22c55e', '#a855f7', '#f59e0b']
    chunks = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
              '<rect width="100%" height="100%" fill="white"/>',
              f'<text x="{left}" y="25" font-family="sans-serif" font-size="16">{html.escape(title)}</text>']
    for tick in range(5):
        value = maximum * tick / 4
        y = top + ploth - ploth * tick / 4
        chunks.append(f'<path d="M{left},{y} h{plotw}" stroke="#e2e8f0"/>')
        chunks.append(f'<text x="{left-8}" y="{y+4}" text-anchor="end" font-family="sans-serif" font-size="11">{value:.1f}</text>')
    for i, row in enumerate(rows):
        x = left + plotw * i / max(len(rows) - 1, 1)
        chunks.append(f'<text x="{x}" y="{top+ploth+20}" text-anchor="middle" font-family="sans-serif" font-size="12">{row["vus"]} VUs</text>')
    for index, metric in enumerate(metrics):
        points = ' '.join(f'{left+plotw*i/max(len(rows)-1,1)},{top+ploth-ploth*r[metric]/maximum}' for i, r in enumerate(rows))
        color = colors[index % len(colors)]
        chunks.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>')
        chunks.append(f'<text x="{left+index*150}" y="305" fill="{color}" font-family="sans-serif" font-size="12">{html.escape(metric)}</text>')
    chunks.append('</svg>')
    return '\n'.join(chunks) + '\n'


def generate(directory):
    datasets = []
    matrices = ['# Виміряні метрики лабораторної №5', '',
                'Warm-up не входить у ці перцентилі. RPS враховує час завершення запитів після ступеня.', '']
    for source in sorted(directory.glob('*.json')):
        if source.name == 'passport.json':
            continue
        data = json.loads(source.read_text(encoding='utf-8'))
        if 'summary' not in data:
            continue
        datasets.append(data)
        rows = data['summary']['rows']
        if data['summary'].get('workload') == 'workflow':
            for row in rows:
                if row['error_rate'] == 0 and row['requests'] != 5 * row['completed_workflows']:
                    raise ValueError('Incomplete or semantically invalid workflow despite HTTP 200')
        matrices += [f'## {data["label"]}', '',
                     '| VUs | RPS | Avg ms | p50 ms | p95 ms | p99 ms | Помилки % |',
                     '|---:|---:|---:|---:|---:|---:|---:|']
        for row in rows:
            matrices.append(f'| {row["vus"]} | {row["rps"]:.2f} | {row["avg_ms"]:.2f} | {row["p50_ms"]:.2f} | {row["p95_ms"]:.2f} | {row["p99_ms"]:.2f} | {row["error_rate"]*100:.2f} |')
        matrices += ['', f'![Пропускна спроможність]({data["label"]}-throughput.svg)',
                     f'![Затримки]({data["label"]}-latency.svg)', f'![Помилки]({data["label"]}-errors.svg)', '']
        with (directory / f'{data["label"]}.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        for suffix, metrics in [('throughput', ['rps']), ('latency', ['avg_ms', 'p50_ms', 'p95_ms', 'p99_ms']),
                                ('errors', ['error_rate'])]:
            (directory / f'{data["label"]}-{suffix}.svg').write_text(
                svg_chart(rows, metrics, f'{data["label"]} / {suffix}'), encoding='utf-8')
    if not datasets:
        raise ValueError('No measured results found')
    passport = {'date': '2026-10-13', 'scope': 'local controlled laboratory baseline, not production capacity',
                'profiles': {data['label']: classify(data['summary']['rows']) for data in datasets}}
    labels = {d['label']: d for d in datasets}
    if 'read_three_cached' in labels:
        main = passport['profiles']['read_three_cached']
        passport['system_performance_baseline'] = {
            'primary_workload': 'Scenario A: public statistics, three instances, warm Redis',
            'environment': '12 logical CPU / 16726044672 bytes Docker RAM; per-instance 1 CPU/512MiB; DB 2 CPU/768MiB',
            'stable_throughput_rps': main['stable']['rps'] if main['stable'] else None,
            'p95_ms': main['stable']['p95_ms'] if main['stable'] else None,
            'p99_ms': main['stable']['p99_ms'] if main['stable'] else None,
            'error_rate': main['stable']['error_rate'] if main['stable'] else None,
            'saturation_interval_vus': main['saturation_interval_vus'],
            'critical_scenario': 'Scenario A without cache: repeated public statistics aggregation',
            'error_rate_at_observed_saturation': next((r['error_rate'] for r in labels['read_three_cached']['summary']['rows']
                                                     if main['saturation_interval_vus'] and r['vus'] == main['saturation_interval_vus'][1]), None),
            'primary_bottleneck': 'Application CPU; exact hot path requires profiling (Lab 9)',
            'evidence_peak_cpu_pct': labels['read_three_cached'].get('resource_peak_cpu_pct', {})}
    if all(k in labels for k in ['read_three_cached', 'read_one_cached', 'read_three_uncached']):
        passport['comparisons'] = []
        for row, one, uncached in zip(labels['read_three_cached']['summary']['rows'],
                                     labels['read_one_cached']['summary']['rows'],
                                     labels['read_three_uncached']['summary']['rows']):
            assert row['vus'] == one['vus'] == uncached['vus']
            passport['comparisons'].append({'vus': row['vus'], 'scaling_ratio': row['rps'] / one['rps'],
                                           'scaling_efficiency': row['rps'] / one['rps'] / 3,
                                           'caching_ratio': row['rps'] / uncached['rps'],
                                           'cached_p95_ms': row['p95_ms'], 'uncached_p95_ms': uncached['p95_ms']})
    (directory / 'passport.json').write_text(json.dumps(passport, indent=2) + '\n', encoding='utf-8')
    (directory / 'metrics.md').write_text('\n'.join(matrices) + '\n', encoding='utf-8')
    return passport


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(generate(args.directory), indent=2))
