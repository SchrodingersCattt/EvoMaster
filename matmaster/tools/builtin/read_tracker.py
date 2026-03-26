"""ReadTracker -- shared state for Read-Before-Modify protocol.

Tracks which files have been read in the current agent run.
WriteTool and EditTool consult the tracker before modifying files,
enforcing the D-02 Read-Before-Modify safety protocol.

Path normalization uses posixpath.normpath() because the remote session
environment is always Linux. No Windows path handling needed.
"""

from __future__ import annotations

import posixpath


class ReadTracker:
    """Track which remote files have been read in the current run.

    Used by WriteTool/EditTool to enforce Read-Before-Modify:
    - mark_read(path) after a successful read
    - has_been_read(path) before allowing write/edit
    - clear() at run start to reset state
    """

    def __init__(self) -> None:
        self._read_files: set[str] = set()

    def mark_read(self, path: str) -> None:
        """Record that *path* has been read. Normalizes via posixpath."""
        self._read_files.add(posixpath.normpath(path))

    def has_been_read(self, path: str) -> bool:
        """Check whether *path* was previously read. Normalizes via posixpath."""
        return posixpath.normpath(path) in self._read_files

    def clear(self) -> None:
        """Reset all tracked state (call at run start)."""
        self._read_files.clear()
