#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PI_HOST="${1:-${ESTRATTO_PI_HOST:-pi}}"
PI_USER="${ESTRATTO_PI_USER:-ea}"
PI_DIR="${ESTRATTO_PI_DIR:-~/estratto}"
REMOTE_NAME="${ESTRATTO_GIT_REMOTE:-origin}"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

prompt_if_empty() {
  local var_name="$1"
  local prompt_text="$2"
  local current_value="${!var_name:-}"
  if [[ -n "$current_value" ]]; then
    return
  fi
  read -r -p "$prompt_text" current_value
  printf -v "$var_name" "%s" "$current_value"
}

prompt_if_empty PI_USER "Pi SSH user [$PI_USER]: "
PI_USER="${PI_USER:-ea}"
prompt_if_empty PI_DIR "Pi Estratto directory [$PI_DIR]: "
PI_DIR="${PI_DIR:-~/estratto}"

quote_for_shell() {
  printf "%q" "$1"
}

git add -A

if ! git diff --cached --quiet; then
  read -r -p "Commit message: " COMMIT_MESSAGE
  if [[ -z "${COMMIT_MESSAGE// }" ]]; then
    echo "Commit message cannot be empty." >&2
    exit 1
  fi
  git commit -m "$COMMIT_MESSAGE"
else
  echo "No local changes to commit."
fi

if git rev-parse --abbrev-ref --symbolic-full-name "@{upstream}" >/dev/null 2>&1; then
  git push
else
  git push -u "$REMOTE_NAME" "$BRANCH"
fi

REMOTE_DIR_Q="$(quote_for_shell "$PI_DIR")"
REMOTE_NAME_Q="$(quote_for_shell "$REMOTE_NAME")"
BRANCH_Q="$(quote_for_shell "$BRANCH")"

ssh "${PI_USER}@${PI_HOST}" "bash -lc '
set -euo pipefail
cd ${REMOTE_DIR_Q}
git pull --ff-only ${REMOTE_NAME_Q} ${BRANCH_Q}
docker compose up --build -d
docker compose ps
'"
