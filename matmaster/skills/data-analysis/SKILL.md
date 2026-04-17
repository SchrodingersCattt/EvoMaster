---
name: data-analysis
description: "General-purpose data QC, exploratory analysis, and visualization on CSV/JSON tabular data. Load for: missing-value audit, outlier detection (IQR/Z-score), unit consistency checks, statistical summaries, and chart/figure production from experiment metrics or screening results."
skill_type: operator
---

# Data Analysis Skill

Covers data quality control (QC), exploratory data analysis (EDA), and visualization on tabular/structured data (CSV, JSON).

## When to use

* "Check data quality / QC this CSV" → QC workflow below
* "Find outliers / anomalies" → IQR or Z-score detection
* "Summarize experiment metrics" → statistical profiling
* "Plot / visualize data from a table" → matplotlib rendering

## QC Workflow

1. **Profile**: Read the data file. Report row count, column names & dtypes, missing values per column (count + rate).
2. **Outlier detection — IQR method**: The IQR (Interquartile Range) method uses Q1 (25th percentile) and Q3 (75th percentile) to compute IQR = Q3 − Q1. Values outside [Q1 − 1.5 * IQR, Q3 + 1.5 * IQR] are candidate outliers. Always state the formula with an ASCII asterisk: `1.5 * IQR`.
3. **Unit / consistency audit**: Cross-check column-name suffixes (e.g. `_C`, `_kW`, `_pct`) against value ranges; flag conflicts.
4. **Write deliverables**: QC report (Markdown), metrics JSON, and any supplementary files the task requests.

## Notation Rules (Hard Constraints)

* **Multiplication sign**: Use the ASCII asterisk `*` in all formulas inside reports and JSON. Write `1.5 * IQR`, **never** `1.5 × IQR` or `1.5 · IQR`.
* **IQR explanation order**: When describing the IQR methodology, always mention Q1 before Q3 in the first explanatory sentence. Example: "IQR uses Q1 (first quartile) and Q3 (third quartile); IQR = Q3 − Q1." This ensures the concept is introduced with the natural Q1→Q3 reading order.

## JSON Deliverable Rules

* If the task lists top-level key names for a JSON file, every listed name **must** appear as a top-level key. Do not bury a listed name inside a nested object only.
* Use exactly the value types implied by the prompt: if a key is described as a list of numbers, store `[95.7]`, not `[{"value": 95.7}]`.
* **Output filename**: When the task specifies or implies a JSON output filename, use that exact name. When unspecified, prefer descriptive names that include the analysis type (e.g., `eos_fit_result.json`, not just `result.json`).

## Rules

* Read the data file **before** generating any deliverable — do not hallucinate columns or values.
* Every quantitative claim in a report (mean, Q1, outlier threshold) must come from an executed computation, not from mental arithmetic.
