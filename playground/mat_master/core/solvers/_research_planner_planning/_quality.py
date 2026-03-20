"""Document-quality gate classification and survey artifact checks."""

import json
import re
from pathlib import Path
from typing import Any

from evomaster.agent.session.ssh import SSHSession
from evomaster.utils.types import Dialog, SystemMessage, UserMessage

from ....prompts.build_prompt import LANGUAGE_RULE
from ..plan_utils import _extract_json_from_content


class ResearchPlannerPlanningQualityMixin:
    def _is_quality_critical_step(
        self,
        intent: str,
        *,
        state: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> bool:
        """Ask LLM whether this step needs document-quality gating."""
        if not (intent or '').strip():
            return False
        prompt = f"""STEP INTENT:
{intent}

Question: Should this step be judged by long-form document quality criteria (survey/manuscript/report writing quality), rather than only by normal execution success?

Return exactly one JSON object:
{{
  "apply_quality_gate": false,
  "reason": ""
}}

Rules:
- Return true only when the step's primary deliverable is clearly a written document whose quality must be reviewed.
- Return false for data collection, extraction, normalization, scripts, tables, calculations, and generic execution steps.
- Be conservative. If the intent is ambiguous, return false.
"""
        dialog = Dialog(
            messages=[
                SystemMessage(
                    content=f"You are a strict step-quality classifier. Output only JSON.\n\n{LANGUAGE_RULE}"
                ),
                UserMessage(content=prompt),
            ],
            tools=[],
        )
        try:
            if state is not None and task_id is not None:
                if not self._consume_turns(state, task_id, 1):
                    self._fail_max_turns_exceeded(task_id, state)
                    return False
            reply = self.agent.llm.query(dialog)
            raw = _extract_json_from_content(reply.content or '')
            if not raw:
                return False
            result = json.loads(raw)
            return bool(result.get('apply_quality_gate', False))
        except Exception as e:
            self.logger.debug('Quality gate classification skipped: %s', e)
            return False

    @staticmethod
    def _extract_markdown_paths_from_text(text: str, workspace_dir: Path) -> list[Path]:
        candidates: list[Path] = []
        if not text:
            return candidates
        for raw in re.findall(r'([A-Za-z0-9_./\\:\-]+\.md)', text):
            cleaned = raw.strip().strip("`'\"")
            if not cleaned:
                continue
            path = Path(cleaned)
            if not path.is_absolute():
                path = workspace_dir / path
            if path.exists() and path.is_file():
                candidates.append(path)
        return candidates

    @staticmethod
    def _count_substantial_lines(content: str, min_length: int = 60) -> int:
        count = 0
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('#'):
                continue
            if stripped.startswith('<!--'):
                continue
            if len(stripped) < min_length:
                continue
            count += 1
        return count

    @staticmethod
    def _reference_metrics(content: str) -> tuple[int, int]:
        lower = content.lower()
        match = re.search(
            r'^\s{0,3}#{1,6}\s+references?\s*$',
            lower,
            flags=re.MULTILINE,
        )
        ref_text = content[match.end() :] if match else content
        ref_entries = re.findall(r'(?m)^\s*\[(\d+)\]\s+', ref_text)
        if ref_entries:
            ref_count = len(set(ref_entries))
        else:
            cite_entries = re.findall(r'\[(\d+)\]\(https?://[^\)]+\)', ref_text)
            ref_count = len(set(cite_entries))
        doi_urls = set(re.findall(r'https?://(?:dx\.)?doi\.org/([^\s\)]+)', lower))
        bare_dois = set(re.findall(r'\b10\.\d{4,9}/[-._;()/:a-z0-9]+\b', lower))
        unique_doi_count = len({doi.rstrip('.,;:)') for doi in doi_urls | bare_dois})
        return ref_count, unique_doi_count

    def _collect_quality_files(
        self, step_dir: Path, workspace_dir: Path, result_text: str
    ) -> list[Path]:
        session = getattr(getattr(self, 'agent', None), 'session', None)
        file_io = getattr(self, '_file_io', None)
        use_remote = isinstance(session, SSHSession) and file_io is not None

        files: list[Path] = []
        seen: set[str] = set()
        ws_str = str(workspace_dir)

        def add(path: Path) -> None:
            key = str(path.resolve()) if path.exists() else str(path)
            if key in seen:
                return
            seen.add(key)
            files.append(path)

        def add_remote(path_str: str) -> None:
            if path_str in seen:
                return
            if not file_io or not file_io.exists(path_str):
                return
            seen.add(path_str)
            files.append(Path(path_str))

        min_root_bytes = int(
            self._quality_gate_cfg.get('survey_min_root_md_bytes', 400)
        )
        max_root_files = int(self._quality_gate_cfg.get('survey_max_root_md_files', 48))

        if use_remote and file_io and hasattr(file_io, 'glob'):
            survey_dir = f"{ws_str.rstrip('/')}/_tmp/surveys"
            if file_io.exists(survey_dir):
                for p in file_io.glob(survey_dir, '*.md'):
                    add_remote(p)
            for p in file_io.glob(ws_str, '*_review_*.md'):
                add_remote(p)
            for p in file_io.glob(ws_str, '*_survey_*.md'):
                add_remote(p)
            for raw in re.findall(r'([A-Za-z0-9_./\\:\-]+\.md)', result_text or ''):
                cleaned = raw.strip().strip("`'\"")
                if not cleaned:
                    continue
                path = Path(cleaned)
                full = path if path.is_absolute() else Path(ws_str) / path
                add_remote(str(full).replace('\\', '/'))
            # Workspace-root *.md (e.g. tutorial drafts not under _tmp/surveys).
            root_list = file_io.glob(ws_str, '*.md')
            sized: list[tuple[str, int]] = []
            for p in root_list:
                sz = file_io.stat_size(p) if hasattr(file_io, 'stat_size') else None
                if min_root_bytes > 0 and sz is not None and sz < min_root_bytes:
                    continue
                if sz is None:
                    sized.append((p, 0))
                else:
                    sized.append((p, sz))
            sized.sort(key=lambda t: t[1], reverse=True)
            for p, _ in sized[:max_root_files]:
                add_remote(p)
        else:
            for path in step_dir.glob('*.md'):
                add(path)
            survey_dir = workspace_dir / '_tmp' / 'surveys'
            if survey_dir.exists():
                for path in survey_dir.glob('*.md'):
                    add(path)
            for path in workspace_dir.glob('*_review_*.md'):
                add(path)
            for path in workspace_dir.glob('*_survey_*.md'):
                add(path)
            for path in self._extract_markdown_paths_from_text(
                result_text, workspace_dir
            ):
                add(path)
            root_paths = sorted(
                workspace_dir.glob('*.md'),
                key=lambda x: x.stat().st_size if x.is_file() else 0,
                reverse=True,
            )
            for path in root_paths[:max_root_files]:
                if min_root_bytes > 0 and path.is_file():
                    try:
                        if path.stat().st_size < min_root_bytes:
                            continue
                    except OSError:
                        continue
                add(path)
        return files

    def _detect_survey_quality_failure(
        self,
        *,
        intent: str,
        quality_files: list[Path],
        evidence_delta: int,
        state: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> tuple[bool, str]:
        if not self._is_quality_critical_step(intent, state=state, task_id=task_id):
            return False, ''
        if not quality_files:
            return (
                True,
                'No markdown artifact found for quality-critical survey/literature step.',
            )

        min_line_len = self._quality_gate_cfg.get('survey_min_line_length', 60)
        best: tuple[int, int, int, Path] | None = None
        best_content = ''
        for path in quality_files:
            try:
                content = self._read_workspace_text(path)
            except Exception:
                continue
            refs, dois = self._reference_metrics(content)
            substantial = self._count_substantial_lines(
                content,
                min_length=min_line_len,
            )
            score = (refs, dois, substantial, path)
            if best is None or score[:3] > best[:3]:
                best = score
                best_content = content

        if best is None:
            return True, 'Survey quality check found no readable markdown artifacts.'

        refs, dois, substantial, best_path = best
        if refs < self._quality_gate_cfg['survey_min_references']:
            return True, (
                f"Insufficient references in {best_path.name}: {refs} "
                f"(min {self._quality_gate_cfg['survey_min_references']})."
            )
        if dois < self._quality_gate_cfg['survey_min_unique_dois']:
            return True, (
                f"Insufficient unique DOIs in {best_path.name}: {dois} "
                f"(min {self._quality_gate_cfg['survey_min_unique_dois']})."
            )
        if substantial < self._quality_gate_cfg['survey_min_substantial_lines']:
            return True, (
                f"Insufficient substantive content in {best_path.name}: {substantial} lines "
                f"(min {self._quality_gate_cfg['survey_min_substantial_lines']})."
            )
        if evidence_delta < self._quality_gate_cfg['survey_min_evidence_delta']:
            return True, (
                f"No evidence growth in literature index: delta={evidence_delta} "
                f"(min {self._quality_gate_cfg['survey_min_evidence_delta']})."
            )
        if best_content and re.search(r'\btbd\b', best_content.lower()):
            return (
                True,
                f"Survey artifact still contains TBD placeholder: {best_path.name}",
            )
        return False, ''
