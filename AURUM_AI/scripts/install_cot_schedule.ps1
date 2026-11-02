# AURUM AI - schedule the weekly COT update (Module 02).
#   Install:    powershell -ExecutionPolicy Bypass -File scripts\install_cot_schedule.ps1
#   Uninstall:  powershell -ExecutionPolicy Bypass -File scripts\install_cot_schedule.ps1 -Uninstall
# Runs Saturday and Tuesday 08:00 local time (CFTC releases Friday 15:30 ET; holiday weeks on Monday).
# StartWhenAvailable: if the PC was off, the task runs at the next opportunity; the update catches up on
# every missed report. Needs only internet + the database (Docker), not MT5. No admin rights needed.
param([switch]$Uninstall)

$TaskName = "AURUM AI COT Update"
$Root = Split-Path -Parent $PSScriptRoot
$PythonW = Join-Path $Root ".venv\Scripts\pythonw.exe"
$Script = Join-Path $Root "scripts\run_cot_update.py"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Removed scheduled task '$TaskName'."
    return
}
if (-not (Test-Path $PythonW)) { throw "Virtual environment not found: $PythonW" }

$action = New-ScheduledTaskAction -Execute $PythonW -Argument "`"$Script`"" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday, Tuesday -At "08:00"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 30) -RunOnlyIfNetworkAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Weekly CFTC Commitments of Traders update for AURUM AI (read-only)." -Force | Out-Null
Write-Output "Registered '$TaskName' (Saturday + Tuesday 08:00, catches up if missed). Log: logs\cot_update.txt"
