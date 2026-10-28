param(
    [Parameter(Mandatory)]
    [ValidateSet('v1', 'v2', 'v3')]
    [string]$Version
)

$ErrorActionPreference = 'Stop'
$tags = @{
    v1 = 'sha-805ca0af230d9ece135982527f3c69ba6ca231b0'
    v2 = 'sha-07f44c2e07c73ad805d978dedb06ca231f3f8850'
    v3 = 'sha-b200fa774adfe73da92ea630f25b6e399c3beb43'
}
$path = Join-Path $PSScriptRoot '../../helm/mtgmods-license/values-rollouts.yaml'
$source = Get-Content -LiteralPath $path -Raw
$source = $source -replace '(?m)^  tag: .*$', "  tag: $($tags[$Version])"
$source = $source -replace '(?m)^  appVersion: .*$', "  appVersion: devops-lab6-$Version"
[IO.File]::WriteAllText($path, $source.Replace("`r`n", "`n"), [Text.UTF8Encoding]::new($false))
Write-Host "Prepared $Version ($($tags[$Version])). Review git diff, commit and open a PR."
