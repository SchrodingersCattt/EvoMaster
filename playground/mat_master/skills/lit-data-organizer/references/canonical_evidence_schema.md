# Canonical Evidence Schema

This document defines the canonical row schema used by `lit-data-organizer`.

The skill outputs one table shape only: `lit_evidence_table`.

## Design Principles

- One row = one evidence card.
- Preserve source traceability for every row.
- Keep measurement conflicts as separate rows.
- Support independent, dependent, and intermediate variable roles.
- Use one canonical schema for both CSV and JSONL exports.

## Canonical Fields

| Field | Type | Description |
|---|---|---|
| `source_id` | string | Stable source record identifier (existing or generated). |
| `source_type` | string | `pdf` or `web`. |
| `source_title` | string | Source title or document name. |
| `source_url_or_path` | string | URL or local path for traceability. |
| `topic` | string | Topic/domain tag. |
| `claim_text` | string | Claim extracted or normalized from source content. |
| `quote_text` | string | Direct evidence text/snippet. |
| `summary_text` | string | Short normalized summary. |
| `evidence_span` | string | Locator (for example page/section/paragraph marker). |
| `tags` | string | Comma-separated tags. |
| `confidence` | string | Confidence score/value if available. |
| `created_at` | string | Record creation timestamp. |
| `material_name` | string | Material/common name. |
| `formula` | string | Chemical formula (if available). |
| `composition` | string | Composition descriptor. |
| `phase_or_polymorph` | string | Phase/crystal polymorph information. |
| `independent_vars` | string | JSON string for structured independent-variable values. |
| `property_name` | string | Property/measurement name. |
| `property_value` | string | Property/measurement value. |
| `property_unit` | string | Unit of `property_value`. |
| `property_role` | string | `independent`, `dependent`, or `intermediate`. |
| `test_method` | string | Measurement/test method. |
| `conditions` | string | JSON string for conditions (temperature, pressure, etc.). |
| `uncertainty` | string | Uncertainty/error expression. |
| `conflict_group_id` | string | Group ID linking conflicting measurements. |
| `conflict_note` | string | Human-readable reason for conflict preservation. |

## Conflict Metadata Rules

- Never overwrite one measurement with another when rows refer to the same material-property group but values differ.
- Keep all conflicting rows and assign the same `conflict_group_id`.
- Set `conflict_note` to describe that the conflict is preserved across sources/methods/conditions.
- If no conflict exists for a row, `conflict_group_id` and `conflict_note` may be empty.

## Suggested Alias Override Config (Optional)

Use `--schema <config.json>` with a JSON object:

```json
{
  "field_aliases": {
    "source_title": ["title", "document_title"],
    "source_url_or_path": ["url", "path"],
    "property_name": ["measurement_name", "property"],
    "property_value": ["value", "measurement_value"]
  },
  "defaults": {
    "topic": "literature-extraction"
  },
  "property_role_map": {
    "example_intermediate_property": "intermediate"
  }
}
```

## Generic CSV Row Example

```text
source_id,source_type,source_title,source_url_or_path,topic,claim_text,quote_text,summary_text,evidence_span,tags,confidence,created_at,material_name,formula,composition,phase_or_polymorph,independent_vars,property_name,property_value,property_unit,property_role,test_method,conditions,uncertainty,conflict_group_id,conflict_note
abc123,pdf,Document A,/path/doc_a.pdf,topic-a,Claim A,Quote A,Summary A,page:3,"tag1,tag2",0.82,2026-02-17T10:00:00+00:00,Material A,AxBy,,Phase I,"{""var1"":""v1""}",property_x,12.5,unit_x,dependent,method_1,"{""temperature"":300}",0.2,conflict-001,Conflicting measurements detected for the same material-property group across sources or methods.
```

## Generic JSONL Row Example

```json
{"source_id":"abc123","source_type":"web","source_title":"Source B","source_url_or_path":"https://example.org/item","topic":"topic-b","claim_text":"Claim B","quote_text":"Quote B","summary_text":"Summary B","evidence_span":"section:results","tags":"tagA,tagB","confidence":"0.76","created_at":"2026-02-17T10:00:00+00:00","material_name":"Material B","formula":"CxDy","composition":"","phase_or_polymorph":"","independent_vars":"{\"var2\":\"v2\"}","property_name":"property_y","property_value":"4.1","property_unit":"unit_y","property_role":"intermediate","test_method":"method_2","conditions":"{\"pressure\":1}","uncertainty":"","conflict_group_id":"","conflict_note":""}
```
