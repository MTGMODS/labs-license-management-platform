# ІВСАВПЗ · Лабораторна робота №2

## Тема і межі роботи

**Тема:** архітектура без стану (Stateless Application Architecture) та винесення стану системи.

Для експерименту горизонтально масштабується **License Service**. Дві API-репліки
створюються з одного Docker image, працюють без sticky sessions і використовують
спільні PostgreSQL, Redis та RabbitMQ. Інші сервіси системи наведені в аудиті,
але не дублюються у демонстраційному Compose, щоб сценарій був коротким і
відтворюваним.

## 1. Аудит стану

### Ephemeral / Local Safe

| Стан | Де виникає | Чому безпечно |
|---|---|---|
| Об'єкти HTTP request/response | FastAPI handlers | Існують лише під час одного запиту |
| `AsyncSession` | dependency `get_db()` | Окрема DB-сесія на запит, закривається після відповіді |
| DTO, серіалізовані відповіді, згенерований ключ | application layer | Не використовуються як джерело істини між запитами |
| Логи та діагностичні повідомлення | stdout контейнера | Не впливають на бізнес-рішення |
| Каталог тарифів | immutable JSON у Docker image | Це конфігурація релізу, а не змінний бізнес- або сесійний стан; усі репліки одного image мають однаковий файл |

### Shared / Externalized

| Стан | Спільне сховище | Використання |
|---|---|---|
| Ліцензії, активації пристроїв, покупки | License PostgreSQL | Джерело істини для License Service |
| Користувачі, OAuth handoffs, refresh sessions | User PostgreSQL | Ідентифікація та серверні сесії |
| Події запуску helper | Usage PostgreSQL | Незмінна usage telemetry |
| Public stats cache | Redis | Спільний кеш між процесами |
| Lock оновлення статистики | Redis `SET NX EX` + Lua | Лише один instance перераховує важку статистику |
| Команди ботам і file-generation RPC | RabbitMQ | Черга не прив'язана до пам'яті API-процесу |

### Critical Stateful Dependencies та рішення

| Було / ризик | Зміна в лабораторній | Результат |
|---|---|---|
| Python-кеш public stats був би окремим у кожному worker | Статистика вже перенесена в Redis; lock також розподілений | Репліки бачать один кеш і не запускають паралельний refresh |
| Кожна License API-репліка запускала `check_expired_licenses_task()` | Worker винесено в окремий контейнер `license-expiry-worker`; в API `RUN_EXPIRY_WORKER=false` | Додавання API-реплік не дублює kick/unrole повідомлення |
| Кожна репліка виконувала `Base.metadata.create_all()` під час одночасного cold start | Додано one-shot `license-db-init`; у реплік `INITIALIZE_SCHEMA=false` | DDL завершується до старту обох API instances |
| Instance неможливо було ідентифікувати | Кожна відповідь має `X-Instance-ID`; `/health` також повертає `instance_id` | Видно, який контейнер обробив запит |
| Локальні build-файли Distribution Service | Не входить у масштабований контур цієї роботи | Перед горизонтальним масштабуванням Distribution їх треба винести в S3/MinIO або спільне сховище |

Telegram/Discord polling-процеси та Distribution worker не є взаємозамінними
HTTP-репліками в цьому стенді. Вони класифіковані явно, а не приховані під назвою
stateless.

## 2. Реалізований stateless request flow

Демонстраційний бізнес-сценарій використовує наявний адміністративний CRUD
ліцензій:

1. `POST /api/v1/license/generate` надходить у **instance 1** з access JWT.
2. Instance 1 в одній DB-транзакції створює ліцензію та purchase transaction у
   спільній PostgreSQL.
3. `GET /api/v1/license/find?key=...` виконується через **instance 2** і одразу
   читає створений запис зі спільної БД.
4. `PATCH /api/v1/license/{id}` через instance 2 атомарно змінює `reset_limit`
   та виконує commit.
5. Повторний `GET` через instance 1 бачить нове значення.

Вхідний контекст складається з JSON payload, параметра `key`/`license_id` і JWT.
JWT самодостатній, а вся змінна бізнес-інформація читається з PostgreSQL. Після
відповіді в Python-процесі не залишається мутації, необхідної наступному запиту.

```mermaid
sequenceDiagram
    participant C as HTTP client
    participant A as License instance 1
    participant B as License instance 2
    participant DB as Shared PostgreSQL

    C->>A: POST /generate
    A->>DB: INSERT license + transaction; COMMIT
    A-->>C: key + X-Instance-ID: license-service-1
    C->>B: GET /find?key=...
    B->>DB: SELECT
    B-->>C: current license
    C->>B: PATCH /{id}
    B->>DB: UPDATE; COMMIT
    C->>A: GET /{id}
    A->>DB: SELECT
    A-->>C: updated license
```

## 3. Архітектура

C4/Container-схема стенда: [`architecture-c4-stateless.mmd`](architecture-c4-stateless.mmd).

- `stateless-gateway` балансує запити без sticky sessions;
- `license-service-1` і `license-service-2` мають однаковий image та різняться
  лише значенням `INSTANCE_ID`;
- обидві репліки використовують одну PostgreSQL і один Redis;
- schema init і expiry processing відокремлені від API lifecycle.

## 4. Запуск двох instances

