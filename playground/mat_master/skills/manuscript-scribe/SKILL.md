---
name: manuscript-scribe
description: "Specialized skill for WRITING long-form academic content (Papers, Grants, Reports). BYPASSES the chat output limit by writing directly to local Markdown/LaTeX files. Use when: (1) User asks to write or draft a paper, introduction, methods, results, or discussion. (2) User provides bullet points or data and wants a section written to a file. (3) User wants to polish or formalize an existing section. Do NOT use for short replies in chat—only when the deliverable is a file."
skill_type: operator
---

# Manuscript Scribe Skill

The "Ghostwriter" for MatMaster. Output is always to **files**; chat is only for instructions and progress.

## Three-step flow: chunked writing → assemble → polish

1. **Chunked writing**: Draft each section in chunks. Use `write_section.py` to create a section, then call it again with **`--append`** to add more paragraphs. Repeat until each section (Introduction, Methods, Results, Discussion) is complete. Optionally use `init_manuscript.py` first to create the outline or `sections/` directory.
2. **Assemble**: Run `assemble_manuscript.py` to merge section files (or the single draft) into one document and run the three checks (terms, abbreviations, references). Fix any reported issues.
3. **Polish**: Run `polish_text.py` on the assembled file for each section that needs it (e.g. `--target_section Introduction`, then Methods, Results, Discussion). This step smooths language and removes redundancy; run after assembly so the full context is in one file.

Do not skip the assemble step: writing is chunked, then concatenated, then polished.

## Information retrieval (mandatory before writing)

Writing sections **must** be grounded in information retrieval at a **level of detail** that matches the task (intro/background → broader search; methods/results → method- and data-focused search). **If the prompt does not specify sources or the user does not provide files**, you **must expand and search the literature yourself**: call MCP retrieval tools (e.g. `mat_sn_search-papers-normal`, `mat_sn_scholar-search`, `mat_sn_web-search`) with queries derived from the section topic, then use the results as the basis for cited, factual content. Do not write sections from memory or unsupported claims when no materials were given.

## Chunked writing (how to get substantial sections)

The script `write_section.py` does **not** expand short text; it writes exactly what you pass. To get substantial sections without generating the whole section in one long turn:

1. **First call** for a section: create it with the first paragraph(s), e.g. `write_section.py --section "Introduction" --content_file "intro_p1.txt" --output sections/Introduction.md` (or `--draft draft.md`).
2. **Later calls** for the same section: use **`--append`** to add more paragraphs or chunks, e.g. `write_section.py --section "Introduction" --append --content "Next paragraph..." --draft draft.md`. Repeat for each new chunk (e.g. 3–5 paragraphs per section).
3. You can also build the section in a **temporary file** (e.g. with create/edit, appending paragraph by paragraph), then pass that file once with `--content_file`.

So substantial length comes from **multiple** write_section calls (create + append) or from building a full section file before calling the script once. Do not rely on a single call with one short paragraph.

## Detail (before and during step 1)

- **Initialize** (optional): Create outline or `sections/` with `init_manuscript.py`.
- **Gather material**: For each section, if the user did not provide source files or the prompt lacks content, **run literature/search** (see above) at the appropriate level of detail; then draft from the retrieved material and cite it.
- **Citation**: Each section must follow the citation and reference rules below; assemble_manuscript validates them in step 2.

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

* **Usage**:  
  Create or replace: `python write_section.py --section "Methods" --content_file "methods_notes.txt" --draft draft_manuscript.md`  
  Append to section: `python write_section.py --section "Introduction" --append --content "Next paragraph..." --draft draft_manuscript.md`
* **Logic**:
  * Writes the given content into the section (no expansion; script writes exactly what you pass). Use **`--append`** to add more paragraphs to an existing section so you can build it in chunks (multiple calls) instead of one long generation.
  * Citation-backed prose: every claim that needs a source must have `[n](URL)` or `[n](#ref-n)` and a corresponding entry in References.
  * Output: update `--draft` or write to `--output sections/SectionName.md`.

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
* **Chunked writing**: Use multiple `write_section.py` calls per section (first call creates, further calls use `--append`) or build the full section in a file then pass with `--content_file`; the script does not expand short text.
* Citations: **text + hyperlink** to original source; References section must match in-text [n] exactly (see reference/citation_and_references.md).
* Always write long content to **files**; one section per call for `write_section.py`.
* Before finalizing, run `assemble_manuscript.py` with `--validate` and address term, abbreviation, and reference checks.
