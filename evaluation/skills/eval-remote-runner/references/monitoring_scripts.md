# Monitoring & Results Scripts

Inline Python scripts for SSH execution. Replace `<question_id>` with actual ID.

## Periodic Progress Reporting (`/loop`)

Use `/loop` to set up recurring progress checks during a running eval:

```
/loop 10m ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST "cd /root/matmaster-evo && wc -l results/devshell_eval_*/raw_runs.jsonl 2>/dev/null; echo '---'; tail -5 /tmp/eval_run.log 2>/dev/null; echo '---'; ps aux | grep 'run_devshell_eval\|run_devshell_agent_loop' | grep -v grep | wc -l"
```

This reports every 10 minutes:
1. Number of completed runs (`raw_runs.jsonl` line count)
2. Last 5 lines of the eval log (shows recent task status)
3. Number of active eval processes (0 = eval finished)

### macOS Desktop Notifications

Use `osascript` to push a popup after each progress check (no extra install needed):

```bash
osascript -e 'tell application "System Events" to display dialog "已完成 N 条，M 个进程活跃中" with title "Eval Progress" buttons {"OK"} default button "OK" giving up after 5'
```

`giving up after 5` makes the dialog auto-dismiss after 5 seconds so it doesn't block.

Note: `display notification` (notification center) may require terminal notification permissions. The `display dialog` approach above works without extra setup.

## Tuning `--jobs`

- The remote machine (30 vCPU, 112GB RAM) is heavily underutilized at `--jobs 8` — the bottleneck is API I/O, not CPU.
- To check if you're hitting rate limits: `grep -i '429\|rate\|retry' /tmp/eval_run.log`
- If no 429 errors appear, increase `--jobs` (try 16 → 20). Dial back if rate-limit errors show up.

## Check Progress

```bash
ssh -p $EVAL_SSH_PORT $EVAL_SSH_USER@$EVAL_SSH_HOST \
  "cd /root/matmaster-evo && python3 -c \"
import json
from pathlib import Path

run_dirs = sorted(Path('results').glob('devshell_eval_*'))
if not run_dirs:
    print('No eval runs found'); exit()
run_dir = run_dirs[-1]
raw = run_dir / 'raw_runs.jsonl'
if not raw.exists():
    print(f'{run_dir.name}: no results yet'); exit()

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

## Per-Question Summary

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

## Detailed Failure Info (from pending_ingest)

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
