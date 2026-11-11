# AURUM AI - keep this PC's copy of the project in step with GitHub.
#   Sync now:          powershell -ExecutionPolicy Bypass -File scripts\sync_from_github.ps1
#   Sync every 15 min: powershell -ExecutionPolicy Bypass -File scripts\sync_from_github.ps1 -Install
#   Follow a branch:   add  -Branch claude/wf08-01-market-monitoring  (default: the repository's default branch)
#   Stop syncing:      powershell -ExecutionPolicy Bypass -File scripts\sync_from_github.ps1 -Uninstall
#
# Safe by design: it only fast-forwards. If you have edited tracked files, or your copy has commits GitHub does not,
# it changes nothing and writes why to logs\sync.txt. .env, .venv and logs are never touched (they are not in Git).
# No admin rights needed.
param(
    [string]$Branch = "",
    [int]$EveryMinutes = 15,
    [switch]$Install,
    [switch]$Uninstall
)

$TaskName = "AURUM AI GitHub Sync"
$AurumDir = Split-Path -Parent $PSScriptRoot          # ...\AI_TradingBot\AURUM_AI
$RepoRoot = Split-Path -Parent $AurumDir              # ...\AI_TradingBot
$LogDir = Join-Path $AurumDir "logs"
$Log = Join-Path $LogDir "sync.txt"

function Write-Log([string]$Message) {
    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $Log -Value $line
    Write-Output $line
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Removed scheduled task '$TaskName'."
    return
}

if ($Install) {
    $self = Join-Path $PSScriptRoot "sync_from_github.ps1"
    $argList = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$self`""
    if ($Branch) { $argList += " -Branch `"$Branch`"" }
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argList -WorkingDirectory $RepoRoot
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew -RunOnlyIfNetworkAvailable
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Description "Fast-forwards the AURUM AI project from GitHub." -Force | Out-Null
    $which = if ($Branch) { $Branch } else { "the repository's default branch" }
    Write-Output "Registered '$TaskName': every $EveryMinutes minutes, following $which. Log: logs\sync.txt"
    return
}

# ---- one sync ----
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Write-Log "git is not installed or not on PATH."; exit 1 }
if (-not (Test-Path (Join-Path $RepoRoot ".git"))) { Write-Log "Not a Git checkout: $RepoRoot"; exit 1 }
Set-Location $RepoRoot

git fetch --prune origin 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Log "Could not reach GitHub; will try again next time."; exit 1 }

if (-not $Branch) {
    $Branch = (git symbolic-ref --short refs/remotes/origin/HEAD 2>$null) -replace '^origin/', ''
    if (-not $Branch) {
        git remote set-head origin --auto 2>&1 | Out-Null
        $Branch = (git symbolic-ref --short refs/remotes/origin/HEAD 2>$null) -replace '^origin/', ''
    }
    if (-not $Branch) { Write-Log "Could not work out the default branch; pass -Branch."; exit 1 }
}

$dirty = git status --porcelain --untracked-files=no
if ($dirty) { Write-Log "Skipped: tracked files have local edits. Commit or undo them, then sync again."; exit 0 }

$current = git rev-parse --abbrev-ref HEAD
if ($current -ne $Branch) {
    git checkout $Branch 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Log "Could not switch from $current to $Branch."; exit 1 }
}

$before = git rev-parse HEAD
git merge --ff-only "origin/$Branch" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Log "Skipped: $Branch has local commits GitHub does not have (no fast-forward)."; exit 0 }
$after = git rev-parse HEAD

if ($before -eq $after) { Write-Log "Up to date ($Branch @ $($after.Substring(0,7)))." }
else {
    $count = git rev-list --count "$before..$after"
    Write-Log "Updated $Branch $($before.Substring(0,7)) -> $($after.Substring(0,7)) ($count new commit(s))."
    $req = git diff --name-only $before $after -- AURUM_AI/requirements.txt
    if ($req) { Write-Log "requirements.txt changed: run  .venv\Scripts\pip install -r requirements.txt  in AURUM_AI." }
}
