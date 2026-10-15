# ІВСАВПЗ №5 — технічні подробиці та початкові вимірювання

## Тема та результат

Навантажувальне тестування та кількісний Performance Baseline системи після
лабораторних №1–4. Реалізовано k6-сценарії читання, запису та сквозного workflow,
ізольований Compose-стенд з фіксованими ресурсами, warm-up, ступеневе навантаження,
порівняння кількості instances і кешування, збір SQL/resource метрик та паспорт.

Результати — вимірювання локального стенда, не обіцянка production capacity.
Тести не звертаються до production API і не використовують бойові дані/токени.

## 2.1 Бізнес-сценарії

- **A Read-intensive:** `GET /api/v1/license/stats/public`, реальна агрегована
  статистика 2000 продажів. На warm cache SQL-агрегації пропускаються, але JSON
  передається й серіалізується. В uncached-режимі кожен запит обчислює статистику.
- **B Write-intensive:** `POST /api/v1/license/generate`, створення ліцензії та
  покупки в PostgreSQL, перевірка вхідних даних, генерація ключа, commit та
  розподілена інвалідація. Немає звернень до реальних платіжних сервісів.
- **C Complex workflow:** generate → find за ключем → PATCH суми → GET за ID
  з перевіркою нової суми → DELETE. Це 5 послідовних бізнес-запитів, із CRUD,
  транзакційною бізнес-логікою та перевіркою консистентності через балансувальник.

Для C додатково рахується кількість завершених workflows. HTTP RPS не є
workflow/s: один успішний workflow виконує 5 запитів. Перевірка неправильної
суми не приховується статусом 200: workflow зараховується лише після перевірки
нової суми. Report перевіряє, що без HTTP-помилок кожні 5 запитів відповідають
одному завершеному workflow, і відхиляє семантично некоректний результат.

## 2.2 Середовище та ізоляція

Контур: `docker-compose.scaling.yml` + `docker-compose.cache.yml` +
`docker-compose.load.yml`; project `mtgmods_load_baseline_lab5`.
Gateway — тільки `127.0.0.1:18105`; PostgreSQL/Redis/API не опубліковані.

Ресурси зафіксовані у load overlay:

- кожен API: 1 CPU, 512 MiB; CPU sets 0/1/2;
- PostgreSQL: 2 CPU, 768 MiB; CPU sets 3–4;
- Redis: 0.5 CPU, 128 MiB; CPU set 5;
- Nginx: 0.5 CPU, 128 MiB; CPU set 5;
- k6: 2 CPU, 1 GiB; окремі CPU sets 6–7.

Потрібно щонайменше 8 logical CPU, доступних Docker. Виміряний host має 12 CPU
та 16726044672 bytes RAM у Docker Desktop Linux containers, Engine 29.2.1.
CPU sets мінімізують конкуренцію генератора з SUT. Фізичний host, Docker VM,
мережа та фонові процеси все одно спільні — це не апаратно ізольований cluster.

k6 image зафіксований: `grafana/k6:1.0.0`. PostgreSQL 16, Redis 7.4, Nginx 1.27.
Один uvicorn process на instance. Реально перевірені параметри SQLAlchemy pool:
size 5, max overflow 10, timeout 30 s, до 15 connections на instance.

Перед кожним порівнянням відновлюється один і той самий початковий dataset:
2000 synthetic licenses + 2000 purchases. Write-сценарій природно збільшує
таблиці всередині прогону; це зафіксований профіль, не steady-state benchmark
незмінного dataset. Workflow видаляє власні успішно створені записи.

Reset-скрипт допускає TRUNCATE лише з явним `LAB5_DEMO=true` у cache-tools,
у строгому лабораторному режимі. Runner жорстко задає окремий Compose project.
Не можна переносити цей profile чи його прапорці до production.

## 2.3 Профіль навантаження та warm-up

`load-tests/baseline.js` використовує офіційний k6 constant-vus executor.
Після окремого 5-секундного warm-up послідовно виконуються ступені
**1 → 10 → 25 → 50 → 100 → 200 VUs**, по 8 секунд кожен.
Це ступеневий ramp-up через послідовні constant concurrency фази.

