# ІВСАВПЗ · Лабораторна робота №3

## Тема та мета

**Тема:** горизонтальне масштабування та балансування навантаження.

Мета роботи — масштабувати stateless License Service, приховати backend-вузли за
єдиною точкою входу, порівняти Round Robin і Least Connections, перевірити
відмовостійкість та оцінити зміну пропускної спроможності для 1, 2 і 3 instances.

Лабораторна продовжує Stateless Lab №2. У попередній роботі прямі порти instances
використовувалися для доказу cross-instance consistency. У цьому стенді вони не
публікуються: клієнту доступний лише Nginx на `http://localhost:18100`.

## Архітектура

Джерело C4-схеми: [`architecture-c4-horizontal-scaling.mmd`](architecture-c4-horizontal-scaling.mmd).

- `scaling-gateway` — єдина зовнішня точка входу;
- `license-service-1/2/3` — приватний upstream pool;
- усі репліки створені з одного image та повертають `X-Instance-ID`;
- PostgreSQL зберігає спільний бізнес-стан;
- Redis зберігає спільний кеш і distributed lock;
- схема БД створюється one-shot контейнером до запуску API;
- локальна пам'ять instance не використовується як джерело бізнес-стану.

## Запуск

```powershell
docker compose -f docker-compose.scaling.yml up --build -d
docker compose -f docker-compose.scaling.yml ps
```

У `docker compose ps` лише gateway має published port `18100`. API-контейнери
показують тільки внутрішній `8000/tcp`.

Перевірка розподілу:

```powershell
1..9 | ForEach-Object {
    $response = Invoke-WebRequest http://127.0.0.1:18100/health
    $response.Headers['X-Instance-ID']
}
```

Для Round Robin очікується циклічний розподіл `1 → 2 → 3`. Endpoint `/health`
виконує `SELECT 1` у PostgreSQL та повертає `database_status: UP`. Endpoint
`/health/live` перевіряє лише процес.

## Алгоритми балансування

Nginx використовує спільну конфігурацію
`deploy/horizontal-scaling/nginx.conf` та один із взаємозамінних upstream-файлів.

### Round Robin

```powershell
$env:SCALING_UPSTREAM_CONFIG = './deploy/horizontal-scaling/upstream-round-robin.conf'
docker compose -f docker-compose.scaling.yml up -d --no-deps --force-recreate scaling-gateway
```

Round Robin не враховує кількість активних з'єднань або швидкість вузла. За
однакових instances він дає рівномірний і передбачуваний розподіл.

### Least Connections

```powershell
$env:SCALING_UPSTREAM_CONFIG = './deploy/horizontal-scaling/upstream-least-connections.conf'
docker compose -f docker-compose.scaling.yml up -d --no-deps --force-recreate scaling-gateway
```

Least Connections направляє новий запит до вузла з найменшою кількістю активних
з'єднань. Це корисно, коли частина запитів або instances повільніша.

Після демонстрації змінну можна видалити:

```powershell
Remove-Item Env:SCALING_UPSTREAM_CONFIG
```

## Benchmark-скрипт

Скрипт `scripts/highload-lab3/benchmark.py` виконує warm-up, паралельні HTTP
запити та рахує throughput, error rate, average, p50, p95, p99 і розподіл по
`X-Instance-ID`.

```powershell
.\.venv\Scripts\python scripts/highload-lab3/benchmark.py `
    --url http://127.0.0.1:18100/health `
    --requests 600 `
    --concurrency 60 `
    --warmup 30
