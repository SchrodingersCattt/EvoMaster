---
name: bohrium
description: "Bohrium HPC platform knowledge only. This skill is guidance-only and has no runnable scripts; all submit, poll, list_images, and list_machines operations must use the built-in Bohrium tool."
skill_type: operator
---

# Bohrium Platform Guide

This skill provides **platform knowledge only**.

- It is **guidance-only**
- It has **no runnable scripts**
- All actual operations (`submit`, `poll`, `list_images`, `list_machines`) must use the built-in `Bohrium` tool

## Workflow Pattern

1. Load a **software skill** (cp2k, qe, abacus, orca, lammps, gromacs, pyscf, abinit, pyatb) to get `image`, `machine`, and `cmd` parameters
2. `Bohrium(action="submit", input_dir=..., image=..., cmd=..., machine=...)`
3. `Bohrium(action="poll", job_id=<id>)` — repeat until Finished or Failed
4. Analyze results in `result_dir`

## Monitoring Strategy

The `poll` action returns the **current status in one call** (non-blocking):

| Status | Meaning | What to Do |
|--------|---------|------------|
| Pending / Scheduling | Job queued, waiting for resources | Poll again later |
| Running | Job executing on HPC node | Poll again later |
| Finished | Completed; results downloaded to result_dir | Analyze output files and log_tail |
| Failed | Job crashed or errored | Read log_tail for diagnostics |

**Poll interval guidance**: HPC jobs typically run minutes to hours. A reasonable pattern is to poll every 30-60 seconds for short jobs, or inform the user and re-check on request for long jobs. There is no need to poll continuously — each poll is a single lightweight API call.

## Credential Resolution

Bohrium credentials are resolved through a unified runtime bridge with the following precedence:

1. **Explicit params** -- caller-provided values (e.g. passed directly to API functions)
2. **Session / runtime** -- `session._bohrium_credentials` injected by the platform during a run (production path)
3. **Environment fallback** -- `BOHRIUM_ACCESS_KEY` / `BOHRIUM_PROJECT_ID` from `.env` or os.environ (local development)

In production, credentials come from the runtime session automatically. Setting `BOHRIUM_ACCESS_KEY` in `.env` is the local development fallback for running without a platform session.

## Troubleshooting

| Symptom | Likely Cause | Resolution |
|---------|-------------|------------|
| submit returns `credentials unavailable` | No session and no env variable | In production: verify session has Bohrium credentials. In local dev: set `BOHRIUM_ACCESS_KEY` in `.env` |
| submit returns `project ID unavailable` | Missing or invalid project ID | In production: verify session credentials include project_id. In local dev: set `BOHRIUM_PROJECT_ID` in `.env` |
| Failed + empty log_tail | Command errored before writing to log | Check image/cmd compatibility; wrong binary path or missing dependencies |
| Failed + OOM in log | Insufficient machine memory | Switch to a larger machine (e.g. `c32_m128_cpu` → `c64_m256_cpu`) |
| Failed + "not enough slots" | MPI process count > available cores | Reduce `-np` to match machine core count |
| Finished but results empty | Output files not in expected locations | Check the command writes to the working directory (not a temp path) |

## Image / Machine Discovery

When a software skill's default image is outdated or the user needs a specific version:

- `Bohrium(action="list_images", keyword="<software>")` — find available Docker images
- `Bohrium(action="list_machines", machine_type="cpu")` — find CPU machine options
- `Bohrium(action="list_machines", machine_type="gpu", keyword="4090")` — find GPU options

Use `url` from image results as the `image` parameter, and `skuEnName` from machine results as the `machine` parameter.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `BOHRIUM_ACCESS_KEY` | local dev only | Bohrium API access key (production uses session credentials) |
| `BOHRIUM_PROJECT_ID` | local dev only | Bohrium project ID, positive integer (production uses session credentials) |
| `BOHRIUM_BASE_URL` | no | API base URL (default: `https://open.bohrium.com`) |
| `BOHRIUM_USE_SANDBOX` | no | `1` = sandbox API paths (default when unset); `0` = standard HPC paths |

## Sandbox vs Standard HPC

The API path set is determined by `BOHRIUM_USE_SANDBOX`:

- **Sandbox** (`1`, default when unset): uses `/openapi/v1/sandbox/job/...` paths. Suitable for testing and development.
- **Standard HPC** (`0`): uses `/openapi/v1/job/create`, `/openapi/v2/job/add`, `GET /openapi/v1/job/{id}`. For production workloads.

The `Bohrium` tool handles path selection automatically based on this environment variable.
