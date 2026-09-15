from pathlib import Path

from ..security import redact_artifact, redact_text


def sanitize_persisted_payload(value):
    return redact_artifact(value)


def sanitize_persisted_text(value):
    return redact_text(value)


class MemoryStore:
    def __init__(self, workspace):
        self.root = Path(workspace)

    def read_long_term(self):
        path = self.root / "user_memory/profile/user.md"
        return path.read_text(encoding="utf-8") if path.is_file() else ""
