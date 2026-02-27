# collected.json Schema (deep-survey brief mode)

When `run_survey.py` is called with `--depth brief`, it outputs a `collected.json` file in `_tmp/surveys/`. This file serves as the structured evidence exchange format for downstream skills such as `lit-data-organizer` and `manuscript-scribe`.

## Schema

```json
{
  "topic": "string — the survey topic",
  "depth": "brief",
  "facets": ["string — facet name", "..."],
  "evidence_cards": [
    {
      "source_title": "string — paper or page title",
      "source_url": "string — DOI URL (https://doi.org/<DOI>) or direct URL",
      "year": 2024,
      "first_author": "string — first author surname or full name",
      "facet": "string — which facet this card belongs to (must match one of facets[])",
      "claim": "string — the key finding or claim from this source",
      "data_points": {
        "any_property": "any value — numeric, string, or nested object for structured data"
      }
    }
  ]
}
```

## Field descriptions

| Field | Required | Description |
|---|---|---|
| `topic` | yes | The survey topic as passed to `--topic` / `--title` |
| `depth` | yes | Always `"brief"` for this output |
| `facets` | yes | List of facets used for retrieval (1-2 for brief mode) |
| `evidence_cards` | yes | List of evidence cards populated by the LLM from retrieval results |
| `evidence_cards[].source_title` | yes | Title of the paper, article, or webpage |
| `evidence_cards[].source_url` | yes | Canonical URL; prefer `https://doi.org/<DOI>` for papers |
| `evidence_cards[].year` | yes | Publication year (integer) |
| `evidence_cards[].first_author` | yes | First author's name |
| `evidence_cards[].facet` | yes | Which facet this evidence belongs to |
| `evidence_cards[].claim` | yes | The key finding, result, or claim from this source |
| `evidence_cards[].data_points` | no | Structured key-value data (e.g. material, property, value, units) for `lit-data-organizer` ingestion |

## Example

```json
{
  "topic": "Perovskite stability under moisture",
  "depth": "brief",
  "facets": ["Mechanism", "Methods"],
  "evidence_cards": [
    {
      "source_title": "Ion migration and moisture-induced degradation in halide perovskites",
      "source_url": "https://doi.org/10.1039/D0EE01016B",
      "year": 2020,
      "first_author": "Zhang",
      "facet": "Mechanism",
      "claim": "Moisture accelerates ion migration at grain boundaries, leading to phase segregation.",
      "data_points": {
        "material": "MAPbI3",
        "humidity_threshold_pct": 30,
        "degradation_mechanism": "hydrolysis + ion migration"
      }
    }
  ]
}
```

## How downstream skills use this file

- **`lit-data-organizer`**: Reads `evidence_cards` as structured input rows. Pass the file via `--input_json collected.json`. Each card maps to one evidence row; `data_points` fields are ingested as property columns.
- **`manuscript-scribe`**: The agent reads `collected.json` and uses `evidence_cards` as the source material for writing sections. Pass the file path in `--content_file` or reference it in the section content.
- **`write_survey_report.py`**: Accepts `collected.json` as `--input` to compile a Markdown report from the evidence cards.
