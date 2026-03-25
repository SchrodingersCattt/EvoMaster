# Coding Conventions

**Analysis Date:** 2026-03-21

## Naming Patterns

**Python Files:**
- Private/internal modules prefixed with underscore: `_research_planner_planning`, `_planner_file_io`, `_precheck.py`
- Descriptive module names matching functionality: `agent.py`, `registry.py`, `constants.py`, `tool_guard.py`
- Script/tool files: `build_lit_table.py`, `submit_job.py`, `check_compliance.py`

**Python Classes:**
- PascalCase: `MatMasterAgent`, `MatMasterSkillRegistry`, `AsyncToolRegistry`, `ToolGuard`
- Private classes prefixed with underscore: `_AsyncEntry`, `_NoDbConnection`
- Data classes for small structures: `@dataclass StructCandidateRecord`, `@dataclass StructRetrievalState`

**Python Functions:**
- snake_case: `_load_skills_from()`, `_derive_name()`, `_has_remote_profile()`, `auto_save_tool_output()`
- Private functions prefixed with underscore: `_mock_sessions_table()`, `_check_quota_noop()`
- Helper functions with verbs: `format_tool_observation()`, `compact_mat_sn_papers_observation()`, `summarize_large_tool_observation()`

**Python Variables/Constants:**
- UPPER_SNAKE_CASE for module-level constants: `MANUSCRIPT_FAIL_MARKERS`, `AUTH_FAILURE_MARKERS_STRONG`, `LOOP_WINDOW`, `AUTH_FAILURE_THRESHOLD`
- snake_case for local/instance variables: `self._direct_max_workers`, `self._tool_guard`, `self._finish_block_count`
- Tuple unpacking for immutable constants: `frozenset(...)` used for safe collections

**TypeScript Files:**
- kebab-case for filenames: `ExecutionGraphRenderer.tsx`, `ChatPanel.tsx`, `FileTree.tsx`, `ContentRenderer.tsx`, `icons.tsx`
- Files exported as modules use their descriptive names: `@/components/MatMasterView`, `@/lib/utils`

**TypeScript Components:**
- PascalCase for React components: `ExecutionGraphRenderer`, `ChatPanel`, `LogStream`, `WorkspacePanel`, `ConversationPanel`
- Prefix with memo for memoized components: `React.memo(function ToolCard(...) { ... })`
- Export named components, not default: `export function SendIcon(...) { ... }`

**TypeScript Functions:**
- camelCase: `escapeMermaidLabel()`, `generateMermaidDiagram()`, `isEmptyThought()`, `isStatusEvent()`
- Descriptive helper names: `getVal()`, `renderContent()`, `renderMarkdown()`
- Handler functions: `on_token=deltas.append` (callback), `event_callback = lambda ...`

**TypeScript Types/Interfaces:**
- PascalCase with suffix: `ExecutionGraphRendererProps`, `LogEntry` (from context), types describe shape of data

## Code Style

**Formatting:**
- Python: Default per `pyproject.toml` (Ruff, pytest configured)
- TypeScript/Frontend: Next.js defaults (no explicit `.eslintrc` or `.prettierrc` found)
- Indentation: 2 spaces (TypeScript/JSX), standard Python (4 spaces implied)

**Linting:**
- Python: Ruff via `pyproject.toml` (tool.hatch config present)
- TypeScript: Next.js built-in linting via `"next lint"` script in package.json
- No explicit ESLint or Prettier config files detected

**Docstring/Comment Style:**
- Module docstrings: First line summary, blank line, detailed description with context
  - Example: `"""MatMasterAgent: finish only when task_completed=true.\n\nSystem prompt uses file-first loading with runtime composition fallback."""`
- Class docstrings: Summary with key behaviors/invariants
  - Example: `"""Agent that ends the run when the finish tool is called with task_completed=true or partial."""`
- Long multi-purpose classes documented with numbered lists of concerns
  - Example in `tool_guard.py`: Six concerns (Loop detection, Manuscript gate, Structure-retrieval gate, etc.) each labeled with numbers
- Private/internal documentation uses docstrings over inline comments

## Import Organization

