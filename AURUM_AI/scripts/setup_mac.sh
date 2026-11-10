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
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "You have edited tracked files, so the branch was left as it is:"
  git status --short --untracked-files=no
else
  git checkout "$DEFAULT"
  git merge --ff-only "origin/$DEFAULT"
  echo "On $DEFAULT, up to date."
fi

# 2. Background sync.
bash "$SCRIPT_DIR/sync_from_github.sh" --install

# 3. Terminal shortcuts.
BLOCK="$BEGIN
export AURUM_HOME=\"$REPO_ROOT\"
aurum() { cd \"\$AURUM_HOME/AURUM_AI\" || return; [ -f .venv/bin/activate ] && . .venv/bin/activate; return 0; }
alias aurum-root='cd \"\$AURUM_HOME\"'
alias aurum-sync='bash \"\$AURUM_HOME/AURUM_AI/scripts/sync_from_github.sh\"'
alias aurum-log='tail -n 20 \"\$AURUM_HOME/AURUM_AI/logs/sync.txt\"'
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
