---
name: manuscript-scribe
description: "Specialized skill for WRITING long-form academic content (Papers, Grants, Reports, Patents, Theses). BYPASSES the chat output limit by writing directly to local Markdown files, with optional Word (.docx) export. Use when: (1) User asks to write or draft a paper, report, patent, thesis chapter, or computational write-up. (2) User provides bullet points or data and wants sections written to files. (3) User wants to polish or formalize existing sections. Do NOT use for short replies in chat—only when the deliverable is a file."
skill_type: operator
---

<!-- multi-server: mat_sn, mat_doc -->

# Manuscript Scribe Skill

The "Ghostwriter" for MatMaster. Primary output is **files**. In planner mode, full-document chat output is expected for final delivery; in direct mode, default to concise summary + file path unless the user asks for full text.

**When NOT to use**: If the user is only asking a technical question (e.g. "what is X?", "how does Y work?") and does **not** ask for a paper/report/写一篇/输出到文件, do **not** invoke this skill. Use 1–2 mat_sn or web searches, answer in chat, and finish. Use manuscript-scribe only when the deliverable is clearly a **file** (paper, report, section to file).

## Format profiles

The skill supports multiple document types via **format profiles**. Always select the profile matching the user's request:

| Profile | Sections | Use Case | Min words |
|---|---|---|---|
| `research_paper` | Abstract, Introduction, Methods, Results, Discussion, References | Standard academic paper (default IMRaD order; Nature-style variant: Methods after Discussion) | 3000 |
| `grant` | Summary/Abstract, Significance, Approach, Preliminary Results, Timeline, References | Grant proposal | 2500 |
| `computational_report` | Methods, Results and Discussion, References | Lean DFT/MD/simulation write-up | 800 |
| `patent` | Technical Field, Background Art, Summary of Invention, Detailed Description, Claims, Abstract | Patent application | 3000 |
| `review` | Abstract, Introduction, Scope and Methodology, State of the Art, Critical Analysis, Future Directions, Conclusions, References | Review article | 6000 |
| `technical_report` | Executive Summary, Introduction, Methodology, Findings, Analysis and Discussion, Recommendations, Appendices, References | Technical/engineering report | 3000 |
| `thesis_section` | Introduction, Literature Review, Methodology, Results, Discussion, Conclusion, References | Single thesis chapter | 5000 |

To list all profiles with details: `init_manuscript.py --list_formats`

**Profile selection rules:**
- Computational study output (DFT, MD, phonons, etc.): use `computational_report`
- Patent application: use `patent`
- Thesis chapter: use `thesis_section`
- Literature review / survey: use `review` for full review articles (6000+ words). When the caller is **deep-survey** (5-section output: Executive Summary, Key Methodologies, State of the Art, Gap Analysis, References), use **`literature_review`** profile only, not `review`. This skill does not trigger deep-survey.
- Standard paper: use `research_paper`. For Nature-style ordering (Methods after Discussion), still use `research_paper` — the section order can be resequenced at assembly time.
- Default to `research_paper` if unclear.

**Profile-specific writing rules**: After selecting a profile, check if `prompts/<profile>.md` exists in this skill's folder; if so, read it for profile-specific writing guidance (De-AIGC rules, formula requirements, claims conventions, etc.) before drafting any section.

## Step 0: Information retrieval (mandatory before any writing)

**Do not** call init_manuscript or write_section until you have run literature search for the topic. Writing **must** be grounded in retrieval:

- Call available MCP retrieval tools (e.g. **mat_sn_search-papers-enhanced**, **mat_sn_scholar-search**, **web-search**, or any other available search tool) with queries derived from the section/title. Run at least a few searches (e.g. topic + "review", topic + "methods") before drafting. If a tool returns errors or is unavailable, switch to a different available search tool or method — do not retry the same failing tool.
- If the user did not provide source files or references, you **must** search the literature yourself and use the results as the basis for cited content. Do not write sections from memory only.
- **Exception**: For `computational_report`, retrieval focuses on method references (software, functional, basis set citations). If the user provides all computational parameters, method retrieval is optional.

Then proceed with **search → summarize → append** and the four-step flow below.

## Search → summarize → append to temp file (recommended per-section flow)

After **each** search (or batch of searches) for a section:

