$ErrorActionPreference = 'Stop'
$dashboardRoot = Split-Path -Parent $PSScriptRoot
$dashboardInstaller = Join-Path $dashboardRoot 'vendor/PawnIO_setup.exe'
New-Item -ItemType Directory -Path (Join-Path $dashboardRoot 'vendor') -Force | Out-Null
if (-not (Test-Path -LiteralPath $dashboardInstaller)) {
    Invoke-WebRequest -Uri 'https://github.com/namazso/PawnIO.Setup/releases/latest/download/PawnIO_setup.exe' -OutFile $dashboardInstaller -UseBasicParsing
}
$dashboardSignature = Get-AuthenticodeSignature -LiteralPath $dashboardInstaller
if ($dashboardSignature.Status -ne 'Valid') { throw 'The sensor driver installer has an invalid Windows signature.' }
Write-Host 'Installing the signed PawnIO sensor driver. Windows will request administrator permission.'
$dashboardInstallProcess = Start-Process -FilePath $dashboardInstaller -ArgumentList '-install', '-silent' -Verb RunAs -WindowStyle Hidden -Wait -PassThru
if ($dashboardInstallProcess.ExitCode -ne 0) { throw "Sensor driver installer returned $($dashboardInstallProcess.ExitCode)." }
Write-Host 'Installed. Stop and start the dashboard to enable CPU readings.'
