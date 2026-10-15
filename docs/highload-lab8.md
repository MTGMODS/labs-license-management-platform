# Лабораторна №8. Захист кешу від сплесків навантаження

**Ідея:** №4 дала кеш, №5 виміряла швидкість, №8 перевіряє проблемні ситуації:

- Один ключ зник, його одночасно попросили 500 користувачів — **stampede**.
- Одночасно закінчився кеш багатьох різних ключів — **avalanche**.
- Постійно запитують ID, якого немає — **penetration**.

Команди для **PowerShell**. Виконуй блоки зверху вниз в одному вікні.
Потрібні Docker Desktop з Linux containers, Python 3.12+ та щонайменше 8 logical CPU у Docker.
Venv необов'язковий. Інші навантажувальні стенди спочатку зупини командами `down` з їхніх звітів.
Runner-и очищають **власну навчальну БД/Redis №8**. Production дані сюди не переносити.

## Підготовка — один раз

```powershell
Set-Location C:\MTGMODS\labs-license-management-platform
$labPython = if (Test-Path .\.venv\Scripts\python.exe) { (Resolve-Path .\.venv\Scripts\python.exe).Path } else { 'python' }
& $labPython --version
docker info --format 'OS={{.OSType}}; logical CPU={{.NCPU}}'
$env:LAB8_PROTECTED = 'true'
$env:LAB8_NEGATIVE_CACHE = 'true'
$env:LAB4_CACHE_ENABLED = 'true'
$env:LAB4_CACHE_TTL_SECONDS = '300'
$lab8Compose = @('-p', 'mtgmods_cache_resilience_lab8', '-f', 'docker-compose.scaling.yml', '-f', 'docker-compose.cache.yml', '-f', 'docker-compose.load.yml', '-f', 'docker-compose.resilience.yml')
docker compose @lab8Compose up --build -d
docker compose @lab8Compose up -d --no-deps --force-recreate scaling-gateway
docker compose @lab8Compose run --rm --no-deps cache-tools python /load-tests/reset_dataset.py
docker compose @lab8Compose run --rm --no-deps cache-tools python /lab4/seed.py --rows 2000
docker compose @lab8Compose ps
```

Очікується `linux`, CPU ≥8, три healthy API, PostgreSQL і Redis. Адреса — `18108`.
Авторизацію для навантажувальних сценаріїв runner готує сам; реальних оплат немає.
Імена директорій нижче автоматично унікальні, вручну нічого підставляти не потрібно.

## 2.1. Обрати дорогий популярний об'єкт

**Що вимагається:** пояснити, який кешований результат небезпечно одночасно перераховувати.

**Що зроблено:** та сама публічна статистика продажів License Service, що в №4.
Один розрахунок виконує **11 бізнес-SELECT**, а не один SQL-запит.
Щоб гарантовано встигло прийти багато одночасних MISS, у тесті перед реальним
SQL додається **штучна затримка 2 s**. Вона однакова з/без захисту і не видається за production-поведінку.

```powershell
$lab8Stats = Invoke-RestMethod http://127.0.0.1:18108/api/v1/license/stats/public
$lab8Stats.subscriptions.overview | Format-List
```

Конфігурація тестового режиму: [docker-compose.resilience.yml](../docker-compose.resilience.yml).

## 2.2. Відтворити Cache Stampede та Thundering Herd

**Що вимагається:** прибрати ключ і одночасно подати 500+ запитів, показати сплеск SQL.

**Що зроблено:** `run_stampede.py` сам очищає кеш і запускає два тести по 500 запитів:
спочатку без координації, потім із захистом. В обох однакові дані та ресурси.
Цей самий експеримент демонструє захист із пункту **2.4**, повторно його запускати не треба.

```powershell
$lab8Burst = 'load-tests/results/defense-burst-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
& $labPython load-tests/run_stampede.py --vus 500 --output-dir $lab8Burst
if ($LASTEXITCODE -ne 0) { throw 'Stampede-тест не пройшов: дивись помилку вище' }
$lab8BurstRows = Get-Content (Join-Path $lab8Burst 'stampede.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$lab8BurstRows | Select-Object protected, requests, computations, stats_select_calls, p95_ms, p99_ms, error_rate | Format-Table -AutoSize
```

**Що побачити:** `protected=false` — багато однакових обчислень;
`protected=true` — 1 обчислення / 11 SELECT, усі 500 відповідей успішні.
У збереженому повному прогоні було 500→1 обчислень, SQL 5500→11.
`computations` — кількість розрахунків, `stats_select_calls` — SQL, `error_rate=0` — без помилок.
Масова хвиля конкурентних звернень до БД тут і є **thundering herd**.

## 2.3. Відтворити Cache Avalanche — завершення групи ключів

**Що вимагається:** багато різних ключів одночасно втрачають кеш; виміряти наслідки.

