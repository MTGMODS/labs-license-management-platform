# Лабораторна робота №4 — пакування застосунку в Helm-чарт

## 1. Мета роботи

Метою роботи є перетворення Kubernetes-маніфестів лабораторної роботи №3 на
параметризований Helm-чарт і перевірка повного життєвого циклу релізу:
валідації, встановлення, оновлення, перегляду ревізій, відкату, тестування та
видалення.

Чарт [`mtgmods-license`](../helm/mtgmods-license/) описує ту саму архітектуру,
що й каталог `k8s`: License Service, PostgreSQL, Redis, ConfigMap, Secret, PVC,
Service, опціональний Ingress і Job ініціалізації схеми.

## 2. Підготовка середовища

Роботу виконано з Helm 3.20.0 у локальному Minikube-кластері, створеному в
лабораторній роботі №3.

```powershell
helm version
minikube status
kubectl get nodes
```

Перед інсталяцією Helm-релізу початкові raw-маніфести видалено, щоб однойменні
ресурси не конфліктували з ресурсами, якими керуватиме Helm:

```powershell
kubectl delete -k k8s
kubectl create namespace mtgmods-helm
```

Namespace створюється окремою командою. Helm спочатку зберігає інформацію про
реліз у цільовому namespace, тому сам чарт не повинен володіти namespace і
видаляти його разом із релізом.

## 3. Структура Helm-чарта

Чарт має таку структуру:

```text
helm/mtgmods-license/
├── Chart.yaml
├── values.yaml
├── values-dev.yaml
├── values-prod.yaml
├── README.md
└── templates/
    ├── _helpers.tpl
    ├── NOTES.txt
    ├── deployment.yaml
    ├── service.yaml
    ├── ingress.yaml
    ├── configmap.yaml
    ├── secret.yaml
    ├── postgres-deployment.yaml
    ├── postgres-service.yaml
    ├── postgres-pvc.yaml
    ├── redis-deployment.yaml
    ├── redis-service.yaml
    ├── db-init-job.yaml
    ├── pre-upgrade-check.yaml
    └── tests/connection-test.yaml
```

Кожному workload або інфраструктурному об’єкту лабораторної роботи №3 відповідає
окремий шаблон. Це зберігає модульність вихідних маніфестів і полегшує аналіз
згенерованого YAML.

## 4. Метадані Chart.yaml

У [`Chart.yaml`](../helm/mtgmods-license/Chart.yaml) задано назву, опис, тип,
версію чарта та версію застосунку.

- `version` — версія самого пакета Helm. Вона змінюється при зміні шаблонів,
  структури або стандартних значень чарта.
- `appVersion` — інформаційна версія застосунку, для якого створено чарт.
- фактичний контейнерний образ визначається значеннями `image.repository` та
  `image.tag`, а не полем `appVersion`.

Таке розділення дозволяє випустити нову версію шаблонів без обов’язкової зміни
коду застосунку або, навпаки, розгорнути інший image tag через `--set`.

## 5. Параметризація шаблонів

Жорстко задані параметри маніфестів винесено до `values.yaml`:

- репозиторій, тег і pull policy образу;
- кількість реплік API, PostgreSQL та Redis;
- тип і порт Service;
- CPU/memory requests і limits;
- параметри ConfigMap;
- параметри probes;
- параметри PostgreSQL, Redis і PVC;
- увімкнення та hostname Ingress;
- створення Secret або використання вже наявного Secret;
- увімкнення pre-upgrade hook і release test.

У шаблонах немає фіксованого імені релізу, SHA-тега чи кількості реплік. Імена
формуються з `.Release.Name`, тому один чарт можна встановити кілька разів у
тому самому кластері.

Значення Secret надходять із values, а не записані безпосередньо в шаблоні.
Збережені в репозиторії значення є локальними навчальними placeholders. Для
зовнішнього Secret використовується така конфігурація:

```yaml
secrets:
  create: false
  existingSecret: mtgmods-license-prod-secrets
```

Якщо `create=false`, але ім’я Secret не задане, рендеринг завершується помилкою
через функцію `required`.

## 6. Спільні шаблони та NOTES

