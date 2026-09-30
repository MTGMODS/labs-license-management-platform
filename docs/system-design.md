# ІВСАВПЗ · Лабораторна робота №1

## Тема і мета

**Тема:** проєктування архітектури та базового прототипу MTG MODS — платформи керування користувачами, ліцензіями, usage-аналітикою та персональною видачею VIP-файлів.

**Мета:** описати доменну модель і API, зафіксувати архітектуру на рівні C4 Container, показати наявний persistence/deployment каркас і визначити потенційні вузькі місця за реальними шляхами запитів.

Робота виконується одноосібно: автор і виконавець усіх модулів — одна людина.

## Поточний стан системи

Система розділена на User, License, Usage та Distribution FastAPI-сервіси. Web-клієнт, Telegram-бот і Discord-бот є окремими контейнерами. PostgreSQL використовується окремо для User, License та Usage. RabbitMQ передає запити на генерацію файлів і команди ботам.

У production Nginx працює на хості перед Docker Compose стеком: HTTPS-запити маршрутизуються до Web або потрібного API-сервісу. Між контейнерами використовується внутрішня мережа Compose та HTTP за іменами сервісів. Production gateway і Compose описані в `gateway/` та `docker-compose.yml`.

### Доменна модель

DBML-модель для імпорту в dbdiagram.io: [`er-model.dbml`](er-model.dbml). Вона включає фізичні таблиці трьох баз даних:

- User DB: `users`, `oauth_handoffs`, `refresh_sessions`;
- License DB: `licenses`, `license_activations`, `transaction_purchases`;
- Usage DB: `launches`.

У License DB одна ліцензія може мати багато записів активації пристроїв. Транзакція пов’язана з ліцензією через унікальний nullable `license_id`, а ORM представляє цей зв’язок як one-to-one. `licenses.user_id` і `transaction_purchases.user_id` посилаються логічно на User Service, але не мають FK: це різні бази даних.

У схемі збережені фактичні обмеження моделей. Деякі значення за замовчуванням (`role`, `status`, `reset_limit`, `max_devices`) задаються SQLAlchemy на рівні застосунку; це не означає, що в PostgreSQL є server-side DEFAULT.

### C4 Container diagram

Джерело діаграми: [`architecture-c4.mmd`](architecture-c4.mmd). Це Container-level схема: прямокутники всередині межі продукту є контейнерами виконання, зовнішній Nginx показаний окремо, стрілки підписані протоколами або призначенням.

Основні потоки:

1. Web-користувач звертається по HTTPS до Nginx; Nginx віддає SPA або проксуює API-запит до потрібного сервісу.
2. Helper надсилає `license/check` та `usage/launch` через API gateway.
3. License Service працює зі своєю PostgreSQL БД і звертається до User Service по внутрішньому HTTP для профілю/соціальних ID.
4. Для генерації VIP-файлу License Service публікує завдання в RabbitMQ і очікує RPC-відповідь від Distribution Service.
5. Distribution Service читає VIP-шаблон, додає персональні дані/тайм-бомбу, запускає production obfuscation layer і записує тимчасовий файл у змонтований каталог.
6. Telegram- і Discord-боти звертаються до License Service по внутрішньому HTTP та отримують команди з RabbitMQ; з Telegram/Discord API вони працюють через HTTPS.

## Специфікація API

Нижче наведені бізнес-endpoint-и з внутрішніми шляхами FastAPI. У production gateway публічний префікс зазвичай `/v1/...`, а Nginx проксуює його на внутрішній `/api/v1/...`. Swagger/OpenAPI доступний на `/docs` кожного API-сервісу у відповідному середовищі.

### 1. Usage: запис запуску helper

`POST /api/v1/usage/launch` — викликається під час кожного запуску helper усіма користувачами: Free та VIP. JWT не потрібен.

Приклад запиту:

```json
{
  "version": "1.0 VIP",
  "mode": "police",
  "server": 1,
  "device": "PC",
  "hwid": "ABCD12345"
}
```

