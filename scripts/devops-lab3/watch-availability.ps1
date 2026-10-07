param(
    [int]$Requests = 30,
    [int]$DelayMilliseconds = 500,
    [string]$IngressAddress = "127.0.0.1"
)

1..$Requests | ForEach-Object {
    $headers = curl.exe --silent --show-error --output NUL --dump-header - `
        --resolve "mtgmods.local:80:$IngressAddress" `
        http://mtgmods.local/health
    $status = ($headers | Select-String '^HTTP/').Line | Select-Object -Last 1
    $instance = ($headers | Select-String '^x-instance-id:').Line.Trim()
    "{0:HH:mm:ss.fff}  {1}  {2}" -f (Get-Date), $status, $instance
    Start-Sleep -Milliseconds $DelayMilliseconds
}
