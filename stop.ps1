$ErrorActionPreference = 'Stop'
try {
    $dashboardState = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/state' -TimeoutSec 2
} catch {
    Write-Host 'Dashboard is already stopped.'
    exit 0
}
if ($dashboardState.state.project_root -and $dashboardState.state.project_root -ne $PSScriptRoot) {
    throw 'The running dashboard belongs to another folder. Use its Stop Dashboard.cmd.'
}
$dashboardProcessId = [int]$dashboardState.state.pid
Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/shutdown' -Method Post -ContentType 'application/json' -Body '{}' -Headers @{ 'X-Dashboard-Token' = $dashboardState.token } -TimeoutSec 5 | Out-Null
for ($dashboardAttempt = 0; $dashboardAttempt -lt 40; $dashboardAttempt++) {
    if (-not (Get-Process -Id $dashboardProcessId -ErrorAction SilentlyContinue)) {
        Write-Host 'Dashboard stopped.'
        exit 0
    }
    Start-Sleep -Milliseconds 250
}
throw 'Dashboard did not finish stopping. See runtime/dashboard.log.'
