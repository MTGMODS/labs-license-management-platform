# Helm-чарт MTG MODS License Service

Чарт пакує License Service, PostgreSQL, Redis, постійне сховище, конфігурацію та
опціональний Ingress, реалізовані Kubernetes-маніфестами лабораторної роботи №3.

Усі імена об’єктів залежать від назви Helm-релізу. Завдяки цьому чарт можна
встановити в один кластер кілька разів без конфлікту між Deployment, Service,
Secret, ConfigMap і PVC різних релізів.

## Версії чарта та застосунку

У `Chart.yaml` визначено два незалежні поняття версії:

- `version` — версія пакета Helm; змінюється при модифікації шаблонів або
  стандартної конфігурації;
- `appVersion` — інформаційна версія застосунку, для якого підготовлено чарт.

Фактичний контейнерний образ завжди визначається парою `image.repository` і
`image.tag`. Зміна `appVersion` сама по собі не змінює образ у Kubernetes.

## Основні параметри

| Параметр | Стандартне значення | Призначення |
|---|---:|---|
| `replicaCount` | `2` | кількість pod’ів License Service |
| `image.repository` | GHCR-репозиторій License Service | репозиторій образу застосунку |
| `image.tag` | незмінний `sha-805ca0a…` | версія образу застосунку |
| `image.pullPolicy` | `IfNotPresent` | політика завантаження образу |
| `service.type` | `ClusterIP` | тип Service застосунку |
| `service.port` | `80` | внутрішній порт Service |
| `resources` | CPU/memory requests і limits | ресурси pod’ів API |
| `probes.*` | readiness/liveness settings | параметри health-перевірок |
| `config.*` | development-конфігурація | нечутливі змінні середовища застосунку |
| `ingress.enabled` | `false` | створення або пропуск Ingress |
| `ingress.host` | `mtgmods.local` | hostname Ingress |
| `postgres.replicaCount` | `1` | кількість екземплярів PostgreSQL |
| `postgres.persistence.enabled` | `true` | використання PVC замість `emptyDir` |
| `postgres.persistence.size` | `1Gi` | розмір тому PostgreSQL |
| `redis.replicaCount` | `1` | кількість екземплярів Redis |
| `secrets.create` | `true` | створення Secret із values |
| `secrets.existingSecret` | порожнє | ім’я зовнішнього Secret при вимкненому створенні |
| `hooks.preUpgradeCheck.enabled` | `true` | health-перевірка поточного релізу перед upgrade |
| `tests.enabled` | `true` | створення test hook для `helm test` |

Збережені в репозиторії секретні значення є лише демонстраційними даними
ізольованого Minikube-середовища. Для реального середовища Secret створюється
окремо, після чого чарт отримує:

```yaml
secrets:
  create: false
  existingSecret: mtgmods-license-prod-secrets
```

## Пріоритет values

Helm застосовує значення в такому порядку, від найнижчого до найвищого пріоритету:

1. `values.yaml` чарта;
2. кожен файл `-f` у порядку його зазначення;
3. параметри `--set`.

Наприклад, `--set image.tag=...` перевизначає тег зі стандартного `values.yaml`
і з environment-файлу.

## Валідація й рендеринг

```powershell
helm lint helm/mtgmods-license
helm lint helm/mtgmods-license -f helm/mtgmods-license/values-dev.yaml
helm lint helm/mtgmods-license -f helm/mtgmods-license/values-prod.yaml

helm template dev-release helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-dev.yaml
helm template prod-release helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-prod.yaml
```

`helm lint` перевіряє структуру чарта й шаблони. `helm template` виконує
рендеринг локально без створення ресурсів у кластері та дозволяє порівняти dev і
prod конфігурації.

## Встановлення development-профілю

```powershell
kubectl create namespace mtgmods-helm
helm upgrade --install lab-license helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-dev.yaml --wait --wait-for-jobs --timeout 5m
```

Development-профіль створює дві репліки API й не створює Ingress. Доступ до API
забезпечується port-forward:

```powershell
kubectl port-forward -n mtgmods-helm service/lab-license-mtgmods-license 8080:80
```

Після цього OpenAPI доступний за адресою `http://127.0.0.1:8080/docs`.

## Встановлення production-like профілю

```powershell
kubectl create namespace mtgmods-helm
helm upgrade --install lab-license helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-prod.yaml --wait --wait-for-jobs --timeout 5m
```

Production-like профіль створює три API-репліки, застосовує збільшені ресурси,
другий immutable image tag та Ingress `mtgmods-helm.local`.

## Другий незалежний реліз

```powershell
helm install lab-license-copy helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-dev.yaml --wait --wait-for-jobs
helm list -n mtgmods-helm
```

Назва `lab-license-copy` входить до імен усіх ресурсів другого релізу, тому він
може працювати паралельно з `lab-license`.

## Перевірка релізу

```powershell
helm status lab-license -n mtgmods-helm
helm get values lab-license -n mtgmods-helm --all
helm test lab-license -n mtgmods-helm
helm history lab-license -n mtgmods-helm
```

Test hook виконує HTTP-запит до `/health` через ClusterIP Service. Успішний тест
підтверджує одночасну доступність API й PostgreSQL зсередини кластера.

## Оновлення та відкат

```powershell
helm upgrade lab-license helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-dev.yaml --set image.tag=sha-f83b4c7cf2a84fb90200932522f24aad9ebe31df --wait --wait-for-jobs
helm upgrade lab-license helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-prod.yaml --wait --wait-for-jobs
helm history lab-license -n mtgmods-helm
helm rollback lab-license 2 -n mtgmods-helm --wait
```

Перед кожним upgrade pre-upgrade hook перевіряє `/health` поточного релізу. Після
rollback Helm не переписує історію, а створює нову ревізію на основі вибраної.

## Видалення

```powershell
helm uninstall lab-license -n mtgmods-helm
kubectl get all,pvc,ingress -n mtgmods-helm
```

Команда видаляє всі керовані ресурси релізу. Namespace залишається незалежним і
за потреби видаляється окремо.

Повний опис реалізації та зафіксованих результатів міститься у
[`docs/devops-lab4.md`](../../docs/devops-lab4.md).
