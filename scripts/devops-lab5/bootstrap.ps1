param(
    [string]$Profile = 'mtgmods-devops5',
    [switch]$SkipRoot
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$localDir = Join-Path $repoRoot '.local/devops5'
$cliDir = Join-Path $env:LOCALAPPDATA 'Programs/ArgoCD'
$argoVersion = 'v3.5.6'
New-Item -ItemType Directory -Force -Path $localDir, $cliDir | Out-Null
$env:PATH = "$cliDir;$env:PATH"

minikube start -p $Profile --driver=docker --cpus=4 --memory=6144 --container-runtime=containerd --kubernetes-version=v1.35.1

$cli = Join-Path $cliDir 'argocd.exe'
if (-not (Test-Path -LiteralPath $cli)) {
    Invoke-WebRequest "https://github.com/argoproj/argo-cd/releases/download/$argoVersion/argocd-windows-amd64.exe" -OutFile $cli
}
$checksums = (Invoke-WebRequest "https://github.com/argoproj/argo-cd/releases/download/$argoVersion/cli_checksums.txt").Content
if ($checksums -is [byte[]]) { $checksums = [Text.Encoding]::UTF8.GetString($checksums) }
$expected = ($checksums -split "`n" | Where-Object { $_ -match '\s+argocd-windows-amd64.exe\s*$' }).Trim().Split(' ')[0]
if ((Get-FileHash -LiteralPath $cli -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
    throw 'Argo CD CLI checksum mismatch. Remove the downloaded executable and retry.'
}
& $cli version --client --short

kubectl --context $Profile create namespace argocd --dry-run=client -o yaml | kubectl --context $Profile apply -f -
kubectl --context $Profile apply -n argocd --server-side --force-conflicts -f "https://raw.githubusercontent.com/argoproj/argo-cd/$argoVersion/manifests/install.yaml"

# Local-only HTTP UI. The port-forward binds to loopback, not the LAN.
kubectl --context $Profile -n argocd patch configmap argocd-cmd-params-cm --type merge -p '{"data":{"server.insecure":"true"}}'
kubectl --context $Profile -n argocd rollout restart deployment/argocd-server
kubectl --context $Profile -n argocd rollout status deployment/argocd-server --timeout=180s
kubectl --context $Profile -n argocd rollout status deployment/argocd-repo-server --timeout=180s
kubectl --context $Profile -n argocd rollout status statefulset/argocd-application-controller --timeout=180s

kubectl --context $Profile create namespace mtgmods-gitops --dry-run=client -o yaml | kubectl --context $Profile apply -f -
$existingSecrets = kubectl --context $Profile -n mtgmods-gitops get secrets -o json | ConvertFrom-Json
if ('mtgmods-license-secrets' -notin $existingSecrets.items.metadata.name) {
    $dbPassword = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    $jwtSecret = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    $botSecret = [guid]::NewGuid().ToString('N')
    $internalSecret = [guid]::NewGuid().ToString('N')
    $databaseUrl = "postgresql+asyncpg://mtg_license:${dbPassword}@mtgmods-license-postgres:5432/licenses"
    kubectl --context $Profile -n mtgmods-gitops create secret generic mtgmods-license-secrets `
        --from-literal=POSTGRES_USER=mtg_license --from-literal=POSTGRES_DB=licenses `
        "--from-literal=POSTGRES_PASSWORD=$dbPassword" "--from-literal=DATABASE_URL=$databaseUrl" `
        "--from-literal=DATABASE_POSTGRES_URL=$databaseUrl" "--from-literal=JWT_SECRET=$jwtSecret" `
        "--from-literal=BOT_SECRET_TOKEN=$botSecret" "--from-literal=INTERNAL_SECRET_TOKEN=$internalSecret" `
        --from-literal=RABBITMQ_URL=amqp://guest:guest@127.0.0.1:1/
}

if (-not (Get-NetTCPConnection -LocalPort 18090 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath (Get-Command kubectl).Source -WindowStyle Hidden `
        -ArgumentList "--context $Profile -n argocd port-forward svc/argocd-server 18090:80 --address 127.0.0.1" `
        -RedirectStandardOutput (Join-Path $localDir 'argocd-port-forward.log') `
        -RedirectStandardError (Join-Path $localDir 'argocd-port-forward-error.log') | Out-Null
}
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        Invoke-RestMethod 'http://127.0.0.1:18090/api/version' -TimeoutSec 2 | Out-Null
        break
    } catch {
        if ($attempt -eq 29) { throw }
        Start-Sleep -Seconds 1
    }
}

$credentialFile = Join-Path $localDir 'argocd-admin.clixml'
if (-not (Test-Path -LiteralPath $credentialFile)) {
    $initialSecret = kubectl --context $Profile -n argocd get secret argocd-initial-admin-secret -o json | ConvertFrom-Json
    $initialPassword = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($initialSecret.data.password))
    $newPassword = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    & $cli login 127.0.0.1:18090 --plaintext --username admin --password $initialPassword
    & $cli account update-password --current-password $initialPassword --new-password $newPassword
    $credential = [pscredential]::new('admin', (ConvertTo-SecureString $newPassword -AsPlainText -Force))
    $credential | Export-Clixml -LiteralPath $credentialFile
    kubectl --context $Profile -n argocd delete secret argocd-initial-admin-secret
} else {
    $credential = Import-Clixml -LiteralPath $credentialFile
    & $cli login 127.0.0.1:18090 --plaintext --username admin --password $credential.GetNetworkCredential().Password
}

if (-not $SkipRoot) {
    kubectl --context $Profile apply -f (Join-Path $repoRoot 'bootstrap/root.yaml')
}
Write-Host 'Argo CD: http://127.0.0.1:18090 ; user: admin'
Write-Host "Encrypted local credentials: $credentialFile"
