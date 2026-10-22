# Лабораторна робота №5 — Навантажувальне тестування

За допомогою k6 виміряно читання, запис і складений сценарій License Service. Порівняно 1/3 API та роботу з кешем/без кешу; зафіксовано затримки, насичення й Performance Baseline.

## Запуск середовища

Команди для **Windows CMD**, послідовно в одному вікні. Потрібні Python 3.12+, Docker Desktop Linux containers, Compose 2.24.4+ та **8+ logical CPU, доступних Docker**: у конфігурації використовуються CPU 0–7. Інші навантажувальні стенди та Minikube мають бути зупинені. Venv необов'язковий; host runner використовує стандартну бібліотеку Python.

```cmd
cd /d C:\MTGMODS\labs-license-management-platform
set "LABPY=python"
if exist ".venv\Scripts\python.exe" set "LABPY=.venv\Scripts\python.exe"
"%LABPY%" --version
docker info --format "OS={{.OSType}}; logical CPU={{.NCPU}}"
set "LAB4_CACHE_ENABLED=true"
set "LAB4_CACHE_TTL_SECONDS=300"
set "LAB5_UPSTREAM_CONFIG=./deploy/horizontal-scaling/upstream-round-robin.conf"
set "DC5=docker compose -p mtgmods_load_baseline_lab5 -f docker-compose.scaling.yml -f docker-compose.cache.yml -f docker-compose.load.yml"
%DC5% build license-db-init
%DC5% up -d --wait --wait-timeout 180
%DC5% up -d --no-deps --force-recreate scaling-gateway
%DC5% run --rm -T --no-deps cache-tools python /load-tests/reset_dataset.py < NUL
%DC5% run --rm -T --no-deps cache-tools python /lab4/seed.py --rows 2000 < NUL
```

Стенд працює на `127.0.0.1:18105`. Reset очищає **лише окрему навчальну БД №5**, seed створює 2000 покупок. Production дані та реальні платежі не використовуються.

## 2.1. Бізнес-сценарії навантаження

