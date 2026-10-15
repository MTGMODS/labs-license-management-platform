# Лабораторна №5. Навантажувальне тестування

**Ідея:** виміряти, скільки система обробляє запитів і коли починає гальмувати.
№4 додала кеш; №5 вимірює користь кешу та кількох копій сервісу.

Команди для **PowerShell**. Виконуй зверху вниз в одному вікні.
Потрібні Docker Desktop (Linux containers), Python 3.12+ і **щонайменше 8 logical CPU**,
доступних Docker. Це потрібно через закріплення контейнерів за CPU 0–7.
Не запускай паралельно вимірювання інших лаб. БД тут лише навчальна й очищається runner-ом.
Venv необов'язковий: Python керує тестами, а навантаження генерує k6 у Docker.

## Підготовка — один раз

```powershell
Set-Location C:\MTGMODS\labs-license-management-platform
$labPython = if (Test-Path .\.venv\Scripts\python.exe) { (Resolve-Path .\.venv\Scripts\python.exe).Path } else { 'python' }
& $labPython --version
docker info --format 'OS={{.OSType}}; logical CPU={{.NCPU}}'
$env:LAB4_CACHE_ENABLED = 'true'
$env:LAB4_CACHE_TTL_SECONDS = '300'
$env:LAB5_UPSTREAM_CONFIG = './deploy/horizontal-scaling/upstream-round-robin.conf'
$lab5Compose = @('-p', 'mtgmods_load_baseline_lab5', '-f', 'docker-compose.scaling.yml', '-f', 'docker-compose.cache.yml', '-f', 'docker-compose.load.yml')
docker compose @lab5Compose up --build -d
docker compose @lab5Compose up -d --no-deps --force-recreate scaling-gateway
docker compose @lab5Compose run --rm --no-deps cache-tools python /load-tests/reset_dataset.py
docker compose @lab5Compose run --rm --no-deps cache-tools python /lab4/seed.py --rows 2000
```

Очікується `linux`, CPU ≥ 8, три healthy API. Порт цього стенда — `18105`.
Останні дві команди очищають **тільки навчальну БД №5** і створюють 2000 тестових покупок.
Не використовуй production `.env` або дані. Якщо запущено №4, спочатку заверши її командою `down` із її звіту.

## 2.1. Три бізнес-сценарії

**Що вимагається:** перевірити читання, запис та складений сценарій.

**Що зроблено** у [baseline.js](../load-tests/baseline.js), функція `exercise`:

- `read`: GET публічної статистики — багато читань.
- `write`: POST створення ліцензії та покупки — багато записів у БД.
- `workflow`: створити → знайти → змінити суму → прочитати й перевірити суму → видалити.

Один workflow = **5 HTTP-запитів**. Тести не проводять реальних оплат.
Запуск усіх сценаріїв і порівнянь є в пункті 2.6, не треба запускати кожен вручну.

## 2.2. Окремий навантажувальний стенд

**Що вимагається:** автоматизований інструмент і відомі CPU/RAM/налаштування.

**Що зроблено:** k6, Nginx, три API, Redis і PostgreSQL у власному Docker-проєкті.
Кожному API виділено 1 CPU / 512 MiB, БД — 2 CPU / 768 MiB.
Генератор k6 має окремі logical CPU 6–7, щоб менше заважати самим сервісам.
Пул БД: 5 основних + до 10 додаткових з'єднань на instance, очікування до 30 s.

**Показати контейнери та ресурси одного API:**

```powershell
docker compose @lab5Compose ps
docker inspect mtgmods_load_baseline_lab5-license-service-1-1 --format 'CPU set={{.HostConfig.CpusetCpus}}; NanoCPUs={{.HostConfig.NanoCpus}}; Memory bytes={{.HostConfig.Memory}}'
```

Очікується CPU set `0`, NanoCPUs `1000000000` (= 1 CPU), Memory `536870912` (= 512 MiB).
Конфігурація: [docker-compose.load.yml](../docker-compose.load.yml).
Це один фізичний ПК з Docker, не кластер окремих серверів.

