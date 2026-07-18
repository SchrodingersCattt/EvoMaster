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
| `global.anthropic.claude-opus-4-6-v1` | Claude Opus 4.6（fallback 路由） |
| `gemini-3.1-pro-preview` | Gemini 3.1 Pro |
| `matmaster/gpt-5.6-sol` | GPT-5.6 Sol |
| `matmaster/DeepSeek-v4-Flash` | DeepSeek V4 Flash（bohr-cli 专项用这个） |
| `matmaster/DeepSeek-v4-Pro` | DeepSeek V4 Pro |
| `matmaster/qwen3.7-max` | Qwen 3.7 Max |
| `matmaster/zhipu/glm-5.2` | GLM 5.2 |

⚠️ **`--model` 必须显式传**。代码默认路由 `bedrock-claude-opus`（`eval_model_routes.py`）在机器 profile 中已不存在，不传 `--model` 时所有任务瞬间失败（症状：`LLM profile 'bedrock-claude-opus' not found`，报错里的 available 列表即当前真实 profile 清单，与本表不一致时以报错为准；本表 2026-07-19 校准）。

## Hard Rules

- **Never kill a running eval** — always check `ps` before launching.
- **`--eval-ingest-pending-only`** — MANDATORY on every run. Without it, scoring has no pending files to evaluate.
- Bohr CLI 专项必须带 **`--bohrium-env prod`**（凭据注入语义见 Step 1 flags 表后的专项段落）。

## Workflow

### Step 0: Question Catalog Sync（仅当本次改动新增或修改了题目）

题目 ingest 前会校验 `question_id` 是否在 tools-server 的 active catalog 里。新题没同步就跑评测，每个任务都会报 `[ingest] ... failed: question_id not in active eval catalog` 白跑一轮。

推送 `sync-question-catalog` 分支会**自动触发**目录同步（服务端执行 `sync_question_catalog_to_tools_server.py`，语义为全量替换 active 集合）：

```bash
git push origin eval:sync-question-catalog
```

自动同步有延迟。启动评测前在 eval 机器手动执行一次做确认兜底（幂等）：

```bash
ssh -p $PORT $USER@$HOST "cd /root/matmaster-evo && uv run python evaluation/scripts/sync_question_catalog_to_tools_server.py 2>&1 | tail -3"
# 成功判据：输出中 loaded N question row(s) 的 N 与 success active_count=N 相等。
# --dry-run 只预览前 20 个 ID，不能当同步验证。
```

### Step 1: Sync and Launch

