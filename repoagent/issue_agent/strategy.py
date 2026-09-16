"""Explicit host-owned Skill intervention for Issue repair experiments."""

from pathlib import Path

SKILL_PATH = "skills/issue-repair/SKILL.md"
SKILL_HEADER = (
    "---\nid: issue-repair\nname: Issue repair\n"
    "description: Verified repair workflow for repository issues\n"
    "version: 1\nalways: true\n---\n"
)


def validate_strategy(text):
    if (not isinstance(text, str) or not text.startswith(SKILL_HEADER)
            or not text[len(SKILL_HEADER):].strip() or "\0" in text
            or len(text.encode("utf-8")) > 8192):
        raise ValueError("strategy requires fixed issue-repair Skill header and bounded body")
    return text


def install_strategy(bundle, text):
    text = validate_strategy(text)
    path = Path(bundle) / SKILL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(text)
    return path


def candidate_config(config, proposal, *, expected_base_commit):
    """Apply only a matching Evolver Skill candidate to a fresh Issue config.

    This prepares an isolated experiment, not production activation. Existing
    Evolver acceptance/approval gates remain required for activation.
    """
    from ..evolver.contracts import EvolutionLabel, sha256_bytes
    from .execution import validate_config

    config = validate_config(config)
    manifest = proposal.manifest
    if (manifest.base_commit != expected_base_commit
            or manifest.label is not EvolutionLabel.SKILL
            or set(proposal.content) != {SKILL_PATH}
            or len(manifest.mutations) != 1):
        raise ValueError("candidate must target only the pinned Issue repair Skill")
    before = config.get("strategy_skill")
    expected_before = sha256_bytes(before.encode()) if before is not None else None
    if manifest.mutations[0].before_sha256 != expected_before:
        raise ValueError("candidate baseline Skill differs from experiment baseline")
    content = proposal.content[SKILL_PATH].decode("utf-8")
    config["strategy_skill"] = validate_strategy(content)
    return config
