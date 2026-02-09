---
name: manuscript-scribe
description: "Specialized skill for WRITING long-form academic content (Papers, Grants, Reports). BYPASSES the chat output limit by writing directly to local Markdown/LaTeX files. Use when: (1) User asks to write or draft a paper, introduction, methods, results, or discussion. (2) User provides bullet points or data and wants a section written to a file. (3) User wants to polish or formalize an existing section. Do NOT use for short replies in chat—only when the deliverable is a file."
skill_type: operator
---

# Manuscript Scribe Skill

The "Ghostwriter" for MatMaster. Output is always to **files**; chat is only for instructions and progress.

## Step 0: Information retrieval (mandatory before any writing)

**Do not** call init_manuscript or write_section until you have run literature search for the topic. Writing **must** be grounded in retrieval:

- Call MCP retrieval tools (**mat_sn_search-papers-normal**, **mat_sn_scholar-search**, **mat_sn_web-search**) with queries derived from the section/title. Run at least a few searches (e.g. topic + "review", topic + "methods") before drafting.
- If the user did not provide source files or references, you **must** search the literature yourself and use the results as the basis for cited content. Do not write sections from memory only.

Then proceed with **search → summarize → append** and the three-step flow below.

## Search → summarize → append to temp file (recommended per-section flow)

After **each** search (or batch of searches) for a section:

1. **Agent LLM summarizes**: In the same or next turn, you (the agent) summarize the retrieved content in your own words, with citations `[n](URL)`.
2. **Append to a temp file**: Call `append_chunk.py --path "_tmp/section_<Name>.md" --content "..."` (or `--content_file`) to append that summary to a temporary file. Repeat for each search cycle so the temp file grows (intro, methods, etc.).
3. When the temp file for a section is complete, either pass it to `write_section.py --section "<Name>" --content_file "_tmp/section_<Name>.md" --draft draft.md`, or copy/merge into `sections/<Name>.md` for assembly.

So: **one search → agent LLM summary → append to temp file**; then concatenate into the long document (see below).

## Three-step flow: chunked writing → assemble → polish

1. **Chunked writing**: Draft each section in chunks. Either (a) use the temp-file flow above (append_chunk → then write_section from that file), or (b) use `write_section.py` to create a section and call it again with **`--append`** to add more paragraphs. Optionally use `init_manuscript.py` first to create the outline or `sections/` directory.
2. **Assemble**: Run `assemble_manuscript.py` to merge section files (or the single draft) into **one long document** and run the three checks (terms, abbreviations, references). Fix any reported issues.
3. **Polish (LLM point-by-point)**: After assembly, run `polish_text.py --file <assembled> --target_section <Name> --use_llm` for each section. With `--use_llm`, the script calls an LLM to revise the section point-by-point (grammar, redundancy, formality); env: LITELLM_PROXY_API_BASE, LITELLM_PROXY_API_KEY or OPENAI_API_BASE, OPENAI_API_KEY. Without `--use_llm`, only regex-based cleanup is applied.

Do not skip the assemble step: writing is chunked (or built in temp files), then concatenated into one long document, then polished with LLM.

**Delivery**: When the final manuscript is assembled and polished, **first output the complete final document** in your reply (message text) so the user sees it in the chat/frontend; then call finish. The .md file should already be written; your reply makes the document visible to the user. Do not only say "Saved to path" without outputting the document.

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

Full format details: use_skill get_reference with reference_name="citation_and_references.md" (not the skill name).

## Scripts

### `init_manuscript.py`

* **Required**: `--title` (no default). Always pass it to avoid script error.
* **Usage**: `python init_manuscript.py --title "My Paper" --template "Nature"`  
  Or with section files: `python init_manuscript.py --title "My Paper" --template "generic" --sections_dir sections/`
* **use_skill example**: script_name=init_manuscript.py, script_args="--title \"My Paper\""
* **Effect**: Creates a draft outline (Abstract, Introduction, Methods, Results, Discussion, References). With `--sections_dir`, creates empty section files under `sections/` so each section can be written and then assembled.

### `write_section.py` (workhorse)

