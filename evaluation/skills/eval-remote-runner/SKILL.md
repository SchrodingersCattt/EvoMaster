---
name: eval-remote-runner
description: Trigger, monitor, and analyze devshell eval runs on the remote evaluation machine via SSH. Use when asked to run eval, check eval progress, or pull eval results.
---

# Eval Remote Runner

Trigger devshell eval runs on the remote machine, monitor progress, pull results.

## SSH Setup

Credentials from `.env.test`: `EVAL_SSH_HOST`, `EVAL_SSH_PORT`, `EVAL_SSH_USER`.
Remote repo: `/root/matmaster-evo`

All commands that use `uv run` MUST inline proxy exports (`.bashrc` not loaded in non-interactive SSH):

```bash
SSH_CMD="ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST"
PROXY="export http_proxy='http://ga.xdptech.com:8118' && export https_proxy='http://ga.xdptech.com:8118'"
```

## Model Route Keys

| Key | Model |
|---|---|
| `global.anthropic.claude-opus-4-6-v1` | Claude Opus 4.6 (default) |
| `claude-sonnet-4-6` | Claude Sonnet 4.6 |
| `claude-haiku-4-5` | Claude Haiku 4.5 |
| `gemini-3-flash-preview` | Gemini 3 Flash |
| `matmaster/qwen3.6-plus` | Qwen 3.6 Plus |

## Workflow (two-step: run → score)

### Step 1: Run Eval

```bash
# 1a. Sync code
git push origin eval
$SSH_CMD "cd /root/matmaster-evo && git pull origin eval"

# 1b. Verify idle
$SSH_CMD "ps aux | grep 'run_devshell_eval\|run_devshell_agent_loop' | grep -v grep"

# 1c. Launch (background, flock for mutual exclusion)
$SSH_CMD "cd /root/matmaster-evo && $PROXY && \
  nohup flock -n /tmp/eval.lock \
    uv run python evaluation/scripts/devshell/run_devshell_eval.py \
      --slices '<slice>' \
      --model 'global.anthropic.claude-opus-4-6-v1' \
      --jobs 8 --k 3 \
      --eval-ingest-pending-only \
    > /tmp/eval_run.log 2>&1 &"
```

Key flags:
- `--eval-ingest-pending-only` — **MANDATORY**. Writes pending files for step 2.
- `--k N` — repeats per question (NOT `--repeats`). Default 3.
- `--slices` — use `@tag_name` for tags (e.g. `'@struct_surface'`). Without `@` it matches capability names.
- `--limit N` — cap total tasks (for testing).
- `--no-clean-results` — keep prior results.

**Fallback if `uv run` fails** (e.g. GitHub unreachable for `molcrys-kit`):
replace `uv run python` with `.venv/bin/python` to skip dependency resolution.

### Step 2: Score and Submit

Run **after step 1 completes**. Evaluates all scoring_checklist items and POSTs to tools-server:

```bash
$SSH_CMD "cd /root/matmaster-evo && $PROXY && \
  uv run python evaluation/scripts/devshell/score_devshell_tasks.py \
    --run-dir \$(ls -dt results/devshell_eval_* | head -1) --submit"
```

Same `.venv/bin/python` fallback applies here if `uv run` fails.

Without this step, the frontend shows NO checklist-level pass/fail data.

## Monitoring

Check progress → `references/monitoring_scripts.md`

Quick check (no Python):

```bash
$SSH_CMD "wc -l /root/matmaster-evo/results/devshell_eval_*/raw_runs.jsonl"
```

### Periodic Progress Reporting

Use `/loop` to set up recurring progress checks during a running eval:

```
/loop 10m ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST "cd /root/matmaster-evo && wc -l results/devshell_eval_*/raw_runs.jsonl 2>/dev/null; echo '---'; tail -5 /tmp/eval_run.log 2>/dev/null; echo '---'; ps aux | grep 'run_devshell_eval\|run_devshell_agent_loop' | grep -v grep | wc -l"
```

This reports every 10 minutes:
1. Number of completed runs (`raw_runs.jsonl` line count)
2. Last 5 lines of the eval log (shows recent task status)
3. Number of active eval processes (0 = eval finished)

When process count drops to 0, the eval is done — proceed to Step 2 (score and submit).

### macOS Desktop Notifications

Use `osascript` to push a popup after each progress check (no extra install needed):

```bash
osascript -e 'tell application "System Events" to display dialog "已完成 N 条，M 个进程活跃中" with title "Eval Progress" buttons {"OK"} default button "OK" giving up after 5'
```

`giving up after 5` makes the dialog auto-dismiss after 5 seconds so it doesn't block.

Note: `display notification` (notification center) may require terminal notification permissions. The `display dialog` approach above works without extra setup.

### Auto-Score on Completion

When monitoring detects process count = 0, immediately run Step 2 (score and submit) without waiting for the next loop cycle. This ensures results are available on the frontend as soon as possible.

## Tuning `--jobs`

- The remote machine (30 vCPU, 112GB RAM) is heavily underutilized at `--jobs 8` — the bottleneck is API I/O, not CPU.
- To check if you're hitting rate limits: `grep -i '429\|rate\|retry' /tmp/eval_run.log`
- If no 429 errors appear, increase `--jobs` (try 16 → 20). Dial back if rate-limit errors show up.

## Hard Rules

- **Never kill a running eval** — check `ps` first.
- **`flock -n /tmp/eval.lock`** ensures only one run at a time.
- Default behavior cleans `results/` on each new run.
- `exit_code == 0` ≠ criteria passed. Only `score_devshell_tasks.py` determines pass/fail.

## Troubleshooting

When things go wrong → `references/pitfalls.md`
