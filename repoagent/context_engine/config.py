from dataclasses import dataclass


@dataclass(frozen=True)
class ContextConfig:
    engine: str = "unified"
    fast_path_threshold: float = 0.60
    curator_model: str = "gemini-2.5-flash"
    curator_timeout_seconds: float = 30.0
    relevance_decay: float = 0.95
    relevance_reference_boost: float = 0.15
    protect_first_n: int = 3
    archive_dir: str = "memory/.curator/archive"

    def __post_init__(self):
        from pathlib import PurePosixPath

        if not 0 <= self.fast_path_threshold <= 1:
            raise ValueError("fast_path_threshold must be between zero and one")
        if self.curator_timeout_seconds <= 0 or self.protect_first_n < 0:
            raise ValueError("invalid curator timeout or protected history count")
        path = PurePosixPath(self.archive_dir)
        if path.is_absolute() or ".." in path.parts or "\\" in self.archive_dir:
            raise ValueError("archive_dir must remain inside the state directory")
