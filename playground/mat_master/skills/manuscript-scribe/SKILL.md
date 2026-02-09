---
name: manuscript-scribe
description: "Specialized skill for WRITING long-form academic content (Papers, Grants, Reports). BYPASSES the chat output limit by writing directly to local Markdown/LaTeX files. Use when: (1) User asks to write or draft a paper, introduction, methods, results, or discussion. (2) User provides bullet points or data and wants a section written to a file. (3) User wants to polish or formalize an existing section. Do NOT use for short replies in chat—only when the deliverable is a file."
skill_type: operator
---

# Manuscript Scribe Skill

The "Ghostwriter" for MatMaster. It manages **per-section drafts** (each section written separately), then **assembles** them into one manuscript and runs **three consistency checks**. Output is always to **files**; chat is only for instructions and progress (e.g. "Methods written to sections/Methods.md. Run assemble_manuscript next.").

## Information retrieval (mandatory before writing)

Writing sections **must** be grounded in information retrieval at a **level of detail** that matches the task (intro/background → broader search; methods/results → method- and data-focused search). **If the prompt does not specify sources or the user does not provide files**, you **must expand and search the literature yourself**: call MCP retrieval tools (e.g. `mat_sn_search-papers-normal`, `mat_sn_scholar-search`, `mat_sn_web-search`) with queries derived from the section topic, then use the results as the basis for cited, factual content. Do not write sections from memory or unsupported claims when no materials were given.

## Workflow (section-by-section then assemble)

Do **not** generate the whole paper in one turn. Use this process:

1. **Initialize**: Create outline and optionally a `sections/` directory (`init_manuscript.py`).
2. **Gather material**: For each section, if the user did not provide enough source files or the prompt lacks content, **run literature/search** (see above) at the appropriate level of detail; then draft from the retrieved material and cite it.
3. **Draft each section**: Write **one section at a time** to a section file (e.g. `sections/Introduction.md`, `sections/Methods.md`) or into a single draft file using `write_section.py`. Each section must follow **citation and reference rules** (see below).
4. **Assemble and validate**: Run `assemble_manuscript.py` to concatenate sections and run the **three checks**:
   - **Technical terms**: Are all domain terms defined at first use?
   - **Abbreviations**: Are all abbreviations defined exactly once (no duplicate definitions)?
   - **References**: Are all in-text citations present in the References section, and do all reference links point to valid original sources?
5. **Refine**: Use `polish_text.py` on specific sections if needed.

## Citation and references (mandatory)

- **In text**: Every cited claim must have the reference **immediately after** the relevant text. Use a **hyperlink**: `[n]` must link to the original source URL or to the References section entry (e.g. `[1](#ref-1)`). Example:  
  `Perovskite stability has been widely studied [1](https://doi.org/...).`
- **References section**: Must list **exactly** the same numbers as in the text, in order. Each entry must include the index [n], full citation (Authors, Title, *Journal*, Year), and the **original source URL**. No extra or missing entries.
- **Consistency**: At assembly time, the script checks that every in-text [n] has a matching [n] in References and that reference URLs are valid.

Full format details: **reference/citation_and_references.md**.

## Scripts

### `init_manuscript.py`

* **Usage**: `python init_manuscript.py --title "My Paper" --template "Nature"`  
  `python init_manuscript.py --title "My Paper" --template "generic" --sections_dir sections/`
* **Effect**: Creates a draft outline (Abstract, Introduction, Methods, Results, Discussion, References). With `--sections_dir`, creates empty section files under `sections/` so each section can be written and then assembled.

### `write_section.py` (workhorse)

* **Usage**: `python write_section.py --section "Methods" --content_file "methods_notes.txt" --tone "formal" --draft "draft_manuscript.md"`  
  Or write to a section file: `python write_section.py --section "Introduction" --content_file "intro_bullets.txt" --output "sections/Introduction.md"`
* **Logic**:
  * Takes raw notes or data (file or inline). Generates polished, **citation-backed** prose: every claim that needs a source must have `[n](URL)` or `[n](#ref-n)` and a corresponding entry in References.
  * Writes **one section only** (avoids token limits). When writing to a section file, the References for that section can be collected and merged at assemble time.
  * Output: either update a single `--draft` or write to `--output sections/SectionName.md`.

### `assemble_manuscript.py` (concatenate + review)

* **Usage**:  
  `python assemble_manuscript.py --sections_dir sections/ --output draft_manuscript.md --validate --report report.json`  
  Or from a single draft: `python assemble_manuscript.py --draft draft_manuscript.md --output final.md --validate`
* **Logic**:
  1. **Concatenate**: Merge section files (in order: Abstract, Introduction, Methods, Results, Discussion, References) or use the single draft as-is.
  2. **Check 1 – Technical terms**: Ensure all required terms (optional list via `--terms`) are defined at first use (heuristic: "X is defined as", "X refers to", or "Full Name (ABBR)").
  3. **Check 2 – Abbreviations**: Extract all "Full Name (ABBR)" / "ABBR (Full Name)"; report duplicate definitions; ensure no re-definition in later sections.
  4. **Check 3 – References**: Extract all [n] from body and from References section; ensure 1:1 correspondence; with `--validate`, check that each reference URL is reachable (HTTP HEAD).
* **Output**: Writes the assembled manuscript to `--output` and prints a short report; optionally writes `--report report.json`.

### `polish_text.py`

* **Usage**: `python polish_text.py --file "draft.md" --target_section "Introduction"`
* **Logic**: Reads the section, applies academic-English smoothing (removes redundant "In this paper", "We show that"), and overwrites the section.

## When to use

* "Write the Introduction based on these bullet points." → `write_section.py` with content from user or file; include citations with links.
* "Draft the Methods section describing our VASP settings." → `write_section.py` (with VASP inputs as context); add references for methods.
* "Assemble the sections and check references." → `assemble_manuscript.py --sections_dir sections/ --output draft.md --validate`
* "The Results section is too colloquial." → `polish_text.py`.
* "Start a new paper draft titled X." → `init_manuscript.py` (optionally with `--sections_dir`).

## Best practice

* Write **one section per file** (e.g. `sections/Introduction.md`), then run **assemble_manuscript** once all sections are ready. Fix any issues reported (undefined terms, duplicate abbreviations, broken or inconsistent references) before considering the draft complete.
* In chat: report progress and **file paths** only; never stream the full manuscript.

## Tool (via use_skill)

- **run_script** with **script_name**: `init_manuscript.py`, `write_section.py`, `assemble_manuscript.py`, or `polish_text.py`; **script_args** as in Usage above.

## Rules

* **Information retrieval**: Before writing a section, ensure you have enough source material. If the prompt or user did not provide files or references, **proactively run literature/search** (MCP paper and web search) at the appropriate level of detail; then write from the retrieved content with proper citations.
* Citations: **text + hyperlink** to original source; References section must match in-text [n] exactly (see reference/citation_and_references.md).
* Always write long content to **files**; one section per call for `write_section.py`.
* Before finalizing, run `assemble_manuscript.py` with `--validate` and address term, abbreviation, and reference checks.
