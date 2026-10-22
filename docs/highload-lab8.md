# Лабораторна робота №8 — Інженерія стійкості кешу

На стенді License Service відтворено Cache Stampede, Cache Avalanche та Cache Penetration. Реалізовано Redis lock, TTL jitter і negative caching; ефект підтверджено вимірюваннями SQL, затримок і ресурсів БД.

## Запуск середовища

Команди для **Windows CMD**, послідовно в одному вікні. Потрібні Python 3.12+, Docker Desktop Linux containers, Compose 2.24.4+ та **8+ logical CPU у Docker**. Інші навантажувальні стенди та Minikube мають бути зупинені. Використовується окрема синтетична БД, не production.

```cmd
cd /d C:\MTGMODS\labs-license-management-platform
set "LABPY=python"
if exist ".venv\Scripts\python.exe" set "LABPY=.venv\Scripts\python.exe"
"%LABPY%" --version
docker info --format "OS={{.OSType}}; logical CPU={{.NCPU}}"
set "LAB4_CACHE_ENABLED=true"
set "LAB4_CACHE_TTL_SECONDS=300"
set "LAB8_PROTECTED=true"
set "LAB8_NEGATIVE_CACHE=true"
set "DC8=docker compose -p mtgmods_cache_resilience_lab8 -f docker-compose.scaling.yml -f docker-compose.cache.yml -f docker-compose.load.yml -f docker-compose.resilience.yml"
%DC8% build license-db-init
%DC8% up -d --wait --wait-timeout 180
%DC8% up -d --no-deps --force-recreate scaling-gateway
%DC8% run --rm -T --no-deps cache-tools python /load-tests/reset_dataset.py < NUL
%DC8% run --rm -T --no-deps cache-tools python /lab4/seed.py --rows 2000 < NUL
%DC8% ps
```

Три API, Redis і PostgreSQL доступні через Nginx на `127.0.0.1:18108`. Runner-и скидають лише власні навчальні БД/Redis, самі готують авторизацію та повертають захист у ввімкнений стан через `finally`.

## 2.1. Вибір ресурсоємного об'єкта

