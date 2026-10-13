param([switch]$SetupOnly)
$ErrorActionPreference = 'Stop'
$dashboardRoot = $PSScriptRoot
Set-Location -LiteralPath $dashboardRoot
$dashboardPython = Join-Path $dashboardRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $dashboardPython)) {
    $dashboardInterpreter = $null
    $dashboardCandidates = @(
        @{Name='py.exe'; Arguments=@('-3.12')},
        @{Name='python.exe'; Arguments=@()}
    )
    foreach ($dashboardCandidate in $dashboardCandidates) {
        $dashboardCommand = Get-Command $dashboardCandidate.Name -ErrorAction SilentlyContinue
        if (-not $dashboardCommand) { continue }
        try {
            $dashboardPrefix = $dashboardCandidate.Arguments
            & $dashboardCommand.Source @dashboardPrefix -c 'import sys,struct; sys.exit(0 if sys.version_info[:2] == (3,12) and struct.calcsize(chr(80)) == 8 else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { $dashboardInterpreter = $dashboardCommand.Source; break }
        } catch { }
    }
    if (-not $dashboardInterpreter) { throw 'Install Python 3.12 (64-bit) from python.org, then run Start Dashboard.cmd again. See README.md.' }
    & $dashboardInterpreter @dashboardPrefix -m venv (Join-Path $dashboardRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the private Python environment.' }
}
$dashboardDependenciesMissing = $false
try {
    & $dashboardPython -c 'import PIL, psutil, hid' 2>$null
    $dashboardDependenciesMissing = $LASTEXITCODE -ne 0
} catch { $dashboardDependenciesMissing = $true }
if ($dashboardDependenciesMissing) {
    & $dashboardPython -m pip install --disable-pip-version-check -r (Join-Path $dashboardRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Could not install dashboard dependencies.' }
}
$dashboardBridge = Join-Path $dashboardRoot 'vendor/LibreHardwareMonitor/SensorBridge.exe'
if (-not (Test-Path -LiteralPath $dashboardBridge)) {
    & $dashboardPython (Join-Path $dashboardRoot 'tools/fetch_resources.py')
    if ($LASTEXITCODE -ne 0) { throw 'Could not download the hardware monitor.' }
    & "$env:WINDIR/Microsoft.NET/Framework64/v4.0.30319/csc.exe" /nologo /target:exe /platform:x64 "/out:$dashboardBridge" "/reference:$dashboardRoot/vendor/LibreHardwareMonitor/LibreHardwareMonitorLib.dll" /reference:System.Web.Extensions.dll (Join-Path $dashboardRoot 'SensorBridge.cs')
    if ($LASTEXITCODE -ne 0) { throw 'Could not compile the sensor reader.' }
}
if (-not (Test-Path -LiteralPath (Join-Path $dashboardRoot 'settings.json'))) {
    Copy-Item -LiteralPath (Join-Path $dashboardRoot 'settings.example.json') -Destination (Join-Path $dashboardRoot 'settings.json')
}
if ($SetupOnly) { Write-Host 'Dashboard setup is complete.'; exit 0 }
$dashboardPythonWindowless = Join-Path $dashboardRoot '.venv/Scripts/pythonw.exe'
$dashboardArguments = '"' + (Join-Path $dashboardRoot 'app.py') + '"'
Start-Process -FilePath $dashboardPythonWindowless -ArgumentList $dashboardArguments -WorkingDirectory $dashboardRoot -WindowStyle Hidden
for ($dashboardAttempt = 0; $dashboardAttempt -lt 20; $dashboardAttempt++) {
    try {
        $dashboardState = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/state' -TimeoutSec 1
        if ($dashboardState.state.pid) { break }
    } catch { }
    Start-Sleep -Milliseconds 300
}
if (-not $dashboardState.state.pid) { throw 'Dashboard did not start. See runtime/dashboard.log.' }
if ($dashboardState.state.project_root -and $dashboardState.state.project_root -ne $dashboardRoot) {
    throw 'A dashboard in another folder is already running. Stop that copy before starting this one.'
}
if ($dashboardState.config.lcd_enabled) {
    $dashboardConnectBody = @{ close_icue = [bool]$dashboardState.config.close_icue_on_start } | ConvertTo-Json
    Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/connect' -Method Post -ContentType 'application/json' -Body $dashboardConnectBody -Headers @{ 'X-Dashboard-Token' = $dashboardState.token } -TimeoutSec 5 | Out-Null
}
if (Test-Path 'HKLM:/SYSTEM/CurrentControlSet/Services/PawnIO') {
    if ($dashboardState.state.pid -and -not $dashboardState.state.hardware.cpu_temp) {
        & (Join-Path $dashboardRoot 'tools/start_sensor.ps1') -DashboardPid $dashboardState.state.pid
    }
}
