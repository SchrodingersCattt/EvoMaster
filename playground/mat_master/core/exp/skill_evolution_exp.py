"""SkillEvolutionExp: evolution layer (mode='skill_evolution').

When the agent lacks a tool: code -> register via MatMasterSkillRegistry.
Uses run_dir for workspace when set.

Note: Automated sandbox testing is not performed. After registration a warning
is emitted so the caller and user know to manually verify the new skill's
scripts before relying on them in production.
"""

import logging
import shutil
from pathlib import Path
from typing import Optional

from evomaster.core.exp import BaseExp
from evomaster.utils.types import TaskInstance


def _get_mat_master_config(config) -> dict:
    try:
        if hasattr(config, 'model_dump'):
            d = config.model_dump()
        else:
            d = dict(config) if config else {}
        return d.get('mat_master') or {}
    except Exception:
        return {}


class SkillEvolutionExp(BaseExp):
    """Skill evolution mode: evolution layer.

    When the main task needs a capability that does not exist, this Exp
    guides the agent to write a new Skill (Python script + SKILL.md),
    tests it in a sandbox, then registers it via MatMasterSkillRegistry.
    """

    def __init__(self, agent, config):
        super().__init__(agent, config)
        self.logger = logging.getLogger(self.__class__.__name__)

    def run(
        self,
        task_description: str = '',
        task_id: str = 'evo_task',
        task: Optional[TaskInstance] = None,
        images: Optional[list[str]] = None,
        append_result: bool = True,
    ) -> dict:
        """Evolve a new skill for the given requirement.

        若传入 task，则使用 task.description / task.task_id；否则使用 task_description / task_id。
        与 BaseExp.run() 接口一致，便于 direct_solver / agent_run_service 等统一调用。
        """
        if task is not None:
            task_description = task.description
            task_id = task.task_id
        self.logger.info(
            '[Evo] Attempting to evolve skill for: %s', (task_description or '')[:80]
        )

        # Single source of truth for output directory name.
        # Using task_id as suffix ensures uniqueness when two skill_evolution steps
        # run in the same planner session (shared workspace).
        output_dir = f"new_skill_{task_id}"

        prompt = (
            f"I need a new tool to handle this requirement: {task_description}\n"
            'Please write a Python script and a SKILL.md following EvoMaster standards.\n'
            'The script should be standalone and testable.\n\n'
            'Requirements:\n'
            f"1. Output directory must be exactly: {output_dir} (create {output_dir}/ and {output_dir}/scripts/ as needed). Do not use a different name.\n"
            '2. Write all file contents with the str_replace_editor tool (command=create, path=<absolute path>, file_text=<content>). Use the current working directory shown above as the base; '
            f"e.g. <working_dir>/{output_dir}/SKILL.md and <working_dir>/{output_dir}/scripts/<script>.py. Do not use bash (cat, echo, here-docs) or Python one-liners to write long file content—on Windows these often fail or write to the wrong place.\n"
            f"3. Create {output_dir}/SKILL.md (with YAML frontmatter: name, description) and {output_dir}/scripts/<your_script>.py with full, runnable code.\n"
            '4. IMPORTANT: After writing the skill, inform the user that the new skill has NOT been automatically tested and should be manually verified before use in production.'
        )
        run_task = TaskInstance(
            task_id=f"{task_id}_code", task_type='discovery', description=prompt
        )
        self.agent.run(run_task)

        # Derive workspace from the agent's actual session config (not from task_id),
        # because the agent writes files to its configured workspace_path.
        agent_workspace = getattr(
            getattr(self.agent, 'session', None),
            'config',
            None,
        )
        agent_workspace = (
            getattr(agent_workspace, 'workspace_path', None)
            if agent_workspace
            else None
        )
        if agent_workspace:
            workspace = Path(agent_workspace)
        else:
            run_dir = Path(self.run_dir) if self.run_dir else Path('.')
            workspace = run_dir / 'workspaces' / f"{task_id}_code"
        new_skill_path = workspace / output_dir
        if not new_skill_path.is_dir():
            new_skill_path = workspace / 'workspace' / output_dir

        if not (new_skill_path / 'SKILL.md').exists():
            self.logger.error(
                '[Evo] Agent did not produce SKILL.md at %s', new_skill_path
            )
            return {'status': 'failed', 'reason': 'no_skill_md'}

        registry = getattr(self.agent, 'skill_registry', None)
        if not registry or not getattr(registry, 'register_dynamic_skill', None):
            self.logger.warning(
                '[Evo] No MatMasterSkillRegistry with register_dynamic_skill.'
            )
            return {'status': 'failed', 'reason': 'no_registry'}

        if registry.register_dynamic_skill(new_skill_path):
            self.logger.info(
                '[Evo] Skill %s registered successfully.', new_skill_path.name
            )
            self.logger.warning(
                '[Evo] Skill %s registered WITHOUT sandbox testing. '
                'Run its scripts manually before relying on it in production.',
                new_skill_path.name,
            )
            return {
                'status': 'completed',
                'skill_path': str(new_skill_path),
                'warning': (
                    f"New skill registered without automated test verification. "
                    f"Please review the generated scripts in {output_dir}/scripts/ "
                    'and run them manually to confirm correctness before using in subsequent tasks.'
                ),
            }

        self.logger.warning('[Evo] Skill evolution failed to register.')
        return {'status': 'failed', 'reason': 'register_failed'}

    def _copy_to_user_skills(
        self, skill_path: Path, skill_name: str, user_skills_root: Path
    ) -> str:
        """Copy a newly evolved skill into the user's persistent skill library.

        This is a pure file operation -- no ask-human logic here.  The caller
        (ResearchPlanner._execute_step) is responsible for deciding whether to
        persist and for asking the user if needed.

        Args:
            skill_path: Path to the skill directory (in workspace).
            skill_name: Skill name from SKILL.md frontmatter (used as subdir name).
            user_skills_root: Root of the user skill library (e.g. ~/.evomaster-skills).

        Returns:
            String path of the destination directory.
        """
        user_skills_root.mkdir(parents=True, exist_ok=True)
        dest = user_skills_root / skill_name
        shutil.copytree(str(skill_path), str(dest), dirs_exist_ok=True)
        self.logger.info("[Evo] Skill '%s' persisted to %s", skill_name, dest)
        return str(dest)
