"""Resolve bundled Skill paths without reading their contents or escaping the root."""

import re
from pathlib import Path


_LINK = re.compile(
    r"\[([^\]]+)\]\(((?:\./)?(?:references|scripts|assets|examples)/[^)\s]+)\)"
)
_BASE = re.compile(r"\{baseDir\}(?:/([^\s)\"'`]+))?")


def resolve_skill_refs(body, skill_dir):
    root = Path(skill_dir).resolve()
    if not root.is_dir():
        return body

    def target(value):
        file, *suffix = re.split(r"(?=[#?])", value, maxsplit=1)
        path = (root / file).resolve()
        if not path.is_relative_to(root) or not path.exists():
            return None
        return str(path) + (suffix[0] if suffix else "")

    def link(match):
        path = target(match[2])
        return f"[{match[1]}]({path})" if path is not None else match[0]

    def base(match):
        path = target(match[1]) if match[1] else str(root)
        return path if path is not None else match[0]

    # Preserve fenced examples, including unterminated fences and tilde fences.
    lines, fence = [], None
    for line in body.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence:
            lines.append(line)
            if (
                marker
                and marker[1][0] == fence[0]
                and len(marker[1]) >= len(fence)
                and not line[marker.end() :].strip()
            ):
                fence = None
        elif marker:
            fence = marker[1]
            lines.append(line)
        else:
            lines.append(_BASE.sub(base, _LINK.sub(link, line)))
    return "".join(lines)
