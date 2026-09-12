import pytest

from repoagent.tools import tool_patch_file, validate_tool
from test_patch_line_endings import context


def patch(root, raw, old, new, **options):
    path = root / "code.py"
    path.write_bytes(raw)
    args = {"path": "code.py", "old_text": old, "new_text": new, **options}
    validate_tool(context(root), "patch_file", args)
    return tool_patch_file(context(root), args), path.read_bytes()


def test_unique_line_whitespace_fallback(tmp_path):
    result, raw = patch(
        tmp_path,
        b"before\r\n    x = 1   \r\n    y = 2\r\nafter\n",
        "x = 1\ny = 2",
        "    x = 3\n    y = 4",
    )
    assert raw == b"before\r\n    x = 3\r\n    y = 4\r\nafter\n"
    assert result.metadata["patch_match_mode"] == "line_whitespace"
    assert result.metadata["patch_replacements"] == 1


def test_replace_all_handles_different_whitespace_and_local_endings(tmp_path):
    result, raw = patch(
        tmp_path,
        b"    x  \r\n    y\r\nkeep\n\tx\n\ty\n",
        "x\ny",
        "z\nw",
        replace_all=True,
    )
    assert raw == b"z\r\nw\r\nkeep\nz\nw\n"
    assert result.metadata["patch_replacements"] == 2


def test_exact_matches_take_priority_over_whitespace_fallback(tmp_path):
    result, raw = patch(
        tmp_path, b"x\ny\n    x\n    y\n", "x\ny", "z", replace_all=True
    )
    assert raw == b"z\n    x\n    y\n"
    assert result.metadata["patch_match_mode"] == "exact"


def test_ambiguous_fallback_requires_explicit_replace_all(tmp_path):
    raw = b"    x\n    y\n\tx\n\ty\n"
    with pytest.raises(ValueError, match="exactly once"):
        patch(tmp_path, raw, "x\ny", "z")
    assert (tmp_path / "code.py").read_bytes() == raw


def test_replace_all_refuses_overlapping_fallback_windows(tmp_path):
    raw = b"    x\n    x\n    x\n"
    with pytest.raises(ValueError, match="overlap"):
        patch(tmp_path, raw, "x\nx", "z", replace_all=True)
    assert (tmp_path / "code.py").read_bytes() == raw


@pytest.mark.parametrize("value", ["true", "false", 1, 0, None])
def test_replace_all_is_strict_boolean(tmp_path, value):
    with pytest.raises(ValueError):
        patch(tmp_path, b"x x", "x", "z", replace_all=value)
    assert (tmp_path / "code.py").read_bytes() == b"x x"


def test_fallback_consumes_requested_terminal_newline_only(tmp_path):
    _, raw = patch(
        tmp_path,
        b"    first \r\n    second\r\nsuffix\n",
        "first\nsecond\n",
        "replacement\n",
    )
    assert raw == b"replacement\r\nsuffix\n"


@pytest.mark.parametrize("old", ["ab\ncd", "ax\ncd", "a b\nc d"])
def test_fallback_does_not_fuzz_characters_or_internal_spaces(tmp_path, old):
    raw = b"    a  b\n    c  d\n"
    with pytest.raises(ValueError):
        patch(tmp_path, raw, old, "replacement")
    assert (tmp_path / "code.py").read_bytes() == raw


def test_exact_replace_all_does_not_revisit_newly_inserted_text(tmp_path):
    result, raw = patch(tmp_path, b"x\r\nx\n", "x", "xx\ny", replace_all=True)
    assert raw == b"xx\r\ny\r\nxx\ny\n"
    assert result.metadata["patch_replacements"] == 2


def test_gateway_reports_match_metadata_and_preserves_approval(tmp_path):
    from test_tool_gateway import build_agent

    path = tmp_path / "code.py"
    original = b"    a  \r\n    b\r\n"
    path.write_bytes(original)
    args = {"path": "code.py", "old_text": "a\nb", "new_text": "c"}
    denied_agent = build_agent(tmp_path, approval_policy="never")
    denied = denied_agent.execute_tool("patch_file", args)
    assert denied.status != "ok"
    assert path.read_bytes() == original
    allowed_agent = build_agent(tmp_path)
    result = allowed_agent.execute_tool("patch_file", args)
    assert result.status == "ok"
    assert result.metadata["patch_match_mode"] == "line_whitespace"
    assert result.metadata["patch_replacements"] == 1
    assert path.read_bytes() == b"c\r\n"


def test_runtime_accepts_explicit_json_replace_all(tmp_path):
    import json
    from test_tool_gateway import build_agent

    path = tmp_path / "code.py"
    path.write_bytes(b"x = 1\r\nx = 1\r\n")
    call = {
        "name": "patch_file",
        "args": {
            "path": "code.py",
            "old_text": "x = 1",
            "new_text": "x = 2",
            "replace_all": True,
        },
    }
    agent = build_agent(
        tmp_path,
        outputs=[
            "<tool>" + json.dumps(call) + "</tool>",
            "<final>Updated both assignments.</final>",
        ],
    )
    assert "Updated" in str(agent.ask("Update both assignments."))
    assert path.read_bytes() == b"x = 2\r\nx = 2\r\n"


def test_execution_rechecks_fallback_ambiguity_after_preflight(tmp_path):
    path = tmp_path / "code.py"
    original = b"    a\n    b\n"
    path.write_bytes(original)
    args = {"path": "code.py", "old_text": "a\nb", "new_text": "c"}
    validate_tool(context(tmp_path), "patch_file", args)
    path.write_bytes(original * 2)
    with pytest.raises(ValueError, match="exactly once"):
        tool_patch_file(context(tmp_path), args)
    assert path.read_bytes() == original * 2
