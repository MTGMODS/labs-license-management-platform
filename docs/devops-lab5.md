# Лабораторна робота №5 — GitOps через Argo CD

> Цей звіт фіксує завершену ЛР-5 з Deployment. ЛР-6 продовжує її й перемикає
> поточний GitOps Application на Rollout; команди звернення до Deployment
> у цьому звіті стосуються попереднього стану. Актуальний стенд і команди:
> [devops-lab6.md](devops-lab6.md). Сам `values-minikube.yaml` зберігає режим ЛР-5.

Розгортання License Service переведено під керування Argo CD: конфігурація з Git автоматично застосовується до Kubernetes. Реалізовано App of Apps, оновлення образу, виявлення дрейфу, self-healing та відкат комітом.

Команди для **Windows CMD** з кореня `C:\MTGMODS\labs-license-management-platform`. Потрібні Git, PowerShell 7 (`pwsh`), Docker Desktop Linux containers, Minikube, kubectl та Helm. Кластер потребує 4 CPU / 6 GiB RAM; навантажувальні стенди Highload мають бути зупинені. Код застосунку та production не змінюються.

## 1. Репозиторій конфігурації

Конфігурація зберігається поряд із застосунком: [bootstrap/root.yaml](../bootstrap/root.yaml) читає `argocd-apps/`; [argocd-apps/license.yaml](../argocd-apps/license.yaml) використовує наявний [Helm-чарт](../helm/mtgmods-license) і [values-minikube.yaml](../helm/mtgmods-license/values-minikube.yaml). Обидва Application стежать за `main`.

**Шлях зміни: гілка → PR → CI → merge у main → Argo CD.** Після кожного `git push` у командах нижче потрібно створити PR і злити його після зелених перевірок. Посилання `compare` відкриває потрібну гілку. До merge конфігурація кластера не змінюється.

