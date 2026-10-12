"""Deterministic tests for baseline analysis, no load against services in CI."""
import importlib.util
import json
import pytest
from pathlib import Path
from xml.etree import ElementTree


def module():
    path = Path(__file__).resolve().parents[1] / 'load-tests' / 'report.py'
    spec = importlib.util.spec_from_file_location('load_report', path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def rows():
    return [{'vus': 1, 'rps': 100, 'requests': 100, 'p95_ms': 30, 'p99_ms': 40,
             'avg_ms': 20, 'p50_ms': 18, 'error_rate': 0},
            {'vus': 10, 'rps': 110, 'requests': 110, 'p95_ms': 300, 'p99_ms': 400,
             'avg_ms': 200, 'p50_ms': 180, 'error_rate': 0},
            {'vus': 100, 'rps': 112, 'requests': 112, 'p95_ms': 3000, 'p99_ms': 4000,
             'avg_ms': 2000, 'p50_ms': 1800, 'error_rate': .1}]


def test_saturation_is_distinct_from_slo():
    result = module().classify(rows())
    assert result['stable']['vus'] == 10
    assert result['saturation_interval_vus'] == [1, 10]


def test_no_stable_profile_and_no_invented_saturation():
    result = module().classify([rows()[-1]])
    assert result['stable'] is None and result['saturation_interval_vus'] is None


def test_svg_escapes_text_and_is_valid():
    result = module().svg_chart(rows(), ['rps'], 'x < y & z')
    ElementTree.fromstring(result)
    assert '&lt;' in result and '&amp;' in result


def test_report_keeps_measured_data_and_generates_passport(tmp_path):
    source = {'label': 'sample', 'summary': {'rows': rows()}}
    (tmp_path / 'sample.json').write_text(json.dumps(source), encoding='utf-8')
    result = module().generate(tmp_path)
    assert result['profiles']['sample']['stable']['vus'] == 10
    assert (tmp_path / 'sample.csv').exists()
    assert (tmp_path / 'sample-latency.svg').exists()
    assert json.loads((tmp_path / 'sample.json').read_text()) == source


def test_http_success_does_not_hide_invalid_workflows(tmp_path):
    data = rows()[0]
    data['completed_workflows'] = 0
    (tmp_path / 'bad.json').write_text(json.dumps({'label': 'bad', 'summary': {'workload': 'workflow', 'rows': [data]}}))
    with pytest.raises(ValueError, match='workflow'):
        module().generate(tmp_path)
