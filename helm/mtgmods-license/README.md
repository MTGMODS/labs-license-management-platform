# MTG MODS License Helm chart

This chart packages the same License Service, PostgreSQL, Redis, persistent
storage, configuration and optional Ingress used in DevOps Lab 3. Every object
name includes the Helm release name, so two releases can coexist in one cluster.

`Chart.yaml` contains two independent versions:

- `version` is the version of the chart package and changes when templates or
  chart defaults change;
- `appVersion` describes the default application version deployed by the chart.
  It is informational; Kubernetes uses `image.tag` as the actual container tag.

## Main values

| Value | Default | Purpose |
|---|---:|---|
| `replicaCount` | `2` | License Service pod count |
| `image.repository` | GHCR License image | application image repository |
| `image.tag` | immutable `sha-805ca0a…` | application image tag |
| `image.pullPolicy` | `IfNotPresent` | Kubernetes image pull policy |
| `service.type` | `ClusterIP` | application Service type |
| `service.port` | `80` | application Service port |
| `resources` | CPU/memory requests and limits | application pod resources |
| `config.*` | development settings | non-sensitive application configuration |
| `ingress.enabled` | `false` | create or omit the Ingress |
| `ingress.host` | `mtgmods.local` | Ingress hostname |
| `postgres.persistence.enabled` | `true` | create or omit the PVC |
| `postgres.persistence.size` | `1Gi` | PostgreSQL volume size |
| `secrets.create` | `true` | create a Secret from chart values |
| `secrets.existingSecret` | empty | use a separately managed Secret when creation is disabled |
| `hooks.preUpgradeCheck.enabled` | `true` | check current release health before upgrade |
| `tests.enabled` | `true` | create the `helm test` health-check hook |

Committed secret values are disposable local-lab placeholders, not real
credentials. For a real environment set `secrets.create=false`, create the
required Secret separately and set `secrets.existingSecret` to its name.

## Validate and render

```powershell
helm lint helm/mtgmods-license
helm template dev-release helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-dev.yaml
helm template prod-release helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-prod.yaml
```

Value precedence from lowest to highest is: chart `values.yaml`, every `-f` file
in command-line order, then `--set`. Thus `--set image.tag=...` overrides both
the chart default and the selected environment file.

## Install

Development:

```powershell
kubectl create namespace mtgmods-helm
helm upgrade --install lab-license helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-dev.yaml --wait --wait-for-jobs --timeout 5m
```

Production-like lab profile:

```powershell
helm upgrade --install lab-license helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-prod.yaml --wait --wait-for-jobs --timeout 5m
```

Install a second independent development release in the same namespace:

```powershell
helm install lab-license-copy helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-dev.yaml --wait --wait-for-jobs
```

Run and remove:

```powershell
helm test lab-license -n mtgmods-helm
helm history lab-license -n mtgmods-helm
helm uninstall lab-license -n mtgmods-helm
```

See [../../docs/devops-lab4.md](../../docs/devops-lab4.md) for the full verified
lifecycle, upgrade and rollback demonstration.
