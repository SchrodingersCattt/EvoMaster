---
name: invar-dopant-validator
description: "Validates a candidate dopant element for Fe-Ni-Co Invar by searching for experimental studies that report measured TEC values, then ranks alternatives quantitatively. Use this AFTER initial screening to confirm or revise an element recommendation before finalising. Returns a dopant_comparison.json with ranked elements and a revised recommendation."
skill_type: operator
depends_on: mcp-mat-sn
---

# Invar Dopant Validator

Quantitatively validates element candidates for Fe-Ni-Co Invar using experimental TEC measurements from the literature. The key differentiator from initial screening: it searches for **numerical TEC values** rather than relying on qualitative arguments about magnetovolume compatibility.

## When to use

Call this skill **after** Task 1 screening but **before** writing `recommendation.json`, when:
- You have 2+ candidate elements that all pass qualitative filters
- Your initial top choice is based on reasoning (e.g. "preserves magnetovolume") rather than measured TEC data
- You need to choose between candidates like Mn, Si, Ge, Cu, etc.

## Usage

```
use_skill action=run skill_name="invar-dopant-validator" candidates="Mn,Si" base_alloy="Fe-Ni-Co"
```

`candidates`: comma-separated chemical symbols (e.g. "Mn,Si")
`base_alloy`: base system (default: "Fe-Ni-Co")

## Workflow

### Step 1 — Search for systematic comparison studies

Run these searches using mat_sn_search-papers-enhanced (or web-search if mat_sn unavailable):

Query A — combinatorial/systematic study:
  words: ["Fe-Ni-Co", "Invar", "thermal expansion", "systematic", "element addition", "doping"]
  question: "Experimental study comparing multiple element additions at equal at.% on TEC in Fe-Ni-Co Super Invar. Need measured TEC values for Si, Mn, Al, Cr, Cu."
  page_size: 20, rerank: 1

Query B — per-candidate targeted (repeat for each candidate X):
  words: ["Fe-Ni", "Invar", X, "thermal expansion coefficient", "ppm/K"]
  question: "Experimental measurement of TEC for X addition to Fe-Ni or Fe-Ni-Co Invar at 1-5 at.% X."
  page_size: 15, rerank: 1

### Step 2 — Extract TEC numbers

For each paper found, read the abstract and fetch the full page if accessible:
  extract_info_from_webpage(url=paper_url, info_to_extract="TEC values ppm/K for each element, at.%, base alloy composition")

Look specifically for:
- Tables: Element | at.% | TEC (ppm/K)
- Sentences like "X at 5 at.% gives TEC = Y ppm/K"
- Systematic studies comparing Fe-Ni-Co + 5 at.% X across multiple X

### Step 3 — Rank candidates

Build comparison table: Element | at.% tested | Measured TEC (ppm/K) | Source
Rank by TEC (lower = better).
If no quantitative data exists for a candidate, mark "no experimental TEC data in Invar context" — this is evidence against it vs a candidate with confirmed low TEC.

### Step 4 — Write dopant_comparison.json

If search succeeded and data found:
{
  "search_performed": true,
  "candidates_evaluated": ["Mn", "Si"],
  "tec_data": [
    {"element": "Si", "at_pct": 5, "tec_ppm_K": 1.5, "source": "Author et al. Year, DOI"},
    {"element": "Mn", "at_pct": 5, "tec_ppm_K": 2.8, "source": "Author et al. Year, DOI"}
  ],
  "ranking": ["Si", "Mn"],
  "revised_primary": "Si",
  "revision_note": "Initial screening preferred Mn on magnetovolume grounds; experimental TEC data shows Si achieves lower TEC at comparable at.%.",
  "confidence": "high"
}

If search tools unavailable:
{
  "search_performed": false,
  "reason": "Literature search tools not available in this configuration.",
  "revised_primary": null,
  "confidence": "low"
}

### Step 5 — Report and use revised_primary

Report clearly: whether a revision was made, validated primary element (use in recommendation.json), key TEC evidence with citations.

## Notes
- Prioritise studies testing the same at.% across candidates in the same base alloy.
- Target metric is numerical TEC, not density or Curie temperature alone.
- If only one candidate has quantitative TEC below 3 ppm/K, prefer it over qualitatively-justified alternatives.
