param(
    [string]$Profile = 'mtgmods-devops6',
    [ValidateRange(1, 10000)]
    [int]$Count = 100,
    [ValidateRange(0, 10000)]
    [int]$DelayMs = 100
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$podName = 'lab6-traffic-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$script = @'
import collections
import json
import sys
import time
import urllib.request

totals = collections.Counter()
for _ in range(int(sys.argv[1])):
    with urllib.request.urlopen('http://mtgmods-license/health', timeout=5) as response:
        data = json.load(response)
    totals[data['version']] += 1
    print(json.dumps({'version': data['version'], 'instance': data['instance_id']}), flush=True)
    time.sleep(int(sys.argv[2]) / 1000)
print('SUMMARY ' + json.dumps(dict(sorted(totals.items()))), flush=True)
'@
$pod = @{
    apiVersion = 'v1'
    kind = 'Pod'
    metadata = @{ name = $podName; namespace = 'mtgmods-gitops'; labels = @{ 'app.kubernetes.io/component' = 'lab6-traffic' } }
    spec = @{
        restartPolicy = 'Never'
        activeDeadlineSeconds = [int]([Math]::Ceiling($Count * (5 + $DelayMs / 1000)) + 30)
        containers = @(@{
            name = 'requests'
            image = 'ghcr.io/mtgmods/labs-license-management-platform-license:sha-805ca0af230d9ece135982527f3c69ba6ca231b0'
            command = @('python', '-u', '-c', $script, "$Count", "$DelayMs")
            resources = @{ requests = @{ cpu = '10m'; memory = '32Mi' }; limits = @{ cpu = '100m'; memory = '128Mi' } }
        })
    }
}
try {
    $pod | ConvertTo-Json -Depth 12 | kubectl --context $Profile apply -f - | Out-Host
    kubectl --context $Profile -n mtgmods-gitops logs -f $podName --pod-running-timeout=120s
    $status = kubectl --context $Profile -n mtgmods-gitops get pod $podName -o json | ConvertFrom-Json
    for ($attempt = 0; $status.status.phase -notin @('Succeeded', 'Failed') -and $attempt -lt 10; $attempt++) {
        Start-Sleep -Milliseconds 200
        $status = kubectl --context $Profile -n mtgmods-gitops get pod $podName -o json | ConvertFrom-Json
    }
    if ($status.status.phase -ne 'Succeeded') { throw "Traffic check ended with phase $($status.status.phase)" }
} finally {
    kubectl --context $Profile -n mtgmods-gitops delete pod $podName --ignore-not-found --wait=false | Out-Host
}
