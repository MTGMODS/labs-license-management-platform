# DevOps Lab 4 — Helm packaging and release lifecycle

DevOps Lab 3 raw manifests were converted into the
[`mtgmods-license`](../helm/mtgmods-license/) Helm chart. The chart produces the
same runtime architecture while making environment-specific values and release
names configurable.

## Chart structure and templating

Each workload from `k8s/` has a corresponding template under
`helm/mtgmods-license/templates/`. Namespace creation is intentionally performed
by the deployment command: Helm stores its release Secret in the target namespace
before rendering managed resources, so a chart must not own and later delete its
own release namespace.

The `_helpers.tpl` file provides reusable full-name, common-label,
component-label and Secret-name templates. All managed object names and selectors
derive from `.Release.Name`; no fixed release name is left in a template.

Optional resources are controlled by values:

- `ingress.enabled` creates or omits Ingress;
- `postgres.persistence.enabled` chooses a PVC or disposable `emptyDir`;
- `secrets.create` chooses a chart-managed demonstration Secret or a separately
  managed Secret named by `secrets.existingSecret`.

The complete parameter table and environment commands are in the
[chart README](../helm/mtgmods-license/README.md).

## Environment comparison without installation

```powershell
helm lint helm/mtgmods-license
helm template dev-release helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-dev.yaml > dev-rendered.yaml
helm template prod-release helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-prod.yaml > prod-rendered.yaml
```

The verified differences are:

| Setting | Development | Production-like |
|---|---|---|
| API replicas | 2 | 3 |
| image tag | `sha-805ca0a…` | `sha-f83b4c7…` |
| CPU request / limit | 50m / 300m | 200m / 1 CPU |
| memory request / limit | 96Mi / 256Mi | 256Mi / 768Mi |
| Ingress | disabled | enabled at `mtgmods-helm.local` |

Both rendered outputs passed Kubernetes client-side validation. Value precedence
is `values.yaml` < each later `-f` file < `--set`.

## Install and inspect release state

Remove Lab 3 objects, create a clean namespace and install the dev profile:

```powershell
kubectl delete -k k8s
kubectl create namespace mtgmods-helm
helm install lab-license helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-dev.yaml --wait --wait-for-jobs --timeout 5m
```

Inspect the release, computed values and cluster-side release storage:

```powershell
helm list -n mtgmods-helm
helm status lab-license -n mtgmods-helm
helm get values lab-license -n mtgmods-helm --all
kubectl get secrets -n mtgmods-helm -l owner=helm
```

Helm stores each revision as a Secret such as
`sh.helm.release.v1.lab-license.v1` with type `helm.sh/release.v1`. The verified
cold install produced two Ready API replicas, Ready PostgreSQL and Redis pods, a
Completed schema Job and a Bound 1 GiB PVC.

## Upgrade, second environment and rollback

Revision 2 changed only the image tag through the highest-precedence `--set`:

```powershell
helm upgrade lab-license helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-dev.yaml --set image.tag=sha-f83b4c7cf2a84fb90200932522f24aad9ebe31df --wait --wait-for-jobs
```

Revision 3 applied the production-like file and produced three Ready API replicas
plus the Ingress:

```powershell
helm upgrade lab-license helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-prod.yaml --wait --wait-for-jobs
```

The release was then rolled back to revision 2. Helm recorded the rollback as a
new deployed revision 4, while revision 3 became superseded:

```powershell
helm history lab-license -n mtgmods-helm
helm rollback lab-license 2 -n mtgmods-helm --wait
helm history lab-license -n mtgmods-helm
```

The chart was also installed simultaneously as `lab-license-copy` in the same
namespace. Both releases became deployed and their generated names, databases,
Services and PVCs remained independent.

## Hook and release test

The optional task uses both Helm mechanisms:

- a `pre-upgrade` Job calls the currently deployed `/health` endpoint and blocks
  an upgrade if the existing release is unhealthy;
- a test hook calls the Service `/health` endpoint from inside the cluster.

```powershell
helm test lab-license -n mtgmods-helm
```

The test succeeded before and after upgrades and after rollback; the response
reported `status: UP`, the serving pod ID and `database_status: UP`.

## Uninstall verification

```powershell
helm uninstall lab-license-copy -n mtgmods-helm
helm uninstall lab-license -n mtgmods-helm
helm list -n mtgmods-helm
kubectl get all,pvc,ingress -n mtgmods-helm
```

After workload termination completed, Helm listed no releases and Kubernetes
reported no managed resources in the namespace. The namespace itself remains as
the deployment boundary and can be removed separately with
`kubectl delete namespace mtgmods-helm`.

## Verification record

The full procedure was executed locally on 7 October 2026 using Helm 3.20.0,
Minikube 1.39.0 and Kubernetes 1.37.0. `helm lint` passed for default, dev and
prod values; the dev render contained 12 objects and prod contained 13 because
Ingress is enabled only for prod. Install, two upgrade paths, the pre-upgrade
hook, two concurrent releases, release test, history, rollback and uninstall all
completed successfully.
