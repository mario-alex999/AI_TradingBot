#!/bin/bash
# AURUM AI - one-time Mac setup. Safe to run again.
#   bash ~/AI_TradingBot/AURUM_AI/scripts/setup_mac.sh
#
# 1. Puts this copy of the project on the repository's default branch, where every merged update lands.
# 2. Turns on the background sync (every 15 minutes, fast-forward only; see sync_from_github.sh).
# 3. Adds terminal shortcuts to ~/.zshrc (and ~/.bash_profile if you use bash):
#      aurum        go to the AURUM_AI folder (and activate .venv if it exists)
#      aurum-root   go to the project root
#      aurum-sync   pull the latest version now
#      aurum-log    show the last sync results
#      aurum-start  run the whole project (database, API, dashboard) and open it   (start_mac.sh)
#      aurum-stop   stop it;  aurum-status  show what is running
#    plus AURUM_HOME, the project root, for your own scripts.
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AURUM_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$AURUM_DIR")"
BEGIN="# >>> AURUM AI shortcuts >>>"
END="# <<< AURUM AI shortcuts <<<"

cd "$REPO_ROOT"
echo "Project: $REPO_ROOT"

# 1. Follow the default branch.
git fetch --prune origin
git remote set-head origin --auto >/dev/null
DEFAULT="$(git symbolic-ref --short refs/remotes/origin/HEAD | sed 's#^origin/##')"
FOLLOW="$DEFAULT"
SYNC_ARGS=""
if ! git cat-file -e "origin/$DEFAULT:AURUM_AI/scripts/sync_from_github.sh" 2>/dev/null; then
  # The default branch does not have these scripts yet (their pull request is not merged): stay where we are.
  FOLLOW="$(git rev-parse --abbrev-ref HEAD)"
  SYNC_ARGS="--branch $FOLLOW"
  echo "Note: $DEFAULT does not have the sync scripts yet, so this copy follows $FOLLOW for now."
  echo "      After the pull request is merged, run this setup again to follow $DEFAULT."
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "You have edited tracked files, so the branch was left as it is:"
  git status --short --untracked-files=no
else
  git checkout "$FOLLOW"
  git merge --ff-only "origin/$FOLLOW"
  echo "On $FOLLOW, up to date."
fi

# 2. Background sync.
# shellcheck disable=SC2086
bash "$SCRIPT_DIR/sync_from_github.sh" --install $SYNC_ARGS

# 3. Terminal shortcuts.
BLOCK="$BEGIN
export AURUM_HOME=\"$REPO_ROOT\"
aurum() { cd \"\$AURUM_HOME/AURUM_AI\" || return; [ -f .venv/bin/activate ] && . .venv/bin/activate; return 0; }
alias aurum-root='cd \"\$AURUM_HOME\"'
alias aurum-sync='bash \"\$AURUM_HOME/AURUM_AI/scripts/sync_from_github.sh\"'
alias aurum-log='tail -n 20 \"\$AURUM_HOME/AURUM_AI/logs/sync.txt\"'
alias aurum-start='bash \"\$AURUM_HOME/AURUM_AI/scripts/start_mac.sh\"'
alias aurum-stop='bash \"\$AURUM_HOME/AURUM_AI/scripts/start_mac.sh\" --stop'
alias aurum-status='bash \"\$AURUM_HOME/AURUM_AI/scripts/start_mac.sh\" --status'
$END"

add_block() {
  local rc="$1"
  touch "$rc"
  if grep -qF "$BEGIN" "$rc"; then
    # Replace the old block so a moved project still points to the right place.
    awk -v b="$BEGIN" -v e="$END" '$0==b{skip=1;next} $0==e{skip=0;next} !skip' "$rc" > "$rc.aurum.tmp"
    mv "$rc.aurum.tmp" "$rc"
  fi
  printf '\n%s\n' "$BLOCK" >> "$rc"
  echo "Shortcuts added to $rc"
}

add_block "$HOME/.zshrc"
case "${SHELL:-}" in */bash) add_block "$HOME/.bash_profile" ;; esac

echo
echo "Done. Open a new Terminal window (or run: source ~/.zshrc), then type:  aurum"
