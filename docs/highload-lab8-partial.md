# ІВСАВПЗ Часткова лабораторна робота №8

## Обсяг роботи

Після повністю реалізованих і злитих №4 та №5 виконано цілісний блок
Resilience Engineering: cold-key burst, Cache Stampede, distributed lock,
TTL jitter та кількісний аналіз захисту. Це **часткова робота**, не повна №8.

Виконані пункти: **2.1, 2.2, 2.4, 2.5 та 2.7**.
Не виконані: **2.3 Cache Avalanche experiment** та **2.6 Negative Caching**.
Оцінка визначається викладачем; кількість пунктів не гарантує конкретних балів.
Реплікація й шардинг №6/№7 у цьому експерименті не потрібні технічно і не заявляються.

## 2.1 Ресурсоємний кешований об’єкт

Використано ту саму реальну статистику License Service, що у №4/№5:
`GET /api/v1/license/stats/public`, PostgreSQL з 2000 synthetic licenses/purchases.
При MISS виконується повний набір SQL-агрегацій і формується великий JSON.
Ключ популярного aggregate один на всі три instances, тому одночасний MISS
створює ризик багатьох однакових розрахунків.

Стенд окремий: `mtgmods_cache_resilience_lab8`, порт `127.0.0.1:18108`, власні
PostgreSQL/Redis, CPU/RAM та CPU sets із №5. Бойові дані й production не використовуються.

## 2.2 Відтворення Cache Stampede та Thundering Herd

`load-tests/stampede.js` створює **500 VUs, по одному запиту на кожен**.
Часова gate після setup дає VUs можливість підготуватися і звернутися одночасно,
а не поступово прогріти кеш. Перед кожним режимом очищається лише окремий lab Redis,
відновлюється тотожний dataset і скидаються SQL-лічильники.

Для контрольованого широкого MISS-вікна обидва режими мають однакову явно
синтетичну затримку **2000 ms перед реальним SQL**. Вона не замінює SQL і не
видається за властивість production. Її призначення — відтворюваність одночасних MISS.

У режимі `LAB8_PROTECTED=false` вимкнено лише координацію MISS. TTL,
revision fencing і захист від застарілої публікації залишаються активними.
Кожен конкурентний MISS сам обчислює статистику, перевантажуючи application/DB.

Два незалежні вимірювання підтверджують явище:

- Redis diagnostic counter рахує реальні входи в stats loader;
- `pg_stat_statements` рахує SELECT, що містять `FROM licenses` або
  `FROM transaction_purchases`, без probe SELECT 1 та службових запитів.

Один aggregate виконує **11 таких SELECT**, а не один SQL-запит. Це важлива
відмінність між «одне обчислення» і «одне звернення/statement до БД».

## 2.4 Координація запитів

`LAB8_PROTECTED=true` використовує Redis `SET NX EX` distributed lock:
лише власник обчислює aggregate, інші перечитують спільний результат.
Token перевіряється при публікації й звільненні lock; lease 120 s більший
за загальний deadline 90 s. Revision fencing не дозволяє публікувати
результат, інвалідований паралельним записом.

Захист працює між трьома processes, не лише всередині одного Python instance.
Кеш, lock і counters зберігаються в Redis. Після відповіді не залишається
локального бізнес-стану або локального singleton-результату.

Default №4/№5 залишається protected, без synthetic delay і diagnostic counter.
Незахищений режим та instrumentation увімкнені лише явними Lab 8 settings.

## 2.5 TTL jitter

Алгоритм: `TTL = 300 + random_integer(-30, 30)` секунд, із захистом від
непозитивного expiration. Jitter обчислюється при публікації кожного ключа,
а не один раз при старті instance. Основний API використовує base TTL 300.

`load-tests/ttl_demo.py` показує фактичні TTL двадцяти окремих synthetic demo
aggregate keys у реальному Redis. У прогоні зафіксовані значення від 272 до 330 s,
не один синхронний expiration. Сирі TTL:
[ttl-jitter.json](../load-tests/results/lab8/ttl-jitter.json).

Основна бізнес-статистика зараз має один aggregate key. Для одного ключа jitter
не усуває stampede — це робить lock. Рознесення expiration є корисним для
множини ключів; демонстрація їхніх TTL не підмінює ще не виконаний avalanche benchmark.

## 2.7 Порівняльні вимірювання та failure modes

Вимірювання від 13 жовтня 2026 року, однакові ресурси/дані/delay, 500 запитів:

| Режим | Обчислення | Бізнес SELECT | p95 ms | p99 ms | HTTP-помилки |
|---|---:|---:|---:|---:|---:|
| Без координації | 500 | 5500 | 22546.26 | 22879.35 | 0% |
| Distributed lock | 1 | 11 | 8041.09 | 8237.14 | 0% |

