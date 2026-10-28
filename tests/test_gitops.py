"""Render the real Helm chart and check GitOps-specific ordering and CI gates."""
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / 'helm/mtgmods-license'


def render(*args):
    if not shutil.which('helm'):
        pytest.skip('Helm is required for chart rendering')
    result = subprocess.run(
        ['helm', 'template', 'mtgmods-license', str(CHART), '-n', 'mtgmods-gitops', *args],
        check=True, capture_output=True, text=True,
    )
    return list(yaml.safe_load_all(result.stdout))


def test_gitops_bootstraps_the_existing_chart_from_git():
    root = yaml.safe_load((ROOT / 'bootstrap/root.yaml').read_text())
    app = yaml.safe_load((ROOT / 'argocd-apps/license.yaml').read_text())
    assert root['spec']['source']['path'] == 'argocd-apps'
    assert root['spec']['syncPolicy']['automated'] == {'prune': True, 'selfHeal': True}
    assert app['spec']['source']['path'] == 'helm/mtgmods-license'
    assert app['spec']['source']['helm']['valueFiles'] == ['values-minikube.yaml', 'values-rollouts.yaml']
    assert app['spec']['destination']['namespace'] == 'mtgmods-gitops'
    assert root['spec']['source']['targetRevision'] == app['spec']['source']['targetRevision']
    for manifest in (root, app):
        assert manifest['spec']['source']['targetRevision']
        assert manifest['spec']['source']['repoURL'] == 'https://github.com/MTGMODS/labs-license-management-platform.git'


def test_gitops_uses_external_secret_and_orders_schema_before_api():
    manifests = render('-f', str(CHART / 'values-minikube.yaml'))
    assert not any(doc['kind'] == 'Secret' for doc in manifests)
    assert not any('helm.sh/hook' in doc['metadata'].get('annotations', {}) for doc in manifests)
    api = next(doc for doc in manifests if doc['kind'] == 'Deployment' and doc['metadata']['name'] == 'mtgmods-license')
    job = next(doc for doc in manifests if doc['kind'] == 'Job')
    assert api['metadata']['annotations']['argocd.argoproj.io/sync-wave'] == '2'
    assert api['spec']['progressDeadlineSeconds'] == 90
    assert api['spec']['template']['metadata']['annotations']['checksum/config']
    assert job['metadata']['annotations'] == {
        'argocd.argoproj.io/hook': 'Sync', 'argocd.argoproj.io/sync-wave': '1',
        'argocd.argoproj.io/hook-delete-policy': 'BeforeHookCreation,HookSucceeded',
    }
    for doc in manifests:
        if doc['kind'] in ('Deployment', 'Job') and doc['metadata']['name'] != 'mtgmods-license-redis':
            assert doc['spec']['template']['spec']['containers'][0]['envFrom'][-1]['secretRef']['name'] == 'mtgmods-license-secrets'


def test_bad_api_image_does_not_break_the_schema_hook():
    manifests = render('-f', str(CHART / 'values-minikube.yaml'), '--set', 'image.tag=nonexistent-lab5')
    job = next(doc for doc in manifests if doc['kind'] == 'Job')
    api = next(doc for doc in manifests if doc['kind'] == 'Deployment' and doc['metadata']['name'] == 'mtgmods-license')
    assert not job['spec']['template']['spec']['containers'][0]['image'].endswith(':nonexistent-lab5')
    assert api['spec']['template']['spec']['containers'][0]['image'].endswith(':nonexistent-lab5')


def test_helm_lab4_defaults_keep_original_hooks_and_secrets():
    manifests = render()
    assert any(doc['kind'] == 'Secret' for doc in manifests)
    assert any(doc['metadata'].get('annotations', {}).get('helm.sh/hook') == 'pre-upgrade' for doc in manifests)
    assert any(doc['metadata'].get('annotations', {}).get('helm.sh/hook') == 'test' for doc in manifests)
    assert not any('argocd.argoproj.io/hook' in doc['metadata'].get('annotations', {}) for doc in manifests)


def test_deployment_changes_preserve_required_matrix_checks_without_building_images():
    workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
    jobs = workflow['jobs']
    assert 'if' not in jobs['image-build']  # Skipping the entire matrix would omit required check names.
    assert 'changes' in jobs['image-build']['needs']
    for step in jobs['image-build']['steps']:
        if step.get('name') in ('Build image locally (no registry push)', 'Verify image exists'):
            assert step['if'] == "needs.changes.outputs.image == 'true'"
    assert "needs.changes.outputs.image == 'true'" in jobs['publish']['if']