Публічна статистика продажів: `GET /api/v1/license/stats/public`. Один розрахунок виконує **11 бізнес-SELECT**; масове одночасне читання холодного ключа дублює цю роботу. Код агрегацій — [repository.py:154](../services/license/app/infrastructure/repository.py#L154).

У [docker-compose.resilience.yml](../docker-compose.resilience.yml) задано однакову для обох режимів **штучну затримку 2 s** перед SQL, щоб відтворити конкурентний MISS. Це контрольоване підсилення експерименту, не production-затримка.

## 2.2. Відтворення Cache Stampede та Thundering Herd

[run_stampede.py:28](../load-tests/run_stampede.py#L28) очищає кеш і запускає два однакові bursts по 500 VUs: без координації та з Redis lock. Усі запити очікують реальну статистику. `computations` рахує розрахунки; `stats_select_calls` — фактичні бізнес-SELECT у `pg_stat_statements`.

```cmd
set "BURST8=load-tests\results\lab8-burst-%RANDOM%-%RANDOM%"
"%LABPY%" load-tests\run_stampede.py --vus 500 --output-dir "%BURST8%" < NUL
"%LABPY%" load-tests\report_resilience.py "%BURST8%"
start "" "%BURST8%\computations.svg"
```

Без захисту багато запитів паралельно рахують один ключ; ця хвиля звернень до БД є thundering herd. Із захистом очікуються **500 успішних відповідей, 1 розрахунок і 11 SELECT**. Порівняльні числа містяться у виводі, `stampede.json` і `comparison.csv`; незадовільний protected-результат завершує runner помилкою.

## 2.3. Cache Avalanche та синхронне завершення TTL

Створено **24 різні агрегати продажів**, окремі ключі та 40 VUs на 12 s. Порівнюються одночасне завершення TTL через 6 s і розподілене через 3–9 s. Lock увімкнений в обох режимах: досліджується сплеск різних ключів, не stampede одного.

```cmd
set "EXTRA8=load-tests\results\lab8-extra-%RANDOM%-%RANDOM%"
"%LABPY%" load-tests\run_cache_resilience.py --only avalanche --output-dir "%EXTRA8%" < NUL
```

Очікуються `synchronized` та `jitter`, по **24 SELECT**, без помилок. SQL, CPU та latency timelines зберігаються в `avalanche.json`; графіки будуються в п. 2.7. Jitter зменшує одночасний пік, а не кількість необхідних розрахунків.

Код — [run_cache_resilience.py:113](../load-tests/run_cache_resilience.py#L113) і [lab8_routes.py](../services/license/app/api/lab8_routes.py). Навчальний SQL використовує однакове підсилення роботи ×4000 в обох режимах. Endpoint `/api/v1/lab8/aggregate/{bucket}` увімкнений лише для експериментів.

## 2.4. Координація регенерації кешу

У [cache_aside.py:115](../services/license/app/infrastructure/cache_aside.py#L115) перший запит отримує спільний Redis lock через `SET NX EX`; лише він рахує, інші читають готовий результат після очікування. Lease — 120 s, граничний час запиту — 90 s. Перед публікацією перевіряються token і revision; зняття lock видаляє лише блокування його власника.

Перевірка — protected-частина **вже виконаного п. 2.2**, повторний тест не потрібний. Це координація між усіма трьома API, не локальний mutex. Один розрахунок статистики = 11 SQL, тому результат «1» стосується саме розрахунку.

## 2.5. TTL Jittering

[cache_aside.py:91](../services/license/app/infrastructure/cache_aside.py#L91), `expiration_seconds`, задає `TTL = 300 ± 30 s`. Випадкові значення розносять завершення ключів у часі.

```cmd
%DC8% run --rm -T --no-deps cache-tools python /load-tests/ttl_demo.py < NUL
```

`observed_ttls_seconds` містить реально прочитані TTL 20 демонстраційних ключів, приблизно 270–330 s. Jitter не усуває stampede одного ключа — для нього використовується lock.

## 2.6. Negative Caching проти Cache Penetration

[negative_cache.py:30](../services/license/app/infrastructure/negative_cache.py#L30) зберігає лише факт відсутності ліцензії за ID на **15 s**. Маршрут [admin_routes.py:41](../services/license/app/api/admin_routes.py#L41) перевіряє права до кешу. Приватні дані наявних ліцензій не кешуються. API-створення інвалідує попередню відсутність.

```cmd
"%LABPY%" load-tests\run_cache_resilience.py --only negative --output-dir "%EXTRA8%" < NUL
"%LABPY%" -c "import json,sys; r=json.load(open(sys.argv[1])); print(json.dumps(r[1]['verified'],indent=2))" "%EXTRA8%\negative.json"
```

Тест повторює запити до п'яти відсутніх ID. Без захисту кожен звертається до БД; із захистом після **5 початкових SQL** у вимірюваному вікні має бути **0 SQL**. HTTP `404` — правильний бізнес-результат, не помилка тесту. У `verified` мають бути три `true`: створення після 404, завершення TTL та fallback при відмові Redis.

## 2.7. Порівняльні метрики та Resilience Profile

Звіти з результатів поточного запуску:

```cmd
"%LABPY%" load-tests\report_cache_resilience.py "%EXTRA8%"
type "%BURST8%\comparison.csv"
type "%EXTRA8%\comparison.csv"
start "" "%BURST8%\p99_ms.svg"
start "" "%EXTRA8%\avalanche-cpu-timeline.svg"
start "" "%EXTRA8%\avalanche-latency-timeline.svg"
start "" "%EXTRA8%\negative-sql-timeline.svg"
```

Початкові повні вимірювання на ПК: [stampede CSV](../load-tests/results/lab8/comparison.csv), [avalanche / negative CSV](../load-tests/results/lab8-verified/comparison.csv). RPS — завершені HTTP-запити за секунду, p95/p99 — затримки у ms:

```text
Режим                  SELECT   RPS      p95       p99       Помилки
Stampede без lock      5500     17.81    22546.26  22879.35  0%
Stampede з lock        11       37.22    8041.09   8237.14   0%
Avalanche синхронний   24       907.32   88.54     165.70    0%
Avalanche jitter      24       855.81   96.31     143.91    0%
Negative без кешу     8177     676.51   146.08    489.85    0%
Negative з кешем      0        1271.97  60.18     125.61    0%
```

Burst RPS включає очікування стартового бар'єра; його не слід порівнювати з тривалим steady-state RPS. У avalanche sampled peak CPU PostgreSQL знизився **156.61% → 104.92%**, максимальний секундний p99 — **678.91 → 201.81 ms**. CPU вимірювався приблизно кожні 100 ms; одне ядро = 100%. Нові цифри залежать від ПК, ці експерименти не є production SLA.

**Resilience Profile:**

- Lock має TTL після загибелі власника; перевірка token захищає блокування нового власника. При відмові Redis читання йде до БД, але координація втрачається.
- Jitter допускає вік статистики до 330 s без мутацій; SWR у цьому режимі не використовується.
- Negative markers витрачають пам'ять: TTL обмежує час, не кількість. Різні випадкові ID все одно потребують SQL; прямий SQL може залишити хибний `404` до 15 s.
- Аварія writer може залишити `BYPASS`; marker не можна прибирати до завершення його транзакції.
- Один Redis та одна БД залишаються точками відмови; реплікація/шардинг №6–7 не заявляються.

**Альтернативи.** Поточний `SET NX` — не Redlock: Redlock координує більшість незалежних Redis-вузлів і має часові/мережеві припущення. Bloom Filter відповідає «точно немає / можливо є» та допускає false positives. XFetch ймовірнісно починає оновлення до expiration. Ці альтернативи не реалізовані; обрано спільний lock, jitter та короткі negative markers.

Зупинка без видалення тому навчальної БД:

```cmd
%DC8% down
```
