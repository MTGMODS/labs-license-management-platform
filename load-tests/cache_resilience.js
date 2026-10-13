import http from 'k6/http';
import { sleep, check } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

const BASE = __ENV.BASE_URL || 'http://scaling-gateway:8080';
if (BASE !== 'http://scaling-gateway:8080') throw new Error('Only local lab gateway is allowed');
const MODE = __ENV.EXPERIMENT || 'avalanche';
if (!['avalanche', 'negative'].includes(MODE)) throw new Error('Select a bounded Lab 8 experiment');
const requests = new Counter('experiment_requests');
const latency = new Trend('experiment_latency', true);
const errors = new Rate('experiment_errors');
const hits = new Counter('experiment_hits');
const thresholds = {};
for (let second = 0; second < 16; second++) {
  thresholds['experiment_latency{second:' + second + '}'] = ['p(99)<30000'];
}
export const options = {
  vus: Number(__ENV.EXPERIMENT_VUS || 40), duration: __ENV.EXPERIMENT_DURATION || '12s',
  discardResponseBodies: true, summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'min', 'max'],
  thresholds,
};
http.setResponseCallback(http.expectedStatuses(MODE === 'negative' ? 404 : 200));
export function setup() { return { started: Date.now() }; }
export default function(data) {
  const id = (__VU + __ITER) % (MODE === 'avalanche' ? 24 : 5);
  const url = MODE === 'avalanche' ? '/api/v1/lab8/aggregate/' + id : '/api/v1/license/' + (9999990 + id);
  const response = http.get(BASE + url, { timeout: '30s',
    headers: MODE === 'negative' ? { Authorization: 'Bearer ' + __ENV.ADMIN_TOKEN } : {} });
  const correct = response.status === (MODE === 'negative' ? 404 : 200);
  requests.add(1);
  latency.add(response.timings.duration, { second: Math.floor((Date.now() - data.started) / 1000) });
  errors.add(!correct);
  hits.add(response.headers[MODE === 'negative' ? 'X-Negative-Cache' : 'X-Cache'] === 'HIT' ? 1 : 0);
  check(response, { 'expected business status': () => correct });
  sleep(.01);
}
export function handleSummary(data) {
  const measured = data.metrics.experiment_latency.values;
  return { stdout: 'LAB8_EXPERIMENT=' + JSON.stringify({ mode: MODE,
    requests: data.metrics.experiment_requests.values.count,
    hits: data.metrics.experiment_hits.values.count,
    avg_ms: measured.avg, p50_ms: measured.med, p95_ms: measured['p(95)'], p99_ms: measured['p(99)'],
    error_rate: data.metrics.experiment_errors.values.rate,
    latency_timeline: Object.entries(data.metrics).filter(([name]) => name.startsWith('experiment_latency{'))
      .map(([name, metric]) => ({ second: Number(name.match(/second:(\d+)/)[1]), ...metric.values })),
    elapsed_seconds: data.state.testRunDurationMs / 1000 }) + '\n' };
}