**Що зроблено:** 24 групи тестових продажів мають окремі агрегати та ключі.
Спочатку TTL усіх закінчується через 6 s, потім порівнюється з TTL 3–9 s.
40 VUs читають ці групи протягом 12 s. Lock увімкнений в обох режимах:
ми перевіряємо одночасне завершення різних ключів, а не дублювання одного.

```powershell
$lab8Extra = 'load-tests/results/defense-extra-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
& $labPython load-tests/run_cache_resilience.py --only avalanche --output-dir $lab8Extra
if ($LASTEXITCODE -ne 0) { throw 'Avalanche-тест не пройшов' }
$lab8Avalanche = Get-Content (Join-Path $lab8Extra 'avalanche.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$lab8Avalanche | ForEach-Object {
    [pscustomobject]@{ Mode = $_.label; Keys = $_.keys; SQL = $_.database.calls; p99ms = $_.summary.p99_ms; Errors = $_.summary.error_rate }
} | Format-Table -AutoSize
```

**Очікується:** `synchronized` та `jitter`, в обох 24 SQL і 0 помилок.
Змінюється не кількість потрібних розрахунків, а їх розподіл у часі.
Графіки будуються в пункті 2.7 після другого експерименту.

Endpoint `/api/v1/lab8/aggregate/{bucket}` існує лише в навчальному режимі.
У SQL є **штучне посилення роботи ×4000**, однакове в обох режимах; це не реальний
production-запит. Воно дозволяє на 2000 записах побачити сплеск ресурсів БД.

## 2.4. Захист: лише один сервіс рахує холодний ключ

**Що вимагається:** не дати всім запитам паралельно рахувати однаковий результат.

**Що зроблено:** перший сервіс бере спільний Redis lock (`SET NX EX`).
Він рахує статистику, інші чекають і читають готове. Lock автоматично закінчується
через 120 s, запит чекає не більше 90 s. Власник перевіряється перед зняттям lock.

**Показати доказ із уже виконаного пункту 2.2:**

```powershell
$lab8BurstRows | Where-Object protected -eq $true | Select-Object requests, computations, stats_select_calls, error_rate | Format-List
```

Очікується 500 запитів, 1 розрахунок, 11 SELECT, 0 помилок.
Це спільний Redis lock між трьома сервісами, не локальна Python-змінна.
Код: [cache_aside.py](../services/license/app/infrastructure/cache_aside.py).
Якщо Redis недоступний, координація втрачається — це не захист від падіння самого Redis.

## 2.5. TTL jitter — різний час завершення кешу

**Що вимагається:** не дати всім ключам завершитися в одну мить.

**Що зроблено:** TTL = базовий час ± випадкова поправка.
Для звичайної статистики №8 це 300±30 s; у прискореному avalanche-тесті — перше TTL 6±3 s.

```powershell
docker compose @lab8Compose run --rm --no-deps cache-tools python /load-tests/ttl_demo.py
```

**Очікується:** `observed_ttls_seconds` — різні значення приблизно 270–330.
Це фактичні TTL двадцяти демонстраційних ключів Redis. Точні числа щоразу різні.
Сам jitter не рятує один популярний ключ від stampede — для цього потрібен lock.

## 2.6. Negative Caching — запам'ятати, що ID не існує

**Що вимагається:** повторні запити відсутнього об'єкта не мають щоразу йти в БД.

**Що зроблено:** для ADMIN GET ліцензії за ID кешуємо лише факт відсутності на 15 s.
Права перевіряються до кешу. Знайдені ліцензії та приватні дані не кешуються.
Створення ліцензії через API робить старий маркер відсутності недійсним.

**Продовження в ту саму директорію, яку створив пункт 2.3:**

```powershell
& $labPython load-tests/run_cache_resilience.py --only negative --output-dir $lab8Extra
if ($LASTEXITCODE -ne 0) { throw 'Negative-cache тест не пройшов' }
$lab8Negative = Get-Content (Join-Path $lab8Extra 'negative.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$lab8Negative | ForEach-Object {
    [pscustomobject]@{ Mode = $_.label; Requests = $_.summary.requests; SQL = $_.database.calls; WarmupSQL = $_.warmup_sql_calls; Errors = $_.summary.error_rate }
} | Format-Table -AutoSize
$lab8Negative[1].verified | Format-List
```

**Очікується:** без захисту SQL приблизно стільки ж, скільки запитів; із захистом — 0
у вимірюваному вікні. Перед ним є **5 початкових SQL**, щоб створити маркери для 5 відсутніх ID.
404 тут правильна відповідь, не збій сервера. `verified` має три `true`:
створення після 404, завершення TTL та робота через БД при вимкненому Redis.
Runner реально перевіряє ці умови, а не просто друкує наперед задані результати.

**Показати MISS → HIT вручну:**

