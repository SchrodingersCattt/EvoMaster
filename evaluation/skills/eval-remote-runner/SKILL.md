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
| `global.anthropic.claude-opus-4-6-v1` | Claude Opus 4.6 |
| `claude-sonnet-4-6` | Claude Sonnet 4.6 |
| `gemini-3.1-pro-preview` | Gemini 3.1 Pro |
| `cds/GPT-5.4` | GPT-5.4 |
| `matmaster/DeepSeek-v4-Flash` | DeepSeek V4 Flash |
| `matmaster/qwen3.7-max` | Qwen 3.7 Max (default) |
| `matmaster/dsk-v4p` | DeepSeek V4 Pro |

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
| `--questions ID [ID ...]` | Run only these question IDs (alternative to `--slices`; do NOT combine — they AND). Use full IDs, e.g. `WO_dpa4_neo_optimize_001_20260607` |
| `--k N` | Repeats per question (NOT `--repeats`) |
| `--jobs N` | Parallel workers. Default 16, dial back if 429s — see `references/monitoring_scripts.md` |
| `--limit N` | Cap total tasks (for testing) |
| `--no-clean-results` | Keep prior results dir |

Bohr CLI 专项评测使用 `--slices '@bohr-cli' --model 'matmaster/DeepSeek-v4-Flash'`。

> **Per-call LLM usage is always reported** to tools-server (populates `llm_usage`, `billing_mode=eval`: record + price, no credit debit; per-call cost back-filled into ingest `extra.per_call_usage`). No flag needed; requires `MATMASTER_TOOLS_SERVER` reachable (defaults to `matmaster-tools-server.<env>.bohrium.com`).

**Fallback**: if `uv run` fails (GitHub unreachable), replace with `.venv/bin/python`.

### Step 2: Monitor with Notification

**Immediately after launch**, do two things:

**2a.** Confirm eval started (run once):

```bash
ssh -p $PORT $USER@$HOST "ps aux | grep 'run_devshell_eval\|run_devshell_agent_loop' | grep -v grep | wc -l && echo '---' && tail -5 /tmp/eval_run.log"
```

**2b.** Start recurring monitoring — run this command directly (replace `$PORT/$USER/$HOST` with actual values):

```
/loop 10m Run: ssh -p $PORT $USER@$HOST "ps aux | grep 'run_devshell_eval\|run_devshell_agent_loop' | grep -v grep | wc -l && echo '---' && wc -l /root/matmaster-evo/results/devshell_eval_*/raw_runs.jsonl && echo '---' && tail -3 /tmp/eval_run.log". Extract PROCS (first line) and TASKS (number before raw_runs.jsonl). Then run: osascript -e "tell application \"System Events\" to display dialog \"已完成 TASKS 条，PROCS 个进程活跃中\" with title \"Eval Progress\" buttons {\"OK\"} default button \"OK\" giving up after 5". If PROCS=0, notify "Eval 已完成！共 TASKS 条结果" and run scoring: ssh -p $PORT $USER@$HOST "cd /root/matmaster-evo && set -a && . ./.env && set +a && export http_proxy='http://ga.xdptech.com:8118' && export https_proxy='http://ga.xdptech.com:8118' && uv run python evaluation/scripts/devshell/score_devshell_tasks.py --run-dir \$(ls -dt results/devshell_eval_* | head -1) --submit"
```

Each tick: SSH check → macOS popup (auto-dismiss 5s) → if PROCS=0, auto-score and cancel loop.

**Note**: `/loop` only fires while REPL is idle. During active conversation it will not trigger — this is normal, not a lost job. Check `CronList` to confirm it still exists.

### Step 3: Score and Submit

Run only after process count = 0:

```bash
ssh -p $PORT $USER@$HOST "cd /root/matmaster-evo && \
  set -a && . ./.env && set +a && \
  export http_proxy='http://ga.xdptech.com:8118' && export https_proxy='http://ga.xdptech.com:8118' && \
  uv run python evaluation/scripts/devshell/score_devshell_tasks.py \
    --run-dir \$(ls -dt results/devshell_eval_* | head -1) --submit"
```

Without this step, the frontend shows NO checklist-level pass/fail data. Same `.venv/bin/python` fallback applies.

> **Must load `.env` before scoring** (`set -a && . ./.env && set +a`). The evaluator LLM in `evaluation/config.yaml` reads `LITELLM_PROXY_API_KEY` / `LITELLM_PROXY_API_BASE` straight from the environment, and neither the SKILL command nor `score_devshell_tasks.py` auto-loads `.env`. A non-interactive SSH shell does NOT export these. Skip this and every `llm_binary_judge` criterion silently falls back to fail (`no evaluator LLM configured`, only a single `warning:` line on stderr) → any question gated on an LLM-judge item scores 0. The eval run itself (Step 1) is unaffected because it shells out to `mm-devshell run`, which loads creds on its own.

**Note**: Scoring 200+ tasks takes 2-5 minutes. Use a long timeout (300s) or run in background.

## Troubleshooting

When things go wrong → `references/pitfalls.md`
