# Лабораторна №4. Розподілене кешування

**Ідея:** не рахувати однакову статистику продажів у БД на кожен запит.
Зберігаємо готову відповідь у спільному Redis; усі три копії сервісу можуть її читати.

Команди для **PowerShell**, не CMD. Виконуй блоки зверху вниз в одному вікні.
Потрібні запущений Docker Desktop з Linux containers і Python 3.12+.
Venv для демонстрацій не обов'язковий; додаткові pip-пакети не потрібні.
Це окремий навчальний стенд: production `.env`, БД та користувачі не використовуються.

## Підготовка — один раз перед демонстрацією

```powershell
Set-Location C:\MTGMODS\labs-license-management-platform
$labPython = if (Test-Path .\.venv\Scripts\python.exe) { (Resolve-Path .\.venv\Scripts\python.exe).Path } else { 'python' }
& $labPython --version
docker info --format '{{.OSType}}'
$env:LAB4_CACHE_ENABLED = 'true'
$env:LAB4_CACHE_TTL_SECONDS = '300'
$lab4Compose = @('-p', 'mtgmods_distributed_cache_lab4', '-f', 'docker-compose.scaling.yml', '-f', 'docker-compose.cache.yml')
docker compose @lab4Compose up --build -d
docker compose @lab4Compose up -d --no-deps --force-recreate scaling-gateway
docker compose @lab4Compose run --rm --no-deps cache-tools
```

Очікується Python 3.12+, Docker `linux`, три здорові API та навчальні дані.
`cache-tools` створює 2000 тестових ліцензій і покупок; повторний запуск їх не дублює.
Якщо `docker info` не працює — спочатку запусти Docker Desktop.

## 2.1. Аналіз та вибір об'єкта кешування

**Що вимагається:** вибрати читання, яке варто кешувати, та пояснити чому.

**Що зроблено:** обрано `GET /api/v1/license/stats/public` — загальні продажі,
дохід, тарифи та історія. Python збирає відповідь через декілька SQL-агрегацій.
Читачі запитують ті самі цифри, тому щоразу повторювати роботу БД невигідно.
Без змін даних дозволяємо кеш до 5 хвилин; після зміни ліцензії скидаємо його одразу.

**Показати відповідь API:**

```powershell
$lab4Stats = Invoke-RestMethod http://127.0.0.1:18104/api/v1/license/stats/public
$lab4Stats.subscriptions.overview | Format-List
```

Код розрахунку: `services/license/app/infrastructure/repository.py`, метод
`get_heavy_public_stats`. Право користувача на VIP цим кешем не перевіряється.

## 2.2. Інтеграція спільного сховища Redis

**Що вимагається:** усі копії backend мають користуватись одним зовнішнім кешем.

**Що зроблено:** Nginx розподіляє запити між трьома License Service.
Вони використовують одну PostgreSQL і один Redis у навчальній Docker-мережі.

```powershell
docker compose @lab4Compose ps
docker compose @lab4Compose exec -T redis redis-cli PING
```

Очікується `PONG`, Redis і три `license-service-*` у стані healthy.
Конфігурація: `docker-compose.scaling.yml` + `docker-compose.cache.yml`.
Схема: [архітектура з Redis](architecture-c4-distributed-cache.mmd).

## 2.3. Cache-Aside: перший запит MISS, наступний HIT

**Що вимагається:** спочатку шукати готову відповідь у Redis; якщо її немає —
порахувати в PostgreSQL, зберегти та повернути клієнту.

Слова в результаті: `MISS` — довелось рахувати; `HIT` — взяли готове;
`BYPASS` — обійшли кеш і читали БД напряму.

