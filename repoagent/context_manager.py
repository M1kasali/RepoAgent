"""Prompt 组装与上下文预算控制。

这个模块负责决定：每一轮到底把多少 prefix、memory、相关笔记、历史
以及当前用户请求送进模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from .compaction import DeterministicHistoryCompactor
from .tokenization import TokenCounter, resolve_token_counter


DEFAULT_TOTAL_TOKEN_BUDGET = 3000
DEFAULT_SEGMENT_TOKEN_BUDGETS = {
    "prefix": 900,
    "memory": 400,
    "relevant_memory": 300,
    "skills": 300,
    "history": 1300,
}
DEFAULT_SEGMENT_TOKEN_FLOORS = {
    "prefix": 300,
    "memory": 100,
    "relevant_memory": 75,
    "skills": 75,
    "history": 375,
}
# Compatibility names now carry token units. New code should use the explicit names.
DEFAULT_TOTAL_BUDGET = DEFAULT_TOTAL_TOKEN_BUDGET
DEFAULT_SECTION_BUDGETS = DEFAULT_SEGMENT_TOKEN_BUDGETS
DEFAULT_SECTION_FLOORS = DEFAULT_SEGMENT_TOKEN_FLOORS
# 当 prompt 超预算时，会优先压缩这些 section。
DEFAULT_REDUCTION_ORDER = ("skills", "relevant_memory", "history", "memory", "prefix")
CURRENT_REQUEST_SECTION = "current_request"
RELEVANT_MEMORY_LIMIT = 3


class ContextBudgetExceededError(ValueError):
    def __init__(self, *, observed_tokens, budget_tokens, token_counter):
        self.observed_tokens = int(observed_tokens)
        self.budget_tokens = int(budget_tokens)
        self.token_counter = dict(token_counter)
        super().__init__(
            "prompt cannot fit the configured token budget: "
            f"observed={self.observed_tokens}, budget={self.budget_tokens}"
        )


@dataclass(frozen=True)
class ContextSegmentDefinition:
    name: str
    source: str
    order: int
    reducible: bool
    mandatory: bool

    def __post_init__(self):
        if not self.name or not self.source:
            raise ValueError("context segment name and source must be non-empty")
        if self.order < 0:
            raise ValueError("context segment order must be non-negative")


CONTEXT_SEGMENT_DEFINITIONS = (
    ContextSegmentDefinition("prefix", "runtime.prefix", 0, True, True),
    ContextSegmentDefinition("checkpoint", "runtime.checkpoint", 1, False, False),
    ContextSegmentDefinition("memory", "memory.working", 2, True, False),
    ContextSegmentDefinition("relevant_memory", "memory.retrieval", 3, True, False),
    ContextSegmentDefinition("skills", "skill.catalog", 4, True, False),
    ContextSegmentDefinition("history", "session.history", 5, True, False),
    ContextSegmentDefinition(CURRENT_REQUEST_SECTION, "request.user", 6, False, True),
)
SEGMENT_DEFINITION_BY_NAME = {
    definition.name: definition for definition in CONTEXT_SEGMENT_DEFINITIONS
}
SECTION_ORDER = tuple(
    definition.name
    for definition in sorted(CONTEXT_SEGMENT_DEFINITIONS, key=lambda item: item.order)
)


def _validate_segment_definitions():
    if len(SEGMENT_DEFINITION_BY_NAME) != len(CONTEXT_SEGMENT_DEFINITIONS):
        raise ValueError("context segment names must be unique")
    orders = [definition.order for definition in CONTEXT_SEGMENT_DEFINITIONS]
    if len(set(orders)) != len(orders):
        raise ValueError("context segment orders must be unique")
    if SECTION_ORDER[-1] != CURRENT_REQUEST_SECTION:
        raise ValueError("current request must be the final context segment")
    current_request = SEGMENT_DEFINITION_BY_NAME[CURRENT_REQUEST_SECTION]
    if current_request.reducible or not current_request.mandatory:
        raise ValueError("current request must be mandatory and non-reducible")


_validate_segment_definitions()


def _tail_clip(text, limit):
    text = str(text)
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


@dataclass(frozen=True)
class ContextSegment:
    definition: ContextSegmentDefinition
    raw: str
    budget: int | None
    rendered: str
    details: Mapping = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "raw", str(self.raw))
        object.__setattr__(self, "rendered", str(self.rendered))
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))
        if self.budget is not None and self.budget < 0:
            raise ValueError("context segment budget must be non-negative")

    @property
    def name(self):
        return self.definition.name

    @property
    def raw_chars(self):
        return len(self.raw)

    @property
    def rendered_chars(self):
        return len(self.rendered)


# Compatibility alias for callers that imported the former render value type.
SectionRender = ContextSegment


class ContextManager:
    def __init__(
        self,
        agent,
        total_budget=None,
        section_budgets=None,
        section_floors=None,
        reduction_order=None,
        token_counter=None,
        total_token_budget=None,
        segment_token_budgets=None,
        segment_token_floors=None,
    ):
        self.agent = agent
        self.token_counter = token_counter or resolve_token_counter(agent.model_client)
        if not isinstance(self.token_counter, TokenCounter):
            raise TypeError("token_counter must implement TokenCounter")
        self.history_compactor = DeterministicHistoryCompactor(self.token_counter)
        if total_budget is not None and total_token_budget is not None:
            raise ValueError("configure total_token_budget, not both budget names")
        if section_budgets is not None and segment_token_budgets is not None:
            raise ValueError("configure segment_token_budgets, not both budget names")
        if section_floors is not None and segment_token_floors is not None:
            raise ValueError("configure segment_token_floors, not both floor names")
        configured_total = (
            total_token_budget
            if total_token_budget is not None
            else total_budget
            if total_budget is not None
            else DEFAULT_TOTAL_TOKEN_BUDGET
        )
        self.total_token_budget = int(configured_total)
        if self.total_token_budget < 1:
            raise ValueError("total_token_budget must be positive")
        self.segment_token_budgets = dict(DEFAULT_SEGMENT_TOKEN_BUDGETS)
        configured_segment_budgets = (
            segment_token_budgets
            if segment_token_budgets is not None
            else section_budgets
        )
        if configured_segment_budgets:
            self.segment_token_budgets.update(
                {
                    str(key): int(value)
                    for key, value in configured_segment_budgets.items()
                }
            )
        if any(value < 0 for value in self.segment_token_budgets.values()):
            raise ValueError("segment token budgets must be non-negative")
        configured_segment_floors = (
            segment_token_floors
            if segment_token_floors is not None
            else section_floors
        )
        self._section_floor_overrides = {
            str(key): int(value)
            for key, value in (configured_segment_floors or {}).items()
        }
        self.section_floors = self._compute_section_floors()
        self.reduction_order = tuple(reduction_order or DEFAULT_REDUCTION_ORDER)

    @property
    def total_budget(self):
        """Compatibility alias; the value is measured in tokens."""
        return self.total_token_budget

    @total_budget.setter
    def total_budget(self, value):
        self.total_token_budget = int(value)

    @property
    def section_budgets(self):
        """Compatibility alias; values are measured in tokens."""
        return self.segment_token_budgets

    @section_budgets.setter
    def section_budgets(self, value):
        self.segment_token_budgets = {
            str(key): int(item) for key, item in dict(value).items()
        }

    def build(self, user_message):
        """按预算组装一轮完整 prompt。

        为什么存在：
        仅靠用户这一轮输入，模型并不知道当前仓库状态、会话里已经读过什么、
        哪些旧信息还值得继续参考。这个函数负责把“稳定基线 + 工作记忆 +
        相关笔记 + 历史 + 当前请求”拼成真正发给模型的 prompt。

        输入 / 输出：
        - 输入：`user_message`，也就是用户当前这一轮的新请求。
        - 输出：`(prompt, metadata)`。
          `prompt` 是最终发送给模型的文本；
          `metadata` 记录了每个 section 的原始长度、裁剪后的长度、是否触发了
          预算收缩等信息，后续会进入 trace/report，便于解释这轮 prompt
          是怎么被拼出来的。

        在 agent 链路里的位置：
        它位于 `RepoAgent.ask()` 的每轮模型调用之前，是“真正发请求给模型”
        的最后一道组装工序。`WorkspaceContext` 提供稳定前缀，`LayeredMemory`
        提供工作记忆，这个函数则把它们和当前请求合成一份可控大小的 prompt。
        """
        user_message = str(user_message)
        self.section_floors = self._compute_section_floors()
        memory_enabled = True
        relevant_memory_enabled = True
        context_reduction_enabled = True
        if hasattr(self.agent, "feature_enabled"):
            memory_enabled = self.agent.feature_enabled("memory")
            relevant_memory_enabled = self.agent.feature_enabled("relevant_memory")
            context_reduction_enabled = self.agent.feature_enabled("context_reduction")
        section_texts = {
            "prefix": str(getattr(self.agent, "prefix", "")),
            "checkpoint": "",
            "memory": "Memory:\n- disabled" if not memory_enabled else str(self.agent.memory_text()),
            "history": "",
            "skills": str(
                getattr(self.agent, "skill_text", lambda: "Skills:\n- none")()
            ),
            CURRENT_REQUEST_SECTION: f"Current user request:\n{user_message}",
        }
        checkpoint_text = ""
        if hasattr(self.agent, "render_checkpoint_text"):
            checkpoint_text = str(self.agent.render_checkpoint_text() or "").strip()
        if checkpoint_text:
            section_texts["checkpoint"] = checkpoint_text
        selected_notes = []
        if memory_enabled and relevant_memory_enabled and hasattr(self.agent, "memory") and hasattr(self.agent.memory, "retrieval_candidates"):
            selected_notes = self.agent.memory.retrieval_candidates(user_message, limit=RELEVANT_MEMORY_LIMIT)
            seen_note_texts = {str(note.get("text", "")) for note in selected_notes}
            for hit in getattr(self.agent, "backend_memory_hits", ()):
                if hit.text in seen_note_texts:
                    continue
                selected_notes.append(
                    {
                        "text": hit.text,
                        "source": str(hit.metadata.get("source", "memory_backend")),
                        "kind": str(hit.metadata.get("kind", "backend")),
                        "created_at": str(hit.metadata.get("created_at", "")),
                        "score": hit.score,
                    }
                )
                seen_note_texts.add(hit.text)
            selected_notes = selected_notes[:RELEVANT_MEMORY_LIMIT]

        if not context_reduction_enabled:
            rendered = self._render_sections_without_reduction(section_texts, selected_notes=selected_notes)
            prompt = self._assemble_prompt(rendered)
            metadata = self._metadata(
                prompt=prompt,
                rendered=rendered,
                budgets={section: render.budget for section, render in rendered.items()},
                reduction_log=[],
                selected_notes=selected_notes,
                user_message=user_message,
                section_texts=section_texts,
            )
            return prompt, metadata

        budgets = dict(self.segment_token_budgets)
        if not section_texts["skills"].strip():
            budgets["skills"] = 0
        rendered = self._render_sections(section_texts, budgets, selected_notes=selected_notes)
        prompt = self._assemble_prompt(rendered)
        reduction_log = []

        # 如果 prompt 超预算，就按固定顺序不断压缩。
        # 这里的顺序体现了平台偏好：
        # 先牺牲 relevant_memory，再牺牲 history，然后才动 memory 和 prefix。
        # 最新用户请求永远不裁剪，因为那是本轮最重要的输入。
        while self._count_tokens(prompt) > self.total_token_budget:
            overflow = self._count_tokens(prompt) - self.total_token_budget
            reduced = False
            for section in self.reduction_order:
                floor = int(self.section_floors.get(section, 0))
                current_budget = int(budgets.get(section, 0))
                if current_budget <= floor:
                    continue
                new_budget = max(floor, current_budget - overflow)
                if new_budget >= current_budget:
                    continue
                reduction_log.append(
                    {
                        "section": section,
                        "before_tokens": current_budget,
                        "after_tokens": new_budget,
                        "overflow_tokens": overflow,
                    }
                )
                budgets[section] = new_budget
                rendered = self._render_sections(section_texts, budgets, selected_notes=selected_notes)
                prompt = self._assemble_prompt(rendered)
                reduced = True
                break
            if not reduced:
                break

        prompt_tokens = self._count_tokens(prompt)
        if prompt_tokens > self.total_token_budget:
            raise ContextBudgetExceededError(
                observed_tokens=prompt_tokens,
                budget_tokens=self.total_token_budget,
                token_counter=self.token_counter.metadata(),
            )

        metadata = self._metadata(
            prompt=prompt,
            rendered=rendered,
            budgets=budgets,
            reduction_log=reduction_log,
            selected_notes=selected_notes,
            user_message=user_message,
            section_texts=section_texts,
        )
        return prompt, metadata

    def _render_sections_without_reduction(self, section_texts, selected_notes=None):
        selected_notes = selected_notes or []
        relevant_lines = ["Relevant memory:"]
        if selected_notes:
            relevant_lines.extend(f"- {note['text']}" for note in selected_notes)
        else:
            relevant_lines.append("- none")
        relevant_raw = "\n".join(relevant_lines)
        history = list(getattr(self.agent, "session", {}).get("history", []))
        history_raw = self._raw_history_text(history)
        return {
            "prefix": self._segment(
                "prefix",
                raw=section_texts["prefix"],
                budget=self._count_tokens(section_texts["prefix"]),
                rendered=section_texts["prefix"],
            ),
            "checkpoint": self._segment(
                "checkpoint",
                raw=section_texts["checkpoint"],
                budget=None,
                rendered=section_texts["checkpoint"],
            ),
            "memory": self._segment(
                "memory",
                raw=section_texts["memory"],
                budget=self._count_tokens(section_texts["memory"]),
                rendered=section_texts["memory"],
            ),
            "relevant_memory": self._segment(
                "relevant_memory",
                raw=relevant_raw,
                budget=self._count_tokens(relevant_raw),
                rendered=relevant_raw,
                details={
                    "selected_notes": [note["text"] for note in selected_notes],
                    "rendered_notes": [note["text"] for note in selected_notes],
                    "selected_count": len(selected_notes),
                    "rendered_count": len(selected_notes),
                    "note_budget": 0,
                },
            ),
            "history": self._segment(
                "history",
                raw=history_raw,
                budget=self._count_tokens(history_raw),
                rendered=history_raw,
                details={
                    "rendered_entries": history_raw.splitlines()[1:],
                    "strategy": "disabled",
                    "compaction_applied": False,
                    "records": [],
                    "source_entry_count": len(history),
                    "included_entry_count": len(history),
                    "dropped_entry_count": 0,
                    "clipped_entry_count": 0,
                },
            ),
            "skills": self._segment(
                "skills",
                raw=section_texts["skills"],
                budget=self._count_tokens(section_texts["skills"]),
                rendered=section_texts["skills"],
                details={
                    "activated_ids": [
                        item.qualified_id
                        for item in getattr(self.agent, "active_skills", ())
                    ]
                },
            ),
            CURRENT_REQUEST_SECTION: self._segment(
                CURRENT_REQUEST_SECTION,
                raw=section_texts[CURRENT_REQUEST_SECTION],
                budget=None,
                rendered=section_texts[CURRENT_REQUEST_SECTION],
            ),
        }

    @staticmethod
    def _segment(name, *, raw, budget, rendered, details=None):
        return ContextSegment(
            definition=SEGMENT_DEFINITION_BY_NAME[name],
            raw=raw,
            budget=budget,
            rendered=rendered,
            details=details or {},
        )

    def _compute_section_floors(self):
        floors = {
            section: max(20, int(budget) // 4)
            for section, budget in self.segment_token_budgets.items()
        }
        floors.update(self._section_floor_overrides)
        return floors

    def _count_tokens(self, text):
        return self.token_counter.count(str(text))

    def _clip_text(self, text, token_limit):
        text = str(text)
        token_limit = int(token_limit)
        if token_limit <= 0:
            return ""
        if self._count_tokens(text) <= token_limit:
            return text
        suffix = "..."
        if self._count_tokens(suffix) > token_limit:
            return ""
        low = 0
        high = len(text)
        while low < high:
            middle = (low + high + 1) // 2
            candidate = text[:middle] + suffix
            if self._count_tokens(candidate) <= token_limit:
                low = middle
            else:
                high = middle - 1
        return text[:low] + suffix

    def _render_sections(self, section_texts, budgets, selected_notes=None):
        rendered = {}
        for section in SECTION_ORDER:
            budget = budgets.get(section)
            definition = SEGMENT_DEFINITION_BY_NAME[section]
            if not definition.reducible:
                raw = section_texts[section]
                rendered[section] = self._segment(
                    section,
                    raw=raw,
                    budget=None,
                    rendered=raw,
                )
            elif section == "relevant_memory":
                rendered[section] = self._render_relevant_memory(selected_notes or [], int(budget or 0))
            elif section == "history":
                rendered[section] = self._render_history_section(int(budget or 0))
            elif section == "skills":
                raw = section_texts.get(section, "Skills:\n- none")
                rendered[section] = self._segment(
                    section,
                    raw=raw,
                    budget=int(budget or 0),
                    rendered=self._clip_text(raw, int(budget or 0)),
                    details={
                        "activated_ids": [
                            item.qualified_id
                            for item in getattr(self.agent, "active_skills", ())
                        ]
                    },
                )
            else:
                raw = section_texts[section]
                rendered_text = self._clip_text(raw, int(budget)) if budget is not None else raw
                rendered[section] = self._segment(
                    section,
                    raw=raw,
                    budget=int(budget) if budget is not None else 0,
                    rendered=rendered_text,
                )
        return rendered

    def _render_relevant_memory(self, selected_notes, budget):
        header = "Relevant memory:"
        note_texts = [str(note.get("text", "")) for note in selected_notes if str(note.get("text", "")).strip()]
        raw_lines = [header] + [f"- {text}" for text in note_texts]
        raw = "\n".join(raw_lines) if note_texts else "\n".join([header, "- none"])
        if not note_texts:
            rendered = self._clip_text(raw, budget)
            return self._segment(
                "relevant_memory",
                raw=raw,
                budget=budget,
                rendered=rendered,
                details={
                    "selected_notes": [],
                    "rendered_notes": [],
                    "selected_count": 0,
                    "rendered_count": 0,
                    "note_budget": 0,
                },
            )

        per_note_budget = max(1, budget // len(note_texts)) if budget > 0 else 0
        rendered_notes = []
        while True:
            # 让每条 note 平分这一段的预算，避免一条超长笔记把其他笔记都挤掉。
            rendered_notes = [self._clip_text(text, per_note_budget) for text in note_texts]
            rendered = "\n".join([header] + [f"- {text}" for text in rendered_notes])
            if self._count_tokens(rendered) <= budget or per_note_budget <= 1:
                break
            per_note_budget -= 1

        if self._count_tokens(rendered) > budget:
            rendered = self._clip_text(raw, budget)
            rendered_notes = [rendered]

        return self._segment(
            "relevant_memory",
            raw=raw,
            budget=budget,
            rendered=rendered,
            details={
                "selected_notes": note_texts,
                "rendered_notes": rendered_notes,
                "selected_count": len(note_texts),
                "rendered_count": len(rendered_notes),
                "note_budget": per_note_budget,
            },
        )

    def _render_history_section(self, budget):
        history = list(getattr(self.agent, "session", {}).get("history", []))
        result = self.history_compactor.compact(
            history,
            budget,
            file_summary_lookup=self._reusable_file_summary_record,
        )
        return self._segment(
            "history",
            raw=result.raw,
            budget=budget,
            rendered=result.rendered,
            details={
                "rendered_entries": result.rendered.splitlines()[1:],
                **result.details(),
            },
        )

    def _reusable_file_summary_record(self, path):
        memory = getattr(self.agent, "memory", None)
        if memory is None or not hasattr(memory, "to_dict"):
            return None
        snapshot = memory.to_dict()
        summary = snapshot.get("file_summaries", {}).get(str(path), {})
        if not str(summary.get("summary", "")).strip():
            return None
        return summary

    def _raw_history_text(self, history):
        return self.history_compactor.raw_history(history)

    def _assemble_prompt(self, rendered):
        # 顺序是刻意设计的：稳定规则放前面，最新请求放最后。
        return "\n\n".join(
            rendered[definition.name].rendered
            for definition in sorted(
                CONTEXT_SEGMENT_DEFINITIONS, key=lambda item: item.order
            )
            if rendered[definition.name].rendered
        ).strip()

    def _metadata(self, prompt, rendered, budgets, reduction_log, selected_notes, user_message, section_texts):
        section_metadata = {}
        for section in SECTION_ORDER:
            segment = rendered[section]
            definition = segment.definition
            section_metadata[section] = {
                "source": definition.source,
                "order": definition.order,
                "reducible": definition.reducible,
                "mandatory": definition.mandatory,
                "raw_chars": segment.raw_chars,
                "raw_tokens": self._count_tokens(segment.raw),
                "budget_chars": None,
                "budget_tokens": segment.budget,
                "rendered_chars": segment.rendered_chars,
                "rendered_tokens": self._count_tokens(segment.rendered),
            }
        prompt_tokens = self._count_tokens(prompt)
        return {
            "prompt_chars": len(prompt),
            "prompt_tokens": prompt_tokens,
            "prompt_budget_chars": None,
            "prompt_token_budget": self.total_token_budget,
            "prompt_over_budget": prompt_tokens > self.total_token_budget,
            "prompt_over_token_budget": prompt_tokens > self.total_token_budget,
            "token_counter": self.token_counter.metadata(),
            "section_order": list(SECTION_ORDER),
            "section_budgets": {
                section: rendered[section].budget
                for section in SECTION_ORDER
            },
            "segment_token_budgets": {
                section: rendered[section].budget for section in SECTION_ORDER
            },
            "segment_manifest": [
                {
                    "name": definition.name,
                    "source": definition.source,
                    "order": definition.order,
                    "reducible": definition.reducible,
                    "mandatory": definition.mandatory,
                    "present": bool(rendered[definition.name].rendered),
                }
                for definition in sorted(
                    CONTEXT_SEGMENT_DEFINITIONS, key=lambda item: item.order
                )
            ],
            "sections": section_metadata,
            "budget_reductions": reduction_log,
            "reduction_order": list(self.reduction_order),
            "relevant_memory": {
                "limit": RELEVANT_MEMORY_LIMIT,
                "selected_count": len(selected_notes),
                "selected_notes": [note["text"] for note in selected_notes],
                "selected_sources": [str(note.get("source", "")).strip() for note in selected_notes],
                "selected_kinds": [str(note.get("kind", "episodic")).strip() or "episodic" for note in selected_notes],
                "selected_durable_count": sum(
                    1 for note in selected_notes if (str(note.get("kind", "episodic")).strip() or "episodic") == "durable"
                ),
                "raw_chars": rendered["relevant_memory"].raw_chars,
                "raw_tokens": self._count_tokens(rendered["relevant_memory"].raw),
                "rendered_chars": rendered["relevant_memory"].rendered_chars,
                "rendered_tokens": self._count_tokens(
                    rendered["relevant_memory"].rendered
                ),
                "rendered_notes": list(rendered["relevant_memory"].details.get("rendered_notes", [])),
                "rendered_count": int(rendered["relevant_memory"].details.get("rendered_count", 0)),
            },
            "history": {
                "raw_chars": rendered["history"].raw_chars,
                "raw_tokens": self._count_tokens(rendered["history"].raw),
                "rendered_chars": rendered["history"].rendered_chars,
                "rendered_tokens": self._count_tokens(rendered["history"].rendered),
                "older_entries_count": int(rendered["history"].details.get("older_entries_count", 0)),
                "collapsed_duplicate_reads": int(rendered["history"].details.get("collapsed_duplicate_reads", 0)),
                "reused_file_summary_count": int(rendered["history"].details.get("reused_file_summary_count", 0)),
                "summarized_tool_count": int(rendered["history"].details.get("summarized_tool_count", 0)),
                "compaction_strategy": str(rendered["history"].details.get("strategy", "disabled")),
                "compaction_applied": bool(rendered["history"].details.get("compaction_applied", False)),
                "compaction_provenance_digest": str(rendered["history"].details.get("provenance_digest", "")),
                "compaction_records": list(rendered["history"].details.get("records", [])),
                "source_entry_count": int(rendered["history"].details.get("source_entry_count", 0)),
                "included_entry_count": int(rendered["history"].details.get("included_entry_count", 0)),
                "dropped_entry_count": int(rendered["history"].details.get("dropped_entry_count", 0)),
                "clipped_entry_count": int(rendered["history"].details.get("clipped_entry_count", 0)),
                "raw_digest": str(rendered["history"].details.get("raw_digest", "")),
                "rendered_digest": str(rendered["history"].details.get("rendered_digest", "")),
            },
            "skills": {
                "activated_ids": list(
                    rendered["skills"].details.get("activated_ids", [])
                ),
                "activated_count": len(
                    rendered["skills"].details.get("activated_ids", [])
                ),
                "raw_tokens": self._count_tokens(rendered["skills"].raw),
                "rendered_tokens": self._count_tokens(
                    rendered["skills"].rendered
                ),
            },
            "current_request": {
                "text": user_message,
                "raw_chars": len(user_message),
                "raw_tokens": self._count_tokens(user_message),
                "rendered_chars": len(user_message),
                "rendered_tokens": self._count_tokens(user_message),
                "section_chars": len(rendered[CURRENT_REQUEST_SECTION].rendered),
                "section_tokens": self._count_tokens(
                    rendered[CURRENT_REQUEST_SECTION].rendered
                ),
            },
        }