SQL-навантаження зменшено у 500 разів; p95 нижчий приблизно на 64.3%,
p99 — на 64.0%. У protected-режимі завершилися **всі 500 запитів**, а не тільки
ті, які gateway допустив до backend. Звіт перевіряє цю умову перед збереженням.

Для одноразового burst додатково збережено requests / повний час тесту:
17.81 проти 37.22 requests/s. Це **burst throughput із 5-секундною gate**,
не steady-state RPS із №5. setup health request виключений із latency/errors/count.
Залишкова затримка включає очікування першого розрахунку, polling, JSON і CPU.
Захист БД не означає миттєву обробку 500 великих відповідей.

Сирі результати: [stampede.json](../load-tests/results/lab8/stampede.json).
Матриця: [comparison.csv](../load-tests/results/lab8/comparison.csv).

![Обчислення](../load-tests/results/lab8/computations.svg)
![SQL](../load-tests/results/lab8/stats_select_calls.svg)
![p99](../load-tests/results/lab8/p99_ms.svg)

### Виявлені обмеження та виправлення

Перша calibration-серія мала близько 52% швидких gateway-помилок: стандартних
512 worker connections недостатньо для 500 одночасних proxy-запитів, що
потребують client і upstream sockets. У lab gateway встановлено 4096 connections
та `worker_rlimit_nofile 8192`; повторний burst успішний в обох режимах.
Перші числа збережені окремо, не використані як фінальний доказ stampede.

Під CPU-pressure одна Redis-операція також перейшла у fallback: protected counter
став 2 замість 1, що перевірка відхилила. Для Lab 8 timeout Redis налаштовано
на 2 s замість default 0.5 s. Після цього повторно підтверджено 1 обчислення.
Default №4/№5 залишився 0.5 s; це свідомий latency/fallback trade-off.

### Resilience Profile

- Worker загинув: lock має lease і не залишається назавжди; dirty write markers
  №4 переводять читання у BYPASS і потребують quiescent recovery.
- Lease вичерпався: старий власник не може публікувати чи видалити lock наступника.
- SQL-розрахунок завис: deadline 90 s та контрольована 503, не нескінченне очікування.
- Redis недоступний: DB fallback зберігає читання, але координація відсутня;
  повторний stampede на БД можливий. Distributed lock не захищає від падіння самого Redis.
- Занадто короткий Redis timeout при CPU/мережевій затримці може помилково
  ввімкнути fallback. Занадто довгий додає latency при справжній відмові.
- Jitter може збільшити дозволене застарівання до 330 s. Мутації все одно
  інвалідують кеш, тому jitter не замінює read-after-write протокол.
- Єдиний Redis і Primary DB залишаються SPOF; реплікація чи автоматичний failover
  не заявляються. Redlock/quorum алгоритми не реалізовані.
- Polling і передавання великого JSON є залишковими витратами. Single-flight
  зменшує обчислення, але не усуває application CPU bottleneck із №5.

## Запуск і live-демонстрація

Потрібні Docker Desktop Linux containers, 8+ logical CPU і Python 3.12+.
Для ізоляції вимірів варто зупинити попередні lab stacks без `-v`.

```powershell
$dc = @('-f', 'docker-compose.scaling.yml', '-f', 'docker-compose.cache.yml', '-f', 'docker-compose.load.yml', '-f', 'docker-compose.resilience.yml')
docker compose @dc up --build -d
python load-tests/run_stampede.py --vus 500 --output-dir load-tests/results/my-burst
python load-tests/report_resilience.py load-tests/results/my-burst
docker compose @dc run --rm --no-deps cache-tools python /load-tests/ttl_demo.py
```

Для повторення оберіть нову директорію результатів. Runner жорстко задає
Lab 8 project і не приймає довільний URL; k6 звертається тільки до внутрішнього
gateway. Script сам перемикає protected/unprotected, відновлює synthetic
dataset, очищає окремий lab Redis і завершує у protected-режимі.

На захисті демонструються: очищений ключ, один burst у кожному режимі,
500→1 computations, 5500→11 SQL statements, p95/p99 та різні TTL ключів.

Перевірки:

```powershell
.\.venv\Scripts\python -m pytest tests -q
.\.venv\Scripts\python -m ruff check services/license load-tests
docker compose @dc exec -T scaling-gateway nginx -t
```

Додано тести unprotected duplication, protected single computation, bounds/реального
TTL jitter, revision safety без coalescing та failure-safe diagnostics. CI
inspect-ить обидва k6-скрипти. Зупинення збереженням БД: `docker compose @dc down`.

## Що залишено для повної №8

2.3: синхронне expiration множини бізнес-ключів, вимір avalanche до/після jitter.
2.6: навантаження неіснуючими ID, negative caching із коротким TTL або Bloom filter,
інвалідація negative entry при появі об’єкта та порівняння SQL.
Ці результати й механізми не підмінені теорією і не позначені виконаними.