Реалізовано у [baseline.js:68](../load-tests/baseline.js#L68), функція `exercise`:

- **A / read:** GET публічної статистики — читання та серіалізація великих агрегатів.
- **B / write:** POST створення ліцензії й покупки — транзакції запису.
- **C / workflow:** створити → знайти → змінити суму → прочитати й перевірити → видалити. Одна ітерація містить 5 HTTP-запитів.

## 2.2. Конфігурація навантажувального контуру

[docker-compose.load.yml](../docker-compose.load.yml) задає кожному API 1 CPU / 512 MiB; PostgreSQL — 2 CPU / 768 MiB. k6 працює на окремих logical CPU 6–7, API — 0–2, БД — 3–4. Пул кожного API: 5 основних + до 10 додаткових з'єднань, очікування 30 s. Це ізоляція ресурсів на одному ПК, не окремі фізичні сервери.

```cmd
%DC5% ps
docker inspect mtgmods_load_baseline_lab5-license-service-1-1 --format "CPU={{.HostConfig.CpusetCpus}}; NanoCPUs={{.HostConfig.NanoCpus}}; RAM={{.HostConfig.Memory}}"
```

Для першого API: `CPU=0`, `NanoCPUs=1000000000`, `RAM=536870912`.

## 2.3. Warm-up та ступеневе нарощування

[baseline.js:26](../load-tests/baseline.js#L26) відокремлює warm-up від вимірювань. Повний профіль: розігрів 5 s, потім 1 → 10 → 25 → 50 → 100 → 200 VUs по 8 s. Короткий живий запуск використовує той самий сценарій з меншими тривалостями:

```cmd
%DC5% run --rm -T --no-deps -e WORKLOAD=read -e LEVELS=1,10,25 -e STAGE_SECONDS=3 -e WARMUP_SECONDS=2 k6 < NUL
```

У виводі видно `warmup`, `level_1`, `level_10`, `level_25` та `LAB5_RESULT`. Між фазами є паузи для завершення запитів. Короткий запуск не замінює повний baseline.

## 2.4. Метрики та розподіл затримок

RPS — завершені HTTP-запити за секунду; Avg — середній час; p50 — медіана; p95/p99 — час, у який вклалися 95%/99% запитів. Хвостові затримки зростають через конкуренцію за CPU й очікування; середнє приховує повільні відповіді.

Нижче — **повний прогін 13.10.2026 на ПК**, не результати поточного ноутбука. Читання, 3 API, теплий Redis; усі значення часу в ms, помилки — %:

```text
VU    RPS     Avg      p50      p95      p99      Помилки
1     32.09   30.85    27.67    50.03    61.62    0
10    81.22   120.83   101.27   247.62   367.71   0
25    80.18   304.14   273.41   549.06   671.34   0
50    73.62   648.03   607.55   1034.71  1112.24  0
100   81.02   1135.91  1151.90  1553.58  1741.89  0
200   79.22   2226.05  2354.76  2846.02  2977.08  0
```

Повні таблиці всіх ступенів: [A / read](../load-tests/results/lab5/read_three_cached.csv), [B / write](../load-tests/results/lab5/write_three.csv), [C / workflow](../load-tests/results/lab5/workflow_three.csv). У [report.py:48](../load-tests/report.py#L48) реалізовано побудову CSV/SVG із вимірювань.

```cmd
start "" "load-tests\results\lab5\read_three_cached-latency.svg"
start "" "load-tests\results\lab5\read_three_cached-errors.svg"
```

## 2.5. Saturation Point та первинне вузьке місце

Між **10 і 25 VU** RPS майже не зріс: 81.22 → 80.18, але p99 виріс 367.71 → 671.34 ms. Це інтервал насичення. У ресурсних вимірюваннях кожен API використовував близько 100% свого ядра, БД — близько 4%, Redis — 10%. Підтверджений bottleneck — **CPU застосунку**; конкретна функція потребує окремого профілювання. Джерело — [passport.json](../load-tests/results/lab5/passport.json).

```cmd
start "" "load-tests\results\lab5\read_three_cached-throughput.svg"
```

## 2.6. Контрольні порівняння масштабування та кешу

[run_baseline.py:42](../load-tests/run_baseline.py#L42) запускає п'ять режимів з однаковим початковим dataset: A/3 API/cache, B/3 API, C/3 API, A/1 API/cache, A/3 API/no cache. У режимі одного API інші два справді зупиняються. Перед кожним режимом дані відновлюються.

На 10 VU в повному прогоні: 1 API/cache — **30.59 RPS**, 3 API/cache — **81.22**, 3 API/no cache — **30.68**. Прискорення масштабування = 81.22 / 30.59 ≈ **2.66**; ефективність = 2.66 / 3 ≈ **88.5%**. Для кешу p95 знизився **553.77 → 247.62 ms**. Реальні SQL-виклики та CPU збережені в JSON кожного режиму поряд із CSV.

За весь профіль читання без кешу зафіксовано **22272 SQL-виклики / 33357.65 ms** виконання в БД, із кешем — **202 / 16.51 ms**. Це сумарні лічильники, включно зі службовими операціями; кількість завершених HTTP-запитів у режимах різна.

Повтор усіх п'яти режимів із коротким профілем; результати записуються в нову директорію:

```cmd
set "RUN5=load-tests\results\lab5-run-%RANDOM%-%RANDOM%"
"%LABPY%" load-tests\run_baseline.py --levels 1,10,25 --stage-seconds 3 --warmup-seconds 2 --output-dir "%RUN5%" < NUL
dir "%RUN5%\*.json"
```

Для повного профілю в цій команді використовуються `--levels 1,10,25,50,100,200 --stage-seconds 8 --warmup-seconds 5`. Збережені графіки вище належать початковому повному прогону, нові JSON — поточному запуску.

## 2.7. Специфікація Performance Baseline

Картка початкового повного прогону, розрахована в [report.py:9](../load-tests/report.py#L9) та збережена в [passport.json](../load-tests/results/lab5/passport.json):

```text
Environment:        12 logical CPU / ~16 GiB Docker RAM; 3 API; ресурси з п. 2.2
Stable throughput:  81.22 RPS для читання статистики з теплим кешем
Saturation point:   між 10 і 25 VU
p95 / p99:          247.62 / 367.71 ms під стабільним навантаженням
Error rate:         0%, також на виявленому порозі насичення
Critical scenario:  повторні розрахунки статистики без кешу
Primary bottleneck: CPU застосунку
```

Прийнятні межі: p95 ≤500 ms, p99 ≤1000 ms, помилки <1%. Картка описує цей стенд, не production SLA. На ноуті межа насичення та RPS можуть відрізнятися.

Модель k6 тут closed: VU чекає відповіді перед наступною дією. При уповільненні він надсилає менше запитів — це ризик coordinated omission. Warm-up прогріває кеш і з'єднання; вплив GC окремо не профілювався, JVM JIT у Python-сервісі немає.

Зупинка без видалення тому навчальної БД:

```cmd
%DC5% down
```
