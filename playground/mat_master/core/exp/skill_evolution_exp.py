"""SkillEvolutionExp: evolution layer (mode='skill_evolution').

When the agent lacks a tool: code -> register via MatMasterSkillRegistry.
Uses run_dir for workspace when set.

Note: Automated sandbox testing is not performed. After registration a warning
is emitted so the caller and user know to manually verify the new skill's
scripts before relying on them in production.
"""

import logging
from pathlib import Path

from evomaster.core.exp import BaseExp
from evomaster.utils.types import TaskInstance


def _get_mat_master_config(config) -> dict:
    try:
        if hasattr(config, "model_dump"):
            d = config.model_dump()
        else:
            d = dict(config) if config else {}
        return d.get("mat_master") or {}
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

    def run(self, task_description: str, task_id: str = "evo_task") -> dict:
        """Evolve a new skill for the given requirement (task_description)."""
        self.logger.info("[Evo] Attempting to evolve skill for: %s", task_description[:80])

        prompt = (
            f"I need a new tool to handle this requirement: {task_description}\n"
            "Please write a Python script and a SKILL.md following EvoMaster standards.\n"
            "The script should be standalone and testable.\n\n"
            "Requirements:\n"
            "1. Output directory must be exactly: new_skill (create new_skill/ and new_skill/scripts/ as needed). Do not use names like new_skill_2.\n"
            "2. Write all file contents with the str_replace_editor tool (command=create, path=<absolute path>, file_text=<content>). Use the current working directory shown above as the base; e.g. <working_dir>/new_skill/SKILL.md and <working_dir>/new_skill/scripts/<script>.py. Do not use bash (cat, echo, here-docs) or Python one-liners to write long file content—on Windows these often fail or write to the wrong place.\n"
            "3. Create new_skill/SKILL.md (with YAML frontmatter: name, description) and new_skill/scripts/<your_script>.py with full, runnable code.\n"
            "4. IMPORTANT: After writing the skill, inform the user that the new skill has NOT been automatically tested and should be manually verified before use in production."
        )
        task = TaskInstance(task_id=f"{task_id}_code", task_type="discovery", description=prompt)
        trajectory = self.agent.run(task)

        # Derive workspace from the agent's actual session config (not from task_id),
        # because the agent writes files to its configured workspace_path.
        agent_workspace = getattr(
            getattr(self.agent, "session", None),
            "config", None,
        )
        agent_workspace = getattr(agent_workspace, "workspace_path", None) if agent_workspace else None
        if agent_workspace:
            workspace = Path(agent_workspace)
        else:
            run_dir = Path(self.run_dir) if self.run_dir else Path(".")
            workspace = run_dir / "workspaces" / f"{task_id}_code"
        new_skill_path = workspace / "new_skill"
        if not new_skill_path.is_dir():
            new_skill_path = workspace / "workspace" / "new_skill"

        if not (new_skill_path / "SKILL.md").exists():
            self.logger.error("[Evo] Agent did not produce SKILL.md at %s", new_skill_path)
            return {"status": "failed", "reason": "no_skill_md"}

        registry = getattr(self.agent, "skill_registry", None)
        if not registry or not getattr(registry, "register_dynamic_skill", None):
            self.logger.warning("[Evo] No MatMasterSkillRegistry with register_dynamic_skill.")
            return {"status": "failed", "reason": "no_registry"}

        if registry.register_dynamic_skill(new_skill_path):
            self.logger.info("[Evo] Skill %s registered successfully.", new_skill_path.name)
            self.logger.warning(
                "[Evo] Skill %s registered WITHOUT sandbox testing. "
                "Run its scripts manually before relying on it in production.",
                new_skill_path.name,
            )
            return {
                "status": "completed",
                "skill_path": str(new_skill_path),
                "warning": (
                    "New skill registered without automated test verification. "
                    "Please review the generated scripts in new_skill/scripts/ "
                    "and run them manually to confirm correctness before using in subsequent tasks."
                ),
            }

        self.logger.warning("[Evo] Skill evolution failed to register.")
        return {"status": "failed", "reason": "register_failed"}
