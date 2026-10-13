"""Generate Lab 8 comparison artifacts from measured burst data."""
import argparse
import csv
import html
import json
from pathlib import Path


def chart(rows, field, title):
    maximum = max(row[field] for row in rows) or 1
    result = ['<svg xmlns="http://www.w3.org/2000/svg" width="640" height="300" viewBox="0 0 640 300">',
              '<rect width="100%" height="100%" fill="white"/>',
              f'<text x="40" y="25" font-family="sans-serif" font-size="16">{html.escape(title)}</text>']
    for index, row in enumerate(rows):
        x = 100 + 270 * index
        height = max(1, 170 * row[field] / maximum)
        color = '#22c55e' if row['protected'] else '#ef4444'
        label = 'Distributed lock' if row['protected'] else 'Без координації'
        result += [f'<rect x="{x}" y="{250-height}" width="130" height="{height}" fill="{color}"/>',
                   f'<text x="{x+65}" y="{240-height}" text-anchor="middle" font-family="sans-serif" font-size="14">{row[field]:.2f}</text>',
                   f'<text x="{x+65}" y="275" text-anchor="middle" font-family="sans-serif" font-size="14">{label}</text>']
    return '\n'.join(result + ['</svg>']) + '\n'


def generate(directory):
    rows = json.loads((directory / 'stampede.json').read_text(encoding='utf-8'))
    assert len(rows) == 2 and rows[0]['protected'] is False and rows[1]['protected'] is True
    if rows[1]['error_rate'] != 0 or rows[1]['computations'] != 1:
        raise ValueError('Protected mode has not met its acceptance criteria')
    with (directory / 'comparison.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for metric, title in [('computations', 'Обчислення статистики'), ('stats_select_calls', 'Бізнес SELECT запити'),
                          ('p95_ms', 'p95 latency ms'), ('p99_ms', 'p99 latency ms')]:
        (directory / f'{metric}.svg').write_text(chart(rows, metric, title), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    generate(parser.parse_args().directory)
