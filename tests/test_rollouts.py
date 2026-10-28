"""Render the actual chart: progressive delivery must preserve the working API pod."""
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / 'helm/mtgmods-license'


def render(progressive=False, *args):
    if not shutil.which('helm'):
        pytest.skip('Helm is required for chart rendering')
    command = ['helm', 'template', 'mtgmods-license', str(CHART), '-n', 'mtgmods-gitops', '-f', str(CHART / 'values-minikube.yaml')]
    if progressive:
        command += ['-f', str(CHART / 'values-rollouts.yaml')]
    result = subprocess.run([*command, *args], capture_output=True, text=True, check=True)
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def api_workload(documents):
    return next(doc for doc in documents if doc['kind'] in ('Deployment', 'Rollout') and doc['metadata']['name'] == 'mtgmods-license')


def test_rollout_replaces_only_the_api_deployment():
    documents = render(True)
    workload = api_workload(documents)
    assert workload['kind'] == 'Rollout'
    assert workload['apiVersion'] == 'argoproj.io/v1alpha1'
    assert workload['spec']['replicas'] == 5
    assert not any(doc['kind'] == 'Deployment' and doc['metadata']['name'] == 'mtgmods-license' for doc in documents)
    assert {doc['metadata']['name'] for doc in documents if doc['kind'] == 'Deployment'} == {'mtgmods-license-postgres', 'mtgmods-license-redis'}


def test_canary_has_manual_and_timed_gates_without_replacing_the_service_selector():
    documents = render(True)
    workload = api_workload(documents)
    canary = workload['spec']['strategy']['canary']
    assert [step['setWeight'] for step in canary['steps'] if 'setWeight' in step] == [20, 60, 80]
    assert {'pause': {}} in canary['steps']
    assert sum('duration' in step.get('pause', {}) for step in canary['steps']) >= 1
    assert canary['maxUnavailable'] == 0
    service = next(doc for doc in documents if doc['kind'] == 'Service' and doc['metadata']['name'] == 'mtgmods-license')
    assert service['spec']['selector'] == workload['spec']['selector']['matchLabels']
    assert 'app.kubernetes.io/version' not in service['spec']['selector']


def test_pod_template_preserves_env_probes_and_schema_wait_in_both_modes():
    deployment = api_workload(render())
    rollout = api_workload(render(True, '--set', 'config.appVersion=devops-lab5-v2', '--set', 'image.tag=sha-07f44c2e07c73ad805d978dedb06ca231f3f8850'))
    assert deployment['kind'] == 'Deployment'
    assert deployment['spec']['template'] == rollout['spec']['template']
    pod = rollout['spec']['template']['spec']
    assert pod['initContainers'][0]['name'] == 'wait-for-schema'
    container = pod['containers'][0]
    assert container['readinessProbe']['httpGet']['path'] == '/health'
    assert container['livenessProbe']['httpGet']['path'] == '/health/live'
    # An old ReplicaSet must not read a new version from the mutable ConfigMap after abort.
    assert {'name': 'APP_VERSION', 'value': 'devops-lab5-v2'} in container['env']


def test_rollout_rejects_too_few_replicas():
    with pytest.raises(subprocess.CalledProcessError, match='returned non-zero exit status'):
        render(True, '--set', 'replicaCount=3')


def test_controller_is_pinned_and_installed_by_the_root_application():
    controller = yaml.safe_load((ROOT / 'argocd-apps/argo-rollouts.yaml').read_text())
    assert controller['metadata']['annotations']['argocd.argoproj.io/sync-wave'] == '-1'
    assert controller['spec']['source']['repoURL'] == 'https://argoproj.github.io/argo-helm'
    assert controller['spec']['source']['chart'] == 'argo-rollouts'
    assert controller['spec']['source']['targetRevision'] == '2.43.8'
    assert controller['spec']['syncPolicy']['automated'] == {'prune': True, 'selfHeal': True}
    application = yaml.safe_load((ROOT / 'argocd-apps/license.yaml').read_text())
    assert application['spec']['source']['helm']['valueFiles'] == ['values-minikube.yaml', 'values-rollouts.yaml']
    assert application['spec']['syncPolicy']['retry']['limit'] == 5
