import http from 'k6/http';
import { sleep, check, fail } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

const BASE = __ENV.BASE_URL || 'http://scaling-gateway:8080';
if (BASE !== 'http://scaling-gateway:8080') throw new Error('Only local lab gateway is allowed');
const VUS = Number(__ENV.BURST_VUS || 500);
const requests = new Counter('burst_requests');
const latency = new Trend('burst_latency', true);
const errors = new Rate('burst_errors');
export const options = { scenarios: { burst: { executor: 'per-vu-iterations', vus: VUS, iterations: 1,
  maxDuration: '120s' } }, discardResponseBodies: true,
  summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'min', 'max'] };

export function setup() {
  if (http.get(BASE + '/health/live').status !== 200) fail('Lab unavailable');
  return { gate: Date.now() + 5000 };
}
export default function(data) {
  sleep(Math.max(0, (data.gate - Date.now()) / 1000));
  const response = http.get(BASE + '/api/v1/license/stats/public', { timeout: '100s' });
  requests.add(1);
  latency.add(response.timings.duration);
  errors.add(response.status !== 200);
  check(response, { 'stats response 200': r => r.status === 200 });
}
export function handleSummary(data) {
  const measured = data.metrics.burst_latency.values;
  return { stdout: 'LAB8_RESULT=' + JSON.stringify({ vus: VUS, requests: data.metrics.burst_requests.values.count,
    avg_ms: measured.avg, p50_ms: measured.med, p95_ms: measured['p(95)'], p99_ms: measured['p(99)'],
    error_rate: data.metrics.burst_errors.values.rate,
    elapsed_seconds: data.state.testRunDurationMs / 1000 }) + '\n' };
}