**Python Order:**
1. Standard library imports (`import logging`, `from pathlib import Path`, `from dataclasses import dataclass`)
2. Third-party imports (`from pydantic import ...`, `from typing import TYPE_CHECKING`, `from evomaster...`)
3. TYPE_CHECKING block for forward references (avoids circular imports)
4. Local/relative imports (`from .agent import MatMasterAgent`, `from .constants import ...`)

**Path Aliases:**
- TypeScript: `@/*` maps to `./src/*` (via `tsconfig.json` paths)
- Usage: `import { cn } from "@/lib/utils"`, `import MatMasterView from "@/components/MatMasterView"`

**Conditional Imports:**
- Try/except for optional dependencies:
  ```python
  try:
      from evomaster.agent.tools.builtin.bash_safety import (
          is_dangerous_bash_command,
          is_dangerous_python_content,
      )
  except ImportError:
      is_dangerous_bash_command = None
      is_dangerous_python_content = None
  ```

## Error Handling

**Patterns:**
- Try/except with specific error recovery or graceful degradation
- Example: Missing optional tools set to `None` then checked before use
- Example: File operations wrapped to handle missing dirs/files
- Log warnings for non-fatal errors, let critical errors propagate

**Approach:**
- Prefer exception handling over manual checks for expected failure modes (e.g., `ImportError`)
- Use dataclass fields with defaults for optional/fallback values
- Return `False` or `None` for validation failures, log the reason

## Logging

**Framework:** Python standard `logging` module

**Patterns:**
- `self.logger = logging.getLogger(self.__class__.__name__)` in class init
- Module-level: `logger = logging.getLogger(__name__)`
- Log at appropriate level:
  - `.info()` for major lifecycle events (skill loaded, agent created)
  - `.warning()` for recoverable issues (failed skill load, missing optional dir)
  - `.debug()` for detailed traces (not commonly used in observed code)

**Examples:**
- `self.logger.info('Loaded skill: %s', skill.meta_info.name)`
- `self.logger.warning('Failed to load skill from %s: %s', skill_dir, e)`

## Comments

**When to Comment:**
- Explain *why*, not *what*: Comments should clarify non-obvious logic or design decisions
- Complex algorithms or state machines: Document the rules and invariants
- Example from code: `# Only STRONG markers trigger the auth-failure gate (system auth, not third-party 403).`

**Docstring/TSDoc Usage:**
- Module docstrings are mandatory for files with public APIs
- Class docstrings required for all public classes
- Method docstrings: Short summary for simple methods; detailed for complex ones
- TypeScript/React: JSDoc comments not heavily used; code is relatively self-documenting via types

**Example Patterns:**
- `"""Summary.\n\nDetailed explanation with context and reasoning."""`
- Numbered lists for related behaviors/concerns
- Code examples in docstrings for non-obvious APIs

## Function Design

**Size:**
- Keep functions under 50 lines where practical
- Complex logic extracted to separate functions with descriptive names
- Example: `_derive_name()`, `_has_remote_profile()`, `format_tool_observation()` are all focused single-purpose helpers

**Parameters:**
- Prefer explicit named parameters over *args/**kwargs for public functions
- Type hints mandatory for Python (observed throughout codebase)
- Default values for optional parameters
- Example: `def __init__(self, agent: 'MatMasterAgent', *, download_subdir: str = _DEFAULT_DOWNLOAD_SUBDIR) -> None:`

**Return Values:**
- Single return type (avoid Union except where semantically necessary)
- Return `None` for side-effect-only functions
- Return `dict`, `list`, or dataclass for complex data
- Example: `register_dynamic_skill(self, skill_path: Path) -> bool` returns boolean success

## Module Design

**Exports:**
- Explicit public API via docstrings and module-level `__all__` (implicit when no conflicts)
- Private functions/classes prefixed with underscore, not exported
- Example: `_AsyncEntry`, `_mock_sessions_table()` not part of public API

**Barrel Files:**
- `__init__.py` files used for package structure (minimal re-exports observed)
- Example: `playground/mat_master/core/callback/__init__.py` likely imports base classes

**File Organization:**
- One main public class per file (e.g., `MatMasterAgent` in `agent.py`)
- Related helpers and data classes in same file
- Large modules split into private submodules: `_research_planner_planning/`, `research_planner_execution/`

---

*Convention analysis: 2026-03-21*
