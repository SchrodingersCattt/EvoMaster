"""BasePlayground 的动态 Session 附着与远程技能同步（Mixin）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from evomaster.agent.session import (
    BaseSession,
    LocalSession,
    SSHSession,
    SSHSessionConfig,
)


class PlaygroundSessionMixin:
    """运行时替换 Session、SSH 便捷方法与技能目录同步。"""

    logger: Any
    config: Any
    session: Any
    agent: Any

    def attach_session(self, session: BaseSession) -> None:
        """Replace the current session with *session* at runtime.

        Closes the previous session (if open and remote), assigns the new
        one, opens it, and propagates the reference to the agent so that
        subsequent tool calls use the new session.

        Args:
            session: An already-configured but not-yet-opened BaseSession
                     (SSHSession, DockerSession, etc.).
        """
        if self.session is not None and self.session.is_open:
            if not isinstance(self.session, LocalSession):
                try:
                    self.session.close()
                    self.logger.info('Previous session closed before attach')
                except Exception as e:
                    self.logger.warning(f"Error closing previous session: {e}")

        self.session = session

        if not self.session.is_open:
            self.session.open()
            self.logger.info(f"Attached session opened: {type(session).__name__}")

        if self.agent is not None:
            self.agent.session = self.session
            self.logger.debug('Agent session reference updated')

    def attach_ssh_session(
        self,
        host: str,
        port: int = 22,
        username: str = 'root',
        password: str | None = None,
        key_file: str | None = None,
        working_dir: str = '/personal/workspace',
        session_id: str | None = None,
        **kwargs,
    ) -> SSHSession:
        """Create and attach an SSHSession from explicit credentials.

        Convenience wrapper around :meth:`attach_session` for the common
        case where the caller has ``(host, port, password)`` from an
        external container allocator (e.g. Bohrium).

        Args:
            session_id: When provided, the remote working directory becomes
                ``{working_dir}/{session_id}`` to isolate concurrent sessions.

        Returns:
            The opened SSHSession instance.
        """
        if session_id:
            working_dir = f"{working_dir.rstrip('/')}/{session_id}"
        config = SSHSessionConfig(
            host=host,
            port=port,
            username=username,
            password=password,
            key_file=key_file,
            working_dir=working_dir,
            workspace_path=working_dir,
            **kwargs,
        )
        session = SSHSession(config)
        self.attach_session(session)
        self.logger.info('SSH workspace: %s', working_dir)
        return session

    def detach_session(self) -> None:
        """Close and remove the current session.

        After this call ``self.session`` is ``None``.  The caller (external
        backend) is responsible for releasing the underlying container.
        """
        if self.session is not None and self.session.is_open:
            if not isinstance(self.session, LocalSession):
                try:
                    self.session.close()
                    self.logger.info('Session detached and closed')
                except Exception as e:
                    self.logger.warning(f"Error closing session during detach: {e}")

        self.session = None

        if self.agent is not None:
            self.agent.session = None
            self.logger.debug('Agent session reference cleared')

    def sync_skills_to_remote(
        self,
        remote_base: str = '/personal/workspace/.evomaster',
    ) -> None:
        """Upload skills directories to the remote SSH node and set remote_project_root.

        Only effective when the current session is an SSHSession.
        Subclasses with additional skill tiers should override this method.
        """
        if not isinstance(self.session, SSHSession):
            self.logger.debug('sync_skills_to_remote: skipped (not an SSH session)')
            return

        env = self.session._env
        exclude = {
            '__pycache__',
            '.git',
            'node_modules',
            '.mypy_cache',
            '.pytest_cache',
            'SKILL.md',
        }

        config_dict = self.config.model_dump()
        skills_config = config_dict.get('skills', {})
        skills_root_rel = skills_config.get('skills_root', 'evomaster/skills')
        skills_root = Path(skills_root_rel)
        if not skills_root.is_absolute():
            skills_root = Path(__file__).resolve().parent.parent.parent / skills_root

        if skills_root.is_dir():
            remote_skills = f"{remote_base}/{skills_root_rel}"
            env.upload_directory(str(skills_root), remote_skills, exclude=exclude)

        self.session.remote_project_root = remote_base
        # Default no-op values for user skill path remapping used by skill.py.
        # Subclasses that support user skills (e.g. MatMasterPlayground) will
        # override these with the actual paths after uploading user skills.
        if not hasattr(self.session, 'remote_user_skills_root'):
            self.session.remote_user_skills_root = None
        if not hasattr(self.session, 'local_user_skills_root'):
            self.session.local_user_skills_root = None
        self.logger.info(
            'sync_skills_to_remote: done, remote_project_root=%s', remote_base
        )