`version` має відповідати формату `число.число Free|VIP|Launcher Edition`; `device` — `PC` або `MOBILE`; `server` перевіряється за дозволеним набором; `mode` — за дозволеним переліком; довжина `hwid` — 5–255 символів.

Успіх: `200 OK`, `{"status":"success","message":"Launch logged"}`. Некоректне тіло: `422 Unprocessable Entity`. Помилка запису: `500 Internal Server Error` з `error_code: USAGE_ERROR`. На production gateway для цього шляху налаштовано rate limit.

### 2. License: перевірка ключа та пристрою клієнтом

`POST /api/v1/license/check` — запит VIP-клієнта при запуску для перевірки оплаченої ліцензії та HWID. JWT не потрібен; ключ перевіряється в тілі.

Приклад запиту:

```json
{
  "key": "1234567890123456789",
  "device": "ABCD12345"
}
```

Успішна активна ліцензія: `200 OK`, `{"valid":true,"user":"nickname","id":123}`. Неактивований або прострочений ключ повертає `200` з `valid:false` і причиною/прапорцем у тілі. Невідомий ключ: `404`; перевищений ліміт пристроїв: `403` з `HWID_LIMIT_REACHED`; помилкове тіло: `422`. Неочікувана помилка обробника наразі повертається як `400` із загальним повідомленням.

Цей шлях генерує менше запитів, ніж usage telemetry, бо його використовують VIP-користувачі. Водночас його доступність критична: якщо check не проходить, VIP-клієнт не запускається або не відкриває платні функції. Навантаження цього шляху можна оцінити окремо як `RPS_vip = N_vip_launches / Δt`.

### 3. License: активація ключа користувачем

`POST /api/v1/license/activate` — прив’язує неактивований ключ до поточного акаунта. Потрібен `Authorization: Bearer <access_token>`.

Приклад запиту:

```json
{
  "key": "1234567890123456789",
  "force": false
}
```

Успіх: `200 OK`, `{"status":"success","message":"License activated successfully"}`. Невалідний або вже активований ключ: `400 INVALID_KEY`; активна підписка без `force`: `409 ACTIVE_LICENSE_EXISTS`; відсутній/невалідний токен: `401`; помилкове тіло: `422`.

### 4. License: дані активної ліцензії у кабінеті

`GET /api/v1/license/info` — повертає активну ліцензію, замасковані пристрої та транзакцію користувача. Потрібен access JWT.

Успіх: `200 OK`, форма відповіді:

```json
{
  "license": {
    "id": 10,
    "user_id": 123,
    "key": "1234567890123456789",
    "status": "ACTIVE",
    "duration_days": 30,
    "max_devices": 2,
    "reset_limit": 1,
    "activated_at": "2026-09-20T12:00:00Z",
    "expires_at": "2026-10-20T12:00:00Z"
  },
  "devices": [],
  "transaction": {
    "amount": 3.0,
    "method": "Card",
    "status": "COMPLETED",
    "purchased_at": "2026-09-20T12:00:00Z"
  }
}
```

Нема активної ліцензії: `404 NO_ACTIVE_LICENSE`; невалідний токен: `401`.

### 5. License: історія ліцензій

`GET /api/v1/license/history` — повертає попередні ліцензії поточного користувача. Потрібен access JWT.

Успіх: `200 OK`, масив об’єктів у формі даних ліцензії, пристроїв і транзакції (так само, як у `/info`). Якщо історії немає — порожній масив. Неавторизований запит: `401`.

### Додаткові endpoint-и

- `DELETE /api/v1/license/device/{device_id}` — скидання пристрою власної активної ліцензії; `403 RESET_LIMIT_REACHED`, якщо ліміт скидань вичерпаний.
- `POST /api/v1/license/download` — створює персональний VIP-файл через RabbitMQ RPC і повертає URL; потрібен access JWT.
- `GET /api/v1/license/tariffs` — каталог тарифів для Web і ботів.
- `GET /api/v1/license/stats/public` — публічна статистика продажів.
- `GET /api/v1/usage/stats/public` — публічна usage-аналітика.

