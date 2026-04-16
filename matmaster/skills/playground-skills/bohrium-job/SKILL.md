---
name: bohrium-job
description: "Submission engine for Bohrium HPC. Receives a prepared input directory (output_dir from input-manual-helper, or a directory containing user-provided ready-to-run input files) and supports split submission and monitoring via submit_job.py and poll_job.py. Covers CP2K, QE, ABINIT, LAMMPS, ORCA, GROMACS and any other software available on Bohrium."
skill_type: operator
---

# Bohrium Job Skill

Submit an input directory to Bohrium HPC. Two entry paths:
- **Normal**: `input-manual-helper` generates files → pass `output_dir` here.
- **User-provided**: User supplies ready-to-run files → skip `input-manual-helper`, pass directory directly.

## Workflow

1. **Confirm input directory** — List contents to find the main input filename for `--cmd`.
2. **Look up image / machine / command** — Check `references/software_reference.md`. If not listed, run `list_images.py` / `list_machines.py` to query Bohrium — **never** assume unavailable without querying.
3. **Submit** (returns `job_id`):
   ```
   Skill bohrium-job run_script submit_job.py \
     --input-dir <dir> --image <image> --cmd "<command>" \
     [--machine c32_m128_cpu] [--job-name <name>]
   ```
   **Log convention**: `--cmd` must redirect to `> log 2>&1`. Do not use custom log filenames.

4. **Monitor + download** — MUST use `poll_job.py`, **NOT** built-in `monitor_job`:
   ```json
   {
     "action": "run_script", "skill": "bohrium-job",
     "script_name": "poll_job.py",
     "script_args": "--job-id <id> [--max-polls 2880] [--poll-interval 30]",
     "script_timeout": 86400
   }
   ```
   > `script_timeout` is a **Skill tool parameter** (not inside script_args). Always set to `max_polls × poll_interval` (default 86400s). Without it, the 60s session default kills the script.
   > **Do NOT reduce `--max-polls` below 2880** unless user explicitly requests shorter timeout.

## Dynamic Discovery

- `list_images.py [--keyword <name>] [--max-results 20]` — Query available Docker images.
- `list_machines.py [--type cpu|gpu] [--keyword <name>]` — Query available machine types.

Use `imageAddress` / `url` from results as `--image`, and `skuEnName` as `--machine`.

## Resuming Monitoring

When `poll_job.py` returns `status: "still_running"`:
- **Re-poll** (preferred): re-invoke with same `--job-id`.
- **Yield**: finish with `task_completed=partial`, report the `job_id`.
- Do NOT start unrelated tasks while job is pending.

## Rules

1. If user provided ready-to-run files, skip `input-manual-helper` and submit directly.
2. Input files land in `oss_downloaded_files/` — always `ls` to confirm actual filename before `--cmd`.
3. Match MPI `-np` to machine core count.
4. Check `"success": true` before proceeding.
5. Always use `poll_job.py` via Skill, never built-in `monitor_job`.
6. All commands: `> log 2>&1` (standardized log filename).
7. Unknown software: run `list_images.py --keyword <software>` first.
