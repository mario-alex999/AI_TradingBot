# AURUM AI - register the collector supervisor to start at every Windows sign-in.
#   Install:    powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
#   Uninstall:  powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1 -Uninstall
# Runs as the current user (MT5 needs the interactive desktop session), hidden, no admin rights needed.
param([switch]$Uninstall)

$TaskName = "AURUM AI Collector"
$Root = Split-Path -Parent $PSScriptRoot
$PythonW = Join-Path $Root ".venv\Scripts\pythonw.exe"
$Script = Join-Path $Root "scripts\run_collector.py"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Removed scheduled task '$TaskName'. A running supervisor keeps going until sign-out or Stop-Process."
    return
}
if (-not (Test-Path $PythonW)) { throw "Virtual environment not found: $PythonW" }

$action = New-ScheduledTaskAction -Execute $PythonW -Argument "`"$Script`"" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$trigger.Delay = "PT1M"   # let the desktop, network and Docker settle
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Keeps the AURUM AI read-only MT5 market data collector running." -Force | Out-Null
Write-Output "Registered scheduled task '$TaskName' (starts 1 minute after you sign in)."