1. **Agent LLM summarizes**: In the same or next turn, you (the agent) summarize the retrieved content in your own words, with citations `[n](URL)`.
2. **Append to a temp file**: Call `append_chunk.py --path "_tmp/section_<Name>.md" --content "..."` (or `--content_file`) to append that summary to a temporary file. Repeat for each search cycle so the temp file grows (intro, methods, etc.).
3. When the temp file for a section is complete, either pass it to `write_section.py --section "<Name>" --content_file "_tmp/section_<Name>.md" --draft draft.md`, or copy/merge into `sections/<Name>.md` for assembly.

So: **one search → agent LLM summary → append to temp file**; then concatenate into the long document (see below).

## Four-step flow: chunked writing → validate → assemble → polish (→ export)

1. **Chunked writing**: Draft each section in chunks. Either (a) use the temp-file flow above (append_chunk → then write_section from that file), or (b) use `write_section.py` to create a section and call it again with **`--append`** to add more paragraphs. Optionally use `init_manuscript.py --template <profile>` first to create the outline or `sections/` directory.
2. **Validate**: Run `validate_content.py --draft draft.md --profile <profile>` to check word counts, completeness, required elements, and information flow. Fix any sections that are under the minimum word count or missing required elements. In planner mode, use `--planner_mode` for stricter thresholds (1.5x base minimums).
3. **Assemble**: Run `assemble_manuscript.py` to merge section files (or the single draft) into **one long document** and run the three consistency checks (terms, abbreviations, references). Use `--profile <profile>` for correct section ordering and `--check_length` for word-count validation. Fix any reported issues.
4. **Polish (LLM point-by-point)**: After assembly, run `polish_text.py --file <assembled> --target_section <Name> --use_llm` for each section. With `--use_llm`, the script calls an LLM to revise the section point-by-point (grammar, redundancy, formality); env: LITELLM_PROXY_API_BASE, LITELLM_PROXY_API_KEY or OPENAI_API_BASE, OPENAI_API_KEY. Without `--use_llm`, only regex-based cleanup is applied.
5. **Export (automatic)**: `assemble_manuscript.py` now auto-exports to all formats by default (`--export all`). After assembly, it produces `.tex` + `.bib` (LaTeX, no deps) and `.docx` (Word, requires `python-docx`; gracefully skipped if not installed). Use `--export md` to skip exports, or `--export docx` / `--export latex` for a single format.

Do not skip the validate and assemble steps: writing is chunked (or built in temp files), validated for length/completeness, then concatenated into one long document, then polished with LLM.

## Resumable pipeline (recommended for long tasks)

For long-running writing tasks, keep persistent workflow state:

- Use `run_pipeline.py --state _tmp/manuscript/state.json --resume` for stage orchestration/checkpoints.
- Pass `--state _tmp/manuscript/state.json --resume` to `write_section.py`, `validate_content.py`, and `assemble_manuscript.py`.
- Structured stage status is emitted as `LONGTASK_RESULT_JSON: {...}` for reliable planner/guard parsing.

**Delivery (mode-aware)**:
- **Planner mode**: When the final manuscript is assembled and polished, output the complete final document in chat, then call finish.
- **Direct mode**: Default to concise summary + file path. Output full document text only when the user explicitly asks for full content in chat.

## Chunked writing (how to get substantial sections)

The script `write_section.py` does **not** expand short text; it writes exactly what you pass. To get substantial sections without generating the whole section in one long turn:

1. **First call** for a section: create it with the first paragraph(s), e.g. `write_section.py --section "Introduction" --content_file "intro_p1.txt" --output sections/Introduction.md` (or `--draft draft.md`).
2. **Later calls** for the same section: use **`--append`** to add more paragraphs or chunks, e.g. `write_section.py --section "Introduction" --append --content "Next paragraph..." --draft draft.md`. Repeat for each new chunk (e.g. 3–5 paragraphs per section).
3. You can also build the section in a **temporary file** (e.g. with create/edit, appending paragraph by paragraph), then pass that file once with `--content_file`.
4. **Use --profile** to get word-count feedback: `write_section.py --section "Methods" --content_file m.txt --draft d.md --profile computational_report`. The script will warn if the section is below the profile minimum.