Між ступенями зарезервовано 10 секунд для завершення попередніх запитів;
graceful stop також 10 секунд. Warm-up виключений з перцентилів за level tags.
Для Python немає JVM-подібної JIT-фази: прогріваються runtime, DNS, ORM/DB pools,
типи asyncpg та Redis. Перша кешована статистика також створюється у warm-up.

RPS обчислюється як кількість завершених запитів / спостережений час фази,
включаючи drain після її 8 секунд. Це запобігає завищенню throughput, коли
високе навантаження залишає багато незавершених запитів. Timeout HTTP-запиту — 10 s.

Профіль є **closed model**: VU починає наступну операцію після завершення
попередньої. При уповільненні сервера offered load також зменшується — це ризик
coordinated omission. Тому результати не описують довільний незалежний потік
arrival-rate запитів. Для такого потоку потрібен окремий open-model executor.

## 2.4 Метрики та артефакти

Для кожного сценарію/ступеня зберігаються requests, observed duration, RPS,
average, p50, p95, p99, HTTP error rate та completed workflows.
Враховані всі завершені HTTP-запити, не лише успішні. p99 не підміняється average.

Runner також збирає CPU/Memory snapshot контейнерів та SQL calls/execution time
через `pg_stat_statements`. SQL-лічильник охоплює увесь прогін із warm-up,
транзакційними BEGIN/COMMIT/ROLLBACK і фоновими health probes; це не число
тільки бізнес-SELECT чи cache computations. Службові запити до самого
`pg_stat_statements` виключаються з підсумку.

Повні таблиці та графіки:
[results/lab5/metrics.md](../load-tests/results/lab5/metrics.md).
Сирі JSON, CSV та окремі SVG лежать поруч у `load-tests/results/lab5/`.
Графік error rate використовує частку 0–1; таблиця показує відсотки.
На графіках VUs — послідовні категорії ступенів, не рівномірна числова шкала.

Короткі 8-секундні фази дають laboratory baseline, але не доводять довготривалу
стабільність чи SLA. Під час повторного дослідження можна збільшити тривалість
і виконати кілька прогонів; точні числа не є універсальними для інших ноутбуків.

## 2.5 Насичення та bottleneck

Для паспорта задано лабораторні критерії прийнятності: p95 ≤ 500 ms,
p99 ≤ 1000 ms, error rate < 1%. Це явно обрані критерії стенда, не бізнес-SLA.

Насичення визначається окремо від SLO: сусідні ступені, де RPS зростає не більше
ніж на 20%, а p99 збільшується щонайменше на 50%, задають спостережений інтервал
насичення. Результат є інтервалом між випробуваними VUs, не математично точною
єдиною межею. Якщо ознак немає, report не вигадує saturation point.

Для трьох instances із warm Redis на 10 VUs виміряно приблизно **81.22 RPS**,
p95 **247.62 ms**, p99 **367.71 ms**, 0% HTTP-помилок. На 25 VUs — приблизно
80.18 RPS, p95 549.06 ms, p99 671.34 ms. Отже, між 10 і 25 VUs throughput
перестає зростати, а tail latency збільшується. На 200 VUs p99 сягає 2977.08 ms
без додаткового приросту throughput.

API доходять приблизно до 100% одного CPU кожен, Redis — нижче 10%, PostgreSQL —
близько 4% у cached read прогоні. Генератор не досягає своїх 2 CPU. Перший
підтверджений ресурсний bottleneck для warm statistics — **application CPU**.
Великий JSON і серіалізація є гіпотезою щодо hot path; точне профілювання — №9.
Для uncached-читання додаються SQL-агрегації й більші витрати CPU у PostgreSQL.

У write та workflow вже на 10→25 VUs також з’являється plateau RPS і зростання
p99. У всіх повних прогонах до 200 VUs зафіксовано 0% HTTP-помилок: насичення
проявляється чергою та latency, не обов’язково помилками чи падінням процесу.

## 2.6 Порівняльні експерименти

