# Pitfalls & Lessons Learned

## Proxy is required for `uv run`

The remote machine cannot access GitHub directly. **Every SSH command that uses
`uv run` must explicitly export proxy env vars**:

```bash
export http_proxy='http://ga.xdptech.com:8118'
export https_proxy='http://ga.xdptech.com:8118'
```

`.bashrc` proxy settings do NOT take effect in non-interactive SSH sessions
(e.g. `ssh host "command"`). You must inline the exports in every command.

## `--eval-ingest-pending-only` is mandatory

Without this flag, `run_devshell_eval.py` POSTs raw results directly to
tools-server **without scores**. Then `score_devshell_tasks.py --submit`
cannot find `pending_ingest/` files to update and submit. Result: scoring
runs but nothing appears on the frontend.

**Correct flow**: run with `--eval-ingest-pending-only` → score with `--submit`.

## Repeat parameter is `--k`, not `--repeats`

The flag to control how many times each question is repeated is `--k N`,
not `--repeats N`. The latter does not exist and will cause an argument error.

## `score_devshell_tasks.py` requires `--run-dir`

The run directory is passed via `--run-dir <path>`, not as a positional argument.

## First `uv run` on a fresh machine is slow

`uv run` syncs all dependencies from `pyproject.toml` before executing. On a
fresh machine this includes compiling C extensions (e.g. lxml for Python 3.13
which lacks prebuilt wheels). This is a one-time cost but can take 5-10 minutes.
If it appears stuck, check `/proc/<pid>/fd/` for open `.so` files being compiled.

## `run_devshell_eval.py` does NOT evaluate scoring checklists

It only runs the agent, collects workspace artifacts, and writes raw results.
The actual per-criterion evaluation (text_file_contains_all, struct_file_*,
llm_binary_judge, etc.) happens in `score_devshell_tasks.py`. Without running
this second step, the frontend shows no checklist-level pass/fail data.

## Exit code 0 ≠ all criteria passed

`devshell_exit_code == 0` only means the agent session completed without
crashing. It does NOT mean the agent's output passes all scoring criteria.
Always run `score_devshell_tasks.py` for the real pass/fail determination.

## `uv run` fails due to proxy/network misconfiguration

The remote machine's network topology:
- **Direct → PyPI**: ✅ works
- **Direct → GitHub**: unstable (sometimes OK, sometimes timeout)
- **Proxy (`ga.xdptech.com:8118`) → PyPI**: ❌ connection reset
- **Proxy → GitHub**: ❌ timeout
- **Proxy → LLM API (ai-gateway-global.dp.tech)**: ✅ required

The proxy is needed for LLM API calls during eval, but `uv run` inherits
the proxy env vars and routes PyPI/GitHub traffic through it, causing
dependency resolution to fail (`hatchling`, `molcrys-kit`, etc.).

**Workaround for launching eval**: bypass `uv run` and use `.venv/bin/python`
directly (proxy still set for LLM API, but Python doesn't use it for imports):

```bash
.venv/bin/python evaluation/scripts/devshell/run_devshell_eval.py ...
```

Only safe when the venv is already synced (check with
`.venv/bin/python -c "import matmaster"`).

**Impact on agent during eval**: agent's tool calls that use `uv run`
(e.g. `validate_structure.py`) will also fail. This causes silent
degradation — agent loses validation signals and may rationalize skipping
checks. No fix at skill level; requires infra fix (e.g. `no_proxy` for
PyPI/GitHub, or pre-synced venv that doesn't trigger resolution).

## Slice syntax: tags need `@` prefix

`--slices 'struct_surface'` treats `struct_surface` as a **capability** name
(matching `question.capability`). To filter by **tag**, prefix with `@`:

```bash
--slices '@struct_surface'
```

Without `@`, the filter matches zero questions and raises:
`ValueError: No questions remaining after applying --slices / --questions filters`

## Scoring pass rate unexpectedly low

If pass rate looks too low, check whether optional checklist items
(`token_budget_total`, `turn_budget`, `efficiency_judge`, `no_retries`) are
being treated as required. The CLI and agent-loop paths should both use
`_DEVSHELL_AGENT_INGEST_OPTIONAL_IDS` to exclude these from the binary 0/100
score. If they're not excluded, every task that exceeds token/turn budgets
will score 0 even if the actual work is correct.
