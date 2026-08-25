"""Deterministic durable-memory consolidation with safety provenance."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .security import REDACTED_VALUE


_INTENT = re.compile(r"(?i)\b(capture|remember|save|store|persist|note)\b")
_INTENT_ZH = re.compile(r"(记住|保存|记录|沉淀|长期记忆|持久记忆)")
_SECRET = re.compile(
    r"(?i)(\b(api[_ -]?key|token|secret|password)\b|sk-[A-Za-z0-9_-]{6,})"
)
_LINE_PATTERNS = (
    ("project-conventions", re.compile(r"(?i)^Project convention:\s*(.+)$")),
    ("key-decisions", re.compile(r"(?i)^Decision:\s*(.+)$")),
    ("dependency-facts", re.compile(r"(?i)^Dependency:\s*(.+)$")),
    ("user-preferences", re.compile(r"(?i)^Preference:\s*(.+)$")),
    ("project-conventions", re.compile(r"^项目约定：\s*(.+)$")),
    ("key-decisions", re.compile(r"^决策：\s*(.+)$")),
    ("dependency-facts", re.compile(r"^依赖：\s*(.+)$")),
    ("user-preferences", re.compile(r"^偏好：\s*(.+)$")),
)
_TRANSIENT_PREFIXES = (
    "current goal",
    "current blocker",
    "next step",
    "current phase",
    "key files",
    "freshness",
    "当前目标",
    "当前卡点",
    "下一步",
    "当前阶段",
    "关键文件",
    "已完成",
    "已排除",
)


@dataclass(frozen=True)
class ConsolidationCandidate:
    topic: str
    text: str
    source: str
    confidence: float

    def to_dict(self):
        return {
            "topic": self.topic,
            "text": self.text,
            "source": self.source,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class ConsolidationRejection:
    topic: str
    reason: str

    def to_dict(self):
        return {"topic": self.topic, "reason": self.reason}


@dataclass(frozen=True)
class ConsolidationResult:
    candidates: tuple[ConsolidationCandidate, ...]
    rejections: tuple[ConsolidationRejection, ...]
    explicit_intent: bool

    def to_dict(self):
        return {
            "explicit_intent": self.explicit_intent,
            "accepted_count": len(self.candidates),
            "rejected_count": len(self.rejections),
            "candidates": [item.to_dict() for item in self.candidates],
            "rejections": [item.to_dict() for item in self.rejections],
        }


class MemoryConsolidator:
    def __init__(self, redactor):
        if not callable(redactor):
            raise TypeError("memory consolidator redactor must be callable")
        self._redact = redactor

    def reject_reason(self, text):
        text = str(text or "").strip()
        lowered = text.lower()
        if not text:
            return "empty"
        redacted = str(self._redact(text))
        if redacted != text or REDACTED_VALUE in redacted or _SECRET.search(text):
            return "secret_shaped"
        if any(lowered.startswith(prefix) for prefix in _TRANSIENT_PREFIXES):
            return "transient_task_state"
        if re.search(r"(?i)\b(stdout|stderr|traceback|exit_code)\b", text):
            return "noisy_output"
        if len(text) > 220:
            return "noisy_output"
        return ""

    def consolidate(self, user_message, final_answer):
        user_text = str(user_message or "")
        explicit_intent = bool(_INTENT.search(user_text) or _INTENT_ZH.search(user_text))
        if not explicit_intent:
            return ConsolidationResult((), (), False)
        candidates = []
        rejections = []
        for line in str(final_answer or "").splitlines():
            text = line.strip()
            for topic, pattern in _LINE_PATTERNS:
                match = pattern.match(text)
                if not match:
                    continue
                note_text = match.group(1).strip()
                reason = self.reject_reason(note_text)
                if reason:
                    rejections.append(ConsolidationRejection(topic, reason))
                else:
                    candidates.append(
                        ConsolidationCandidate(
                            topic=topic,
                            text=note_text,
                            source="assistant.explicit_durable_line",
                            confidence=1.0,
                        )
                    )
                break
        return ConsolidationResult(
            tuple(candidates), tuple(rejections), explicit_intent
        )


__all__ = [
    "ConsolidationCandidate",
    "ConsolidationRejection",
    "ConsolidationResult",
    "MemoryConsolidator",
]
