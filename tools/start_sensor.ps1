param([Parameter(Mandatory=$true)][int]$DashboardPid)
$ErrorActionPreference = 'Stop'
$dashboardRoot = Split-Path -Parent $PSScriptRoot
$dashboardBridge = Join-Path $dashboardRoot 'vendor/LibreHardwareMonitor/SensorBridge.exe'
$dashboardSampleFile = Join-Path $dashboardRoot ("runtime/sensors-$DashboardPid.json")
$dashboardSensorArguments = '--parent ' + $DashboardPid + ' --output "' + $dashboardSampleFile + '"'
Start-Process -FilePath $dashboardBridge -ArgumentList $dashboardSensorArguments -Verb RunAs -WindowStyle Hidden