```powershell
function Show-Lab4Stats {
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $reply = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:18104/api/v1/license/stats/public
    $clock.Stop()
    $body = $reply.Content | ConvertFrom-Json
    [pscustomobject]@{
        Cache = [string]$reply.Headers['X-Cache']
        Instance = [string]$reply.Headers['X-Instance-ID']
        RevenueUSD = $body.subscriptions.overview.total_money
        Milliseconds = [math]::Round($clock.Elapsed.TotalMilliseconds, 2)
    } | Format-Table -AutoSize
}
docker compose @lab4Compose exec -T redis redis-cli DEL mtgmods:license:public_stats:v2
Show-Lab4Stats
Show-Lab4Stats
```

**Очікується:** спочатку `MISS`, потім `HIT`; різні `Instance` підтверджують,
що другий сервіс використав кеш першого. Код: `infrastructure/cache_aside.py` у License Service.

## 2.4. Схема ключів та час життя TTL

**Що вимагається:** зрозуміла назва ключа та обмежений час зберігання.

**Що зроблено:** `mtgmods:license:public_stats:v2`, TTL 300 секунд.
Назва містить проєкт, сервіс, вид даних і версію формату. Однакова відповідь
для всіх читачів — тому user ID у ключі не потрібен.

```powershell
Show-Lab4Stats
docker compose @lab4Compose exec -T redis redis-cli TTL mtgmods:license:public_stats:v2
docker compose @lab4Compose exec -T redis redis-cli EXPIRE mtgmods:license:public_stats:v2 1
Start-Sleep -Seconds 2
Show-Lab4Stats
```

**Очікується:** TTL від 0 до 300; після прискореного завершення — `MISS`.
`EXPIRE ... 1` скорочує життя лише поточного ключа для демки, не змінює конфігурацію.
TTL `-2` означає, що ключ уже зник; `-1` означав би ключ без часу життя.

## 2.5. Інвалідація: змінили дані — скинули кеш

**Що вимагається:** після створення, редагування чи видалення читати свіжі цифри.

**Що зроблено:** API-операції запису скидають кеш. Нижче створюється тестова
покупка $3, активується ліцензія, сума змінюється на $7, потім запис видаляється.
Токен генерується з навчальних налаштувань; реальна авторизація/оплата не потрібні.

```powershell
$lab4Token = docker compose @lab4Compose exec -T license-service-1 python -c "import jwt; from datetime import datetime,timedelta,timezone; from app.shared.config import settings; print(jwt.encode({'sub':'1','role':'ADMIN','type':'access','exp':datetime.now(timezone.utc)+timedelta(minutes=15)},settings.JWT_SECRET,algorithm='HS256'))"
$lab4Auth = @{ Authorization = "Bearer $($lab4Token.Trim())" }
$lab4Before = (Invoke-RestMethod http://127.0.0.1:18104/api/v1/license/stats/public).subscriptions.overview.total_money
$lab4New = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:18104/api/v1/license/generate -Headers $lab4Auth -ContentType 'application/json' -Body '{"duration_days":30,"amount":3,"method":"Stars","max_devices":2,"reset_limit":0}'
$lab4Found = Invoke-RestMethod -Uri "http://127.0.0.1:18104/api/v1/license/find?key=$($lab4New.data.key)" -Headers $lab4Auth
$lab4Id = $lab4Found.data[0].id
try {
    Invoke-RestMethod -Method Patch -Uri "http://127.0.0.1:18104/api/v1/license/$lab4Id" -Headers $lab4Auth -ContentType 'application/json' -Body '{"status":"ACTIVE","user_id":999999}' | Out-Null
    Write-Host "Після активації: очікується MISS і дохід $($lab4Before + 3)"
    Show-Lab4Stats
    Invoke-RestMethod -Method Patch -Uri "http://127.0.0.1:18104/api/v1/license/$lab4Id" -Headers $lab4Auth -ContentType 'application/json' -Body '{"amount":7}' | Out-Null
    Write-Host "Після редагування: очікується MISS і дохід $($lab4Before + 7)"
    Show-Lab4Stats
} finally {
    Invoke-RestMethod -Method Delete -Uri "http://127.0.0.1:18104/api/v1/license/$lab4Id" -Headers $lab4Auth | Out-Null
}
Write-Host "Після видалення: очікується MISS і початковий дохід $lab4Before"
Show-Lab4Stats
```