```

## Scale-out експеримент

Середовище вимірювання:

- Docker Desktop Linux containers;
- 12 logical CPU та приблизно 16 GB RAM доступно Docker;
- Windows host;
- PostgreSQL 16, Redis 7.4, Nginx 1.27;
- один gateway;
- 600 вимірюваних запитів, concurrency 60, warm-up 30;
- endpoint `/health` із реальним `SELECT 1` до shared PostgreSQL;
- усі вимірювання виконані локально, тому це порівняльний laboratory baseline,
  а не production capacity claim.

| Upstream pool | RPS | Avg, ms | p50, ms | p95, ms | p99, ms | Errors | Розподіл |
|---|---:|---:|---:|---:|---:|---:|---|
| 1 instance | 241.11 | 239.04 | 193.25 | 538.66 | 559.04 | 0 | 600 |
| 2 instances | 371.40 | 142.49 | 105.25 | 512.57 | 528.63 | 0 | 300 / 300 |
| 3 instances | 581.08 | 94.64 | 36.96 | 446.60 | 533.77 | 0 | 200 / 200 / 200 |

Для зміни ефективного розміру upstream pool використовуються:

```powershell
$env:SCALING_UPSTREAM_CONFIG = './deploy/horizontal-scaling/upstream-one-instance.conf'
$env:SCALING_UPSTREAM_CONFIG = './deploy/horizontal-scaling/upstream-two-instances.conf'
$env:SCALING_UPSTREAM_CONFIG = './deploy/horizontal-scaling/upstream-round-robin.conf'
docker compose -f docker-compose.scaling.yml up -d --no-deps --force-recreate scaling-gateway
```

Відносно одного instance два instances дали приблизно `1.54×` throughput із
scalability efficiency близько `77%`. Три instances дали `2.41×` із efficiency
близько `80%`. Масштабування не є ідеально лінійним через shared PostgreSQL,
Nginx, Docker networking та конкуренцію за ресурси одного host.

## Асиметричне навантаження

Третій instance підтримує лабораторну затримку через `SLOW_INSTANCE_DELAY_MS`.
Значення повертається в `X-Instance-Delay-Ms`.

```powershell
$env:SLOW_INSTANCE_DELAY_MS = '200'
docker compose -f docker-compose.scaling.yml up -d --no-deps --force-recreate license-service-3
```

Порівняння виконано за однакових умов: 600 запитів, concurrency 60, warm-up 30,
instance 3 має додаткові 200 ms.

| Алгоритм | RPS | Avg, ms | p95, ms | p99, ms | Розподіл 1 / 2 / 3 | Errors |
|---|---:|---:|---:|---:|---|---:|
| Round Robin | 287.76 | 183.10 | 662.05 | 726.16 | 200 / 200 / 200 | 0 |
| Least Connections | 642.04 | 70.85 | 227.68 | 445.16 | 269 / 279 / 52 | 0 |

Round Robin продовжив віддавати повільному вузлу рівно третину трафіку. Least
Connections помітив довші активні з'єднання непрямо та скоротив частку повільного
вузла до 52 із 600 запитів. У цьому прогоні p99 зменшився приблизно на 39%, а
p95 — приблизно на 66%. Абсолютні числа залежать від host, тому висновок
ґрунтується на порівнянні двох алгоритмів в одному середовищі.

Повернення нормальної швидкості:

```powershell
Remove-Item Env:SLOW_INSTANCE_DELAY_MS
docker compose -f docker-compose.scaling.yml up -d --no-deps --force-recreate license-service-3
```

## Health Check і fault tolerance

Docker healthcheck використовує readiness endpoint `/health`. Nginx Open Source
реалізує passive health checking через `max_fails=1` і `fail_timeout=5s`.
`proxy_next_upstream` повторює безпечні запити при connection error, timeout або
502/503/504.

Сценарій відмови:

```powershell
docker compose -f docker-compose.scaling.yml kill license-service-2
.\.venv\Scripts\python scripts/highload-lab3/benchmark.py `
    --requests 180 --concurrency 30 --warmup 10
```

Фактичний результат: 180 успішних відповідей, 0 помилок, error rate `0%`.
Трафік автоматично перейшов на instances 1 і 3. Відновлення:

```powershell
docker compose -f docker-compose.scaling.yml up -d license-service-2
```

Для модифікуючих неідемпотентних запитів автоматичний retry навмисно не
увімкнений: повторний POST після невизначеної відповіді може створити дубль.
У production такі операції потребують idempotency key.

## Bottleneck analysis після scale-out

Першим наступним обмеженням стає shared PostgreSQL. Кожен `/health` виконує SQL,
тому збільшення API-реплік підвищує одночасне використання DB connections. За
подальшого масштабування можливі connection pool exhaustion, PostgreSQL CPU/I/O
та lock contention для write-запитів. Додатковими обмеженнями є Docker Desktop
network/NAT і єдиний Nginx.

Сам Nginx є Single Point of Failure. Варіанти усунення: дві репліки
балансувальника з Keepalived/VRRP, зовнішній cloud load balancer або DNS traffic
management. Для локальної лабораторної одна репліка залишена навмисно.

## Що показувати на захисті

1. `docker compose ... ps`: host port має тільки gateway.
2. Серію запитів і `X-Instance-ID` з розподілом 1/2/3.
3. Live-перемикання upstream-файла з Round Robin на Least Connections.
4. Instance 3 із `SLOW_INSTANCE_DELAY_MS=200` та benchmark обох алгоритмів.
5. `docker kill license-service-2` і повторний benchmark із 0% помилок.
6. Таблицю 1/2/3 instances та пояснення нелінійної scalability efficiency.
7. C4-схему та пояснення shared PostgreSQL як наступного bottleneck.

Зупинення стенда без видалення БД:

```powershell
docker compose -f docker-compose.scaling.yml down
```
