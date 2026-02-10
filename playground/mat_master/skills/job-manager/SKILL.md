---
name: job-manager
description: "Resilient job lifecycle manager: monitors a submitted calculation job (ABACUS, LAMMPS, CP2K, QE, ABINIT, ORCA), polls status, downloads results on success, diagnoses errors on failure, and suggests fixes. Call ONCE after submitting a job — the script blocks until completion or failure. Usage: run_script run_resilient_job.py --job_id <ID> --software <SW> --workspace <PATH>"
skill_type: operator
---

# Job Manager Skill

Encapsulates the **Submit → Monitor → Diagnose → Fix** loop for remote calculation jobs so the agent does NOT need to poll `job_status` in a multi-turn chat loop (which wastes tokens and is fragile).

## Workflow (for the agent)

1. **Submit** the job via the appropriate MCP tool (e.g. `mat_abacus_submit`, `mat_binary_calc_submit_run_lammps`). Note the `job_id` from the response.
2. **Call this skill once**:
   ```
   use_skill(
     skill_name="job-manager",
     action="run_script",
     script_name="run_resilient_job.py",
     script_args="--job_id <JOB_ID> --software <SOFTWARE> --workspace <WORKSPACE_PATH>"
   )
   ```
3. The script **blocks** until the job reaches a terminal state (success or failure after retries).
4. On **success**: returns downloaded result file paths.
5. On **failure**: returns the error code and a suggested fix strategy. The agent can apply the fix, resubmit via MCP, and call this skill again.

## Scripts

### `run_resilient_job.py`

Resilient job lifecycle manager.

**Arguments:**

| Arg | Required | Description |
|-----|----------|-------------|
| `--job_id` | Yes | Job ID returned by the MCP submit tool |
| `--software` | Yes | Software name: `abacus`, `lammps`, `cp2k`, `qe`, `abinit`, `orca`, `gaussian` |
| `--workspace` | No | Workspace directory for result downloads (default: `.`) |
| `--poll_interval` | No | Seconds between status checks (default: `30`) |
| `--max_retries` | No | Max diagnosis-and-retry cycles (default: `3`) |

**Output**: JSON object with keys:
- `status`: `"success"` | `"failed"` | `"needs_fix"` | `"unknown"`
- `job_id`: the (possibly updated) job ID
- `retries`: number of retries attempted
- `results` / `downloads`: result data and local file paths (on success)
- `error_code` / `fix_strategy`: diagnosis info (on failure)
- `message`: human-readable summary

**Example usage:**
```
use_skill(
  skill_name="job-manager",
  action="run_script",
  script_name="run_resilient_job.py",
  script_args="--job_id abc123 --software abacus --workspace /path/to/ws --poll_interval 60"
)
```

## Integration with other skills

- **log-diagnostics** (`extract_error.py`): Called internally to diagnose failed jobs. You do NOT need to call it separately when using job-manager.
- **compliance-guardian** (`check_compliance.py`): The agent should still call this BEFORE submitting a job. job-manager handles post-submission only.
- **input-manual-helper**: The agent should still use this to write correct input files BEFORE submission.

## Error codes & fix strategies

The script includes built-in fix strategies for common errors:

| Error Code | Suggested Fix |
|-----------|---------------|
| `scf_diverged` | Update mixing parameters (ALGO→All, AMIX→0.1) |
| `scf_diagonalization_error` | Switch algorithm (ALGO→Normal, PREC→Accurate) |
| `kpoints_error` | Reduce k-point density |
| `grid_too_coarse` | Increase energy cutoff |
| `lost_atoms` | Reduce timestep |
| `out_of_range` | Reduce timestep |
| `walltime_exceeded` | Increase walltime or split job |
| `oom_error` | Reduce parallelism or system size |

When the script returns `status="needs_fix"`, the agent should:
1. Read the `fix_strategy` from the output
2. Apply the suggested parameter changes to the input files
3. Re-submit via MCP
4. Call this skill again with the new `job_id`
