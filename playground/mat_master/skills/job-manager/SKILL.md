---
name: job-manager
description: "Resilient job lifecycle manager: monitors a submitted remote calculation job (via Bohrium OpenAPI), polls status, downloads results on success, diagnoses errors on failure, and suggests fixes. Supports all async software (DPA, ABACUS, LAMMPS, CP2K, QE, ABINIT, ORCA, Gaussian, etc.). Call ONCE after submitting a job — the script blocks until completion or failure. Usage: run_script run_resilient_job.py --job_id <ID> --software <SW> --workspace <PATH> [--bohr_job_id <BOHRIUM_ID>]"
skill_type: operator
---

# Job Manager Skill

Encapsulates the **Submit → Monitor → Diagnose → Fix** loop for remote calculation jobs so the agent does NOT need to poll `job_status` in a multi-turn chat loop (which wastes tokens and is fragile).

Internally queries the **Bohrium OpenAPI** (`/openapi/v1/sandbox/job/{id}`) for job status, using `BOHRIUM_ACCESS_KEY` from the environment. Aligned with the old MatMaster `services/job.py`.

**Supported software**: Any remote-execution software whose MCP tool returns a `job_id`. Currently includes: DPA, ABACUS, LAMMPS, CP2K, QE, ABINIT, ORCA, Gaussian.

## Workflow (for the agent)

1. **Submit** the job via the appropriate MCP tool (e.g. `mat_dpa_submit_optimize_structure`, `mat_abacus_submit`, `mat_binary_calc_submit_run_lammps`). Note the `job_id` **and** `extra_info.bohr_job_id` (if present) from the response.
2. **Call this skill once**:
   ```
   use_skill(
     skill_name="job-manager",
     action="run_script",
     script_name="run_resilient_job.py",
     script_args="--job_id <JOB_ID> --software <SOFTWARE> --workspace <WORKSPACE_PATH> --bohr_job_id <BOHRIUM_ID>"
   )
   ```
   **IMPORTANT**: For dpdispatcher-based MCP servers (ABACUS, etc.), the MCP `job_id` contains a hex hash that is NOT the Bohrium job ID. You MUST pass `--bohr_job_id` (from `extra_info.bohr_job_id` in the submit response) for status polling to work. For binary_calc servers (CP2K, LAMMPS, etc.), the numeric part of the MCP `job_id` is auto-extracted and `--bohr_job_id` is optional.
3. The script **blocks** until the job reaches a terminal state (success or failure after retries).
4. On **success**: returns job metadata and available output file paths.
5. On **failure**: returns the error code and a suggested fix strategy. The agent can apply the fix, resubmit via MCP, and call this skill again.

## Scripts

### `run_resilient_job.py`

Resilient job lifecycle manager.

**Arguments:**

| Arg | Required | Description |
|-----|----------|-------------|
| `--job_id` | Yes | Job ID returned by the MCP submit tool |
| `--bohr_job_id` | Conditional | Bohrium OpenAPI job ID from `extra_info.bohr_job_id` in submit response. **Required** for ABACUS and other dpdispatcher-based jobs; optional for binary_calc jobs (CP2K, LAMMPS, etc.) |
| `--software` | Yes | Software name (case-insensitive): `dpa`, `abacus`, `lammps`, `cp2k`, `qe`, `abinit`, `orca`, `gaussian`, or any registered async software |
| `--workspace` | No | Workspace directory for result downloads (default: `.`) |
| `--poll_interval` | No | Seconds between status checks (default: `30`) |
| `--max_retries` | No | Max diagnosis-and-retry cycles (default: `5`) |

**Output**: JSON object with keys:
- `status`: `"success"` | `"failed"` | `"needs_fix"` | `"unknown"`
- `job_id`: the MCP job ID
- `bohr_job_id`: the resolved Bohrium job ID (if available)
- `retries`: number of retries attempted
- `results` / `downloads`: result data and local file paths (on success)
- `error_code` / `fix_strategy`: diagnosis info (on failure)
- `message`: human-readable summary

**Example usage (binary_calc — CP2K):**
```
use_skill(
  skill_name="job-manager",
  action="run_script",
  script_name="run_resilient_job.py",
  script_args="--job_id 2026-02-11-09:47:40/614883 --software cp2k --workspace /workspace"
)
```

**Example usage (dpdispatcher — ABACUS, must include bohr_job_id):**
```
use_skill(
  skill_name="job-manager",
  action="run_script",
  script_name="run_resilient_job.py",
  script_args="--job_id 2026-02-11-09:51:01/4bf6fc28... --bohr_job_id f9db60be1b58484f85ad6932da35a205 --software abacus --workspace /workspace"
)
```

## Integration with other skills

- **log-diagnostics** (`extract_error.py`): Called internally to diagnose failed jobs. You do NOT need to call it separately when using job-manager.
- **compliance-guardian** (`check_compliance.py`): The agent should still call this BEFORE submitting a job. job-manager handles post-submission only.
- **input-manual-helper**: The agent should still use this to fix input files BEFORE submission.

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
