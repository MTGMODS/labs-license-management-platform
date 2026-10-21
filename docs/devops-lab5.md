# DevOps №5 — GitOps через Argo CD

## 1. Репозиторій конфігурації

Бажаний стан кластера зберігається в тому самому репозиторії, що й застосунок.
`bootstrap/root.yaml` описує кореневий Application `mtgmods-root`, який читає
каталог `argocd-apps/`. Дочірній Application `mtgmods-license` використовує
наявний чарт `helm/mtgmods-license` та `values-minikube.yaml`. Другу копію чарта
не створено; матеріали DevOps №3–4 і Highload залишаються придатними до запуску.

Після злиття PR обидва Application стежать за `main`. Зміни проходять шлях
**гілка → Pull Request → CI → merge → Argo CD**. Для перевірки до merge
обидва `targetRevision` тимчасово можуть вказувати на тестову гілку.

Паролі генеруються локально й потрапляють у зовнішній Kubernetes Secret.
GitOps-профіль задає `secrets.create: false` та `secrets.existingSecret`.
Стандартні навчальні placeholders із `values.yaml` у цьому профілі не
рендеряться в Secret і не використовуються як реальні облікові дані.

Зміни лише в `bootstrap/`, `argocd-apps/`, `helm/`, `docs/` і README не
запускають складання/публікацію образів. При цьому сім jobs `Build image / ...`
виконують успішний крок без збірки: їхні назви не зникають із required checks.
Python/Web перевірки й окремий job `GitOps manifests` залишаються активними.

## 2. Образи та версія застосунку

Використовуються публічні образи GHCR, опубліковані CI з DevOps №2.
`image.tag` — конкретний `sha-<commit>`, не `latest`. Minikube завантажує
образ із реєстру; ручний `minikube image load` не потрібний.

Поле `version` уже є у `/health`: `config.appVersion` потрапляє в ConfigMap
як `APP_VERSION`. Checksum конфігурації у Pod template забезпечує рестарт API
при зміні цього значення. Це мітка розгортання; образ окремо перевіряється
через Pod spec/imageID.

Початкова версія використовує образ
`sha-805ca0af230d9ece135982527f3c69ba6ca231b0`.
Для наступної береться новий образ після успішної CI-публікації.

## 3. Встановлення Argo CD

Потрібні PowerShell 7, Docker Desktop (Linux containers), Minikube,
kubectl і Helm. Окремий профіль `mtgmods-devops5` має 4 CPU, 6 GiB RAM
і Kubernetes 1.35.1. Попередній Minikube-профіль не видаляється.
Production Compose та боти не запускаються.

З кореня репозиторію після злиття PR:

```powershell
.\scripts\devops-lab5\bootstrap.ps1
$env:PATH = "$env:LOCALAPPDATA\Programs\ArgoCD;$env:PATH"
kubectl config use-context mtgmods-devops5
argocd version --client
kubectl -n argocd get pods
```

Скрипт встановлює Argo CD **3.5.6**, перевіряє SHA256 офіційного CLI,
створює зовнішній Secret, змінює початковий admin-пароль, видаляє initial
admin Secret і застосовує кореневий Application. Повторний запуск не змінює
паролі наявної PostgreSQL та не видаляє дані.

Інтерфейс: **http://127.0.0.1:18090**, логін `admin`.
HTTP використовується лише в локальному стенді через loopback port-forward;
LAN/публічний доступ не відкрито. Для production потрібний TLS.

Пароль збережено у Windows DPAPI-зашифрованому файлі для цього користувача.
Каталог `.local/` виключено з Git. Скопіювати пароль для браузера:

```powershell
$credential = Import-Clixml .local/devops5/argocd-admin.clixml
Set-Clipboard $credential.GetNetworkCredential().Password
```

Якщо port-forward припинився, запустити в окремому терміналі:

```powershell
kubectl -n argocd port-forward svc/argocd-server 18090:80 --address 127.0.0.1
```

## 4. App of Apps і перша синхронізація

Єдиний ручний apply конфігурації **застосунку** — `bootstrap/root.yaml`.
Інсталяція Argo CD і створення зовнішнього Secret — підготовка інфраструктури.
Дочірній Application та ресурси сервісу створює Argo CD із Git.

```powershell
argocd app list
argocd app get mtgmods-root --refresh
argocd app wait mtgmods-license --sync --health --timeout 300
kubectl -n mtgmods-gitops get pods,svc,pvc
helm list -n mtgmods-gitops
```

Очікується `Synced / Healthy`. У браузері відкрити `mtgmods-root`, потім
`mtgmods-license`: дерево показує ресурси API, PostgreSQL і Redis.
`helm list` порожній: Helm рендерить шаблони, життєвим циклом керує Argo CD.

Порядок sync waves: **0** — конфігурація, сховище й PostgreSQL/Redis;
**1** — ідемпотентний DB-init Sync hook; **2** — два екземпляри API.
У GitOps вимкнено Helm pre-upgrade health hook, який на першому sync перевіряв
би ще не існуючий API. Звичайний профіль ЛР4 зберігає попередню поведінку.

`gitops.schemaImageTag` окремо фіксує перевірений образ DB-init. Навчальний
неіснуючий тег API не повинен зламати hook раніше за Deployment. Для реальної
зміни схеми в майбутньому потрібно окремо оновити цей тег.

Для HTTP-перевірок запустити в окремому терміналі:

