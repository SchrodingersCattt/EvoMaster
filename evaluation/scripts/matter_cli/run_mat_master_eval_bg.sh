#!/usr/bin/env bash
set -euo pipefail

# Run MatMaster evaluation in background, with log file you can tail.
#
# Usage:
#   evaluation/scripts/matter_cli/run_mat_master_eval_bg.sh start [--name NAME] [--cmd "..."]
#   evaluation/scripts/matter_cli/run_mat_master_eval_bg.sh status [--name NAME]
#   evaluation/scripts/matter_cli/run_mat_master_eval_bg.sh log [--name NAME] [-f]
#   evaluation/scripts/matter_cli/run_mat_master_eval_bg.sh stop [--name NAME]
#
# Defaults:
# - name: mat_master_eval
# - cmd:  python -m evaluation

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
RUNS_DIR="$ROOT_DIR/runs/mat_master_eval"

NAME="mat_master_eval"
CMD_DEFAULT="python -m evaluation"
CMD="$CMD_DEFAULT"

# Prefer uv-managed environment if uv is available.
# Repo convention: `uv sync` then `uv run ...`
if command -v uv >/dev/null 2>&1; then
  CMD_DEFAULT="uv run python -m evaluation"
  CMD="$CMD_DEFAULT"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  # Fallback to venv python if present.
  CMD_DEFAULT="$ROOT_DIR/.venv/bin/python -m evaluation"
  CMD="$CMD_DEFAULT"
fi

FOLLOW=false

usage() {
  cat <<'EOF'
Usage:
  evaluation/scripts/matter_cli/run_mat_master_eval_bg.sh start [--name NAME] [--cmd "..."]
  evaluation/scripts/matter_cli/run_mat_master_eval_bg.sh status [--name NAME]
  evaluation/scripts/matter_cli/run_mat_master_eval_bg.sh log [--name NAME] [-f]
  evaluation/scripts/matter_cli/run_mat_master_eval_bg.sh stop [--name NAME]

Examples:
  evaluation/scripts/matter_cli/run_mat_master_eval_bg.sh start
  evaluation/scripts/matter_cli/run_mat_master_eval_bg.sh start --cmd "python -m evaluation --help"
  evaluation/scripts/matter_cli/run_mat_master_eval_bg.sh log -f
EOF
}

if [[ $# -lt 1 ]]; then
  usage
  exit 2
fi

ACTION="$1"
shift

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)
      NAME="$2"
      shift 2
      ;;
    --cmd)
      CMD="$2"
      shift 2
      ;;
    -f)
      FOLLOW=true
      shift 1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      usage
      exit 2
      ;;
  esac
done

mkdir -p "$RUNS_DIR"

state_dir="$RUNS_DIR/$NAME"
pid_file="$state_dir/pid"
log_file="$state_dir/log"

ensure_state_dir() {
  mkdir -p "$state_dir"
}

is_running() {
  if [[ ! -f "$pid_file" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -z "$pid" ]]; then
    return 1
  fi
  # kill -0 checks existence without sending a signal
  kill -0 "$pid" 2>/dev/null
}

case "$ACTION" in
  start)
    ensure_state_dir
    if is_running; then
      echo "Already running, stopping first: name=$NAME pid=$(cat "$pid_file")"
      kill "$(cat "$pid_file")" 2>/dev/null || true
      for _ in {1..15}; do
        if ! is_running; then
          break
        fi
        sleep 1
      done
      if is_running; then
        echo "Force killing: pid=$(cat "$pid_file")" >&2
        kill -9 "$(cat "$pid_file")" 2>/dev/null || true
      fi
      rm -f "$pid_file"
    fi

    ts="$(date +%Y%m%d_%H%M%S)"
    log_path="$state_dir/${ts}.log"
    ln -sfn "$log_path" "$log_file"

    echo "Starting: $CMD"
    echo "Workdir: $ROOT_DIR"
    echo "Log: $log_path"

    {
      echo "==== launcher info ===="
      echo "date: $(date -Is)"
      echo "pwd:  $ROOT_DIR"
      if command -v uv >/dev/null 2>&1; then
        echo "uv:   $(command -v uv)"
        uv --version || true
      fi
      python -V 2>/dev/null || true
      command -v python 2>/dev/null || true
      echo "cmd:  $CMD"
      echo "======================="
    } >>"$log_path" 2>&1

    # Start in background with nohup; redirect stdout+stderr to log.
    # shellcheck disable=SC2086
    # Ensure deps are synced when using uv.
    if [[ "$CMD" == uv\ run* ]]; then
      (cd "$ROOT_DIR" && uv sync >>"$log_path" 2>&1)
    fi

    (cd "$ROOT_DIR" && nohup bash -lc "eval \"$CMD\"" >>"$log_path" 2>&1 & echo $! >"$pid_file")

    echo "Started: name=$NAME pid=$(cat "$pid_file")"
    ;;

  status)
    if is_running; then
      echo "RUNNING name=$NAME pid=$(cat "$pid_file")"
      if [[ -L "$log_file" ]]; then
        echo "Log: $(readlink -f "$log_file")"
      elif [[ -f "$log_file" ]]; then
        echo "Log: $log_file"
      fi
      exit 0
    fi
    echo "STOPPED name=$NAME"
    if [[ -L "$log_file" ]]; then
      echo "Last log: $(readlink -f "$log_file")"
    elif [[ -f "$log_file" ]]; then
      echo "Last log: $log_file"
    fi
    exit 1
    ;;

  log)
    if [[ -L "$log_file" ]]; then
      target="$(readlink -f "$log_file")"
    else
      target="$log_file"
    fi
    if [[ ! -e "$target" ]]; then
      echo "No log found for name=$NAME (expected $target)" >&2
      exit 1
    fi
    if [[ "$FOLLOW" == true ]]; then
      exec tail -n 200 -f "$target"
    else
      exec tail -n 200 "$target"
    fi
    ;;

  stop)
    if ! is_running; then
      echo "Not running: name=$NAME"
      exit 0
    fi
    pid="$(cat "$pid_file")"
    echo "Stopping: name=$NAME pid=$pid"
    kill "$pid" 2>/dev/null || true
    # wait a bit then hard-kill if needed
    for _ in {1..30}; do
      if ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$pid_file"
        echo "Stopped."
        exit 0
      fi
      sleep 1
    done
    echo "Force killing: pid=$pid" >&2
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$pid_file"
    echo "Stopped."
    ;;

  *)
    echo "Unknown action: $ACTION" >&2
    usage
    exit 2
    ;;
esac