### Адміністративний CRUD ліцензій

Адміністративні маршрути License Service закривають CRUD життєвого циклу ліцензії. Для них потрібен access JWT з роллю `ADMIN`:

- `POST /api/v1/license/generate` — створити одну неактивовану ліцензію/ключ і транзакцію; тіло задає `duration_days`, `amount`, `method`, `max_devices`, `reset_limit`.
- `POST /api/v1/license/generate/bulk` — створити набір ключів (`count` від 1 до 1000).
- `GET /api/v1/license/find?user_id=...` або `?key=...` — знайти ліцензії за власником чи точним ключем.
- `GET /api/v1/license/{license_id}` — отримати одну ліцензію з пристроями й транзакцією.
- `PATCH /api/v1/license/{license_id}` — змінити status, owner, duration, amount або ліміти пристроїв.
- `DELETE /api/v1/license/{license_id}` — видалити ліцензію разом із пов’язаними транзакцією та активаціями.

Успішні операції повертають `200`; відсутній/невалідний токен — `401`, токен без admin ролі — `403`, не знайдена ліцензія — `404`, невалідний payload — `422`.

## Сценарії навантаження

### Найбільша частота: Usage `POST /usage/launch`

Кожен запуск helper надсилає telemetry подію незалежно від того, Free це користувач чи VIP. Піковий сценарій — багато гравців запускають helper протягом короткого часу, наприклад після оновлення продукту або перед активною грою. Кожен запит створює рядок у `launches` і комітить транзакцію. Це основний за частотою потік записів. Для оцінки інтенсивності використовувати `RPS_peak = N_launches / Δt`, де `N_launches` — кількість запусків у піковому вікні, а `Δt` — його тривалість.

Для кількісної частини звіту кількість користувачів і часовий інтервал треба взяти з фактичної статистики продукту або явно підписати як припущення лабораторного сценарію. Значення RPS не слід називати виміряним без load test. Врахувати: production Nginx має per-IP ліміт `30r/m` із burst 5 для важких клієнтських endpoint-ів, тому тест із одного IP вимірюватиме також gateway rate limiting.

### Нижча частота, найвища бізнес-критичність: License `POST /license/check`

Цей endpoint використовують платні VIP-користувачі. Обсяг нижчий, ніж у `usage/launch`, але помилка або висока затримка безпосередньо блокує запуск VIP-клієнта і функції, за які заплачено. У high-load аналізі його варто позначати як критичний сервісний шлях, окремо від найбільш частотного потоку.

## Bottleneck analysis

Нижче наведено теоретичні точки деградації, визначені за кодом і структурою production пайплайна. Це не результати вимірювання.

### 1. License Service: `GET /api/v1/license/stats/public`

**Операція:** формування публічної статистики продажів і retention.

**Причина:** `LicenseRepository.get_heavy_public_stats()` виконує серію агрегатних запитів з join-ами, групуванням і підрахунками по таблицях ліцензій та транзакцій. Зі зростанням історії продажів зростає обсяг читання й обчислень у PostgreSQL.

**Поточне пом’якшення:** `LicenseStatsService` зберігає результат у спільному Redis під ключем `mtgmods:license:public_stats:v1`. Дані свіжі 5 хвилин, а Redis зберігає їх до 24 годин для stale-while-revalidate. Перший запит після завершення свіжості повертає попередні дані та запускає background refresh під Redis lock. Блокування також координує cold start між процесами; запис і звільнення перевіряють токен власника. Перезапуск API зберігає кеш у Redis, перезапуск самого Redis очищає його. При недоступності Redis виконуються прямі запити до БД, що тимчасово збільшує навантаження.

**Очікувані симптоми:** повільне перше обчислення після запуску, підвищення PostgreSQL CPU/I/O, зростання p95/p99 для cold refresh, зайнятість connection pool.