* **Usage**:  
  Create or replace: `python write_section.py --section "Methods" --content_file "methods_notes.txt" --draft draft_manuscript.md`  
  Append to section: `python write_section.py --section "Introduction" --append --content "Next paragraph..." --draft draft_manuscript.md`  
  **For long sections** (bullets, multiple refs, 2+ paragraphs): always use **--content_file**; write content to a file first, then pass that path to avoid truncation of `--content` in tool args.
* **Logic**:
  * Writes the given content into the section (no expansion; script writes exactly what you pass). Use **`--append`** to add more paragraphs to an existing section so you can build it in chunks (multiple calls) instead of one long generation.
  * **Prefer --content_file for long content**: Inline --content is prone to truncation when passed via use_skill; for References, Summary, or any section with lists/long text, write to a temp file and use --content_file.
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

### `append_chunk.py`

* **Usage**: `python append_chunk.py --path "_tmp/section_Introduction.md" --content "Summarized paragraph..."` or `--content_file notes.txt`
* **Logic**: Appends the given content to the file (creates parent dirs and file if missing). Use after each "search → agent LLM summarize" cycle so the temp file accumulates; then use that file as `--content_file` for `write_section.py` or as input to assemble.

### `polish_text.py`

* **Usage**: `python polish_text.py --file "draft.md" --target_section "Introduction"` (regex-only) or `--file "draft.md" --target_section "Introduction" --use_llm` (LLM point-by-point revision).
* **Logic**: Reads the section. Without `--use_llm`: applies regex-based smoothing (removes "In this paper", "We show that"). With `--use_llm`: calls an OpenAI-compatible LLM to revise the section point-by-point (grammar, redundancy, formality) and overwrites the section; requires API key in env.

## When to use

* "Write the Introduction based on these bullet points." → `write_section.py` with content from user or file; include citations with links.
* "Draft the Methods section describing our VASP settings." → `write_section.py` (with VASP inputs as context); add references for methods.
* "Assemble the sections and check references." → `assemble_manuscript.py --sections_dir sections/ --output draft.md --validate`
* "The Results section is too colloquial." → `polish_text.py --use_llm`.
* "Start a new paper draft titled X." → `init_manuscript.py` (optionally with `--sections_dir`).

## Best practice

* Write **one section per file** (e.g. `sections/Introduction.md`), then run **assemble_manuscript** once all sections are ready. Fix any issues reported (undefined terms, duplicate abbreviations, broken or inconsistent references) before considering the draft complete.
* In chat: report progress and **file paths** only; never stream the full manuscript.

## Tool (via use_skill)

- **run_script** with **script_name**: `init_manuscript.py`, `write_section.py`, `append_chunk.py`, `assemble_manuscript.py`, or `polish_text.py`; **script_args** as in Usage above.

## Rules

* **Retrieval first**: Before any init_manuscript or write_section call, run literature search (mat_sn_* paper and web search) for the topic; do not write from memory only.
* **Required args**: init_manuscript.py always needs --title; pass it in script_args (e.g. script_args="--title \"My Paper\""). assemble_manuscript.py always needs **--output** and one of **--draft** or **--sections_dir** (e.g. script_args="--draft draft_manuscript.md --output final.md").
* **Long section content (critical)**: Section content passed via **--content** in script_args can be truncated by the tool layer (e.g. ~500–1000 chars). For any section longer than a short paragraph (lists, multiple refs, 2+ paragraphs), **write the content to a file first** (e.g. with str_replace_editor or execute_bash), then call `write_section.py --section "SectionName" --content_file path/to/section.md --draft draft_manuscript.md`. Do not rely on long --content strings for Summary, State-of-the-Art, or References.
* **Chunked writing**: Use multiple `write_section.py` calls per section (first call creates, further calls use `--append`) or build the full section in a file then pass with `--content_file`; the script does not expand short text.
* Citations: **text + hyperlink** to original source; References section must match in-text [n] exactly (see reference/citation_and_references.md).
* Always write long content to **files**; one section per call for `write_section.py`.
* Before finalizing, run `assemble_manuscript.py` with `--validate` and address term, abbreviation, and reference checks.
* Preferred long-form flow: after each search, summarize with the agent LLM and append to a temp file (`append_chunk.py`); then build sections from those files, assemble into one document, and run `polish_text.py --use_llm` for point-by-point revision.
