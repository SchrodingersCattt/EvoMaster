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