```bash
# 1a. Push code（远程分叉时先本地 merge，不要在 eval 机器上产生合并提交）
git push origin eval
ssh -p $PORT $USER@$HOST "cd /root/matmaster-evo && git pull --ff-only origin eval"

# 1b. Verify idle (empty output = safe to proceed; if NOT empty → STOP, tell user an eval is running, ask whether to wait)
ssh -p $PORT $USER@$HOST "ps aux | grep 'run_devshell_eval\|run_devshell_agent_loop' | grep -v grep"

# 1c. Launch
ssh -p $PORT $USER@$HOST "cd /root/matmaster-evo && \
  export http_proxy='http://ga.xdptech.com:8118' && export https_proxy='http://ga.xdptech.com:8118' && \
  export no_proxy='localhost,127.0.0.1,.dp.tech,.bohrium.com,.npmjs.org' && export NO_PROXY=\"\$no_proxy\" && \
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
| `--jobs N` | Parallel workers. Default 1 — 批量跑必须显式加（常用 6-16），429 多时调低 — see `references/monitoring_scripts.md` |
| `--limit N` | Cap total tasks (for testing) |
| `--no-clean-results` | Keep prior results dir。定向复测必加，否则上一批次的费用明细（pending_ingest）被清掉 |
| `--run-label <prefix>` | 结果目录前缀改为 `<prefix>_<ts>`（默认 `devshell_eval_<ts>`）。**用了它就不能再用 Step 2b/3 里的 `devshell_eval_*` glob 定位目录** |
| `--bohrium-env prod` | Inject only production `BOHRIUM_*` credentials from `.env.prod` into task subprocesses; keep eval routing on `SERVICE_ENV` |

Bohr CLI 专项评测使用 `--slices '@bohr-cli' --model 'matmaster/DeepSeek-v4-Flash' --bohrium-env prod`。该参数只向 `mm-devshell` 子进程注入 `.env.prod` 中的 Bohrium 身份凭据并使用生产 Base URL，不改变 `BOHRIUM_USE_SANDBOX` 所选择的接口类型；不要让题目或 agent 自行读取 `.env.prod`。

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
/loop 10m Run: ssh -p $PORT $USER@$HOST "ps aux | grep 'run_devshell_eval\|run_devshell_agent_loop' | grep -v grep | wc -l && echo '---' && wc -l \$(ls -dt /root/matmaster-evo/results/*/ | head -1)raw_runs.jsonl && echo '---' && tail -3 /tmp/eval_run.log". Extract PROCS (first line) and TASKS (number before raw_runs.jsonl). Then run: osascript -e "tell application \"System Events\" to display dialog \"已完成 TASKS 条，PROCS 个进程活跃中\" with title \"Eval Progress\" buttons {\"OK\"} default button \"OK\" giving up after 5". If PROCS=0, notify "Eval 已完成！共 TASKS 条结果" and run scoring: ssh -p $PORT $USER@$HOST "cd /root/matmaster-evo && set -a && . ./.env && set +a && export http_proxy='http://ga.xdptech.com:8118' && export https_proxy='http://ga.xdptech.com:8118' && export no_proxy='localhost,127.0.0.1,.dp.tech,.bohrium.com,.npmjs.org' && export NO_PROXY=\"\$no_proxy\" && uv run python evaluation/scripts/devshell/score_devshell_tasks.py --run-dir \$(ls -dt results/*/ | head -1) --submit"
```

Each tick: SSH check → macOS popup (auto-dismiss 5s) → if PROCS=0, auto-score and cancel loop.

> ⚠️ 上面 /loop 内嵌的打分命令是 Step 3 的**副本**（/loop prompt 独立执行、无本 skill 上下文，必须自包含）：修改 Step 3 命令时必须同步这里，反之亦然。

**Note**: `/loop` only fires while REPL is idle. During active conversation it will not trigger — this is normal, not a lost job. Check `CronList` to confirm it still exists.

### Step 3: Score and Submit

`exit_code == 0` ≠ criteria passed —— 只有本步的 `score_devshell_tasks.py` 决定 pass/fail。Run only after process count = 0:

```bash
ssh -p $PORT $USER@$HOST "cd /root/matmaster-evo && \
  set -a && . ./.env && set +a && \
  export http_proxy='http://ga.xdptech.com:8118' && export https_proxy='http://ga.xdptech.com:8118' && \
  export no_proxy='localhost,127.0.0.1,.dp.tech,.bohrium.com,.npmjs.org' && export NO_PROXY=\"\$no_proxy\" && \
  uv run python evaluation/scripts/devshell/score_devshell_tasks.py \
    --run-dir \$(ls -dt results/*/ | head -1) --submit"
```

`ls -dt results/*/` 取**最新**批次目录（兼容 `--run-label` 自定义前缀）；`--no-clean-results` 场景下 results/ 里有多个批次，确认选中的是本轮目录再打分。

Without this step, the frontend shows NO checklist-level pass/fail data. Same `.venv/bin/python` fallback applies.

> **Must load `.env` before scoring** (`set -a && . ./.env && set +a`). The evaluator LLM in `evaluation/config.yaml` reads `LITELLM_PROXY_API_KEY` / `LITELLM_PROXY_API_BASE` straight from the environment, and neither the SKILL command nor `score_devshell_tasks.py` auto-loads `.env`. A non-interactive SSH shell does NOT export these. Skip this and every `llm_binary_judge` criterion silently falls back to fail (`no evaluator LLM configured`, only a single `warning:` line on stderr) → any question gated on an LLM-judge item scores 0. The eval run itself (Step 1) is unaffected because it shells out to `mm-devshell run`, which loads creds on its own.

**Note**: Scoring 200+ tasks takes 2-5 minutes. Use a long timeout (300s) or run in background.

## Troubleshooting

When things go wrong → `references/pitfalls.md`