```powershell
$lab8Token = docker compose @lab8Compose exec -T license-service-1 python -c "import jwt; from datetime import datetime,timedelta,timezone; from app.shared.config import settings; print(jwt.encode({'sub':'1','role':'ADMIN','type':'access','exp':datetime.now(timezone.utc)+timedelta(minutes=15)},settings.JWT_SECRET,algorithm='HS256'))"
docker compose @lab8Compose exec -T redis redis-cli DEL mtgmods:license:absent:v1:9999989
curl.exe -sS -D - -o NUL -H "Authorization: Bearer $($lab8Token.Trim())" http://127.0.0.1:18108/api/v1/license/9999989
curl.exe -sS -D - -o NUL -H "Authorization: Bearer $($lab8Token.Trim())" http://127.0.0.1:18108/api/v1/license/9999989
```

Двічі HTTP 404; спочатку `X-Negative-Cache: MISS`, потім `HIT`.
Захист допомагає при **повторенні тих самих ID**. Нові випадкові ID все одно доходять до БД.
Код: [negative_cache.py](../services/license/app/infrastructure/negative_cache.py).

## 2.7. Порівняльні результати та ризики захисту

**Що вимагається:** порівняти SQL/RPS/p95/p99/помилки та пояснити обмеження.

**Побудувати звіти з нових результатів і відкрити графіки:**

```powershell
& $labPython load-tests/report_resilience.py $lab8Burst
if ($LASTEXITCODE -ne 0) { throw 'Не вдалося побудувати stampede-звіт' }
& $labPython load-tests/report_cache_resilience.py $lab8Extra
if ($LASTEXITCODE -ne 0) { throw 'Не вдалося побудувати avalanche/negative-звіт' }
Import-Csv (Join-Path $lab8Extra 'comparison.csv') | Select-Object mode, label, sql_calls, rps, p95_ms, p99_ms, error_rate | Format-Table -AutoSize
Invoke-Item (Join-Path $lab8Burst 'computations.svg')
Invoke-Item (Join-Path $lab8Extra 'avalanche-sql-timeline.svg')
Invoke-Item (Join-Path $lab8Extra 'avalanche-cpu-timeline.svg')
Invoke-Item (Join-Path $lab8Extra 'negative-latency-timeline.svg')
```

Читати графіки так: червоне — без захисту/синхронний TTL; зелене — захист/jitter.
У avalanche має стати меншим одночасний пік, **не обов'язково кращий середній RPS**.
CPU вимірюється приблизно кожні 100 ms, одне ядро = 100%; це sampled peak із похибкою.
Нові цифри можуть відрізнятись від початкових: це тести конкретного ПК, не production SLA.

**Resilience Profile простими словами:**

- Сервіс помер із lock — lock закінчиться за TTL; інші не чекають вічно.
- Старий власник не може видалити lock нового власника: перевіряється його token.
- Redis упав — читання йде до БД, але захист від stampede при цьому не працює.
- Ключі живуть довше через jitter — дані можуть бути старішими, до 330 s без записів.
- Багато різних відсутніх ID — negative markers займають пам'ять; TTL обмежує час, не кількість.
- Прямий SQL поза API не інвалідує negative marker: хибний 404 можливий до 15 s.
- Аварія посеред запису може залишити службовий маркер і `BYPASS`; ручне відновлення
  описане у [технічному звіті №4](highload-lab4-technical.md#відновлення-після-аварійно-залишеного-writer).

Початкові перевірені вимірювання: [stampede](../load-tests/results/lab8/comparison.csv)
та [avalanche/negative](../load-tests/results/lab8-verified/metrics.md).

## 3. Матеріали в репозиторії

Lock/jitter — `cache_aside.py`; negative cache — `negative_cache.py`;
генератори й runner-и — `load-tests`; ризики механізмів — Resilience Profile у пункті 2.7.
README посилається на цей звіт, схему наведено в [технічних подробицях](highload-lab8-technical.md#архітектура-та-межі-роботи).

## 4. Порядок захисту

1. Підняти стенд, показати 500 запитів без/із захистом: 2.2. Це одразу перевіряє 2.4.
2. Показати avalanche-тест і різні TTL: 2.3 та 2.5.
3. Показати negative-cache тест і ручні 404 MISS → HIT: 2.6.
4. Побудувати графіки, порівняти SQL та затримки, пояснити ризики: 2.7.

### Якщо запитають альтернативні алгоритми

- Наш lock — один Redis, **не Redlock**. Redlock намагається отримати блокування
  на більшості незалежних Redis-вузлів за обмежений час; має мережеві та часові припущення.
- Bloom Filter компактно перевіряє «точно немає / можливо є»; можливі false positives.
  Треба додавати нові ID до filter. Ми обрали простіші 15-секундні negative markers.
- XFetch / probabilistic early expiration — ймовірнісно почати оновлення ще до завершення TTL,
  щоб усі не почали одночасно. У нас цей алгоритм не реалізований.
- Реплікація й шардинг №6–7 у цій роботі не заявляються: один Redis та одна БД залишаються точками відмови.

## Завершення

```powershell
docker compose @lab8Compose down
```

Без `-v`: БД зберігається. Для повторної демки проходь блоки знову — каталоги отримають нові імена.
Повний алгоритм, схема й методика: [технічний звіт №8](highload-lab8-technical.md).
