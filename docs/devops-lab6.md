# Лабораторна робота №6 — Canary-реліз через Argo Rollouts

License Service із ЛР-5 оновлюється поступово: **20% → ручна пауза → 60% → 30 с → 80% → 30 с → 100%**. Argo CD застосовує Git, Argo Rollouts керує ReplicaSet і паузами.

Команди — для **Windows CMD**, після злиття PR роботи в `main`. Потрібні Docker Desktop Linux containers, Minikube, kubectl, Helm і PowerShell 7 (`pwsh`). Профіль потребує 4 CPU / 6 GiB RAM. Інші стенди варто зупинити; порт 18090 має бути вільний.

## 1. Контролер і плагін

[argo-rollouts.yaml](../argocd-apps/argo-rollouts.yaml) додає контролер до App of Apps: Helm-чарт **2.43.8**, контролер **v1.10.1**, sync wave `-1`, auto sync/prune/selfHeal. Контролер доданий окремим комітом перед зміною застосунку. Bootstrap очікує `Healthy` і CRD; плагін перевіряється за SHA256 офіційного релізу.

```cmd
cd /d C:\MTGMODS\labs-license-management-platform
git switch main
git pull --ff-only
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\devops-lab6\bootstrap.ps1 < NUL
set "PATH=%LOCALAPPDATA%\Programs\ArgoCD;%LOCALAPPDATA%\Programs\ArgoRollouts;%PATH%"
set "K=kubectl --context mtgmods-devops6"
set "REPO=https://github.com/MTGMODS/labs-license-management-platform"
kubectl argo rollouts version
argocd app get argo-rollouts
%K% get crd rollouts.argoproj.io
argocd app wait mtgmods-license --sync --health --timeout 300
pwsh -NoProfile -Command "(Import-Clixml '.local/devops6/argocd-admin.clixml').GetNetworkCredential().Password | Set-Clipboard"
```

UI: **http://127.0.0.1:18090**, `admin`, пароль у буфері після останньої команди. На ноуті генеруються власні Secret і пароль; `.local/` не переноситься Git. Повторний запуск зберігає БД.

## 2. Rollout у Helm-чарті

[deployment.yaml](../helm/mtgmods-license/templates/deployment.yaml) працює при `rollout.enabled=false`, [rollout.yaml](../helm/mtgmods-license/templates/rollout.yaml) — при `true`. Спільний Pod template у [_helpers.tpl](../helm/mtgmods-license/templates/_helpers.tpl) зберігає probes, Secret, ресурси й очікування схеми. Версія чарта — `0.2.0`.

[Application](../argocd-apps/license.yaml) накладає `values-rollouts.yaml` поверх `values-minikube.yaml`: **5 API**, окремі PostgreSQL/Redis та PVC. Default/dev/prod і values ЛР-5 зберігають Deployment. Лише початкова заміна контролера може мати короткий простій.

```cmd
%K% -n mtgmods-gitops get rollout,deploy,rs,pods
kubectl argo rollouts get rollout mtgmods-license -n mtgmods-gitops --context mtgmods-devops6
```

Результат: один Rollout API, 5 Ready, Deployment API відсутній. Початкове створення Rollout запускає stable v1 одразу, без canary-пауз.

## 3. Стратегія та версії

[values-rollouts.yaml](../helm/mtgmods-license/values-rollouts.yaml) містить три `setWeight`, ручну й часові паузи, `maxUnavailable: 0`, `maxSurge: 1`. Canary дозволяє перевірити нову версію на частині запитів до повного переходу.

Без Service Mesh частка визначається **кількістю подів**: на 20% — 1 новий і 4 старих. Частка HTTP-відповідей приблизна. Service вибирає обидві версії; `port-forward` одного пода розподіл не показує.

[set-release.ps1](../scripts/devops-lab6/set-release.ps1) змінює разом SHA-тег образу та `/health.version`:

- **v1:** `sha-805ca0af230d9ece135982527f3c69ba6ca231b0`.
- **v2:** `sha-07f44c2e07c73ad805d978dedb06ca231f3f8850`.
- **v3:** `sha-b200fa774adfe73da92ea630f25b6e399c3beb43`.

Це готові сумісні образи для експерименту. Видима версія задається конфігурацією, фактичний образ видно в CLI. `APP_VERSION` прив'язаний до ReplicaSet, тому після abort старі поди зберігають свою версію. DB-init має незалежний стабільний тег.

## 4. Реліз v2 через Git і promote

Шлях: **гілка → PR → зелені перевірки → merge → Argo CD**. Після `start` потрібно злити PR; до merge кластер не зміниться. Робоче дерево перед командами має бути чистим.

```cmd
git switch main
git pull --ff-only
set "BRANCH=codex/devops6-release-%RANDOM%-%RANDOM%"
git switch -c %BRANCH%
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\devops-lab6\set-release.ps1 -Version v2
git diff -- helm/mtgmods-license/values-rollouts.yaml
git add helm/mtgmods-license/values-rollouts.yaml
git commit -m "feat(rollouts): release license v2 through canary"
git push -u origin %BRANCH%
start "" "%REPO%/compare/main...%BRANCH%?expand=1"
```

**Після merge** дочекайтеся першої ручної паузи. Команда `get rollout` із `--watch` дозволяє спостерігати зміни в окремому CMD.

```cmd
argocd app get mtgmods-license --hard-refresh
argocd app wait mtgmods-license --sync --suspended --timeout 300
kubectl argo rollouts get rollout mtgmods-license -n mtgmods-gitops --context mtgmods-devops6
%K% -n mtgmods-gitops get rs -l app.kubernetes.io/component=api
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\devops-lab6\traffic.ps1 -Count 100
```