Стенд навмисно використовує безпечні демонстраційні значення з
`services/license/.env.example`, тому production-секрети для захисту не потрібні.
Для isolated lab stack hostname-и в URL мають залишатися `license-postgres`,
`redis` і `rabbitmq`.

```powershell
docker compose -f docker-compose.stateless.yml up --build -d
docker compose -f docker-compose.stateless.yml ps
```

Точки входу:

- `http://localhost:18020` — Nginx без affinity;
- `http://localhost:18021` — прямий instance 1;
- `http://localhost:18022` — прямий instance 2.

Перевірка балансування:

```powershell
1..6 | ForEach-Object {
    $response = Invoke-WebRequest http://localhost:18020/health
    $response.Headers['X-Instance-ID']
}
```

## 5. Cross-instance consistency

Створити короткоживучий admin JWT тим самим секретом, що використовують обидві
репліки:

```powershell
$dc = @('-f', 'docker-compose.stateless.yml')
$token = docker compose @dc exec -T license-service-1 python -c "import jwt; from datetime import datetime, timedelta, timezone; from app.shared.config import settings; print(jwt.encode({'sub':'1','role':'ADMIN','type':'access','exp':datetime.now(timezone.utc)+timedelta(minutes=30)}, settings.JWT_SECRET, algorithm='HS256'))"
$headers = @{ Authorization = "Bearer $token" }
```

### POST через instance 1

```powershell
$body = @{
    duration_days = 30
    amount = 5
    method = 'Card'
    status = 'COMPLETED'
    max_devices = 2
    reset_limit = 1
} | ConvertTo-Json

$createdResponse = Invoke-WebRequest -Method Post `
    -Uri http://localhost:18021/api/v1/license/generate `
    -Headers $headers -ContentType 'application/json' -Body $body
$createdResponse.Headers['X-Instance-ID']
$created = $createdResponse.Content | ConvertFrom-Json
$key = $created.data.key
```

### GET і PATCH через instance 2

```powershell
$foundResponse = Invoke-WebRequest `
    -Uri "http://localhost:18022/api/v1/license/find?key=$key" `
    -Headers $headers
$foundResponse.Headers['X-Instance-ID']
$found = $foundResponse.Content | ConvertFrom-Json
$licenseId = $found.data[0].id

$updatedResponse = Invoke-WebRequest -Method Patch `
    -Uri "http://localhost:18022/api/v1/license/$licenseId" `
    -Headers $headers -ContentType 'application/json' `
    -Body (@{ reset_limit = 7 } | ConvertTo-Json)
$updatedResponse.Headers['X-Instance-ID']
```

### GET через instance 1

```powershell
$verifiedResponse = Invoke-WebRequest `
    -Uri "http://localhost:18021/api/v1/license/$licenseId" `
    -Headers $headers
$verifiedResponse.Headers['X-Instance-ID']
($verifiedResponse.Content | ConvertFrom-Json).data.reset_limit
```

Очікується `license-service-1`, потім `license-service-2`, а останнє значення —
`7`. Це доводить, що дані не зберігаються в пам'яті конкретного instance.

## 6. Restart / instance loss

Аварійно зупинити першу репліку:

```powershell
docker compose -f docker-compose.stateless.yml kill license-service-1
1..4 | ForEach-Object {
    $response = Invoke-WebRequest http://localhost:18020/health
    $response.Headers['X-Instance-ID']
}
```

Gateway продовжує відповідати через `license-service-2`. Перевірити раніше
записану ліцензію через gateway:

```powershell
$afterFailure = Invoke-WebRequest `
    -Uri "http://localhost:18020/api/v1/license/$licenseId" `
    -Headers $headers
$afterFailure.Headers['X-Instance-ID']
($afterFailure.Content | ConvertFrom-Json).data.reset_limit
```

Очікується instance 2 і значення `7`. Повернути репліку:

```powershell
docker compose -f docker-compose.stateless.yml up -d license-service-1
Invoke-WebRequest http://localhost:18021/health
```

PostgreSQL volume не залежить від життєвого циклу API-контейнера, тому restart
або loss instance не видаляє бізнес-стан. Redis містить лише відновлюваний кеш;
втрата Redis не є втратою джерела істини.

Зупинити стенд без видалення даних:

```powershell
docker compose -f docker-compose.stateless.yml down
```

Повністю очистити лабораторний volume лише після демонстрації:

```powershell
docker compose -f docker-compose.stateless.yml down -v
```

## 7. Що показувати на захисті

1. Відкрити `services/license/app/main.py`: умовний schema init, окремий worker і
   middleware `X-Instance-ID`.
2. Відкрити `docker-compose.stateless.yml`: дві однакові API-репліки, shared DB,
   Redis, один expiry worker і gateway без sticky sessions.
3. Показати C4-схему `docs/architecture-c4-stateless.mmd`.
4. Виконати POST → GET → PATCH → GET через різні direct ports.
5. Виконати `docker ... kill license-service-1` і повторити GET через gateway.
6. Пояснити: stateless не означає «система не має даних». Це означає, що
   взаємозамінний API worker не є джерелом бізнес-стану; стан живе у shared
   storage, а кожен запит містить або отримує потрібний контекст.

Прихована affinity виникла б, якби сесія, lock, кошик, лічильник або кеш жили
тільки в RAM одного process, або якби Nginx мусив завжди направляти користувача
на ту саму репліку. У цьому стенді такої вимоги немає.
