# Business export: canonical table → candidates.csv

The lit-data-organizer skill outputs a **canonical evidence table** (one row per evidence card). For phase-one deliverables such as `candidates.csv` (material_name, class, Ps, Pr, units, DOI, reference_URL, etc.), treat the canonical table as the **intermediate** artifact and produce the business CSV in one of these ways:

## Option 1: Schema mapping at export

Use `--schema <config.json>` with `field_aliases` and `defaults` so that the exported CSV uses column names expected by the downstream task. Map canonical columns to business columns, e.g.:

- `material_name` → keep
- `source_title` / `formula` → use for class or material_name if needed
- `property_name` / `property_value` / `property_unit` → for Ps, Pr (may require multiple rows per material to be pivoted or filtered)
- `source_url_or_path` → reference_URL; extract DOI from URL or a dedicated field if present
- `conditions` (JSON) → temp_K, frequency_Hz, sample_form if stored there

If the canonical table has one row per evidence card and you need one row per material with Ps/Pr columns, the agent may need to aggregate or run a small script after export.

## Option 2: Post-export script

After `build_lit_table.py --output lit_evidence_table.csv --format csv`:

1. Read `lit_evidence_table.csv`.
2. Filter rows that have the required fields (e.g. material_name, property_name in ['Ps','Pr'], source_url_or_path).
3. Pivot or aggregate so each material has at most one row with columns: material_name, class, Ps, Pr, Ps_units, Pr_units, temp_K, frequency_Hz, sample_form, DOI, reference_URL.
4. Write `candidates.csv`.

This keeps the skill generic and pushes business-specific shape to a clear second step.

## Recommendation

- **Planner**: Treat lit-data-organizer output as the **intermediate** table. Add a follow-up step (or script) that produces the final deliverable (e.g. `candidates.csv`) from the canonical export.
- **Executor**: When the goal is "produce candidates.csv", run lit-data-organizer first, then either use a schema that approximates the target columns or run a small Python script to map and export `candidates.csv` from the canonical CSV.
