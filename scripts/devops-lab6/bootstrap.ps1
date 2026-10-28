param(
    [string]$Profile = 'mtgmods-devops6',
    [string]$Revision = 'main'
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$stateDirectory = Join-Path $repoRoot '.local/devops6'
$pluginDirectory = Join-Path $env:LOCALAPPDATA 'Programs/ArgoRollouts'
$version = 'v1.10.1'
New-Item -ItemType Directory -Force -Path $stateDirectory, $pluginDirectory | Out-Null

# Reuse lab 5's Argo CD/Secret setup, with separate local credentials.
& (Join-Path $repoRoot 'scripts/devops-lab5/bootstrap.ps1') -Profile $Profile -StateDirectory $stateDirectory -SkipRoot

$plugin = Join-Path $pluginDirectory 'kubectl-argo-rollouts.exe'
if (-not (Test-Path -LiteralPath $plugin)) {
    Invoke-WebRequest "https://github.com/argoproj/argo-rollouts/releases/download/$version/kubectl-argo-rollouts-windows-amd64" -OutFile $plugin
}
$checksums = (Invoke-WebRequest "https://github.com/argoproj/argo-rollouts/releases/download/$version/argo-rollouts-checksums.txt").Content
if ($checksums -is [byte[]]) { $checksums = [Text.Encoding]::UTF8.GetString($checksums) }
$checksumLine = @($checksums -split "`n" | Where-Object { $_ -match '\s+kubectl-argo-rollouts-windows-amd64\s*$' })
if ($checksumLine.Count -ne 1) { throw 'The release does not contain a unique Windows plugin checksum.' }
$expected = ($checksumLine[0].Trim() -split '\s+')[0]
if ((Get-FileHash -LiteralPath $plugin -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
    throw 'Argo Rollouts plugin checksum mismatch. Remove the downloaded executable and retry.'
}
$env:PATH = "$pluginDirectory;$env:LOCALAPPDATA/Programs/ArgoCD;$env:PATH"
kubectl argo rollouts version

# The root comes from the checkout. Child applications and controller come from Git.
$root = Get-Content (Join-Path $repoRoot 'bootstrap/root.yaml') -Raw
$root -replace 'targetRevision: main', "targetRevision: $Revision" | kubectl --context $Profile apply -f -
argocd app get mtgmods-root --refresh | Out-Host
kubectl --context $Profile -n argocd wait application/argo-rollouts --for=create --timeout=180s
argocd app get argo-rollouts --refresh | Out-Host
argocd app wait argo-rollouts --sync --health --timeout 300
kubectl --context $Profile wait --for=condition=Established crd/rollouts.argoproj.io --timeout=120s
Write-Host 'Argo CD: http://127.0.0.1:18090 ; user: admin'
Write-Host "Plugin directory for CMD PATH: $pluginDirectory"
Write-Host 'Next: argocd app get mtgmods-license --refresh'
