# Calculation job submit and tool usage

Rules for calling Mat MCP calculation tools (mat_sg, mat_dpa, mat_doc) to avoid common errors and pointless polling.

## Code examples

### Chaining URL from previous tool (correct)

```text
Step 1: mat_sg_build_bulk_structure_by_template(...) 
  → returns structure_paths: "https://bohrium.oss-xxx/evomaster/calculation/123_Fe_bcc.cif"

Step 2: mat_dpa_optimize_structure(input_structure="https://bohrium.oss-xxx/evomaster/calculation/123_Fe_bcc.cif", ...)
  → use the full URL, not "Fe_bcc.cif"
```

### Submit → poll → get results (async flow)

```text
1. mat_dpa_submit_optimize_structure(input_structure="https://...", ...)  → job_id: "abc-123"
2. Wait 30–60 s, then mat_dpa_query_job_status(job_id="abc-123")  → status: "Running"
3. Wait again, mat_dpa_query_job_status(job_id="abc-123")  → status: "Done"
4. mat_dpa_get_job_results(job_id="abc-123")  → result file URLs
5. Download those URLs into workspace (see Download example below)
```

### Upload: pass local path when file is in workspace

```text
# User uploaded Fe_bcc.cif to workspace. Call tool with that path; system uploads to OSS and passes URL.
mat_dpa_optimize_structure(input_structure="Fe_bcc.cif", ...)
# or
mat_dpa_optimize_structure(input_structure="/workspace/data/Fe_bcc.cif", ...)
```

### Download: save result URLs to workspace

```text
# After get_job_results returns e.g. {"output_cif": "https://bohrium.oss-.../out.cif", ...}
# Save to workspace so the user can open the file:
execute_bash(cmd="curl -o output.cif 'https://bohrium.oss-.../out.cif'")
# Or in resilient_calc mode the system already places results under workspace/calculation_results/
```

---

## 1. Use URLs from previous tool output as downstream input

- If the previous tool returned an **https URL** (e.g. `structure_paths`, `job_link`, file URL), the next tool that needs that file **must use that URL** as input. Do not pass only a filename (e.g. `Fe_bcc.cif`).
- Example: `mat_sg_build_bulk_structure_by_template` returns `structure_paths: "https://bohrium.oss-.../Fe_bcc.cif"`; then `mat_dpa_optimize_structure`'s `input_structure` must be that **full URL**, not `Fe_bcc.cif`.
- Use a local path only when the file is actually in the local workspace and must be uploaded (and OSS env vars are configured).

## 2. Long-running DPA tasks: always use submit → poll status → get results

- **Do not** use the synchronous tools: `mat_dpa_optimize_structure`, `mat_dpa_calculate_phonon`, `mat_dpa_run_molecular_dynamics`, etc., as they may timeout.
- **Do** use the corresponding **submit_** tool to submit the job, then `mat_dpa_query_job_status` to check status, and when status is Done/Success call `mat_dpa_get_job_results` to fetch results.
- Flow: `mat_dpa_submit_optimize_structure` → get `job_id` → loop `mat_dpa_query_job_status(job_id)` until not Running → `mat_dpa_get_job_results(job_id)`.

## 3. How to poll job status

- Wait at least **30–60 seconds** between two `query_job_status` calls; avoid high-frequency polling.
- When status is **Running**, keep waiting and query again later, or do other steps and then check again.
- When status is **Done / Success**, call **get_job_results** immediately to get the result and continue; do not keep querying status without fetching results.

## 4. Chaining with Structure Generator

- URLs returned by `mat_sg_*` for structure files can be used directly as `input_structure` (and similar) for `mat_dpa_*`.
- For "structure generation then DPA calculation", prefer using the URL from the previous step rather than downloading to local and passing a local path (unless you explicitly need local upload and have OSS configured).

## 5. Upload: local files as calculation input

- Calculation MCP tools (mat_sg_*, mat_dpa_*, mat_doc_*, mat_abacus_*) require **OSS or HTTP URLs** for path arguments (e.g. structure_path, input_structure, pdf_path). The **path adaptor** handles upload automatically when you pass a **local path**.
- If the file is in the **workspace** (e.g. user-uploaded CIF, PDF, or a file you created), you may pass its path as-is (e.g. `Fe_bcc.cif`, `/workspace/data/input.cif`, or a path relative to workspace). The system will upload it to OSS and pass the resulting URL to the MCP tool. Ensure OSS env vars (OSS_ENDPOINT, OSS_BUCKET_NAME, OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET) are set at project root if you use local paths.
- Prefer **chaining URLs** from previous tool output when possible; use local path only when the file exists only in the workspace.

## 6. Download: result URLs to workspace

- When a calculation job returns **result file URLs** (e.g. from `mat_dpa_get_job_results`, or any OSS/HTTP link in the response), **download them into the current workspace** so the user can open or post-process the files. In resilient_calc mode the system places results under the workspace (e.g. `calculation_results/`). When using tools directly, save or fetch result URLs into the working directory so outputs are visible and persistent.
