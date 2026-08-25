"""Model context-window reservation and request admission contracts."""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_CONTEXT_WINDOW_TOKENS = 32768


class ContextWindowConfigurationError(ValueError):
    pass


class ContextWindowExceededError(ValueError):
    def __init__(self, admission):
        self.admission = admission
        super().__init__(
            "model request exceeds the configured context window: "
            f"input={admission.prompt_tokens}, "
            f"reserved_output={admission.reserved_output_tokens}, "
            f"window={admission.context_window_tokens}"
        )


@dataclass(frozen=True)
class ContextWindowAdmission:
    context_window_tokens: int
    configured_input_tokens: int
    effective_input_tokens: int
    reserved_output_tokens: int
    prompt_tokens: int
    window_source: str

    @property
    def total_reserved_tokens(self):
        return self.prompt_tokens + self.reserved_output_tokens

    @property
    def headroom_tokens(self):
        return self.context_window_tokens - self.total_reserved_tokens

    @property
    def admitted(self):
        return (
            self.prompt_tokens <= self.effective_input_tokens
            and self.total_reserved_tokens <= self.context_window_tokens
        )

    def to_dict(self):
        return {
            "context_window_tokens": self.context_window_tokens,
            "configured_input_tokens": self.configured_input_tokens,
            "effective_input_tokens": self.effective_input_tokens,
            "reserved_output_tokens": self.reserved_output_tokens,
            "prompt_tokens": self.prompt_tokens,
            "total_reserved_tokens": self.total_reserved_tokens,
            "headroom_tokens": self.headroom_tokens,
            "admitted": self.admitted,
            "window_source": self.window_source,
        }


@dataclass(frozen=True)
class ContextWindowBudget:
    context_window_tokens: int
    configured_input_tokens: int
    reserved_output_tokens: int
    window_source: str = "runtime-default"

    def __post_init__(self):
        for name in (
            "context_window_tokens",
            "configured_input_tokens",
            "reserved_output_tokens",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ContextWindowConfigurationError(
                    f"{name} must be a positive integer"
                )
        if self.reserved_output_tokens >= self.context_window_tokens:
            raise ContextWindowConfigurationError(
                "reserved output tokens must be smaller than the context window"
            )
        if not isinstance(self.window_source, str) or not self.window_source.strip():
            raise ContextWindowConfigurationError(
                "window_source must be a non-empty string"
            )
        object.__setattr__(self, "window_source", self.window_source.strip())

    @property
    def available_input_tokens(self):
        return self.context_window_tokens - self.reserved_output_tokens

    @property
    def effective_input_tokens(self):
        return min(self.configured_input_tokens, self.available_input_tokens)

    def admit(self, prompt_tokens):
        if isinstance(prompt_tokens, bool) or not isinstance(prompt_tokens, int):
            raise TypeError("prompt_tokens must be an integer")
        if prompt_tokens < 0:
            raise ValueError("prompt_tokens must not be negative")
        admission = ContextWindowAdmission(
            context_window_tokens=self.context_window_tokens,
            configured_input_tokens=self.configured_input_tokens,
            effective_input_tokens=self.effective_input_tokens,
            reserved_output_tokens=self.reserved_output_tokens,
            prompt_tokens=prompt_tokens,
            window_source=self.window_source,
        )
        if not admission.admitted:
            raise ContextWindowExceededError(admission)
        return admission

    def to_dict(self):
        return {
            "context_window_tokens": self.context_window_tokens,
            "configured_input_tokens": self.configured_input_tokens,
            "available_input_tokens": self.available_input_tokens,
            "effective_input_tokens": self.effective_input_tokens,
            "reserved_output_tokens": self.reserved_output_tokens,
            "window_source": self.window_source,
        }


__all__ = [
    "ContextWindowAdmission",
    "ContextWindowBudget",
    "ContextWindowConfigurationError",
    "ContextWindowExceededError",
    "DEFAULT_CONTEXT_WINDOW_TOKENS",
]
