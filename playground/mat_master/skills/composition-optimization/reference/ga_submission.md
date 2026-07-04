# GA Submission via Bohrium Job

Submit DART GA optimization through the `bohrium-job` skill. This is the
**preferred path** (more stable than MCP `mat_compdart`).

## Image & Machine

| Item | Value |
|---|---|
| Image | `registry.dp.tech/dptech/dpa-calculator:1d4c78b4` |
| Machine | `c32_m128_cpu` |
| Command | `python run_ga.py > log 2>&1` |

## Workflow

### 1. Prepare Input Directory

Use `prepare_ga_config.py` to generate the input directory:

```
use_skill composition-optimization run_script prepare_ga_config.py \
  --elements '["Fe","Ni","Co","Cr","Si"]' \
  --targets '<targets_json>' \
  --constraints '<constraints_json>' \
  --output-dir /workspace/ga_input
```

This creates:
- `ga_config.json` — full GA configuration
- `run_ga.py` — wrapper script that invokes `comp_dart` inside the container

See [`ga_config_schema.md`](ga_config_schema.md) for the config format.

### 2. Submit Job

```
use_skill bohrium-job run_script submit_job.py \
  --input-dir /workspace/ga_input \
  --image registry.dp.tech/dptech/dpa-calculator:1d4c78b4 \
  --cmd "python run_ga.py > log 2>&1" \
  --machine c32_m128_cpu \
  --job-name "dart-ga-<candidate_element>"
```

### 3. Monitor + Download Results

```json
{
  "action": "run_script",
  "skill_name": "bohrium-job",
  "script_name": "poll_job.py",
  "script_args": "--job-id <job_id> --result-dir /workspace/ga_results",
  "script_timeout": 3600
}
```

### 4. Parse Results

```
use_skill composition-optimization run_script parse_ga_results.py \
  --input /workspace/ga_results \
  --output /workspace/ranked_compositions.json
```

## Fallback: MCP mat_compdart

If `bohrium-job` is unavailable, use the MCP tool directly:

```
Tool: mat_compdart_submit_run_dart_ga
Args: { elements, targets, constraints, ... }
```

Then monitor via `monitor_job` with `software="compdart"`.

## Fallback: Manual Linear Mixture

If both paths fail after 2 retries, compute density manually:

```
alloy_density = sum(element_density[i] * fraction[i] for i in elements)
```

Report as T4-level evidence (qualitative fallback).
