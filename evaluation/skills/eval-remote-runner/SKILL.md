---
name: eval-remote-runner
description: Trigger, monitor, and analyze devshell eval runs on the remote evaluation machine via SSH. Use when asked to run eval, check eval progress, or pull eval results.
---

# Eval Remote Runner

Trigger devshell evaluation runs on the remote machine, monitor progress, and pull results for analysis.

## SSH Access

Credentials are read from environment variables (defined in `.env.test` at repo root):

```
EVAL_SSH_HOST=<ip>
EVAL_SSH_PORT=<port>
EVAL_SSH_USER=<user>
```

SSH authentication uses key-based auth (agent forwarding or key already deployed on the machine). If password-based auth is needed, the user must configure SSH keys or run `! ssh ...` interactively.

Build the SSH command as:

```bash
ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST "<command>"
```

The remote repo is at: `/root/matmaster-evo`

## Operations

### 1. Trigger an Eval Run

Before triggering:

1. **Push local changes** and **pull on remote**:

```bash
git push origin eval
ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST \
  "cd /root/matmaster-evo && git pull origin eval"
```

2. **Verify no run is in progress**:

```bash
ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST \
  "ps aux | grep 'run_devshell_eval\|run_devshell_agent_loop' | grep -v grep"
```

3. If idle, start a run (use `flock` to prevent double-runs).
   **IMPORTANT**: Always use `--eval-ingest-pending-only` so that scoring can
   be done separately and results properly submitted to the frontend:

```bash
ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST \
  "cd /root/matmaster-evo && \
   export http_proxy='http://ga.xdptech.com:8118' && \
   export https_proxy='http://ga.xdptech.com:8118' && \
   nohup flock -n /tmp/eval.lock \
    uv run python evaluation/scripts/devshell/run_devshell_eval.py \
      --slices '<slice>' \
      --model '<model_route_key>' \
      --jobs 8 \
      --k 3 \
      --eval-ingest-pending-only \
    > /tmp/eval_run.log 2>&1 &"
```

Common parameters:
- `--slices`: `@struct_surface`, `structure_construction`, `input_generation`, etc.
- `--model`: `global.anthropic.claude-opus-4-6-v1` (default), `claude-sonnet-4-6`, `claude-haiku-4-5`
- `--jobs`: parallelism (default 8)
- `--k N`: repeat each question N times (default 3). NOT `--repeats`.
- `--limit N`: only run N tasks total (useful for testing)
- `--no-clean-results`: preserve prior run results

### 1b. Score and Submit (MANDATORY after run completes)

**IMPORTANT**: `run_devshell_eval.py` only runs the agent and writes
`pending_ingest/<task_id>.json` files (without scores). You MUST run scoring
separately for per-checklist-item results to appear on the frontend dashboard.

The scoring step reads `pending_ingest/` files, evaluates each checklist item,
writes the score back, and POSTs to tools-server:

```bash
ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST \
  "cd /root/matmaster-evo && \
   export http_proxy='http://ga.xdptech.com:8118' && \
   export https_proxy='http://ga.xdptech.com:8118' && \
   uv run python evaluation/scripts/devshell/score_devshell_tasks.py \
    --run-dir results/devshell_eval_<timestamp> --submit"
```

Find the latest run directory:

```bash
ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST \
  "ls -dt /root/matmaster-evo/results/devshell_eval_* | head -1"
```

### 2. Check Progress

```bash
ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST \
  "cd /root/matmaster-evo && python3 -c \"
import json
from pathlib import Path

run_dirs = sorted(Path('results').glob('devshell_eval_*'))
if not run_dirs:
    print('No eval runs found')
    exit()
run_dir = run_dirs[-1]
raw = run_dir / 'raw_runs.jsonl'
if not raw.exists():
    print(f'{run_dir.name}: no results yet')
    exit()

rows = [json.loads(l) for l in raw.open()]
manifest = json.loads((run_dir / 'manifest.json').read_text())
total = manifest.get('plan_count', '?')
done = len(rows)
passed = sum(1 for r in rows if r.get('devshell_exit_code') == 0)
print(f'Run: {run_dir.name}')
print(f'Progress: {done}/{total} tasks')
print(f'Exit codes: {passed} ok, {done - passed} failed')
\""
```

### 3. Pull Results Summary

After a run completes, get the per-question pass/fail:

```bash
ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST \
  "cd /root/matmaster-evo && python3 -c \"
import json
from pathlib import Path
from collections import defaultdict

run_dirs = sorted(Path('results').glob('devshell_eval_*'))
run_dir = run_dirs[-1]
raw = run_dir / 'raw_runs.jsonl'
rows = [json.loads(l) for l in raw.open()]

by_q = defaultdict(list)
for r in rows:
    by_q[r['question_id']].append(r)

print(f'Run: {run_dir.name}')
print(f'Questions: {len(by_q)}')
print()
for qid in sorted(by_q):
    repeats = by_q[qid]
    exits = [r.get('devshell_exit_code', -1) for r in repeats]
    all_ok = all(e == 0 for e in exits)
    print(f'  {qid}: {\"PASS\" if all_ok else \"FAIL\"} ({len(repeats)} repeats, exits={exits})')
\""
```

### 4. Pull Detailed Failure Info

For a specific question, get the score_reason from pending_ingest:

```bash
ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST \
  "cd /root/matmaster-evo && python3 -c \"
import json
from pathlib import Path

run_dirs = sorted(Path('results').glob('devshell_eval_*'))
run_dir = run_dirs[-1]
pending = run_dir / 'pending_ingest'

for f in sorted(pending.glob('*<question_id>*.json')):
    data = json.loads(f.read_text())
    item = data.get('item', {})
    print(f'Task: {data.get(\"task_id\")}')
    print(f'Score: {item.get(\"score\")}')
    print(f'Reason: {item.get(\"score_reason\", \"\")[:500]}')
    print()
\""
```

### 5. Sync Code Before Running

If local changes need to be on the remote machine:

```bash
# First push from local
git push origin eval

# Then pull on remote
ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST \
  "cd /root/matmaster-evo && git pull origin eval"
```

## Available Model Route Keys

| Key | Model |
|---|---|
| `global.anthropic.claude-opus-4-6-v1` | Claude Opus 4.6 (Global) |
| `claude-opus-4-6` | Claude Opus 4.6 (LiteLLM) |
| `claude-sonnet-4-6` | Claude Sonnet 4.6 |
| `claude-haiku-4-5` | Claude Haiku 4.5 |
| `gemini-3-flash-preview` | Gemini 3 Flash |
| `matmaster/qwen3.6-plus` | Qwen 3.6 Plus |

## Pitfalls & Lessons Learned

### Proxy is required for `uv run`

The remote machine cannot access GitHub directly. **Every SSH command that uses
`uv run` must explicitly export proxy env vars**:

```bash
export http_proxy='http://ga.xdptech.com:8118'
export https_proxy='http://ga.xdptech.com:8118'
```

`.bashrc` proxy settings do NOT take effect in non-interactive SSH sessions
(e.g. `ssh host "command"`). You must inline the exports in every command.

### `--eval-ingest-pending-only` is mandatory

Without this flag, `run_devshell_eval.py` POSTs raw results directly to
tools-server **without scores**. Then `score_devshell_tasks.py --submit`
cannot find `pending_ingest/` files to update and submit. Result: scoring
runs but nothing appears on the frontend.

**Correct flow**: run with `--eval-ingest-pending-only` → score with `--submit`.

### Repeat parameter is `--k`, not `--repeats`

The flag to control how many times each question is repeated is `--k N`,
not `--repeats N`. The latter does not exist and will cause an argument error.

### `score_devshell_tasks.py` requires `--run-dir`

The run directory is passed via `--run-dir <path>`, not as a positional argument.

### First `uv run` on a fresh machine is slow

`uv run` syncs all dependencies from `pyproject.toml` before executing. On a
fresh machine this includes compiling C extensions (e.g. lxml for Python 3.13
which lacks prebuilt wheels). This is a one-time cost but can take 5-10 minutes.
If it appears stuck, check `/proc/<pid>/fd/` for open `.so` files being compiled.

### `run_devshell_eval.py` does NOT evaluate scoring checklists

It only runs the agent, collects workspace artifacts, and writes raw results.
The actual per-criterion evaluation (text_file_contains_all, struct_file_*,
llm_binary_judge, etc.) happens in `score_devshell_tasks.py`. Without running
this second step, the frontend shows no checklist-level pass/fail data.

### Exit code 0 ≠ all criteria passed

`devshell_exit_code == 0` only means the agent session completed without
crashing. It does NOT mean the agent's output passes all scoring criteria.
Always run `score_devshell_tasks.py` for the real pass/fail determination.

## Important Notes

- **Never kill a running eval** — always check status first before triggering
- The remote machine cleans `results/` by default at each run start; use `--no-clean-results` to preserve prior runs
- `flock -n /tmp/eval.lock` ensures mutual exclusion — if a run is in progress, the new command exits immediately
- After runs complete, use the `evaluation-iteration` skill to query results from the API and analyze failures