Саме створення неактивованого ключа без власника ще не додає продаж у статистику;
тому в демці є активація. `finally` видаляє власний тестовий запис і при помилці.
Код скидання після записів: `services/license/app/main.py`.

## 2.6. Час відповіді та відмова Redis

**Що вимагається:** показати джерело відповіді, порівняти холодний/гарячий запити,
вимкнути Redis та перевірити роботу через БД.

```powershell
docker compose @lab4Compose stop redis
try {
    Show-Lab4Stats
} finally {
    docker compose @lab4Compose start redis
}
```

**Очікується:** HTTP-запит успішний, `BYPASS` замість `HIT`.
Читання працює без Redis, але записи цього строгого стенда тимчасово отримують 503:
без кешу ми не можемо гарантувати коректне скидання при паралельних змінах.

**Автоматична перевірка всього сценарію й медіани 10 cold/hot вимірів:**

```powershell
$lab4Raw = & $labPython scripts/highload-lab4/verify.py --samples 10
if ($LASTEXITCODE -ne 0) { throw 'Перевірка №4 не пройшла: дивись помилку вище' }
$lab4Check = ($lab4Raw | Out-String) | ConvertFrom-Json
$lab4Check.checks | ConvertTo-Json -Depth 4
$lab4Check.timings | Select-Object samples_per_mode, cold_median_ms, hot_median_ms | Format-List
```

Скрипт сам перевіряє MISS/HIT, інвалідацію, TTL, відмову Redis та відновлення.
У збереженому прогоні медіана була 138.7 ms без готового кешу і 53.9 ms з ним.
На іншому ПК цифри відрізнятимуться; один HIT не зобов'язаний бути швидшим за кожен MISS.
Початкові дані: [highload-lab4.json](results/highload-lab4.json).

## 3. Матеріали в репозиторії

Запуск — `docker-compose.cache.yml`; реалізація — `cache_aside.py` і `main.py`;
перевірки — `scripts/highload-lab4/verify.py` та `tests/system/test_license_cache.py`.
README посилається на цей звіт; схема й початкові вимірювання наведені вище.

## 4. Порядок захисту

1. Підняти стенд блоком «Підготовка».
2. Показати MISS → HIT і різні instances: пункт 2.3.
3. Показати зміну доходу після редагування й повернення після видалення: пункт 2.5.
4. Вимкнути Redis, отримати BYPASS, відновити Redis та показати cold/hot: пункт 2.6.

### Якщо запитають про Lua та терміни

- Продажі рахує **Python + PostgreSQL**, не Lua. Короткі Lua-скрипти Redis
  разом перевіряють версію даних і записують кеш. Інший сервіс не вклиниться
  між цими двома діями й не дозволить повернути старий розрахунок після покупки.
- TTL / expiration — кеш закінчився за часом. Invalidation — скинули через зміну даних.
- LRU / LFU — витісняти найдавніше / найрідше використані ключі при нестачі пам'яті.
  Окреме налаштування цих політик у стенді не заявляється.
- Stampede — багато запитів рахують один холодний ключ; avalanche — багато ключів
  одночасно втратили кеш; thundering herd — масова хвиля звернень до спільного ресурсу.

Службова «версія даних» захищає старий розрахунок, а маркер поточного запису
не дає кешувати проміжний стан. Це не спільна транзакція PostgreSQL і Redis.
Якщо процес загинув посеред запису, може залишитись `BYPASS`; процедуру відновлення
винесено в [технічні подробиці](highload-lab4-technical.md#відновлення-після-аварійно-залишеного-writer).

## Завершення

```powershell
docker compose @lab4Compose down
```

Без `-v`: БД зберігається. Після зупинки №4 можна запускати вимірювання №5.
Повний опис алгоритму, схема та обмеження: [технічний звіт №4](highload-lab4-technical.md).