Паролі генеруються локально в Kubernetes Secret; `secrets.existingSecret` виключає їх із рендерингу чарта. CI перевіряє GitOps-маніфести, але не збирає образи для змін лише deployment-файлів і документації: [ci.yml:19](../.github/workflows/ci.yml#L19).

## 2. Образ застосунку та версія

Образи публічні в `ghcr.io/mtgmods/labs-license-management-platform-license`, кластер завантажує їх без `minikube image load`. Зафіксовані дві версії:

- **v1:** `sha-805ca0af230d9ece135982527f3c69ba6ca231b0`.
- **v2:** `sha-07f44c2e07c73ad805d978dedb06ca231f3f8850`.

`config.appVersion` задає поле `version` у `/health` через [configmap.yaml:8](../helm/mtgmods-license/templates/configmap.yaml#L8). Це видима версія розгортання; фактичний образ перевіряється окремо. `checksum/config` у [deployment.yaml:29](../helm/mtgmods-license/templates/deployment.yaml#L29) запускає rollout після зміни конфігурації.

## 3. Встановлення Argo CD

```cmd
cd /d C:\MTGMODS\labs-license-management-platform
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\devops-lab5\bootstrap.ps1 < NUL
set "PATH=%LOCALAPPDATA%\Programs\ArgoCD;%PATH%"
set "K=kubectl --context mtgmods-devops5"
set "REPO=https://github.com/MTGMODS/labs-license-management-platform"
argocd version --client
%K% -n argocd get pods
pwsh -NoProfile -Command "(Import-Clixml '.local/devops5/argocd-admin.clixml').GetNetworkCredential().Password | Set-Clipboard"
```

[bootstrap.ps1](../scripts/devops-lab5/bootstrap.ps1) створює окремий Minikube-профіль, встановлює Argo CD 3.5.6 та перевірений за SHA256 CLI, створює зовнішній Secret, змінює admin-пароль і видаляє initial Secret. UI — **http://127.0.0.1:18090**, логін `admin`, пароль після останньої команди в буфері обміну.

На ноуті bootstrap створює власні секрети: `.local/` не переноситься Git, DPAPI-файл пароля з ПК не використовується. Повторний запуск на тому самому ПК зберігає наявні паролі й БД. HTTP дозволений лише через локальний loopback; для production потрібний TLS.

## 4. App of Apps та початкова синхронізація

Bootstrap вручну застосовує лише root Application; дочірній Application і ресурси створює Argo CD. Порядок sync waves: **0** — конфігурація та PostgreSQL/Redis; **1** — DB-init; **2** — два API. DB-init використовує окремий стабільний `gitops.schemaImageTag`.

```cmd
argocd app get mtgmods-root --refresh
argocd app wait mtgmods-license --sync --health --timeout 300
%K% -n mtgmods-gitops get pods,svc,pvc
helm list -n mtgmods-gitops --kube-context mtgmods-devops5
%K% -n mtgmods-gitops exec deployment/mtgmods-license -- python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())" < NUL
%K% -n mtgmods-gitops get deployment mtgmods-license -o jsonpath="{.spec.template.spec.containers[0].image}"
```

Результат: у UI дерево `mtgmods-root → mtgmods-license`, `Synced / Healthy`, два Ready API, PVC `Bound`; `/health` повертає `UP` і версію. `helm list` порожній: Helm лише рендерить шаблони, життєвим циклом керує Argo CD.

## 5. Оновлення через Git

Поточний `main` уже містить фінальні `v2` та `selfHeal: true`. Для повторення експерименту спочатку окремим PR відновлюється **v1 / selfHeal false**. `git restore --source` нижче змінює тільки явно вказані файли на зафіксовані навчальні версії, не скидає репозиторій. Робоче дерево перед початком має бути чистим.

```cmd
git switch main
git pull --ff-only
set "BRANCH=codex/devops5-prepare-%RANDOM%-%RANDOM%"
git switch -c %BRANCH%
git restore --source=1bf4a18 -- helm/mtgmods-license/values-minikube.yaml
pwsh -NoProfile -Command "$p='argocd-apps/license.yaml'; (Get-Content $p -Raw).Replace('selfHeal: true','selfHeal: false') | Set-Content $p -NoNewline -Encoding utf8"
git add helm/mtgmods-license/values-minikube.yaml argocd-apps/license.yaml
git commit -m "test(gitops): prepare v1 without self-healing"
git push -u origin %BRANCH%
start "" "%REPO%/compare/main...%BRANCH%?expand=1"
```

**Після merge підготовчого PR:**

```cmd
argocd app get mtgmods-root --refresh
argocd app wait mtgmods-root --sync --timeout 180
argocd app get mtgmods-license --refresh
argocd app wait mtgmods-license --sync --health --timeout 300
%K% -n mtgmods-gitops exec deployment/mtgmods-license -- python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())" < NUL
```

Очікується `devops-lab5-v1`. Реліз v2 змінює **і SHA-тег образу, і видиму версію**; схема БД не змінюється:

```cmd
git switch main
git pull --ff-only
set "BRANCH=codex/devops5-release-%RANDOM%-%RANDOM%"
git switch -c %BRANCH%
git restore --source=a7dfddb -- helm/mtgmods-license/values-minikube.yaml
git add helm/mtgmods-license/values-minikube.yaml
git commit -m "feat(gitops): release license service v2"
git push -u origin %BRANCH%
start "" "%REPO%/compare/main...%BRANCH%?expand=1"
```

**Після merge релізного PR:**

```cmd
argocd app get mtgmods-license --refresh
argocd app wait mtgmods-license --sync --health --timeout 300
%K% -n mtgmods-gitops exec deployment/mtgmods-license -- python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())" < NUL
%K% -n mtgmods-gitops get deployment mtgmods-license -o jsonpath="{.spec.template.spec.containers[0].image}"
```

Результат: **v2**, новий образ і `Synced / Healthy`. Ручні `helm upgrade` та `kubectl set image` не використовуються.

## 6. Дрейф Health та відкат

### 6a. Без selfHeal та з selfHeal

Після підготовки selfHeal вимкнений, у Git задано 2 репліки. Ручний scale створює дрейф:

```cmd
%K% -n mtgmods-gitops scale deployment mtgmods-license --replicas=3
%K% -n mtgmods-gitops rollout status deployment/mtgmods-license --timeout=180s
argocd app get mtgmods-license --refresh
```

Результат: **3 Ready, OutOfSync / Healthy**. Увімкнення selfHeal також проходить через Git:

```cmd
git switch main
git pull --ff-only
set "BRANCH=codex/devops5-selfheal-%RANDOM%-%RANDOM%"
git switch -c %BRANCH%
git restore --source=a7dfddb -- argocd-apps/license.yaml
git add argocd-apps/license.yaml
git commit -m "feat(gitops): enable self-healing"
git push -u origin %BRANCH%
start "" "%REPO%/compare/main...%BRANCH%?expand=1"
```

**Після merge** root застосовує нову політику. Повторна ручна зміна автоматично скасовується:

```cmd
argocd app get mtgmods-root --refresh
argocd app wait mtgmods-root --sync --timeout 180
%K% -n argocd get application mtgmods-license -o jsonpath="{.spec.syncPolicy.automated.selfHeal}"
%K% -n mtgmods-gitops scale deployment mtgmods-license --replicas=3
argocd app get mtgmods-license --refresh
argocd app wait mtgmods-license --sync --health --timeout 180
%K% -n mtgmods-gitops get deployment mtgmods-license
```

Очікується `true`, повернення **3 → 2**, `Synced / Healthy` без ручного scale назад.

### 6b. Synced але Degraded

Змінюється лише тег API, стабільний DB-init залишається робочим. `BAD_SHA` автоматично зберігає коміт для наступного revert. Синтаксис `for /f %I` призначений для безпосереднього вставлення в CMD, не для `.bat`.

```cmd
git switch main
git pull --ff-only
set "BRANCH=codex/devops5-bad-image-%RANDOM%-%RANDOM%"
git switch -c %BRANCH%
pwsh -NoProfile -Command "$p='helm/mtgmods-license/values-minikube.yaml'; (Get-Content $p -Raw) -replace '(?m)^  tag: .*','  tag: nonexistent-devops-lab5' | Set-Content $p -NoNewline -Encoding utf8"
git add helm/mtgmods-license/values-minikube.yaml
git commit -m "test(gitops): demonstrate an unavailable image"
for /f %I in ('git rev-parse HEAD') do set "BAD_SHA=%I"
git push -u origin %BRANCH%
start "" "%REPO%/compare/main...%BRANCH%?expand=1"
```

**Після merge:**

```cmd
argocd app get mtgmods-license --refresh
argocd app wait mtgmods-license --sync --timeout 180
%K% -n mtgmods-gitops wait deployment/mtgmods-license --for=condition=Progressing=False --timeout=180s
%K% -n mtgmods-gitops get pods
argocd app get mtgmods-license --refresh
```

Новий pod має `ImagePullBackOff`; після deadline 90 s стан — **Synced / Degraded**. Synced означає відповідність маніфестів Git, не працездатність нового образу. Старі Ready pods можуть обслуговувати запити завдяки `maxUnavailable: 0`.

### 6c. Відкат через git revert

```cmd
git switch main
git pull --ff-only
set "BRANCH=codex/devops5-revert-%RANDOM%-%RANDOM%"
git switch -c %BRANCH%
git revert --no-commit %BAD_SHA%
git commit -m "revert(gitops): restore the working image"
git push -u origin %BRANCH%
start "" "%REPO%/compare/main...%BRANCH%?expand=1"
```

**Після merge:**

```cmd
argocd app get mtgmods-license --refresh
argocd app wait mtgmods-license --sync --health --timeout 300
argocd app history mtgmods-license
git log --oneline -10
%K% -n mtgmods-gitops exec deployment/mtgmods-license -- python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())" < NUL
```

Очікуються робочий образ, `UP / v2` і `Synced / Healthy`. Відкочується опис у Git, не кластер вручну; PostgreSQL та PVC не видаляються.

## 7. Результати перевірки

Повний експеримент виконано **21.10.2026**: реліз [0de0c8f](https://github.com/MTGMODS/labs-license-management-platform/commit/0de0c8f), selfHeal [44c925c](https://github.com/MTGMODS/labs-license-management-platform/commit/44c925c), неправильний образ [8c1d35b](https://github.com/MTGMODS/labs-license-management-platform/commit/8c1d35b), revert [2839a2b](https://github.com/MTGMODS/labs-license-management-platform/commit/2839a2b). PVC залишився тим самим; у history зафіксовані ревізії синхронізацій. Докази: [початковий Healthy](results/devops-lab5/01-initial-healthy.jpg), [дрейф](results/devops-lab5/02-drift-without-selfheal.jpg), [Degraded](results/devops-lab5/03-synced-degraded.jpg), [відновлення](results/devops-lab5/04-revert-healthy.jpg).

Перевірки коду — [test_gitops.py](../tests/test_gitops.py): реальний Helm rendering, зовнішній Secret, порядок hooks, незалежний DB-init і збереження CI checks. На ноуті історія кластера створюється заново; збережені докази не замінюють живий показ пунктів 3–6.

Зупинка зі збереженням БД:

```cmd
minikube stop -p mtgmods-devops5
```