[traffic.ps1](../scripts/devops-lab6/traffic.ps1) робить запити **з тимчасового пода всередині кластера** й друкує версію, instance ID та `SUMMARY`. На паузі видно v1 і v2, CLI — `Paused`, Argo CD — `Suspended`. Помилки HTTP завершують перевірку невдало; тимчасовий pod видаляється.

```cmd
kubectl argo rollouts promote mtgmods-license -n mtgmods-gitops --context mtgmods-devops6
kubectl argo rollouts status mtgmods-license -n mtgmods-gitops --context mtgmods-devops6 --timeout 180s
argocd app get mtgmods-license --refresh
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\devops-lab6\traffic.ps1 -Count 50
```

Часові паузи проходять автоматично. Результат: `Healthy`, 5 Ready v2, усі запити до v2. У UI відкривається `mtgmods-license`: Rollout → ReplicaSet → Pods.

## 5. Реліз v3, abort і відкат через Git

```cmd
git switch main
git pull --ff-only
set "BRANCH=codex/devops6-abort-%RANDOM%-%RANDOM%"
git switch -c %BRANCH%
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\devops-lab6\set-release.ps1 -Version v3
git add helm/mtgmods-license/values-rollouts.yaml
git commit -m "test(rollouts): prepare v3 for abort demonstration"
for /f %I in ('git rev-parse HEAD') do set "ABORT_SHA=%I"
git push -u origin %BRANCH%
start "" "%REPO%/compare/main...%BRANCH%?expand=1"
```

**Після merge**, на першій ручній паузі:

```cmd
argocd app get mtgmods-license --hard-refresh
argocd app wait mtgmods-license --sync --suspended --timeout 300
kubectl argo rollouts get rollout mtgmods-license -n mtgmods-gitops --context mtgmods-devops6
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\devops-lab6\traffic.ps1 -Count 100
kubectl argo rollouts abort mtgmods-license -n mtgmods-gitops --context mtgmods-devops6
kubectl argo rollouts get rollout mtgmods-license -n mtgmods-gitops --context mtgmods-devops6
argocd app get mtgmods-license --refresh
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\devops-lab6\traffic.ps1 -Count 50 -WaitForStable
```

Дочекайтеся 5 Ready stable-подів і нуля нових. Очікується **лише v2**, Argo CD — **Synced / Degraded**. У Git і spec досі v3, але реліз скасовано й запити обслуговує stable v2. SelfHeal не скасовує abort: змінюється стан Rollout, а не бажаний шаблон у Git.

Завершення відкату — окремий PR. `ABORT_SHA` збережений перед push; `for /f %I` призначений для вставлення в CMD, не `.bat`.

```cmd
git switch main
git pull --ff-only
set "BRANCH=codex/devops6-revert-%RANDOM%-%RANDOM%"
git switch -c %BRANCH%
git revert --no-commit %ABORT_SHA%
git commit -m "revert(rollouts): restore stable license v2 in Git"
git push -u origin %BRANCH%
start "" "%REPO%/compare/main...%BRANCH%?expand=1"
```

**Після merge:**

```cmd
argocd app get mtgmods-license --hard-refresh
argocd app wait mtgmods-license --sync --health --timeout 300
kubectl argo rollouts get rollout mtgmods-license -n mtgmods-gitops --context mtgmods-devops6
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\devops-lab6\traffic.ps1 -Count 50
```

Очікується **Synced / Healthy**, stable v2 і збережений PVC. Повторна демонстрація потребує спочатку повернути v1 окремим PR через `set-release.ps1 -Version v1` і завершити його rollout. Ручний `set image`, `undo` та `helm upgrade` тут не використовуються.

## 6. Перевірка та завершення

[test_rollouts.py](../tests/test_rollouts.py) рендерить реальний чарт і перевіряє взаємовиключення Deployment/Rollout, однаковий Pod template, Service для обох версій, паузи та мінімальну кількість реплік. Тести включені в CI.

Живий експеримент виконано **28.10.2026** у гілці роботи перед PR. Реліз v2 — [a716415](https://github.com/MTGMODS/labs-license-management-platform/commit/a716415), v3 — [75b78e5](https://github.com/MTGMODS/labs-license-management-platform/commit/75b78e5), повторна перевірка abort — [e19b1ed](https://github.com/MTGMODS/labs-license-management-platform/commit/e19b1ed), завершальний Git revert — [af29ded](https://github.com/MTGMODS/labs-license-management-platform/commit/af29ded). Фінальні Application стежать за `main`.

Зафіксовано: 20% — **4 старих + 1 новий**, 60% — **2 + 3**, 80% — **1 + 4**. На першій паузі 100 запитів дали **80 v1 / 20 v2**; після promote — **усі 50 відповідей v2**. Перед abort — **81 v2 / 19 v3**; після abort і Git revert — лише v2. Це вибірки, не гарантія точного відсотка кожних 100 запитів.

Докази: [9 знімків стану та підсумки запитів](results/devops-lab6/results.json), [Suspended](results/devops-lab6/02-v2-paused.png), [Degraded](results/devops-lab6/07-abort-degraded.png), [Healthy після revert](results/devops-lab6/08-git-revert-healthy.png). Перед захистом стенд запускається заново й пункти 4–5 показуються наживо.

```cmd
minikube stop -p mtgmods-devops6
```

Джерела: [Canary](https://argo-rollouts.readthedocs.io/en/stable/features/canary/), [Argo CD та Rollouts](https://argo-rollouts.readthedocs.io/en/stable/FAQ/#how-does-argo-rollouts-integrate-with-argo-cd).
