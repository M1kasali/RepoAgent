"""Query-conditioned host profile rendering."""
import re
from .persistence import MemoryStore as _Store
_RELEVANCE_TOKEN_RE = re.compile(r"\w{2,}", re.UNICODE)

def _tokenize_for_relevance(text: str) -> set[str]:
    """返回 Lowercased、长度至少 2 的 Alphanumeric/CJK Runs Set。

    这是 Proper Tokenization 的 Lightweight Stand-in：Chinese Run 会成为一个 Multi-char Token，例如三字
    Phrase 是 One Token；English 按 Whitespace + Punctuation 分割。它服务 Profile Section Lexical
    Relevance，不理解同义词、词形或语义。
    """
    return {t.lower() for t in _RELEVANCE_TOKEN_RE.findall(text)}


def _parse_user_md_sections(content: str) -> dict[str, str]:
    """把 ``content`` 中每个 H2 Section 解析为 ``{H2_heading_line: body}``。

    First H2 之前的 H1 Preamble 被 Drop；Body 是 Heading 后到 Next H2 或 EOF 之间的文本，移除首尾 Blank
    Lines。Dict Insertion Order 与 Source File 一致，供后续自然渲染。重复 H2 Heading 会由后出现者覆盖，
    当前格式约定要求 Heading 唯一。
    """
    lines = content.splitlines()
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buf).strip("\n")
            current = line.strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip("\n")
    return sections


def _score_section_relevance(query: str, heading: str, body: str) -> float:
    """计算 Lexical-overlap Relevance，即 Query Vocabulary 在 Heading/Body 中的重合量。

    Heading Hit 权重为 Body 的 3x，因为 Section Titles 短且 Intentional；例如询问 ``Projects`` 应可靠拉取
    ``## Projects``。空 Query Tokens 返回 0。分数只用于 Profile Section Selection，不是 Memory Fact 的
    真实性或任务相关性概率。
    """
    q_tokens = _tokenize_for_relevance(query)
    if not q_tokens:
        return 0.0
    heading_hits = len(q_tokens & _tokenize_for_relevance(heading))
    body_hits = len(q_tokens & _tokenize_for_relevance(body))
    return heading_hits * 3.0 + body_hits


class MemoryStore(_Store):
    _SECTION_READ_TOP_K = 2
    _NOTES_HEADING_PREFIX = "## Notes"

    def get_memory_context(
        self,
        current_message: str | None = None,
    ) -> str:
        """返回要嵌入 Agent System Prompt 的 Memory Block。

        ``current_message=None`` 或 Empty 时返回 Full ``user.md`` Dump，适合 Cold-start Session 或无 User
        Query Ping。提供 Message 时，解析 H2 Sections、按 Lexical Overlap 评分，选择默认 Top-K=2，并加入
        ``## Notes`` Catchall；保留的 Sections 按 Original File Order 呈现。

        Section Parsing/Selection 无结果时 Fall Back 到 Full Dump；文件空则返回空字符串。返回 Block 只
        表示选中候选，仍需 Context Assembler 真正放进 Provider Request。
        """
        long_term = self.read_long_term()
        if not long_term:
            return ""
        if not current_message or not current_message.strip():
            return f"## Long-term Memory\n{long_term}"
        sections = _parse_user_md_sections(long_term)
        if not sections:
            return f"## Long-term Memory\n{long_term}"
        selected = self._select_relevant_sections(
            current_message,
            sections,
            top_k=self._SECTION_READ_TOP_K,
        )
        if not selected:
            return f"## Long-term Memory\n{long_term}"
        body = "\n\n".join(f"{heading}\n\n{section_body}".rstrip() for heading, section_body in selected.items())
        return f"## Long-term Memory\n\n{body}\n"


    @classmethod
    def _select_relevant_sections(
        cls,
        query: str,
        sections: dict[str, str],
        top_k: int = 2,
    ) -> dict[str, str]:
        """为每个 Section 打分，保留 Score > 0 的 Top-K，并加入 ``## Notes`` Catchall。

        Score 为 0 的 Sections **NOT Included** as Filler，否则 Tied-zero Content 会 Leak 进 Prompt。Notes
        Heading Prefix 始终保留。Returned Dict 按 Source File Order 重建，确保 Predictable Rendering，而
        不是按 Score 排列。
        """
        scored = [(heading, body, _score_section_relevance(query, heading, body)) for heading, body in sections.items()]
        scored.sort(key=lambda x: x[2], reverse=True)
        keep_keys: set[str] = {h for h, _, score in scored[:top_k] if score > 0}
        for heading in sections:
            if heading.startswith(cls._NOTES_HEADING_PREFIX):
                keep_keys.add(heading)
        return {h: b for h, b in sections.items() if h in keep_keys}
