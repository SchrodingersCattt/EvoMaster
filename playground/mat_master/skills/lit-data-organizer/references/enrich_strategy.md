# Enrich Strategy Guide

After the `normalize` stage, the agent must choose an enrichment strategy to populate key fields like `material_name`, `property_name`, `property_value`, `property_unit`, and `enrich_keep`.

## Quick Comparison

| Dimension | Pattern-Based (Regex) | Semantic-Based (LLM) |
|-----------|----------------------|----------------------|
| **Speed** | Fast (ms per row) | Slow (100-500ms per row) |
| **Cost** | Free (local) | API cost (~$0.01-0.1 per batch) |
| **Accuracy** | High for structured patterns | Higher for ambiguous/complex text |
| **Determinism** | Deterministic | Non-deterministic (varies by model/temp) |
| **Best for** | Known field structures, repeated patterns | Semantic understanding, reasoning, cross-references |
| **Domain knowledge** | Requires domain expertise to write patterns | Works with generic prompting |

---

## Strategy 1: Pattern-Based Enrichment (Regex)

### When to Use

- You have **known, repetitive patterns** in the evidence text (e.g., "Pr = 12 µC/cm²", "material: XYZ")
- The domain is **well-defined** (e.g., ferroelectrics, semiconductors with standard notation)
- You need **high throughput** and low cost
- You want **deterministic, reproducible** results

### How to Implement

#### Step 1: Analyze the Data

```python
# Load normalized_rows.json and inspect quote_text / claim_text samples
import json
with open('_tmp/lit_data/normalized_rows.json') as f:
    rows = json.load(f)

# Print first 5 quote texts to identify patterns
for i, row in enumerate(rows[:5]):
    print(f"Row {i}:")
    print(f"  Material: {row.get('material_name', '')}")
    print(f"  Quote: {row.get('quote_text', '')}")
    print()
```

#### Step 2: Define Extraction Patterns

See [pattern_guide.md](pattern_guide.md) for systematic pattern definition.

#### Step 3: Build Extraction Functions

```python
def extract_material_name(quote_text: str, patterns: dict) -> str:
    """Extract material name from quote text using keyword or regex matching."""
    # Implement based on your patterns
    return ""  # Return empty, not fabricated value

def extract_property_value(quote_text: str, patterns: dict) -> tuple[str, str]:
    """Extract property value and unit. Returns (value, unit) or ("", "")."""
    # Implement based on your patterns
    return "", ""
```

#### Step 4: Apply to All Rows

Enrich all rows, marking `enrich_keep` based on whether critical fields were successfully extracted.

---

## Strategy 2: Semantic-Based Enrichment (LLM)

### When to Use

- Evidence text is **ambiguous, poorly structured, or requires reasoning**
- You need to **cross-reference** information (e.g., resolve abbreviations using context)
- The domain is **not well-structured** or highly variable
- Cost/latency are acceptable

### How to Implement

#### Step 1: Prepare Batch Prompts

Build prompts that ask the LLM to extract `material_name`, `property_name`, `property_value`, `property_unit` from each record. Return JSON array.

#### Step 2: Call LLM in Batches

Send batches to LLM, parse JSON response, map results back to rows by idx.

---

## Strategy 3: Hybrid (Pattern + LLM Fallback)

For robustness, combine both approaches: try patterns first (fast), fall back to LLM for rows with missing critical fields.

---

## After Enrichment: Continue the Pipeline

Once enrichment is complete:

1. Save enriched rows to `_tmp/lit_data/enrich_rows.json`
2. Update `_tmp/lit_data/state.json` with:
   ```json
   {
     "enrich_rows_file": "_tmp/lit_data/enrich_rows.json"
   }
   ```
3. Call the next stage:
   ```bash
   python build_lit_table.py --stage dedup --resume --state _tmp/lit_data/state.json
   ```

---

## See Also

- [pattern_guide.md](pattern_guide.md) — How to design patterns
- [canonical_evidence_schema.md](canonical_evidence_schema.md) — Field definitions
