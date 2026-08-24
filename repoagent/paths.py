"""Stable filesystem locations for RepoAgent state."""

from pathlib import Path


STATE_DIR_NAME = ".repoagent"
LEGACY_STATE_DIR_NAME = ".pico"


def workspace_state_root(workspace_root):
    """Use new state storage, while continuing an existing legacy workspace."""
    workspace_root = Path(workspace_root)
    preferred = workspace_root / STATE_DIR_NAME
    legacy = workspace_root / LEGACY_STATE_DIR_NAME
    if preferred.exists() or not legacy.exists():
        return preferred
    return legacy