Один проти трьох instances: однаковий dataset, той самий k6-профіль, Redis і
ресурси на один API. У final одновузловому прогоні інші два API **зупинені**,
а Nginx має тільки один upstream. Контейнерні ресурси одного instance не
збільшуються до суми трьох. Scalability efficiency = RPS₃ / RPS₁ / 3.

Кеш: три instances із `STATS_CACHE_ENABLED=true` проти тих самих трьох із
`false`. У disabled режимі кожне читання є BYPASS і виконує SQL, а не тільки
один початковий MISS. Warm прогін має 3960 HIT і один MISS (у warm-up);
uncached прогін — 1671 BYPASS.

На 10 VUs: warm — 81.22 RPS і p95 247.62 ms, uncached — 30.68 RPS і
p95 553.77 ms. Redis дає приблизно **2.65× throughput** на цьому ступені.
За весь warm прогін зафіксовано 202 SQL calls проти 22272 без кешу —
понад 100× менше загальних SQL statements у цих прогонах. Через різний throughput
це не буквально «100× менше SQL на однакове число запитів»; абсолютні calls,
кількість HTTP-запитів та обмеження підрахунку наведені в JSON.

Порівняльні коефіцієнти кожного ступеня формуються у passport.json, не вводяться
вручну. Дані проміжного тесту з одним активним upstream, але трьома запущеними
контейнерами, збережені окремо як calibration і не використовуються у final
порівнянні actual 1 vs 3 instances.

## 2.7 Паспорт Performance Baseline

[System Performance Baseline](../load-tests/results/lab5/passport.json)
містить ресурсну конфігурацію, stable throughput, p95/p99, error rate,
інтервал насичення та первинний bottleneck. Окремі profiles показують усі три
бізнес-сценарії й контрольні режими; єдине RPS без назви workload не заявляється.

## Запуск та повторення

```powershell
$dc = @('-f', 'docker-compose.scaling.yml', '-f', 'docker-compose.cache.yml', '-f', 'docker-compose.load.yml')
docker compose @dc up --build -d
python load-tests/run_baseline.py --output-dir load-tests/results/my-run
python load-tests/report.py load-tests/results/my-run
```

Venv не обов’язковий для runner/report: це стандартна бібліотека Python 3.12+,
а самі запити генерує k6 у Docker. Якщо Python лише у локальному venv,
замініть `python` на `.\.venv\Scripts\python`.

`my-run` має бути новою директорією. Runner не перезаписує сирі результати,
відхиляє некоректні VUs/тривалості та не приймає довільного URL чи Compose project.
За потреби можна змінити `--levels` і `--stage-seconds`, але для контрольного
порівняння треба зберігати тотожний профіль в обох режимах.

Швидка live-демонстрація:

```powershell
python load-tests/run_baseline.py --smoke --levels 1,10,25 --stage-seconds 5
```

Окремий workflow або контрольна конфігурація:

```powershell
python load-tests/run_baseline.py --only workflow
python load-tests/run_baseline.py --configuration read_one_cached
```

Тести та статичний аналіз:

```powershell
.\.venv\Scripts\python -m pytest tests -q
.\.venv\Scripts\python -m ruff check services/license load-tests tests/test_load_baseline.py
```

Після експериментів повернення default трьох instances із кешем:

```powershell
docker compose @dc up -d --no-deps --force-recreate license-service-1 license-service-2 license-service-3 scaling-gateway
```

Зупинення без видалення synthetic DB:

```powershell
docker compose @dc down
```

## Інтерпретація перцентилів та меж

p50 описує типовий запит, p95/p99 — довший хвіст. Average може бути нижчим
за latency, яку отримує значна частина користувачів на межі насичення.
Коли CPU або connections стають обмеженням, запити очікують у черзі; throughput
майже не змінюється, а latency зростає. GC, scheduling, health probes та Docker
можуть додавати шум і окремі затримки. Профілювання їхніх причин не підмінюється
припущеннями. Поведінка інших workloads, open-loop arrival rate та довгі soak
прогони потребують окремих вимірювань.

Документація k6: [constant-vus](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-vus/),
[handleSummary](https://grafana.com/docs/k6/latest/results-output/end-of-test/custom-summary/).
