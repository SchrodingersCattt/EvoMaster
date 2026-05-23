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

3. If idle, start a run (use `flock` to prevent double-runs):

```bash
ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST \
  "cd /root/matmaster-evo && nohup flock -n /tmp/eval.lock \
    uv run python evaluation/scripts/devshell/run_devshell_eval.py \
      --slices '<slice>' \
      --model '<model_route_key>' \
      --jobs 8 \
      --notify \
    > /tmp/eval_run.log 2>&1 &"
```

Common parameters:
- `--slices`: `@struct_surface`, `structure_construction`, `input_generation`, etc.
- `--model`: `global.anthropic.claude-opus-4-6-v1` (default), `claude-sonnet-4-6`, `claude-haiku-4-5`
- `--jobs`: parallelism (default 8)
- `--repeats`: how many times each question is run (default 3)
- `--notify`: send Feishu notification when done

### 2. Check Progress

```bash
ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST \
  "cd /root/matmaster-evo && python3 -c \"
import json, glob
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

## Important Notes

- **Never kill a running eval** — always check status first before triggering
- The remote machine cleans `results/` by default at each run start; use `--no-clean-results` to preserve prior runs
- `flock -n /tmp/eval.lock` ensures mutual exclusion — if a run is in progress, the new command exits immediately
- Eval results are also ingested to tools-server and visible on the evaluation dashboard
- After runs complete, use the `evaluation-iteration` skill to query results from the API and analyze failures
