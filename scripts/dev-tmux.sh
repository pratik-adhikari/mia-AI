#!/usr/bin/env bash

set -euo pipefail

session="mia"
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
log_root="$repo_root/logs/development"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is required. Install tmux, then run: make monitor" >&2
  exit 1
fi

cd "$repo_root"

if ! tmux has-session -t "$session" 2>/dev/null; then
  run_id="$(date '+%Y-%m-%dT%H-%M-%S%z')"
  log_dir="$log_root/$run_id"
  mkdir -p "$log_dir"
  ln -sfn "$run_id" "$log_root/latest"

  tmux new-session -d -s "$session" -n logs -c "$repo_root"
  tmux set-environment -t "$session" MIA_LOG_DIR "$log_dir"
  tmux split-window -h -t "$session:logs" -c "$repo_root"
  tmux split-window -v -t "$session:logs" -c "$repo_root"
  tmux split-window -v -t "$session:logs" -c "$repo_root"
  tmux select-layout -t "$session:logs" tiled
  tmux set-option -t "$session" pane-border-status top
  tmux set-option -t "$session" pane-border-format ' #{pane_title} '

  titles=(FRONTEND BACKEND WORKER "SYSTEM / ALL LOGS")
  commands=(
    "docker compose logs --timestamps -f frontend"
    "docker compose logs --timestamps -f backend"
    "docker compose logs --timestamps -f worker"
    "docker compose logs --timestamps -f"
  )
  log_names=(frontend backend worker all)
  for index in 0 1 2 3; do
    log_file="$log_dir/${log_names[$index]}.log"
    tmux select-pane -t "$session:logs.$index" -T "${titles[$index]}"
    tmux respawn-pane -k -t "$session:logs.$index" -c "$repo_root" \
      "bash -lc '${commands[$index]} 2>&1 | tee -a \"$log_file\"; exec bash'"
  done
else
  log_dir="$(tmux show-environment -t "$session" MIA_LOG_DIR 2>/dev/null | cut -d= -f2- || true)"
fi

if [[ -n "${log_dir:-}" ]]; then
  printf 'Saving timestamped logs in: %s\n' "$log_dir"
fi

tmux select-window -t "$session:logs"
if [[ -n "${TMUX:-}" ]]; then
  tmux switch-client -t "$session"
else
  tmux attach-session -t "$session"
fi