```powershell
kubectl -n mtgmods-gitops port-forward svc/mtgmods-license 18091:80 --address 127.0.0.1
```

```powershell
curl.exe -s http://127.0.0.1:18091/health
kubectl -n mtgmods-gitops get deployment mtgmods-license -o jsonpath='{.spec.template.spec.containers[0].image}'
```

## 5. Оновлення через Git

У новій гілці змінити `image.tag` у `values-minikube.yaml` на наявний новий
SHA-тег і `config.appVersion` на `devops-lab5-v2`. `gitops.schemaImageTag`
для звичайного оновлення API не змінюється.

```powershell
git switch main
git pull --ff-only
git switch -c codex/devops5-release-demo
# Відредагувати image.tag та config.appVersion у values-minikube.yaml.
git add helm/mtgmods-license/values-minikube.yaml
git commit -m "feat(gitops): release license service v2"
git push -u origin codex/devops5-release-demo
```

Створити PR у `main`, дочекатися зелених required checks і злити PR.
При `targetRevision: main` незлиті зміни іншої гілки не розгортаються.

```powershell
argocd app get mtgmods-license --refresh
argocd app wait mtgmods-license --sync --health --timeout 300
kubectl -n mtgmods-gitops rollout status deployment/mtgmods-license --timeout=180s
curl.exe -s http://127.0.0.1:18091/health
```

Очікуються новий образ і `version: devops-lab5-v2` без ручного `helm upgrade`.
При заміні pod активний HTTP port-forward може завершитися — тоді повторити
команду port-forward. Це не означає відмову ClusterIP Service.

## 6. Дрейф, Health і відкат

### 6.1. Без selfHeal

У `argocd-apps/license.yaml` повинно бути `selfHeal: false`. Якщо експеримент
уже завершено і там `true`, спочатку повернути `false` через Git/PR.

```powershell
kubectl -n mtgmods-gitops scale deployment mtgmods-license --replicas=3
argocd app get mtgmods-license --refresh
kubectl -n mtgmods-gitops get deployment mtgmods-license
```

Очікується `OutOfSync`: у Git дві репліки, у кластері три. Без нового commit
і без selfHeal Argo CD не повертає вже синхронізовану конфігурацію автоматично.

### 6.2. Із selfHeal

Через нову гілку/PR змінити дочірній Application на `selfHeal: true`.
Кореневий Application сам підхопить зміну після merge.

```powershell
argocd app get mtgmods-root --refresh
argocd app wait mtgmods-root --sync --timeout 180
kubectl -n argocd get application mtgmods-license -o jsonpath='{.spec.syncPolicy.automated.selfHeal}'
kubectl -n mtgmods-gitops scale deployment mtgmods-license --replicas=3
argocd app get mtgmods-license --refresh
argocd app wait mtgmods-license --sync --health --timeout 180
kubectl -n mtgmods-gitops get deployment mtgmods-license
```

Очікується автоматичне повернення **3 → 2**, без ручного scale назад.

### 6.3. Synced, але Degraded

У новій гілці змінити лише `image.tag` на `nonexistent-devops-lab5`.
Зберегти SHA цього commit, запушити та злити PR після CI.

```powershell
argocd app get mtgmods-license --refresh
kubectl -n mtgmods-gitops get pods
kubectl -n mtgmods-gitops describe deployment mtgmods-license
argocd app get mtgmods-license
```

Новий pod отримає `ErrImagePull` / `ImagePullBackOff`. Спочатку Health може бути
`Progressing`, а після `progressDeadlineSeconds: 90` — `Degraded`.
`Synced` означає відповідність маніфестів Git, а не справність застосунку.
Старі ready pods можуть залишатися доступними завдяки `maxUnavailable: 0`.

### 6.4. Відкат через git revert

Відкотити саме commit із неправильним тегом, а не стан кластера вручну:

```powershell
git switch main
git pull --ff-only
git log --oneline -10
git switch -c codex/devops5-revert-demo
git revert SHA_КОМІТУ_З_НЕІСНУЮЧИМ_ТЕГОМ
git push -u origin codex/devops5-revert-demo
```

Після злиття revert PR:

```powershell
argocd app get mtgmods-license --refresh
argocd app wait mtgmods-license --sync --health --timeout 300
argocd app history mtgmods-license
git log --oneline -10
curl.exe -s http://127.0.0.1:18091/health
```

Очікуються `Synced / Healthy` і робочий образ. У history є Git ревізії
синхронізацій; невдалий rollout не обов'язково буде успішним записом history.
Причину збою додатково видно в operationState та умовах Deployment.

## 7. Лайв-демонстрація

Порядок: UI та дерево ресурсів → оновлення образу через Git → дрейф без
selfHeal → автоматичне повернення реплік → неіснуючий тег і `Degraded` →
git revert і `Healthy`. Фактичний прогін описано в
[результатах](results/devops-lab5.md).

Зупинка без видалення БД: `minikube stop -p mtgmods-devops5`.
Видалення профілю знищить локальні дані PostgreSQL — це не звичайне завершення демо.

Джерела: [Getting started](https://argo-cd.readthedocs.io/en/stable/getting_started/),
[Helm та hooks](https://argo-cd.readthedocs.io/en/stable/user-guide/helm/),
[Auto-sync](https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/),
[App of Apps](https://argo-cd.readthedocs.io/en/stable/operator-manual/cluster-bootstrapping/).
