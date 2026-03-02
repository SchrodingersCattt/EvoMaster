"""Skill Tool - 将 Operator Skill 转换为可执行的 Tool

这个工具允许 Agent 使用 Operator 类型的 Skills。
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import Field

from .base import BaseTool, BaseToolParams

if TYPE_CHECKING:
    from evomaster.agent.session import BaseSession
    from evomaster.skills import OperatorSkill, SkillRegistry


class SkillToolParams(BaseToolParams):
    """使用技能并执行相关操作。

    Skill 是 EvoMaster 的扩展能力，包含领域知识和可执行脚本。
    """

    name: ClassVar[str] = 'use_skill'

    skill_name: str = Field(description='技能名称，使用 kebab-case（如 deep-survey）')
    action: str = Field(
        description="要执行的操作：'get_info' 获取完整信息，'get_reference' 获取参考文档，'run_script' 运行脚本"
    )
    reference_name: str | None = Field(
        default=None, description="参考文档名称（当 action='get_reference' 时需要）"
    )
    script_name: str | None = Field(
        default=None, description="脚本名称（当 action='run_script' 时需要）"
    )
    script_args: str | None = Field(
        default=None, description="脚本参数，空格分隔（当 action='run_script' 时可选）"
    )


class SkillTool(BaseTool):
    """Skill 工具

    允许 Agent 使用 Operator 类型的 Skills：
    - 获取技能的完整信息（full_info）
    - 获取技能的参考文档
    - 执行技能中的脚本
    """

    name: ClassVar[str] = 'use_skill'
    params_class: ClassVar[type[BaseToolParams]] = SkillToolParams

    def __init__(self, skill_registry: SkillRegistry):
        """初始化 SkillTool

        Args:
            skill_registry: SkillRegistry 实例
        """
        super().__init__()
        self.skill_registry = skill_registry

    def execute(
        self, session: BaseSession, args_json: str
    ) -> tuple[str, dict[str, Any]]:
        """执行技能操作

        Args:
            session: 环境会话
            args_json: 参数 JSON 字符串

        Returns:
            (observation, info) 元组
        """
        try:
            params = self.parse_params(args_json)

            # 获取 skill
            skill = self.skill_registry.get_skill(params.skill_name)
            if skill is None:
                return (
                    f"Error: Skill '{params.skill_name}' not found",
                    {'error': 'skill_not_found'},
                )

            # 只支持 Operator 类型的 skill
            from evomaster.skills import OperatorSkill

            if not isinstance(skill, OperatorSkill):
                return (
                    f"Error: Skill '{params.skill_name}' is not an Operator skill",
                    {'error': 'invalid_skill_type'},
                )

            self.logger.info(
                'Skill hit: skill_name=%s action=%s ref=%s script=%s',
                params.skill_name,
                params.action,
                params.reference_name or '-',
                params.script_name or '-',
            )

            # 根据 action 执行不同操作
            if params.action == 'get_info':
                return self._get_info(skill)
            elif params.action == 'get_reference':
                return self._get_reference(skill, params.reference_name)
            elif params.action == 'run_script':
                return self._run_script(
                    session, skill, params.script_name, params.script_args
                )
            else:
                return (
                    f"Error: Unknown action '{params.action}'",
                    {'error': 'invalid_action'},
                )

        except Exception as e:
            self.logger.error(f"Skill tool execution failed: {e}", exc_info=True)
            return f"Error: {str(e)}", {'error': str(e)}

    @staticmethod
    def _get_skill_project_root(skill: OperatorSkill | Path | str) -> Path | None:
        """从给定路径向上查找含 evomaster 的项目根，供 PYTHONPATH 使用。"""
        try:
            if isinstance(skill, (Path, str)):
                path = Path(skill)
            else:
                base = getattr(skill, 'skill_path', None)
                if base is None:
                    return None
                path = Path(base) if not isinstance(base, Path) else base
            for parent in (path,) + tuple(path.parents):
                if (parent / 'evomaster').is_dir():
                    return parent.resolve()
        except Exception:
            pass
        return None

    def _get_info(self, skill: OperatorSkill) -> tuple[str, dict[str, Any]]:
        """获取技能的完整信息

        Args:
            skill: OperatorSkill 实例

        Returns:
            (observation, info) 元组
        """
        full_info = skill.get_full_info()
        return (
            f"# Skill: {skill.meta_info.name}\n\n{full_info}",
            {'action': 'get_info', 'skill_name': skill.meta_info.name},
        )

    def _get_reference(
        self, skill: OperatorSkill, reference_name: str | None
    ) -> tuple[str, dict[str, Any]]:
        """获取技能的参考文档

        Args:
            skill: OperatorSkill 实例
            reference_name: 参考文档名称

        Returns:
            (observation, info) 元组
        """
        if not reference_name:
            return (
                "Error: reference_name is required for action='get_reference'",
                {'error': 'missing_parameter'},
            )

        try:
            reference_content = skill.get_reference(reference_name)
            observation = f"# Reference: {reference_name}\n\n{reference_content}"

            # Append co-template hints if available
            co_hint = self._get_co_template_hint(skill, reference_name)
            if co_hint:
                observation += co_hint

            return (
                observation,
                {
                    'action': 'get_reference',
                    'skill_name': skill.meta_info.name,
                    'reference_name': reference_name,
                },
            )
        except FileNotFoundError as e:
            # Guardrail: many skills only ship SKILL.md; when a specific
            # reference file is missing, fall back to full skill info so
            # the agent can still proceed instead of stalling.
            full_info = skill.get_full_info()
            return (
                f"Error: {str(e)}\n\nFallback to skill info:\n\n# Skill: {skill.meta_info.name}\n\n{full_info}",
                {
                    'action': 'get_reference',
                    'skill_name': skill.meta_info.name,
                    'reference_name': reference_name,
                    'warning': 'reference_not_found_fallback_to_get_info',
                },
            )

    def _get_co_template_hint(
        self,
        skill: OperatorSkill,
        reference_name: str,
    ) -> str | None:
        """Check for co-template hints and return a formatted reminder.

        Looks for ``_co_templates.json`` in the skill's ``references/`` (or
        ``reference/``) directory.  If the requested *reference_name* has an
        entry, return a formatted block that reminds the agent to also fetch
        related templates.  Returns ``None`` when no hint applies.
        """
        import json as _json
        from pathlib import Path

        # Locate the hints file
        hints_path: Path | None = None
        for candidate in (
            skill.skill_path / 'references' / '_co_templates.json',
            skill.skill_path / 'reference' / '_co_templates.json',
            skill.skill_path / '_co_templates.json',
        ):
            if candidate.exists():
                hints_path = candidate
                break

        if hints_path is None:
            return None

        try:
            hints = _json.loads(hints_path.read_text(encoding='utf-8'))
        except Exception:
            return None

        entry = hints.get(reference_name)
        if not entry:
            return None

        hint_text = entry.get('hint', '')
        related = entry.get('related', [])
        if not hint_text and not related:
            return None

        lines = [
            '',
            '',
            '=' * 72,
            '>>> IMPORTANT — CO-TEMPLATE REMINDER <<<',
        ]
        if hint_text:
            lines.append(hint_text)
        if related:
            lines.append(
                'Related templates you should ALSO fetch (use get_reference for each):'
            )
            for r in related:
                lines.append(f"  - {r}")
        lines.append(
            'Do NOT skip fetching related templates. Do NOT try to manually '
            'construct these sections by querying the manual.'
        )
        lines.append('=' * 72)
        return '\n'.join(lines)

    def _run_script(
        self,
        session: BaseSession,
        skill: OperatorSkill,
        script_name: str | None,
        script_args: str | None,
    ) -> tuple[str, dict[str, Any]]:
        """运行技能中的脚本

        Args:
            session: 环境会话
            skill: OperatorSkill 实例
            script_name: 脚本名称
            script_args: 脚本参数

        Returns:
            (observation, info) 元组
        """
        if not script_name:
            # Single-script skill: infer script_name so LLM can omit it (e.g. ask-human/ask.py).
            scripts = skill.available_scripts
            if len(scripts) == 1:
                script_name = scripts[0].name
            else:
                available = ', '.join(s.name for s in scripts) if scripts else '(none)'
                return (
                    f"Error: script_name is required for action='run_script'. "
                    f"Skill '{skill.meta_info.name}' has scripts: {available}.",
                    {'error': 'missing_parameter'},
                )

        # 获取脚本路径
        script_path = skill.get_script_path(script_name)
        if script_path is None:
            available_scripts = ', '.join([s.name for s in skill.available_scripts])
            return (
                f"Error: Script '{script_name}' not found in skill '{skill.meta_info.name}'. "
                f"Available scripts: {available_scripts}",
                {'error': 'script_not_found'},
            )
        # 转换为绝对路径
        script_path = script_path.resolve()
        # 解析项目根（含 evomaster 的目录），供 Python 脚本设置 PYTHONPATH
        project_root = self._get_skill_project_root(skill)

        # SSH-aware: remap local paths to remote POSIX paths for SSH execution.
        # Two mapping strategies:
        #   1. Project-scoped skills (mat / core / dynamic): remap via remote_project_root
        #   2. User skills (~/.evomaster-skills): remap via remote_user_skills_root
        #      These paths have no evomaster/ ancestor so _get_skill_project_root returns None.
        remote_root = getattr(session, 'remote_project_root', None)
        remote_user_root = getattr(session, 'remote_user_skills_root', None)
        local_user_root = getattr(session, 'local_user_skills_root', None)

        use_remote = False
        remote_script = None
        pythonpath_remote = None

        from pathlib import PurePosixPath as _PPP
        if remote_root is not None and project_root is not None:
            # Strategy 1: project-scoped skill
            use_remote = True
            try:
                rel = script_path.relative_to(project_root).as_posix()
            except ValueError:
                rel = script_path.name
            remote_script = str(_PPP(remote_root) / rel)
            pythonpath_remote = remote_root
        elif remote_user_root and local_user_root:
            # Strategy 2: user skill -- no evomaster/ ancestor, remap via user roots
            try:
                from pathlib import Path as _Path
                rel = script_path.relative_to(_Path(local_user_root)).as_posix()
                use_remote = True
                remote_script = str(_PPP(remote_user_root) / rel)
                pythonpath_remote = remote_user_root
            except ValueError:
                pass  # not a user skill path, fall through to local execution

        if use_remote and remote_script is not None:
            suffix = script_path.suffix

            if suffix == '.py':
                cmd = f"PYTHONPATH={shlex.quote(pythonpath_remote)} python {shlex.quote(remote_script)}"
            elif suffix == '.sh':
                cmd = f"bash {shlex.quote(remote_script)}"
            elif suffix == '.js':
                cmd = f"node {shlex.quote(remote_script)}"
            else:
                return (
                    f"Error: Unsupported script type: {suffix}",
                    {'error': 'unsupported_script_type'},
                )

            if script_args and script_args.strip():
                try:
                    parts = shlex.split(script_args.strip())
                    cmd += ' ' + ' '.join(shlex.quote(p) for p in parts)
                except ValueError:
                    cmd += ' ' + script_args.strip()
        else:
            # Local execution (original logic)
            if script_path.suffix == '.py':
                import sys

                if sys.platform == 'win32':
                    if project_root is not None:
                        cmd = (
                            f'set "PYTHONPATH={str(project_root)}" '
                            f'&& python "{script_path}"'
                        )
                    else:
                        cmd = f'python "{script_path}"'
                else:
                    py_prefix = ''
                    if project_root is not None:
                        py_prefix = f"PYTHONPATH={shlex.quote(str(project_root))} "
                    cmd = f"{py_prefix}python {shlex.quote(str(script_path))}"
            elif script_path.suffix == '.sh':
                cmd = f"bash {script_path}"
            elif script_path.suffix == '.js':
                cmd = f"node {script_path}"
            else:
                return (
                    f"Error: Unsupported script type: {script_path.suffix}",
                    {'error': 'unsupported_script_type'},
                )

            if script_args and script_args.strip():
                import sys

                if sys.platform == 'win32':
                    cmd += ' ' + script_args.strip()
                else:
                    try:
                        parts = shlex.split(script_args.strip())
                        cmd += ' ' + ' '.join(shlex.quote(p) for p in parts)
                    except ValueError:
                        cmd += ' ' + script_args.strip()

        # 使用 session 的 bash 工具执行脚本
        try:
            timeout: int | None = None
            result = session.exec_bash(cmd, timeout=timeout)
            stdout = result.get('stdout', '')
            stderr = result.get('stderr', '')
            exit_code = result.get('exit_code', 0)

            output = f"Script output:\n{stdout}"
            if stderr:
                output += f"\n\nStderr:\n{stderr}"
            if exit_code != 0:
                output += f"\n\nExit code: {exit_code}"

            # Auto-inject per-mode prompt: scan script_args tokens and append the
            # first prompts/<token>.md that exists. No config file needed — the
            # convention is that argparse choices values match prompt file names.
            prompts_dir = skill.skill_path / "prompts"
            if prompts_dir.exists() and script_args:
                try:
                    tokens = shlex.split(script_args.strip())
                except ValueError:
                    tokens = script_args.strip().split()
                for token in tokens:
                    candidate = prompts_dir / f"{token}.md"
                    if candidate.exists():
                        try:
                            prompt_content = candidate.read_text(encoding="utf-8")
                            output += (
                                f"\n\n{'=' * 60}\n"
                                f"MANDATORY WORKFLOW (auto-loaded: prompts/{token}.md):\n"
                                f"{'=' * 60}\n\n"
                                f"{prompt_content}"
                            )
                        except Exception:
                            pass
                        break  # inject only the first match

            return (
                output,
                {
                    'action': 'run_script',
                    'skill_name': skill.meta_info.name,
                    'script_name': script_name,
                    'script_args': script_args,
                    'exit_code': exit_code,
                },
            )
        except Exception as e:
            return (
                f"Error executing script: {str(e)}",
                {'error': 'script_execution_failed'},
            )