## 2.3. Розігрів і поступове збільшення користувачів

**Що вимагається:** спочатку розігріти систему, потім збільшувати навантаження.

**Що зроблено:** у повному тесті 5 s розігріву, потім 1 → 10 → 25 → 50 → 100 → 200
одночасних віртуальних користувачів, по 8 s кожен ступінь. Розігрів не входить у підсумкові перцентилі.

**Коротка жива демка:** 2 s розігріву, 1 → 10 → 25 користувачів, по 3 s.
Між фазами є додаткові паузи; команда не завершиться за 11 секунд.

```powershell
docker compose @lab5Compose run --rm --no-deps -e 'WORKLOAD=read' -e 'LEVELS=1,10,25' -e 'STAGE_SECONDS=3' -e 'WARMUP_SECONDS=2' k6
```

У виводі k6 видно warmup і фази `level_1`, `level_10`, `level_25`.
Наприкінці `LAB5_RESULT` містить вимірювання кожного ступеня.
Це коротка демонстрація запуску, **не заміна повного збереженого дослідження**.

## 2.4. Метрики: RPS, середнє, p50/p95/p99, помилки

**Що вимагається:** описати швидкість числами, не словами «ніби швидко».

- RPS — кількість завершених запитів за секунду.
- Avg — середній час; p50 — медіана.
- p95 = 250 ms: 95% запитів завершились не довше ніж за 250 ms.
- p99 показує повільні відповіді, які середнє може приховувати.
- `error_rate` — частка помилок: 0 = 0%, 0.01 = 1%.

**Показати вже виміряний повний прогін:**

```powershell
Import-Csv .\load-tests\results\lab5\read_three_cached.csv | Select-Object vus, rps, avg_ms, p50_ms, p95_ms, p99_ms, error_rate | Format-Table -AutoSize
Invoke-Item .\load-tests\results\lab5\read_three_cached-latency.svg
```

Файл і графік — вимірювання 13.10.2026, не щойно запущеної короткої демки.
Кількість запитів, тривалості та інші сценарії є в [повних результатах](../load-tests/results/lab5/metrics.md).

## 2.5. Межа насичення та вузьке місце

**Що вимагається:** знайти момент, коли додаткові користувачі вже не збільшують продуктивність.

**Що встановлено у повному прогоні:** на 10 користувачах ≈81 RPS, на 25 — ≈80 RPS.
При цьому p99 виріс приблизно з 368 до 671 ms. Роботи більше, а система встигає
приблизно стільки ж — запити чекають довше. Насичення спостерігається між 10 і 25 VUs.

**Показати графік та зафіксований CPU:**

```powershell
Invoke-Item .\load-tests\results\lab5\read_three_cached-throughput.svg
$lab5Passport = Get-Content .\load-tests\results\lab5\passport.json -Raw -Encoding UTF8 | ConvertFrom-Json
$lab5Passport.system_performance_baseline.evidence_peak_cpu_pct | Format-List
```

API використовували приблизно 100% виділеного ядра кожен, Redis і БД — значно менше.
Підтверджене ресурсне обмеження — **CPU застосунку**. Точну повільну функцію без
окремого профілювання ми не визначали. На іншому ПК межа може відрізнятись.

## 2.6. Порівняння одного/трьох сервісів і з кешем/без кешу

**Що вимагається:** порівняти конфігурації за однакового профілю навантаження.

**Що зроблено:** Python runner сам запускає 5 режимів:
три API з кешем; записи; workflow; один API з кешем; три API без кешу.
У режимі одного API дві інші копії справді зупиняються. Перед кожним режимом
відновлюються однакові початкові 2000 ліцензій і покупок.

**Скопіюй для нового короткого порівняння всіх режимів:**

