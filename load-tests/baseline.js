import http from 'k6/http';
import exec from 'k6/execution';
import { check, fail } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

const BASE = __ENV.BASE_URL || 'http://scaling-gateway:8080';
if (BASE !== 'http://scaling-gateway:8080') throw new Error('Only the isolated lab gateway is allowed');
const WORKLOAD = __ENV.WORKLOAD || 'read';
if (!['read', 'write', 'workflow'].includes(WORKLOAD)) throw new Error('Invalid WORKLOAD');
const LEVELS = (__ENV.LEVELS || '1,10,25,50,100,200').split(',').map(Number);
const DURATION = Number(__ENV.STAGE_SECONDS || 8);
const WARMUP = Number(__ENV.WARMUP_SECONDS || 5);
if (LEVELS.some(v => !Number.isInteger(v) || v < 1 || v > 2000) || DURATION < 1) throw new Error('Invalid load profile');
const requests = new Counter('business_requests');
const latency = new Trend('business_latency', true);
const errors = new Rate('business_errors');
const completion = new Trend('completion_offset_ms');
const workflowValid = new Rate('workflow_valid');
const workflows = new Counter('completed_workflows');
const cacheHits = new Counter('cache_hits');
const cacheMisses = new Counter('cache_misses');
const cacheBypass = new Counter('cache_bypass');
const thresholds = {};
const scenarios = {
  warmup: { executor: 'constant-vus', exec: 'exercise', vus: 1, duration: `${WARMUP}s`,
    gracefulStop: '10s', tags: { phase: 'warmup', level: '0' } },
};
LEVELS.forEach((vus, index) => {
  scenarios[`level_${vus}`] = { executor: 'constant-vus', exec: 'exercise', vus,
    duration: `${DURATION}s`, startTime: `${WARMUP + 10 + index * (DURATION + 10)}s`,
    gracefulStop: '10s', tags: { phase: 'measure', level: String(vus) } };
  // Thresholds materialize tag-specific submetrics, not arbitrary SLO promises.
  thresholds[`business_latency{level:${vus}}`] = ['p(99)>=0'];
  thresholds[`business_requests{level:${vus}}`] = ['count>=0'];
  thresholds[`business_errors{level:${vus}}`] = ['rate>=0'];
  thresholds[`completed_workflows{level:${vus}}`] = ['count>=0'];
  thresholds[`completion_offset_ms{level:${vus}}`] = ['max>=0'];
});
export const options = { scenarios, thresholds, discardResponseBodies: true,
  summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'min', 'max'],
  tags: { workload: WORKLOAD }, systemTags: ['status', 'method', 'name', 'scenario', 'expected_response'] };

function tags() { return { level: exec.scenario.name === 'warmup' ? '0' : exec.scenario.name.replace('level_', '') }; }
function send(method, path, body, text = false) {
  const params = { headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${__ENV.ADMIN_TOKEN}` },
    tags: { name: path.replace(/\/\d+$/, '/:id').replace(/\?key=.*/, '?key=:key') },
    timeout: '10s', responseType: text ? 'text' : 'none' };
  const response = http.request(method, BASE + path, body === null ? null : JSON.stringify(body), params);
  const t = tags();
  requests.add(1, t);
  completion.add(Date.now() - exec.scenario.startTime, t);
  latency.add(response.timings.duration, t);
  errors.add(response.status !== 200, t);
  check(response, { 'business response 200': r => r.status === 200 });
  if (response.headers['X-Cache'] === 'HIT') cacheHits.add(1, t);
  if (response.headers['X-Cache'] === 'MISS') cacheMisses.add(1, t);
  if (response.headers['X-Cache'] === 'BYPASS') cacheBypass.add(1, t);
  return response;
}

export function setup() {
  if (!__ENV.ADMIN_TOKEN && WORKLOAD !== 'read') fail('Provide a short-lived lab ADMIN_TOKEN');
  const r = http.get(BASE + '/health/live');
  if (r.status !== 200) fail('Lab gateway unavailable');
}

export function exercise() {
  if (WORKLOAD === 'read') { send('GET', '/api/v1/license/stats/public', null); return; }
  const created = send('POST', '/api/v1/license/generate',
    { duration_days: 30, amount: 3, method: 'Stars', max_devices: 2, reset_limit: 0 }, true);
  if (created.status !== 200 || WORKLOAD === 'write') return;
  const key = created.json('data.key');
  const found = send('GET', '/api/v1/license/find?key=' + key, null, true);
  if (found.status !== 200) return;
  const id = found.json('data.0.id');
  if (!id) { workflowValid.add(false, tags()); return; }
  try {
    const patched = send('PATCH', '/api/v1/license/' + id, { amount: 7 }, true);
    if (patched.status !== 200) return;
    const read = send('GET', '/api/v1/license/' + id, null, true);
    const valid = read.status === 200 && read.json('data.transaction.amount') === 7;
    workflowValid.add(valid, tags());
    if (valid) workflows.add(1, tags());
  } finally {
    send('DELETE', '/api/v1/license/' + id, null);
  }
}

export function handleSummary(data) {
  const rows = LEVELS.map(vus => {
    const metric = name => data.metrics[`${name}{level:${vus}}`]?.values || {};
    const count = metric('business_requests').count || 0;
    const observed = Math.max(DURATION, (metric('completion_offset_ms').max || 0) / 1000);
    return { vus, requests: count, observed_seconds: observed, rps: count / observed, avg_ms: metric('business_latency').avg || 0,
      p50_ms: metric('business_latency').med || 0, p95_ms: metric('business_latency')['p(95)'] || 0,
      p99_ms: metric('business_latency')['p(99)'] || 0, error_rate: metric('business_errors').rate || 0,
      completed_workflows: metric('completed_workflows').count || 0 };
  });
  return { stdout: 'LAB5_RESULT=' + JSON.stringify({ workload: WORKLOAD, stage_seconds: DURATION,
    warmup_seconds: WARMUP, rows, cache_hits: data.metrics.cache_hits?.values.count || 0,
    cache_misses: data.metrics.cache_misses?.values.count || 0,
    cache_bypass: data.metrics.cache_bypass?.values.count || 0 }) + '\n' };
}
