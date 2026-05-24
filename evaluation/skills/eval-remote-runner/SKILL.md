---
name: eval-remote-runner
description: Trigger, monitor, and analyze devshell eval runs on the remote evaluation machine via SSH. Use when asked to run eval, check eval progress, or pull eval results.
---

# Eval Remote Runner

Trigger devshell eval runs on the remote machine, monitor progress, pull results.

## SSH Credentials

Read from `.env.test` at start of every invocation. Inline the actual values into every SSH command (each Bash call is a fresh shell — variables do NOT persist across calls).

```bash
grep -E 'EVAL_SSH_(HOST|PORT|USER)' .env.test
```

Remote repo path: `/root/matmaster-evo`

All commands below use placeholders `$HOST`, `$PORT`, `$USER` — replace with actual values from `.env.test` in each Bash call.

## Model Route Keys

| Key | Model |
|---|---|
| `global.anthropic.claude-opus-4-6-v1` | Claude Opus 4.6 (default) |
| `claude-sonnet-4-6` | Claude Sonnet 4.6 |
| `claude-haiku-4-5` | Claude Haiku 4.5 |
| `gemini-3-flash-preview` | Gemini 3 Flash |
| `matmaster/qwen3.6-plus` | Qwen 3.6 Plus |

## Hard Rules

- **Never kill a running eval** — always check `ps` before launching.
- **`flock -n /tmp/eval.lock`** — ensures mutual exclusion (already in launch command).
- **`--eval-ingest-pending-only`** — MANDATORY on every run. Without it, scoring has no pending files to evaluate.
- `exit_code == 0` ≠ criteria passed. Only `score_devshell_tasks.py` determines pass/fail.

## Workflow

### Step 1: Sync and Launch

```bash
# 1a. Push code
git push origin eval
ssh -p $PORT $USER@$HOST "cd /root/matmaster-evo && git pull origin eval"

# 1b. Verify idle (empty output = safe to proceed; if NOT empty → STOP, tell user an eval is running, ask whether to wait)
ssh -p $PORT $USER@$HOST "ps aux | grep 'run_devshell_eval\|run_devshell_agent_loop' | grep -v grep"

# 1c. Launch
ssh -p $PORT $USER@$HOST "cd /root/matmaster-evo && \
  export http_proxy='http://ga.xdptech.com:8118' && export https_proxy='http://ga.xdptech.com:8118' && \
  nohup flock -n /tmp/eval.lock \
    uv run python evaluation/scripts/devshell/run_devshell_eval.py \
      --slices '<slice>' \
      --model 'global.anthropic.claude-opus-4-6-v1' \
      --jobs 16 --k 3 \
      --eval-ingest-pending-only \
    > /tmp/eval_run.log 2>&1 &"
```

| Flag | Purpose |
|------|---------|
| `--slices '@tag'` | Filter by tag (`@eng_abacus`). Without `@` matches capability names |
| `--k N` | Repeats per question (NOT `--repeats`) |
| `--jobs N` | Parallel workers. Default 16, dial back if 429s — see `references/monitoring_scripts.md` |
| `--limit N` | Cap total tasks (for testing) |
| `--no-clean-results` | Keep prior results dir |

**Fallback**: if `uv run` fails (GitHub unreachable), replace with `.venv/bin/python`.

### Step 2: Monitor with Notification

**Immediately after launch**, do two things:

**2a.** Confirm eval started (run once):

```bash
ssh -p $PORT $USER@$HOST "ps aux | grep 'run_devshell_eval\|run_devshell_agent_loop' | grep -v grep | wc -l && echo '---' && tail -5 /tmp/eval_run.log"
```

**2b.** Start recurring monitoring — run this command directly (replace `$PORT/$USER/$HOST` with actual values):

```
/loop 10m Run: ssh -p $PORT $USER@$HOST "ps aux | grep 'run_devshell_eval\|run_devshell_agent_loop' | grep -v grep | wc -l && echo '---' && wc -l /root/matmaster-evo/results/devshell_eval_*/raw_runs.jsonl && echo '---' && tail -3 /tmp/eval_run.log". Extract PROCS (first line) and TASKS (number before raw_runs.jsonl). Then run: osascript -e "tell application \"System Events\" to display dialog \"已完成 TASKS 条，PROCS 个进程活跃中\" with title \"Eval Progress\" buttons {\"OK\"} default button \"OK\" giving up after 5". If PROCS=0, notify "Eval 已完成！共 TASKS 条结果" and run scoring: ssh -p $PORT $USER@$HOST "cd /root/matmaster-evo && export http_proxy='http://ga.xdptech.com:8118' && export https_proxy='http://ga.xdptech.com:8118' && uv run python evaluation/scripts/devshell/score_devshell_tasks.py --run-dir \$(ls -dt results/devshell_eval_* | head -1) --submit"
```

Each tick: SSH check → macOS popup (auto-dismiss 5s) → if PROCS=0, auto-score and cancel loop.

### Step 3: Score and Submit

Run only after process count = 0:

```bash
ssh -p $PORT $USER@$HOST "cd /root/matmaster-evo && \
  export http_proxy='http://ga.xdptech.com:8118' && export https_proxy='http://ga.xdptech.com:8118' && \
  uv run python evaluation/scripts/devshell/score_devshell_tasks.py \
    --run-dir \$(ls -dt results/devshell_eval_* | head -1) --submit"
```

Without this step, the frontend shows NO checklist-level pass/fail data. Same `.venv/bin/python` fallback applies.

## Troubleshooting

When things go wrong → `references/pitfalls.md`
