"""Session implementations for matmaster."""

from .local import LocalSession
from .ssh import SSHSession
from .tmux import PS1_BEGIN, PS1_END, PS1_PATTERN, BashMetadata

__all__ = [
    "LocalSession",
    "SSHSession",
    "PS1_PATTERN",
    "PS1_BEGIN",
    "PS1_END",
    "BashMetadata",
]
