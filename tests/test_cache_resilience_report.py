"""Metric extraction and XML safety for Lab 8 reports."""
import importlib.util
from pathlib import Path
from xml.etree import ElementTree


def module():
    path = Path(__file__).resolve().parents[1] / 'load-tests/report_cache_resilience.py'
    spec = importlib.util.spec_from_file_location('cache_resilience_report', path)
    instance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(instance)
    return instance


def test_sql_rate_accounts_for_variable_sampling_intervals():
    data = {'database_timeline': [{'elapsed_s': 1, 'calls': 0, 'execution_ms': 0},
                                 {'elapsed_s': 3, 'calls': 24, 'execution_ms': 1000}]}
    assert module().sql_timeline(data) == [{'x': 1, 'y': 0}, {'x': 3, 'y': 12}]


def test_timeline_has_safe_xml_and_finite_axes():
    svg = module().timeline([('a < b & c', [{'x': 0, 'y': 0}])], 'SQL < CPU', 'SELECT/s')
    ElementTree.fromstring(svg)
    assert '&lt;' in svg and '&amp;' in svg and 'nan' not in svg
