---
name: research-loop
description: "Use when the user asks to search literature, find tutorials/methods, or answer conceptual questions—including: 检索(某主题)研究进展, 为什么, 如何, 是什么, 查文献, 找方法, 解释某概念. First call use_skill with action=get_info or get_reference (not run_script; no scripts). Then run a multi-angle loop (web + papers), at least 2–3 aspects; for 研究进展/文献 must use paper search; then synthesize."
skill_type: operator
license: null
---

# Research-Loop Skill

Use this skill for **searching literature**, **finding tutorials/methods**, and **answering professional or conceptual questions**. The core behavior is to **search from multiple angles of one concept (web + literature), loop until coverage is sufficient, then synthesize and answer**—avoid finishing after a single search.

## When to Use (Trigger Phrases and Question Types)

Use this skill whenever the task matches any of the following (call `use_skill` with `skill_name="research-loop"` and `action="get_info"` or `get_reference` first):

- **Research progress / literature**: e.g. "检索xxx的研究进展", "查文献", "找综述", "某领域进展".
- **Why / mechanism**: e.g. "为什么", "原因是什么", "机理", "机制".
- **How / procedure**: e.g. "如何", "怎么", "步骤", "方法", "教程".
- **What / definition**: e.g. "是什么", "定义", "解释某概念".
- **Other**: "找方法", "解释某概念", discovery / explanation / review / tutorial lookup.

If in doubt for conceptual or search-heavy questions, prefer using this skill so the answer is grounded in a multi-angle search loop rather than a one-shot reply.

**First step (mandatory):** When you decide to use research-loop, **first** call `use_skill(skill_name="research-loop", action="get_info")` or `action="get_reference", reference_name="workflow.md"`. Do **not** use `action="run_script"`—this skill has **no scripts**. Then follow the workflow returned (multi-angle loop, paper + web, at least 2–3 aspects) before synthesizing. If you skip this and only do one web search then finish, the answer will be shallow and the run too short.

## Tool Mapping (Synchronous Only)

This skill uses **synchronous** tool logic: each step is "call → get result → next step". **No** submit → query_job_status → get_job_results flow is required.

| Purpose | Tools | Notes |
|--------|--------|--------|
| **Literature** | `mat_sn_search-papers-normal`, `mat_sn_search-papers-enhanced`, `mat_sn_scholar-search`, `mat_sn_pubmed-search` | One call returns results. |
| **Web / tutorials** | `mat_sn_web-search` | One call returns results. |
| **Extract page content** | `mat_doc_extract_info_from_webpage` | Synchronous; use when you need to pull main text from a URL. Async submit/poll is for heavy doc jobs (see calculation skill); not required in research-loop. |

## Multi-Angle Loop Workflow

1. **Split aspects**  
   Break the concept or question into several aspects, e.g.: definition, mechanism, experimental/computational methods, tutorials & protocols, recent reviews, caveats or controversies.

2. **Per aspect**  
   Search web first (tutorials, methods), then literature if needed. Use multiple query variants (e.g. Chinese/English, synonyms).

3. **Between steps: discriminate relevance**  
   After each search or each batch of results, **evaluate relevance** of hits (title, URL, snippet) to the current aspect and the user’s intent. Use only **relevant** results for synthesis or for calling `mat_doc_extract_info_from_webpage`; skip or deprioritize clearly irrelevant ones. Do not treat every hit as equally useful—filter between steps so the loop stays focused and the final answer is evidence-based.

4. **Rounds**  
   Cover at least 2–3 aspects before considering done. If one aspect is under-covered, run another round of searches (again filtering by relevance after each round).

5. **Exit**  
   When main aspects have reliable, relevant sources, synthesize one answer (following format rules in reference workflow) and call the **finish** tool with `task_completed=true`. Do not finish without calling the finish tool (see system prompt).

## Source Quality (研究进展 / 文献类必读)

For **研究进展** (research progress), **查文献**, or any literature/review-style task: you **must** use **paper search** (`mat_sn_search-papers-normal`, `mat_sn_search-papers-enhanced`, or `mat_sn_scholar-search`) and use **academic sources as the backbone** of the answer. Web search alone is not sufficient—it often returns non-academic or low-authority pages (e.g. 专栏、自媒体、营销站). Prefer peer-reviewed papers and authoritative institutions (e.g. 高校、研究所、期刊); when you do cite web-only sources, treat them as supplementary and avoid building the whole answer on them. In the synthesis, prioritize and cite paper/scholar results first; then add web snippets only where they add value (e.g. tutorials, definitions).

For a detailed step-by-step flow, **relevance discrimination between steps**, **output format and citation rules** (human-friendly, aligned with Science Navigator), and tool quick reference, use `use_skill` with `action="get_reference"` and `reference_name="workflow.md"`.

## No Script (Current)

There is no script in this skill yet. Follow the reference workflow (get_reference → workflow.md) to perform aspect splitting and query design manually. A future script (e.g. `suggest_aspects.py`) may be added to suggest aspects and query terms from a topic string.