Файл [`_helpers.tpl`](../helm/mtgmods-license/templates/_helpers.tpl) містить
іменовані шаблони для:

- короткого і повного імені ресурсу;
- стандартного набору Helm labels;
- selector labels;
- component labels;
- імені Secret.

Усі Kubernetes-об’єкти підключають спільні labels через `include`. Selector
labels не містять версії чарта, тому оновлення Helm-релізу не змінює незмінний
selector Deployment.

[`NOTES.txt`](../helm/mtgmods-license/templates/NOTES.txt) після інсталяції
виводить актуальну команду доступу до сервісу. Для dev-профілю це port-forward,
а для prod-профілю — URL Ingress і команда `curl --resolve`.

## 7. Конфігурації dev і prod

Основні значення за замовчуванням відповідають середовищу розробки. Окремі файли
[`values-dev.yaml`](../helm/mtgmods-license/values-dev.yaml) і
[`values-prod.yaml`](../helm/mtgmods-license/values-prod.yaml) задають відмінності
між середовищами.

| Параметр | Development | Production-like |
|---|---|---|
| Кількість API-реплік | 2 | 3 |
| Тег образу | `sha-805ca0a…` | `sha-f83b4c7…` |
| CPU request / limit | 50m / 300m | 200m / 1 CPU |
| Memory request / limit | 96Mi / 256Mi | 256Mi / 768Mi |
| Ingress | вимкнено | увімкнено |
| Ingress host | не створюється | `mtgmods-helm.local` |

Відмінності переглядаються без інсталяції в кластер:

```powershell
helm template dev-release helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-dev.yaml > dev-rendered.yaml
helm template prod-release helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-prod.yaml > prod-rendered.yaml
```

Отримано 12 об’єктів для dev і 13 для prod: додатковим об’єктом prod є Ingress.

Пріоритет значень від нижчого до вищого:

1. стандартний `values.yaml` чарта;
2. файли, передані параметрами `-f`, у порядку їх зазначення;
3. параметри `--set` командного рядка.

Тому `--set image.tag=...` перевизначає тег і з `values.yaml`, і з вибраного
environment-файлу.

## 8. Валідація та аналіз згенерованих маніфестів

Синтаксис, обов’язкові поля й базова коректність шаблонів перевірено командою
`helm lint` для всіх трьох наборів значень:

```powershell
helm lint helm/mtgmods-license
helm lint helm/mtgmods-license -f helm/mtgmods-license/values-dev.yaml
helm lint helm/mtgmods-license -f helm/mtgmods-license/values-prod.yaml
```

Усі перевірки завершилися результатом `0 chart(s) failed`. Обидва результати
`helm template` додатково пройшли Kubernetes client-side validation:

```powershell
kubectl apply --dry-run=client -f dev-rendered.yaml
kubectl apply --dry-run=client -f prod-rendered.yaml
```

Згенеровані Deployment, Service, ConfigMap, Secret, PVC, Ingress та Job зберігають
функціональність відповідних маніфестів лабораторної роботи №3.

## 9. Встановлення та стан релізу

Development-реліз встановлено однією командою:

```powershell
helm install lab-license helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-dev.yaml --wait --wait-for-jobs --timeout 5m
```

Прапорець `--wait` очікує готовності workload’ів, а `--wait-for-jobs` — завершення
Job ініціалізації схеми. Після встановлення зафіксовано:

- `2/2` Ready-репліки License Service;
- Ready PostgreSQL і Redis;
- Job ініціалізації в стані Completed;
- PVC обсягом 1 GiB у стані Bound;
- Helm-реліз у стані Deployed.

Стан релізу, обчислені values і створені об’єкти переглядаються командами:

```powershell
helm list -n mtgmods-helm
helm status lab-license -n mtgmods-helm
helm get values lab-license -n mtgmods-helm --all
kubectl get pods,deployments,services,pvc -n mtgmods-helm
```

Helm зберігає кожну ревізію всередині кластера як Secret типу
`helm.sh/release.v1`:

```powershell
kubectl get secrets -n mtgmods-helm -l owner=helm
```

