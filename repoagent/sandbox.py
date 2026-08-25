"""Execution-location adapters for shell tools."""

from abc import ABC, abstractmethod

from .tool_execution import ProcessOutcome, run_bounded_process


class SandboxConfigurationError(ValueError):
    pass


class SandboxAdapter(ABC):
    @property
    @abstractmethod
    def identity(self) -> str: ...

    @property
    @abstractmethod
    def is_isolated(self) -> bool: ...

    @abstractmethod
    def execute(self, command, *, cwd, env, control) -> ProcessOutcome: ...


class DirectSandboxAdapter(SandboxAdapter):
    @property
    def identity(self):
        return "direct_host"

    @property
    def is_isolated(self):
        return False

    def execute(self, command, *, cwd, env, control):
        return run_bounded_process(
            command, cwd=cwd, env=env, shell=True, control=control
        )


class IsolatedSandboxAdapter(SandboxAdapter):
    """Adapter for an injected VM/container backend with explicit isolation."""

    def __init__(self, backend, *, identity="isolated"):
        if not isinstance(identity, str) or not identity.strip():
            raise SandboxConfigurationError("sandbox identity must be non-empty")
        if not callable(getattr(backend, "execute", None)):
            raise SandboxConfigurationError("isolated backend must implement execute")
        if getattr(backend, "is_isolated", None) is not True:
            raise SandboxConfigurationError(
                "isolated backend must explicitly declare is_isolated=True"
            )
        self._backend = backend
        self._identity = identity.strip()

    @property
    def identity(self):
        return self._identity

    @property
    def is_isolated(self):
        return True

    def execute(self, command, *, cwd, env, control):
        outcome = self._backend.execute(
            command, cwd=cwd, env=dict(env), control=control
        )
        if not isinstance(outcome, ProcessOutcome):
            raise TypeError("isolated sandbox backend must return ProcessOutcome")
        return outcome


__all__ = [
    "DirectSandboxAdapter",
    "IsolatedSandboxAdapter",
    "SandboxAdapter",
    "SandboxConfigurationError",
]
