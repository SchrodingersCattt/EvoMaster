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

## Rules

* Read the data file **before** generating any deliverable — do not hallucinate columns or values.
* Every quantitative claim in a report (mean, Q1, outlier threshold) must come from an executed computation, not from mental arithmetic.

## Visualization / Plotting Workflow

When a task requires figures or plots from data:

1. **Write a self-contained Python script** (e.g., `plot_qc.py`) that reads the data and produces the figure.
2. **Use matplotlib** with `Agg` backend for headless environments:
   ```python
   import matplotlib
   matplotlib.use('Agg')
   import matplotlib.pyplot as plt
   ```
3. **Save figures as PNG** (default) or PDF. Always call `plt.savefig(filename, dpi=150, bbox_inches='tight')`.
4. **Common QC plots**:
   - **Box plots**: Show distribution per numeric column (outlier visualization)
   - **Histograms**: Show value distribution for key variables
   - **Scatter plots**: Show relationships between variables
   - **Time series / trend lines**: If data has temporal ordering
   - **Bar charts with error bars**: For summary statistics across groups
5. **Label everything**: title, axis labels with units, legend if multiple series. Use readable font sizes (≥10pt).
6. **Multiple subplots**: Use `plt.subplots(nrows, ncols)` for multi-panel figures. One figure with multiple panels is often better than many separate files.
7. **Verify output**: After running the script, confirm the PNG/PDF file exists and has non-zero size.

### Deliverable Completeness Checklist
- [ ] QC report (Markdown) — with all required sections
- [ ] Metrics JSON — with all task-specified top-level keys
- [ ] Figures (PNG/PDF) — saved to disk, referenced in report
- [ ] All files written before task completion
- [ ] JSON files are parseable (`json.loads()` succeeds)
- [ ] Markdown files are non-empty and well-structured
