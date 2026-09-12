"""Controlled text-edit semantics with byte-stable untouched surroundings."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class PreparedPatch:
    content: bytes
    replacements: int
    match_mode: str


def _whitespace_spans(text, old):
    requested = old.split("\n")
    terminal_newline = requested[-1] == ""
    if terminal_newline:
        requested.pop()
    stripped = [line.strip() for line in requested]
    if not any(stripped):
        return []
    lines = list(re.finditer(r"[^\n]+\n?|\n", text))
    content = [line.group().removesuffix("\n").removesuffix("\r") for line in lines]
    normalized = [line.strip() for line in content]
    spans = []
    for start in range(len(lines) - len(stripped) + 1):
        end = start + len(stripped) - 1
        if normalized[start : end + 1] != stripped:
            continue
        if terminal_newline and not lines[end].group().endswith("\n"):
            continue
        finish = (
            lines[end].end()
            if terminal_newline
            else lines[end].start() + len(content[end])
        )
        spans.append((lines[start].start(), finish))
    return spans


def prepare_text_patch(raw, old_text, new_text, *, replace_all=False):
    if not old_text:
        raise ValueError("old_text must not be empty")
    if type(replace_all) is not bool:
        raise ValueError("replace_all must be boolean")
    text = raw.decode("utf-8")
    normalized_old = old_text.replace("\r\n", "\n")
    pattern = r"\r?\n".join(re.escape(line) for line in normalized_old.split("\n"))
    spans = [match.span() for match in re.finditer(pattern, text)]
    mode = "exact"
    if not spans:
        spans = _whitespace_spans(text, normalized_old)
        mode = "line_whitespace"
    if not spans or (len(spans) != 1 and not replace_all):
        raise ValueError(
            f"old_text must occur exactly once, found {len(spans)}; use replace_all=true only for intended multiple matches"
        )
    if any(current[0] < previous[1] for previous, current in zip(spans, spans[1:])):
        raise ValueError("patch matches overlap; provide more context")
    pieces, cursor = [], 0
    for start, end in spans:
        matched = text[start:end]
        # Include the following terminator for single-line fallback matches.
        sample = matched if "\n" in matched else text[start:]
        newline = sample.find("\n")
        if newline < 0:
            sample, newline = text, text.find("\n")
        ending = "\r\n" if newline > 0 and sample[newline - 1] == "\r" else "\n"
        replacement = new_text.replace("\r\n", "\n").replace("\n", ending)
        pieces.extend((text[cursor:start], replacement))
        cursor = end
    pieces.append(text[cursor:])
    return PreparedPatch("".join(pieces).encode("utf-8"), len(spans), mode)