### 2. Usage Service: `GET /api/v1/usage/stats/public`

**Операція:** формування загальної usage-аналітики за всіма записами запусків.

**Причина:** `LaunchRepository.get_heavy_public_stats()` послідовно рахує розподіли за режимами, серверами, версіями та продуктами, timelines й activity; використовуються `COUNT(DISTINCT hwid)`, conditional aggregates і групування по даті/годині. Таблиця `launches` постійно росте через події від усіх Free і VIP запусків.

**Поточне пом’якшення:** `UsageStatsService` використовує той самий механізм Redis-кешування зі свіжістю 5 хвилин, зберіганням до 24 годин і background refresh. Ключ `mtgmods:usage:public_stats:v1` та його lock незалежні від License. При порожньому кеші один процес виконує перерахунок, інші чекають до 4 секунд і за потреби отримують HTTP 503 з `Retry-After`. Падіння процесу не залишає вічного lock: він завершується через 120 секунд. Самі важкі SQL-запити залишаються потенційним bottleneck під час refresh або відмови Redis.

**Очікувані симптоми:** високий CPU та I/O PostgreSQL під час refresh, довший cold response, конкуренція важких читань зі вставками `usage/launch`, погіршення p99 запису telemetry.

### 3. Distribution Service: персональна генерація VIP-файлу

**Операція:** запит на download проходить через License Service, RabbitMQ RPC і Distribution Service; кінцевий користувач чекає URL відповіді.

**Причина:** Distribution читає VIP template, додає персональний expiration time bomb, запускає приватний production obfuscation layer/лібру та записує результат як тимчасовий файл. Обфускатор є приватним production overlay і не входить до публічного репозиторію. RPC у License Service чекає відповідь до 30 секунд.

**Очікувані симптоми:** збільшення часу до отримання download URL, backlog у черзі, CPU/I/O навантаження на генератор, таймаути RPC і зростання p95/p99 під час одночасних VIP-завантажень.

## Persistence та відтворюване середовище

- User, License і Usage мають окремі PostgreSQL 16 контейнери та named volumes `user_pg_data`, `license_pg_data`, `usage_pg_data`.
- User, License і Usage створюють таблиці з SQLAlchemy metadata при старті. Це дає автоматичне створення схеми на чистій БД, але в репозиторії наразі немає Alembic migrations або окремих DDL scripts для версіонування змін схеми.
- Compose має health checks і залежності між сервісами. Внутрішні HTTP/RabbitMQ зв’язки працюють через мережу Compose.
- README описує копіювання `.env.example` у `.env` і необхідні узгоджені секрети. Для повного VIP download production потребує приватних template/obfuscation assets у Distribution overlay; їх не можна включати до публічного git.

Запуск після надання конфігурації та дозволених приватних assets:

```bash
docker compose up --build -d
docker compose ps
```

Для демонстрації persistence: створити або змінити запис через API, перезапустити відповідний PostgreSQL контейнер, наприклад `docker compose restart license-postgres`, потім прочитати той самий запис повторним API-запитом.

## Внесок автора

| Автор | Модулі |
|---|---|
| Богдан (одноосібна розробка) | User Service, License Service, Usage Service, Distribution Service, Web, Telegram bot, Discord bot, Docker Compose та gateway конфігурація |

Перед здачею за потреби замінити ім’я на повне ПІБ і додати академічну групу.

## Джерела в репозиторії

- `docker-compose.yml`
- `gateway/nginx.conf`, `gateway/sites-available/api.mtgmods.com`
- `services/user/app/infrastructure/repository.py`
- `services/license/app/infrastructure/repository.py`
- `services/usage/app/infrastructure/repository.py`
- `services/license/app/api/`, `services/usage/app/api/routes.py`
- `services/license/app/application/service.py`
- `services/usage/app/application/service.py`
- `services/distribution/app/application/service.py`, `services/distribution/app/infrastructure/messaging.py`
