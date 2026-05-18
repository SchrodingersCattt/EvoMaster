---
name: sample-atomic-structures
description: "Sample crystal candidates via CALYPSO or CrystalFormer—not database lookup (use mcp-mat-struct-db). Requires explicit space_group from the user; never guess."
skill_type: mcp-loader
mcp_server: mat_sg
depends_on: inspect-atomic-structure
---

# Sample Atomic Structures

Use this skill for global or conditional structure generation, where the output
is a set of newly sampled candidate structures. This is not database search and
not a deterministic prototype builder.

## Decision Tree

1. If the user asks for known materials or IDs, do not use this skill.
2. For composition-only global search, use CALYPSO.
3. For target-property constraints, use CrystalFormer.
4. If using CrystalFormer, ask the user for `space_group` explicitly before
   tool execution. Do not infer it from chemistry.
5. After generation, inspect every returned candidate before downstream use.

## Local API

No local inline implementation is expected. These generators run through
dispatcher-side images and private model stacks.

## MCP Tools

This skill is the MCP loading path for `mat_sg`. After this branch the
`mat_sg` server only exposes the sampling pair plus the dispatcher job-control
trio; everything else has been localised into the sibling
`atomic-structure-*` operator skills.

Submit (asynchronous, dispatcher-backed):

- `mat_sg_submit_generate_calypso_structures(species, n_tot, ...)`
- `mat_sg_submit_generate_crystalformer_structures(space_group,
  cond_model_type_list, target_value_list, target_type_list, sample_num,
  mc_steps, ...)`

Job control (synchronous):

- `mat_sg_query_job_status(job_id)` — poll until status is terminal.
- `mat_sg_get_job_results(job_id)` — fetch artifact paths once status is
  `succeeded`/`finished`.
- `mat_sg_terminate_job(job_id)` — abort a run that is misconfigured or
  exceeds the user-approved budget.

Recipe:

1. Call the matching `submit_*` tool and capture `job_id` from the response.
2. Poll `query_job_status` with backoff (e.g. 5s → 15s → 30s, capped at 60s)
   until the status is terminal. Record every status transition.
3. On success, call `get_job_results` and report every returned file path.
4. On failure or user abort, call `terminate_job` and surface the error.

## Hard Guards

- `space_group` for CrystalFormer must be supplied by the user.
- `cond_model_type_list`, `target_value_list`, and `target_type_list` must have
  identical lengths.
- Supported CrystalFormer condition names include `bandgap`, `shear_modulus`,
  `bulk_modulus`, `ambient_pressure`, `high_pressure`, and `sound`.
- Put a reasonable bound on `sample_num` and `mc_steps`; ask before launching
  large searches.
- Treat outputs as candidates. They need inspection and often relaxation before
  simulation or publication.

## Acceptance Checklist

- The final answer states that structures were sampled/generated, not retrieved.
- The requested species/properties are recorded.
- For CrystalFormer, the user-provided `space_group` is included.
- Every returned file or directory path is reported.
- Candidate structures are queued for `inspect-atomic-structure`.

## Cross-Skill Refs

- `inspect-atomic-structure`: validate every sampled candidate.
- `build-atomic-structure`: deterministic prototypes and Wyckoff builds.
- `mcp-mat-struct-db`: known-structure retrieval.
