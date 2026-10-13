# Вимірювання додаткових сценаріїв №8

| Режим | SELECT | RPS | p95 ms | p99 ms | Peak PG CPU % | Peak 1s p99 ms |
|---|---:|---:|---:|---:|---:|---:|
| avalanche / synchronized | 24 | 907.32 | 88.54 | 165.70 | 156.61 | 678.91 |
| avalanche / jitter | 24 | 855.81 | 96.31 | 143.91 | 104.92 | 201.81 |
| negative / unprotected | 8177 | 676.51 | 146.08 | 489.85 | 225.59 | 950.49 |
| negative / protected | 0 | 1271.97 | 60.18 | 125.61 | 56.08 | 219.72 |

HTTP 404 у negative-сценарії є очікуваною відповіддю, не помилкою.
Обидва negative-прогони мають 5 попередніх SQL lookup; вони не входять у таблицю.
CPU — cgroup usage_usec із кроком близько 100 ms; Docker CPU/RAM samples також у JSON.
Це sampled peak, не безперервний максимум. Одне ядро = 100%. Монітор працює в обох режимах.
SQL timeline має wall-clock runner; latency timeline — k6 clock. Їхній нуль не тотожний.
12-секундний closed-model експеримент не є production SLA чи багатогодинним тестом.