Для першої ревізії створено Secret `sh.helm.release.v1.lab-license.v1`. У ньому
Helm зберігає стиснений стан релізу, необхідний для history та rollback.

## 10. Оновлення через --set

Ревізія 2 створена зміною лише image tag через параметр найвищого пріоритету:

```powershell
helm upgrade lab-license helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-dev.yaml --set image.tag=sha-f83b4c7cf2a84fb90200932522f24aad9ebe31df --wait --wait-for-jobs
```

Після оновлення Deployment зберіг дві Ready-репліки, але використовував образ із
тегом `sha-f83b4c7…`. У `helm history` ревізія 1 стала `superseded`, а ревізія 2 —
`deployed`.

## 11. Оновлення через values-prod.yaml

Ревізія 3 створена застосуванням production-like конфігурації:

```powershell
helm upgrade lab-license helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-prod.yaml --wait --wait-for-jobs
```

Результатом стали три Ready API-репліки, збільшені requests/limits і Ingress з
hostname `mtgmods-helm.local`. Перевірка Ingress повернула HTTP 200 та
`X-Instance-ID` одного з нових pod’ів.

## 12. Перевірка повторної інсталяції

Можливість установити чарт двічі перевірено другим релізом у тому самому
namespace:

```powershell
helm install lab-license-copy helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-dev.yaml --wait --wait-for-jobs
helm list -n mtgmods-helm
```

Одночасно працювали релізи `lab-license` і `lab-license-copy`. Кожен отримав
власні Deployment, Service, PostgreSQL, Redis і PVC, оскільки всі імена містять
ім’я релізу.

## 13. Історія та rollback

Історію змін переглянуто командою:

```powershell
helm history lab-license -n mtgmods-helm
```

Відкат виконано до ревізії 2:

```powershell
helm rollback lab-license 2 -n mtgmods-helm --wait
helm history lab-license -n mtgmods-helm
```

Helm не видалив історію новіших ревізій, а створив ревізію 4 зі статусом
`deployed` і описом `Rollback to 2`. Ревізія 3 перейшла в стан `superseded`.
Після відкату знову працювали дві Ready-репліки з конфігурацією ревізії 2.

## 14. Pre-upgrade hook та Helm test

Як додаткове завдання реалізовано два Helm hooks:

- `pre-upgrade` Job перед оновленням звертається до `/health` поточного релізу;
  якщо вже встановлена система не відповідає, upgrade блокується;
- test hook запускає окремий pod усередині кластера й перевіряє `/health` через
  Kubernetes Service.

```powershell
helm test lab-license -n mtgmods-helm
```

Тест успішно виконано після інсталяції, після оновлення та після rollback.
Відповідь містила `status: UP`, ідентифікатор pod’а й
`database_status: UP`. Test pod автоматично видаляється після успішного виконання.

## 15. Видалення релізів

Обидва релізи видалено командами:

```powershell
helm uninstall lab-license-copy -n mtgmods-helm
helm uninstall lab-license -n mtgmods-helm
helm list -n mtgmods-helm
kubectl get all,pvc,ingress -n mtgmods-helm
```

Після завершення termination `helm list` не містив релізів, а Kubernetes повернув
`No resources found`. Namespace залишився як межа розгортання й може бути
видалений незалежно:

```powershell
kubectl delete namespace mtgmods-helm
```

## 16. Результати роботи

Повний сценарій виконано 7 жовтня 2026 року з Helm 3.20.0, Minikube 1.39.0 та
Kubernetes 1.37.0. Підтверджено:

- коректну параметризацію маніфестів лабораторної роботи №3;
- генерацію різних dev і prod конфігурацій без інсталяції;
- встановлення чистого середовища однією Helm-командою;
- збереження стану й історії релізу в Secrets кластера;
- upgrade через `--set` і через environment values-файл;
- одночасну роботу двох незалежних релізів;
- блокуючий pre-upgrade health hook та успішний `helm test`;
- створення нової ревізії під час rollback;
- повне видалення керованих ресурсів командою `helm uninstall`.

Отже, набір статичних Kubernetes-маніфестів перетворено на повторно
використовуваний Helm-чарт із керованим і відтворюваним життєвим циклом релізу.