```powershell
$lab5Demo = 'load-tests/results/defense-lab5-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
& $labPython load-tests/run_baseline.py --levels 1,10 --stage-seconds 3 --warmup-seconds 2 --output-dir $lab5Demo
if ($LASTEXITCODE -ne 0) { throw 'Тест №5 не пройшов: дивись помилку вище' }
& $labPython load-tests/report.py $lab5Demo
if ($LASTEXITCODE -ne 0) { throw 'Не вдалося сформувати звіт №5' }
Get-Content (Join-Path $lab5Demo 'metrics.md') -Encoding UTF8
```

Команда виконає **всі п'ять режимів**, тому це кілька тестів, а не один миттєвий запит.
Ім'я директорії унікальне — нічого вручну перейменовувати не треба.
Результати короткого запуску залишаються окремо від початкових повних.

У початковому повному тесті на 10 VUs: один API з кешем ≈30.59 RPS,
три з кешем ≈81.22 RPS, три без кешу ≈30.68 RPS.
Прискорення 1→3 = RPS трьох / RPS одного; ефективність масштабування = це прискорення / 3.
Розраховані коефіцієнти можна показати так:

```powershell
$lab5Passport.comparisons | Where-Object vus -eq 10 | Format-List
```

За потреби **повний повтор початкового профілю**, замість короткого:

```powershell
$lab5Full = 'load-tests/results/full-lab5-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
& $labPython load-tests/run_baseline.py --output-dir $lab5Full
if ($LASTEXITCODE -ne 0) { throw 'Повний прогін №5 не пройшов' }
& $labPython load-tests/report.py $lab5Full
```

## 2.7. Підсумкова картка Performance Baseline

**Що вимагається:** зафіксувати середовище, стабільний RPS, затримки, межу та вузьке місце.

**Що зроблено:** [passport.json](../load-tests/results/lab5/passport.json).
Це картка конкретного повного прогону, не гарантія production-продуктивності.

```powershell
$lab5Passport.system_performance_baseline | Select-Object environment, stable_throughput_rps, saturation_interval_vus, p95_ms, p99_ms, error_rate, critical_scenario, primary_bottleneck | Format-List
```

У цьому паспорті: ≈81 RPS, p95 ≈248 ms, p99 ≈368 ms, 0% помилок.
Прийнятними для лаби обрали p95 ≤500 ms, p99 ≤1000 ms, помилки <1%.
Короткий демопрогін може не досягти насичення — не треба приписувати йому старий висновок.

## 3. Матеріали в репозиторії

Генератор — `load-tests/baseline.js`; керування режимами — `run_baseline.py`;
таблиці/графіки — `load-tests/results/lab5`; картка системи — `passport.json`.
Конфігурація ресурсів — `docker-compose.load.yml`; README посилається на цей звіт.

## 4. Порядок захисту

1. Підняти стенд і запустити живе нарощування VUs: підготовка та пункт 2.3.
2. Відкрити повну таблицю/графіки й пояснити p95/p99 та насичення: 2.4–2.5.
3. Показати контрольні режими й порівняння: 2.6. Короткий прогін не називати повним baseline.
4. Відкрити картку системи: 2.7.

### Що ще можуть запитати

- Чому не Postman? Він не замінює автоматизований контрольований потік k6.
- Чому більше користувачів ≠ більше RPS? Ресурс вичерпаний, нові запити стають у чергу.
- Що таке coordinated omission? Наші VUs чекають відповіді перед наступною дією.
  Коли сервер гальмує, вони самі надсилають менше запитів. Це не модель незалежного постійного потоку.
- GC/JIT? GC і планування можуть давати окремі затримки. Тут Python, не JVM:
  warmup насамперед прогріває кеш і з'єднання. Конкретну роль GC ми не профілювали.

## Завершення

```powershell
docker compose @lab5Compose down
```

Без `-v`: навчальна БД залишається. Після цього можна запускати №8 без конкуренції за ресурси.
Детальні методика, обмеження й артефакти: [технічний звіт №5](highload-lab5-technical.md).