**Word count targets** (for planner mode, aim for 1.5x these):
- Abstract: 150–300 words
- Introduction: 500+ words
- Methods: 400+ words (200+ for computational_report)
- Results: 500+ words (300+ for computational_report's "Results and Discussion")
- Discussion: 500+ words
- Literature Review (thesis): 1000+ words
- State of the Art (review): 2000+ words

So substantial length comes from **multiple** write_section calls (create + append) or from building a full section file before calling the script once. Do not rely on a single call with one short paragraph.

## Planner mode guidelines

When the ResearchPlanner creates steps for manuscript writing, the executor MUST follow these rules:

1. **Per-section steps**: Each section should be its own step (not one monolithic "write the paper" step).
2. **Multiple search cycles per section**: Each section step should include at least 2–3 search-summarize-append cycles before calling write_section. This ensures content depth.
3. **Validate before assembly**: After all sections are written, run `validate_content.py --planner_mode` to enforce stricter word-count thresholds (1.5x base minimums).
4. **Fix shortfalls**: If validate reports sections under minimum, append more content before proceeding to assembly.

## Detail (before and during step 1)

- **Initialize** (optional): Create outline or `sections/` with `init_manuscript.py --template <profile>`.
- **Gather material**: For each section, if the user did not provide source files or the prompt lacks content, **run literature/search** (see above) at the appropriate level of detail; then draft from the retrieved material and cite it.
- **Citation**: Each section must follow the citation and reference rules below; assemble_manuscript validates them in step 3.

## Concept explanation and conceptual rigor (mandatory)

Academic writing must not skip definitions or leave formulas unexplained.

- **Definitions**: Give a **solid, precise definition** for every key concept at first use. Do not assume the reader knows the term.
- **Formulas**: When you include an equation, **explain every physical quantity/symbol** (e.g. "where *E* is the energy, *k* is the Boltzmann constant"). Do not leave symbols unexplained.
- **Concept relationships**: State **how concepts relate**—dependence, contrast, hierarchy, or causal link. Do not list concepts in isolation.
- **Examples (optional)**: Where helpful, illustrate with concrete examples from retrieval (specific material, method, or result).

## Typographic style (mandatory for all manuscript-scribe output)

The export scripts (`export_docx.py`, `export_latex.py`) apply many of these automatically, but the **LLM must produce correctly formatted Markdown** for the conversion to work.

### Terminology
- **Define every abbreviation** at first use: "density functional theory (DFT)", "projected density of states (PDOS)". After the definition, use only the abbreviation.

### Physical quantities & font conventions in Markdown
- **Italic for scalar physical quantities**: `*U*`, `*E*`, `*k*`, `*T*`, `*V*`. Bold for vectors: `**F**`, `**k**`.
- **Subscripts**: `_{text}` notation: `*U*_{eff}`, `*E*_{F}`, `*E*_{g}`, `*k*_{B}`. Export scripts convert to Word subscript / LaTeX `_{\mathrm{...}}`.
- **Superscripts**: `^{text}` notation: `10^{−6}`, `cm^{−1}`, `Å^{2}`. Export scripts convert to Word superscript / LaTeX `^{}`.
- **Descriptive subscripts are roman** (upright), not italic. Examples: *U*_{eff} — *U* italic, "eff" roman; *E*_{F} — "F" roman (Fermi); *E*_{g} — "g" roman (gap); *k*_{B} — "B" roman (Boltzmann).
- Physical quantity symbols: **always italic** (*U*, *E*, *T*, *k*, *V*, *P*, *H*, *G*, *S*).
- **No Unicode fake superscripts/subscripts**: Do NOT use `³`, `₂`, `⁻⁶`. Use `^{3}`, `_{2}`, `^{−6}`. Exception: in pure Markdown output (no export), Unicode is acceptable as a fallback.

### Symbols: dash and minus
- **Range / connection: en-dash "–"** (U+2013), NOT hyphen "-". Examples: `1.88–1.89 Å`, `pp. 57–70`, `Cu–N bond`.
- **Negative sign: minus "−"** (U+2212), NOT hyphen. Examples: `−6`, `−0.5 eV`, `10^{−6}`.
- **Hyphen "-"** only for compound adjectives: `self-consistent`, `plane-wave`, `Broyden-type`.
- The LLM must use the correct Unicode characters (–, −) in the Markdown source. The export scripts preserve them.

### Chemical formula formatting
- **Element order**: Hill system — C first, H second, then remaining elements alphabetically. For inorganic: electropositive first. Examples: `CH_{4}`, `C_{79}H_{88}N_{23}O_{12}S_{16}Cu_{19}`.
- **No spaces** between elements in a formula.
- **Element counts as subscripts**: Write `C_{7}H_{8}N_{2}O`. Export scripts convert `_{n}` to proper subscripts. For simple inline mentions, export scripts also auto-detect patterns like `CO2`, `H2O`, `Fe2O3`.

### Significant figures
- **Match precision to method accuracy**: DFT bond lengths 2 decimals (1.89 Å, not 1.893742 Å), band gaps 2 decimals (2.34 eV), lattice parameters 3–4 decimals.
- **Use "≈"** for approximate values: "the band gap is ≈ 2.3 eV".
- Do not pile unnecessary decimals: `5 × 10^{−6}` not `5.000000 × 10^{−6}`.

### Reference list format
- `[n] Authors. Title. *Journal*, **Year**, Volume, Pages. URL`
- **Journal name**: italic (*J. Chem. Phys.*). **Year**: bold (**2020**). **Page range**: en-dash (477–506, not 477-506). Article numbers acceptable (e.g. 194103).
- Export scripts auto-format: detect journal names (italic), years (bold), page ranges (en-dash).

### Quick reference: LLM vs export responsibilities

| Rule | LLM writes (Markdown) | Export script handles |
|------|----------------------|---------------------|
| Italic physics quantities | `*E*`, `*U*` | Convert to italic font |
| Subscripts | `_{eff}`, `_{2}` | Word subscript / LaTeX `_{\mathrm{}}` |
| Superscripts | `^{−6}`, `^{2}` | Word superscript / LaTeX `^{}` |
| Chemical formula subscripts | `C_{7}H_{8}` or `CO2` | Auto-detect and subscript numbers |
| En-dash for ranges | `–` (U+2013) | Preserve |
| Minus sign | `−` (U+2212) | Preserve |
| No raw keywords | Describe physically | N/A |
| No file paths | Use generic descriptions | N/A |
| HOCO/LUCO terminology | Use correct terms | N/A |
| Reference formatting | `*Journal*, **Year**, Vol, Pages` | Auto-detect and format |
| Significant figures | Round appropriately | N/A |

## Citation and references (mandatory)

- **In text**: Every cited claim must have the reference **immediately after** the relevant text. Use a **hyperlink**: `[n]` must link to the original source URL or to the References section entry (e.g. `[1](#ref-1)`). Example:
  `Perovskite stability has been widely studied [1](https://doi.org/...).`
- **References section**: Must list **exactly** the same numbers as in the text, in order. Each entry must include the index [n], full citation (Authors, Title, *Journal*, Year), and the **original source URL**. No extra or missing entries.
- **Consistency**: At assembly time, the script checks that every in-text [n] has a matching [n] in References and that reference URLs are valid.

Full format details are in `citation_and_output_format.md`, which is already injected into your system prompt. You do not need to fetch it separately.

## Scripts

### `init_manuscript.py`

* **Required**: `--title` (no default). Always pass it to avoid script error.
* **Usage**: `python init_manuscript.py --title "My Paper" --template "research_paper"`
  Or with section files: `python init_manuscript.py --title "My Paper" --template "research_paper" --sections_dir sections/`
  List profiles: `python init_manuscript.py --list_formats`
* **use_skill example**: script_name=init_manuscript.py, script_args="--title \"My Paper\" --template computational_report"
* **Effect**: Creates a draft outline based on the selected format profile. With `--sections_dir`, creates empty section files under `sections/` and writes `_profile.json` with profile metadata so downstream scripts can auto-detect the profile.
* **Templates**: research_paper, grant, computational_report, patent, review, technical_report, thesis_section.

### `write_section.py` (workhorse)

* **Usage**:
  Create or replace: `python write_section.py --section "Methods" --content_file "methods_notes.txt" --draft draft_manuscript.md`
  Append to section: `python write_section.py --section "Introduction" --append --content "Next paragraph..." --draft draft_manuscript.md`
  With profile check: `python write_section.py --section "Methods" --content_file m.txt --draft d.md --profile computational_report`
  **For long sections** (bullets, multiple refs, 2+ paragraphs): always use **--content_file**; write content to a file first, then pass that path to avoid truncation of `--content` in tool args.
* **Logic**:
  * Writes the given content into the section (no expansion; script writes exactly what you pass). Use **`--append`** to add more paragraphs to an existing section so you can build it in chunks (multiple calls) instead of one long generation.
  * **--profile**: When set, prints word count after writing and warns if below profile minimum.
  * **--min_words**: Explicit floor (overrides profile).
  * **Prefer --content_file for long content**: Inline --content is prone to truncation when passed via use_skill; for References, Summary, or any section with lists/long text, write to a temp file and use --content_file.
  * Citation-backed prose: every claim that needs a source must have `[n](URL)` or `[n](#ref-n)` and a corresponding entry in References.
  * Output: update `--draft` or write to `--output sections/SectionName.md`.

### `validate_content.py` (quality gate)

* **Usage**:
  `python validate_content.py --draft draft.md --profile research_paper`
  `python validate_content.py --sections_dir sections/ --profile computational_report --planner_mode --report report.json`
* **Logic**:
  1. **Word counts**: Per-section and overall against profile minimums. With `--planner_mode`, thresholds are 1.5x.
  2. **Completeness**: Flags sections still containing `(TBD)` or empty body.
  3. **Required elements**: Heuristic check that each section mentions expected topics (e.g. Introduction should mention background and gap).
  4. **Information flow**: Checks cross-section term overlap (Methods terms should appear in Results, Results terms in Discussion).
  5. **Section ordering**: Verifies sections match profile's expected order.
* **Output**: Prints summary; optionally writes JSON report to `--report`.
* **Status contract**: Emits `LONGTASK_RESULT_JSON` with `status=completed` (pass) or `status=retryable_error` (issues to fix).

### `assemble_manuscript.py` (concatenate + review)

* **Usage**:
  `python assemble_manuscript.py --sections_dir sections/ --output draft_manuscript.md --validate --profile research_paper --check_length`
  Or from a single draft: `python assemble_manuscript.py --draft draft_manuscript.md --output final.md --validate`
* **Logic**:
  1. **Concatenate**: Merge section files (in profile section order, or default) or use the single draft as-is.
  2. **Word count summary**: Always prints per-section and total word counts.
  3. **Check 1 – Technical terms**: Ensure all required terms (optional list via `--terms`) are defined at first use.
  4. **Check 2 – Abbreviations**: Extract all "Full Name (ABBR)"; report duplicate definitions.
  5. **Check 3 – References**: Extract all [n] from body and References section; ensure 1:1 correspondence; with `--validate`, check URLs via HTTP HEAD.
  6. **Check 4 – Content validation** (with `--check_length --profile`): Run full word-count and content validation.
* **--profile**: Use the profile's section order instead of the default (Abstract, Intro, Methods, Results, Discussion, References). For `research_paper`, the default order is IMRaD; for Nature-style ordering, resequence sections at assembly time.
* **Output**: Writes the assembled manuscript to `--output` and prints reports; optionally writes `--report report.json`.
* **Status contract**: Emits `LONGTASK_RESULT_JSON` with `status=completed` (pass) or `status=retryable_error` (consistency/content issues to fix).

### `run_pipeline.py` (resumable stage orchestrator)

* **Usage**:
  - Initialize: `python run_pipeline.py --stage init --title "My Paper" --profile research_paper --state _tmp/manuscript/state.json`
  - Checkpoint: `python run_pipeline.py --stage checkpoint --next_stage draft --state _tmp/manuscript/state.json --resume`
  - Validate stage: `python run_pipeline.py --stage validate --profile research_paper --draft draft.md --planner_mode --state _tmp/manuscript/state.json --resume`
  - Assemble stage: `python run_pipeline.py --stage assemble --profile research_paper --draft draft.md --output final.md --export all --state _tmp/manuscript/state.json --resume`
  - Status: `python run_pipeline.py --stage status --state _tmp/manuscript/state.json --resume`
* **Logic**: Persists `state.json` + `events.jsonl`, proxies validate/assemble stages, and emits structured status for resume-friendly orchestration.

### `append_chunk.py`

* **Usage**: `python append_chunk.py --path "_tmp/section_Introduction.md" --content "Summarized paragraph..."` or `--content_file notes.txt`
* **Logic**: Appends the given content to the file (creates parent dirs and file if missing). Use after each "search → agent LLM summarize" cycle so the temp file accumulates; then use that file as `--content_file` for `write_section.py` or as input to assemble.

### `polish_text.py`

* **Usage**: `python polish_text.py --file "draft.md" --target_section "Introduction"` (regex-only) or `--file "draft.md" --target_section "Introduction" --use_llm` (LLM point-by-point revision).
* **Logic**: Reads the section. Without `--use_llm`: applies regex-based smoothing (removes "In this paper", "We show that"). With `--use_llm`: calls an OpenAI-compatible LLM to revise the section point-by-point (grammar, redundancy, formality) and overwrites the section; requires API key in env.

### `export_docx.py` (Word export)

* **Usage**: `python export_docx.py --input final.md --output manuscript.docx`
  With style template: `python export_docx.py --input final.md --output manuscript.docx --style_template template.docx`
* **Requires**: `python-docx` (install via `pip install python-docx` or the project's `docx` optional dependency).
* **Logic**: Converts Markdown to Word format. Smart scientific formatting:
  - Headings (#/##/###), bold, italic, code, bullet/numbered lists, tables.
  - Citations ([n](url) → superscript hyperlink).
  - **Chemical formula subscripts**: Auto-detects `CO2`, `H2O`, `Fe2O3` → proper Word subscript.
  - **Physics subscripts**: `_{eff}`, `_{F}` → Word subscript formatting.
  - **Physics superscripts**: `^{−6}`, `^{2}` → Word superscript formatting.
  - **Reference formatting**: Auto-detects journal names (italic), years (bold), page ranges (en-dash).
  - En-dash and minus sign: preserved as correct Unicode.
  - Times New Roman 11pt, standard margins, page numbers.
* **use_skill example**: script_name=export_docx.py, script_args="--input final.md --output manuscript.docx"

### `export_latex.py` (LaTeX export)

* **Usage**: `python export_latex.py --input final.md --output manuscript.tex --bibfile refs.bib`
* **Requires**: No external dependencies (pure Python string conversion).
* **Logic**: Converts Markdown to LaTeX format. Smart formatting:
  - Headings, bold, italic, code → LaTeX commands.
  - Citations → `\textsuperscript{\href{url}{[n]}}`.
  - Chemical formulas: auto-detected → `CO$_{2}$`.
  - Subscripts `_{text}` → `$_{\mathrm{text}}$` (roman subscript).
  - Superscripts `^{text}` → `$^{text}$`.
  - Markdown tables → LaTeX `tabular`.
  - Optional BibTeX file generation from References section.
* **use_skill example**: script_name=export_latex.py, script_args="--input final.md --output manuscript.tex --bibfile refs.bib"

## When to use

* "Write the Introduction based on these bullet points." → `write_section.py` with content from user or file; include citations with links.
* "Draft the Methods section describing our VASP settings." → `write_section.py` (with VASP inputs as context); add references for methods.
* "Write up the DFT calculation results." → `init_manuscript.py --template computational_report`, then `write_section.py` for Methods and Results and Discussion.
* "Draft a patent for this material." → `init_manuscript.py --template patent`, then write each section.
* "Write a thesis chapter on X." → `init_manuscript.py --template thesis_section`, then write each section.
* "Assemble the sections and check references." → `assemble_manuscript.py --sections_dir sections/ --output draft.md --validate --profile research_paper`
* "Validate my draft length." → `validate_content.py --draft draft.md --profile research_paper`
* "The Results section is too colloquial." → `polish_text.py --use_llm`.
* "Export to Word." → Automatically done by `assemble_manuscript.py --export all` (or standalone `export_docx.py`).
* "Export to LaTeX." → Automatically done by `assemble_manuscript.py --export all` (or standalone `export_latex.py`).
* "Start a new paper draft titled X." → `init_manuscript.py` (optionally with `--sections_dir` and `--template`).

## Best practice

* Write **one section per file** (e.g. `sections/Introduction.md`), then run **validate_content** to check length/completeness, then run **assemble_manuscript** once all sections are ready. Fix any issues reported (short sections, undefined terms, duplicate abbreviations, broken or inconsistent references) before considering the draft complete.
* In chat: report progress and **file paths** only; never stream the full manuscript.
* **Always specify --profile** when calling init_manuscript, write_section, validate_content, and assemble_manuscript so that format-specific section ordering and word-count thresholds apply.

## Tool (via use_skill)

- **run_script** with **script_name**: `init_manuscript.py`, `write_section.py`, `append_chunk.py`, `validate_content.py`, `assemble_manuscript.py`, `run_pipeline.py`, `polish_text.py`, `export_docx.py`, or `export_latex.py`; **script_args** as in Usage above.

## De-AIGC Writing Rules (mandatory for all profiles except the patent Claims section)

Full reference: `use_skill action=get_reference reference_name="de_aigc_style_guide.md"` (in `skills/_common/reference/`).

**Core rules (apply at every writing step):**
1. Lead with the real problem, not broad context.
2. Prefer concrete verbs over abstract labels (`integrate A with B`, not `establish a framework`).
3. Calibrate claims to evidence — match confidence to what is directly shown.
4. Remove filler openers: delete `Notably,`, `Significantly,`, `It is worth noting that`, `Importantly,`, `In this context,`.
5. Replace vague statistics with named ones (MAD, RMSD, STD); add boundary conditions.
6. Goal text per sentence: one main point only; split compound observations.

**High-risk openers to delete on sight**: `It is well known that`, `This section reviews`, `It should be noted that`, `Paves the way for`, `Groundbreaking`, `Unprecedented`, `This work fosters`, `Showcasing`, `Highlighting`.

**After drafting each section**, run the 5-pass De-AIGC checklist (claim calibration → specificity upgrade → compression → redundancy removal → tone scan). See full guide for pattern rewrites and rewrite frames.

**Patent exception**: De-AIGC rules apply to all sections **except Claims**. Claims language is intentionally formal/repetitive ("comprising", "wherein") — do NOT apply De-AIGC passes to the Claims section. All other sections (Technical Field, Background Art, Summary, Detailed Description, Abstract) follow De-AIGC rules normally. See `prompts/patent.md`.

## Rules

* **Retrieval first**: Before any init_manuscript or write_section call, run literature search (mat_sn_* paper and web search) for the topic; do not write from memory only. Exception: `computational_report` with user-provided parameters.
* **Required args**: init_manuscript.py always needs --title; pass it in script_args (e.g. script_args="--title \"My Paper\" --template research_paper"). assemble_manuscript.py always needs **--output** and one of **--draft** or **--sections_dir** (e.g. script_args="--draft draft_manuscript.md --output final.md").
* **Long section content (critical)**: Section content passed via **--content** in script_args can be truncated by the tool layer (e.g. ~500–1000 chars). For any section longer than a short paragraph (lists, multiple refs, 2+ paragraphs), **write the content to a file first** (e.g. with str_replace_editor or execute_bash), then call `write_section.py --section "SectionName" --content_file path/to/section.md --draft draft_manuscript.md`. Do not rely on long --content strings for Summary, State-of-the-Art, or References.
* **Chunked writing**: Use multiple `write_section.py` calls per section (first call creates, further calls use `--append`) or build the full section in a file then pass with `--content_file`; the script does not expand short text.
* **Long-task state (recommended)**: Use `--state _tmp/manuscript/state.json --resume` on write/validate/assemble stages for deterministic resume.
* **Profile**: Always pass `--profile <name>` to init_manuscript, write_section, validate_content, and assemble_manuscript. Use `computational_report` for DFT/MD write-ups, `patent` for patent apps, `thesis_section` for thesis chapters, etc.
* Citations: **text + hyperlink** to original source; References section must match in-text [n] exactly (see `citation_and_output_format.md` already in your system prompt).
* Always write long content to **files**; one section per call for `write_section.py`.
* **User uploads (mandatory)**: If the user uploads files or provides documents in the workspace, you MUST **fully parse/read every such file** before writing any section. Do **not** start writing until all uploaded/workspace PDFs (and other docs) have been completely read. For PDFs, use MCP document tools (mat_doc_*) for full-text extraction; do not skip or only skim.
* Before finalizing, run `validate_content.py` then `assemble_manuscript.py` with `--validate` and address term, abbreviation, reference, and word-count checks.
* Preferred long-form flow: after each search, summarize with the agent LLM and append to a temp file (`append_chunk.py`); then build sections from those files, validate, assemble into one document, and run `polish_text.py --use_llm` for point-by-point revision.
* **Export**: `assemble_manuscript.py` auto-exports to `.tex`+`.bib` and `.docx` by default (`--export all`). Word export requires `python-docx` (gracefully skipped if missing). Use `--export md` to skip.
* **Typographic rules**: Follow the mandatory typographic style section above. The export scripts handle Word subscript/superscript and chemical formula formatting, but the LLM must write correct Markdown notation (`_{text}`, `^{text}`, en-dash "–", minus "−").
