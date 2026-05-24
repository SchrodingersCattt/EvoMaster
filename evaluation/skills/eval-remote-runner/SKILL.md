---
name: eval-remote-runner
description: Trigger, monitor, and analyze devshell eval runs on the remote evaluation machine via SSH. Use when asked to run eval, check eval progress, or pull eval results.
---

# Eval Remote Runner

Trigger devshell eval runs on the remote machine, monitor progress, pull results.

## SSH Setup

Credentials from `.env.test`: `EVAL_SSH_HOST`, `EVAL_SSH_PORT`, `EVAL_SSH_USER`.
Remote repo: `/root/matmaster-evo`

Templates used below (each Bash call is an independent shell — inline these values every time, do NOT rely on shell variables across calls):

- **SSH**: `ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST`
- **Proxy prefix** (`.bashrc` not loaded in non-interactive SSH): `export http_proxy='http://ga.xdptech.com:8118' && export https_proxy='http://ga.xdptech.com:8118'`

## Model Route Keys

| Key | Model |
|---|---|
| `global.anthropic.claude-opus-4-6-v1` | Claude Opus 4.6 (default) |
| `claude-sonnet-4-6` | Claude Sonnet 4.6 |
| `claude-haiku-4-5` | Claude Haiku 4.5 |
| `gemini-3-flash-preview` | Gemini 3 Flash |
| `matmaster/qwen3.6-plus` | Qwen 3.6 Plus |

## Workflow (run → monitor → score)

### Step 1: Run Eval

```bash
# 1a. Sync code
git push origin eval
ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST "cd /root/matmaster-evo && git pull origin eval"

# 1b. Verify idle
ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST "ps aux | grep 'run_devshell_eval\|run_devshell_agent_loop' | grep -v grep"

# 1c. Launch (background, flock for mutual exclusion)
ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST "cd /root/matmaster-evo && \
  export http_proxy='http://ga.xdptech.com:8118' && export https_proxy='http://ga.xdptech.com:8118' && \
  nohup flock -n /tmp/eval.lock \
    uv run python evaluation/scripts/devshell/run_devshell_eval.py \
      --slices '<slice>' \
      --model 'global.anthropic.claude-opus-4-6-v1' \
      --jobs 8 --k 3 \
      --eval-ingest-pending-only \
    > /tmp/eval_run.log 2>&1 &"
```

Key flags:
- `--eval-ingest-pending-only` — **MANDATORY**. Writes pending files for step 3.
- `--k N` — repeats per question (NOT `--repeats`). Default 3.
- `--slices` — use `@tag_name` for tags (e.g. `'@struct_surface'`). Without `@` it matches capability names.
- `--limit N` — cap total tasks (for testing).
- `--no-clean-results` — keep prior results.

**Fallback if `uv run` fails** (e.g. GitHub unreachable for `molcrys-kit`):
replace `uv run python` with `.venv/bin/python` to skip dependency resolution.

### Step 2: Monitor progress

**Immediately after launching**, set up a `/loop` to track progress every 10 minutes:

```
/loop 10m ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST "ps aux | grep 'run_devshell_eval\|run_devshell_agent_loop' | grep -v grep | wc -l && echo '---' && wc -l /root/matmaster-evo/results/devshell_eval_*/raw_runs.jsonl && echo '---' && tail -3 /tmp/eval_run.log"
```

When process count drops to 0, the eval is done — immediately run Step 3.

For desktop notifications and tuning `--jobs` → `references/monitoring_scripts.md`

### Step 3: Score and Submit

Run **after eval finishes** (Step 2 reports process count = 0). Evaluates all scoring_checklist items and POSTs to tools-server:

```bash
ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST "cd /root/matmaster-evo && \
  export http_proxy='http://ga.xdptech.com:8118' && export https_proxy='http://ga.xdptech.com:8118' && \
  uv run python evaluation/scripts/devshell/score_devshell_tasks.py \
    --run-dir \$(ls -dt results/devshell_eval_* | head -1) --submit"
```

Same `.venv/bin/python` fallback applies here if `uv run` fails.

Without this step, the frontend shows NO checklist-level pass/fail data.

## Hard Rules

- **Never kill a running eval** — check `ps` first.
- **`flock -n /tmp/eval.lock`** ensures only one run at a time.
- Default behavior cleans `results/` on each new run.
- `exit_code == 0` ≠ criteria passed. Only `score_devshell_tasks.py` determines pass/fail.

## Troubleshooting

When things go wrong → `references/pitfalls.md`
