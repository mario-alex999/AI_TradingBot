#!/bin/bash
# AURUM AI - keep this Mac's copy of the project in step with GitHub.
#   Sync now:          bash AURUM_AI/scripts/sync_from_github.sh
#   Sync every 15 min: bash AURUM_AI/scripts/sync_from_github.sh --install
#   Follow a branch:   add  --branch claude/wf08-01-market-monitoring  (default: the repository's default branch)
#   Stop syncing:      bash AURUM_AI/scripts/sync_from_github.sh --uninstall
#
# Safe by design: it only fast-forwards. If you have edited tracked files, or your copy has commits GitHub does not,
# it changes nothing and writes why to AURUM_AI/logs/sync.txt. .env, .venv and logs are never touched (not in Git).
# Uses a per-user LaunchAgent; no sudo needed.
set -u

LABEL="com.aurum-ai.github-sync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AURUM_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$AURUM_DIR")"
LOG_DIR="$AURUM_DIR/logs"
LOG="$LOG_DIR/sync.txt"

BRANCH=""
EVERY_MINUTES=15
MODE="sync"
while [ $# -gt 0 ]; do
  case "$1" in
    --branch) BRANCH="${2:-}"; shift 2 ;;
    --every-minutes) EVERY_MINUTES="${2:-15}"; shift 2 ;;
    --install) MODE="install"; shift ;;
    --uninstall) MODE="uninstall"; shift ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

log() {
  mkdir -p "$LOG_DIR"
  local line
  line="$(date '+%Y-%m-%d %H:%M:%S')  $*"
  echo "$line" >> "$LOG"
  echo "$line"
}

if [ "$MODE" = "uninstall" ]; then
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null
  rm -f "$PLIST"
  echo "Removed background sync ($LABEL)."
  exit 0
fi

if [ "$MODE" = "install" ]; then
  mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
  BRANCH_ARGS=""
  if [ -n "$BRANCH" ]; then
    BRANCH_ARGS="<string>--branch</string><string>$BRANCH</string>"
  fi
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$SCRIPT_DIR/sync_from_github.sh</string>$BRANCH_ARGS</array>
  <key>WorkingDirectory</key><string>$REPO_ROOT</string>
  <key>StartInterval</key><integer>$((EVERY_MINUTES * 60))</integer>
  <key>RunAtLoad</key><true/>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>StandardOutPath</key><string>/dev/null</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/sync_errors.txt</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load "$PLIST"
  WHICH="${BRANCH:-the default branch}"
  echo "Background sync installed: every $EVERY_MINUTES minutes, following $WHICH."
  echo "Log: $LOG"
  exit 0
fi

# ---- one sync ----
command -v git >/dev/null 2>&1 || { log "git is not installed (run: xcode-select --install)."; exit 1; }
[ -d "$REPO_ROOT/.git" ] || { log "Not a Git checkout: $REPO_ROOT"; exit 1; }
cd "$REPO_ROOT" || exit 1

if ! git fetch --prune origin >/dev/null 2>&1; then
  log "Could not reach GitHub; will try again next time."
  exit 1
fi

if [ -z "$BRANCH" ]; then
  BRANCH="$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')"
  if [ -z "$BRANCH" ]; then
    git remote set-head origin --auto >/dev/null 2>&1
    BRANCH="$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')"
  fi
  [ -n "$BRANCH" ] || { log "Could not work out the default branch; pass --branch."; exit 1; }
fi

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  log "Skipped: tracked files have local edits. Commit or undo them, then sync again."
  exit 0
fi

CURRENT="$(git rev-parse --abbrev-ref HEAD)"
if [ "$CURRENT" != "$BRANCH" ]; then
  if ! git checkout "$BRANCH" >/dev/null 2>&1; then
    log "Could not switch from $CURRENT to $BRANCH."
    exit 1
  fi
fi

BEFORE="$(git rev-parse HEAD)"
if ! git merge --ff-only "origin/$BRANCH" >/dev/null 2>&1; then
  log "Skipped: $BRANCH has local commits GitHub does not have (no fast-forward)."
  exit 0
fi
AFTER="$(git rev-parse HEAD)"

if [ "$BEFORE" = "$AFTER" ]; then
  log "Up to date ($BRANCH @ ${AFTER:0:7})."
else
  log "Updated $BRANCH ${BEFORE:0:7} -> ${AFTER:0:7} ($(git rev-list --count "$BEFORE..$AFTER") new commit(s))."
  if [ -n "$(git diff --name-only "$BEFORE" "$AFTER" -- AURUM_AI/requirements.txt)" ]; then
    log "requirements.txt changed: run  .venv/bin/pip install -r requirements.txt  in AURUM_AI."
  fi
fi
