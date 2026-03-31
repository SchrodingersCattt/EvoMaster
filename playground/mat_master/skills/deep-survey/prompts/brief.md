# deep-survey: brief depth workflow

**This file is the authoritative workflow for `--depth brief`.**

---

## Purpose

Produce a structured evidence skeleton (`collected.json`) for downstream skills (lit-data-organizer, manuscript-scribe). No Markdown narrative report is produced at this tier.

---

## Workflow

### Step 1 — Plan facets
- Identify **1-2 key facets** from the user's topic (e.g. "mechanism", "performance metrics").
- For each facet, plan **2-3 query variants** (keyword combinations, synonyms).

### Step 2 — Retrieve
- Run **3-5 `mat_sn_*` retrieval calls** total (paper search + web search).
- Filter for relevance; keep only sources clearly related to the user intent.
- Prefer sources with accessible URLs (DOI links or direct paper URLs).

### Step 3 — Populate collected.json

**Attention**: Ensure evidence covers all key concepts in the topic (e.g. for “A vs B”, both A and B should appear in at least some cards). If one side is missing, add retrieval or pass `--facet <facet>` when calling `collect_evidence.py` so facets are set at ingest; finish may be blocked until coverage is sufficient.

Write each retrieved evidence item as a card in `collected.json`. Schema:

```json
{
  "source_title": "Full paper/source title",
  "source_url": "https://doi.org/... or direct URL (REQUIRED)",
  "year": 2024,
  "first_author": "LastName",
  "facet": "facet name (e.g. mechanism, methods, performance)",
  "claim": "One-sentence summary of the specific claim or finding",
  "data_points": ["specific value or observation 1", "specific value or observation 2"]
}
```

Full schema reference: `use_skill action=get_reference skill_name="deep-survey" reference_name="collected_json_schema.md"`

**Required fields**: `source_title`, `source_url`, `year`, `first_author`, `facet`, `claim`.
`data_points` is optional but strongly preferred — list any specific numbers, conditions, or measurements.

### Step 4 — Done
- Write `collected.json` to `_tmp/surveys/` (or as instructed by the calling skill).
- **Do NOT produce a Markdown report.** brief is a machine-readable feed, not a human document.
- Pass `collected.json` path to the calling skill (lit-data-organizer, manuscript-scribe, etc.).

---

## Quality rules

- Every card must have a URL. If a source has no accessible URL, record the DOI as `https://doi.org/<DOI>`.
- `claim` must be specific and falsifiable — not "paper discusses X" but "X improves Y by Z under conditions A".
- `data_points` should capture any concrete numbers or observations from the abstract/result.
- Facet labels must be consistent across all cards (e.g. always "mechanism" not sometimes "mechanisms").

---

## Citation format

Follow the citation and output format rules already in your system prompt (`citation_and_output_format.md`) for any in-text citations if a brief summary note is added.

---

## What NOT to do at this tier

- Do NOT write Executive Summary, Key Methodologies, or Gap Analysis sections.
- Do NOT leave any `(TBD)` fields.
- Do NOT run 10+ retrievals — stay within the 3-5 call budget.
