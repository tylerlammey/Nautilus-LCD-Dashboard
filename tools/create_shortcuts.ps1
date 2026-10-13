param([switch]$Desktop)
$ErrorActionPreference = 'Stop'
$dashboardRoot = Split-Path -Parent $PSScriptRoot
$dashboardShell = New-Object -ComObject WScript.Shell
$dashboardLocations = @($dashboardRoot)
if ($Desktop) { $dashboardLocations += [Environment]::GetFolderPath('Desktop') }
foreach ($dashboardLocation in $dashboardLocations) {
    foreach ($dashboardAction in @('Start', 'Stop')) {
        $dashboardLinkPath = Join-Path $dashboardLocation ("$dashboardAction AIO Dashboard.lnk")
        $dashboardScript = if ($dashboardAction -eq 'Start') { 'launch.ps1' } else { 'stop.ps1' }
        $dashboardIcon = $dashboardAction.ToLower() + '.ico'
        $dashboardLink = $dashboardShell.CreateShortcut($dashboardLinkPath)
        $dashboardLink.TargetPath = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
        $dashboardLink.Arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + (Join-Path $dashboardRoot $dashboardScript) + '"'
        $dashboardLink.WorkingDirectory = $dashboardRoot
        $dashboardLink.IconLocation = (Join-Path $dashboardRoot "assets/$dashboardIcon") + ',0'
        $dashboardLink.Description = "$dashboardAction the Nautilus LCD dashboard"
        $dashboardLink.WindowStyle = 7
        $dashboardLink.Save()
        Write-Host "Created $dashboardLinkPath"
    }
}
