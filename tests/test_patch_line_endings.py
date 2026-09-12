import pytest

from repoagent.tool_context import ToolContext
from repoagent.tools import tool_patch_file, validate_tool


def context(root):
    return ToolContext(
        root=root,
        path_resolver=lambda path: root / path,
        shell_env_provider=dict,
        depth=0,
        max_depth=0,
        spawn_delegate=lambda args: "unused",
    )


@pytest.mark.parametrize("old", ["first\nsecond", "first\r\nsecond"])
def test_patch_preserves_crlf_and_accepts_both_prompt_line_endings(tmp_path, old):
    path = tmp_path / "code.py"
    path.write_bytes(b"first\r\nsecond\r\nuntouched\r\n")
    args = {"path": "code.py", "old_text": old, "new_text": "changed\nblock"}
    ctx = context(tmp_path)
    validate_tool(ctx, "patch_file", args)
    tool_patch_file(ctx, args)
    assert path.read_bytes() == b"changed\r\nblock\r\nuntouched\r\n"


def test_patch_does_not_normalize_untouched_mixed_line_endings(tmp_path):
    path = tmp_path / "code.py"
    path.write_bytes(b"prefix\nfirst\r\nsecond\r\nsuffix\n")
    tool_patch_file(
        context(tmp_path),
        {"path": "code.py", "old_text": "first\nsecond", "new_text": "changed\nblock"},
    )
    assert path.read_bytes() == b"prefix\nchanged\r\nblock\r\nsuffix\n"


@pytest.mark.parametrize(
    "raw,old,new,expected",
    [
        (b"a\nb\n", "a\r\nb", "x\r\ny", b"x\ny\n"),
        (b"a\r\nb", "a", "x\ny", b"x\r\ny\r\nb"),
        (b"a\r\nb", "b", "", b"a\r\n"),
        (b"a", "a", "b", b"b"),
        (b"\xef\xbb\xbfa\r\nb", "a", "x", b"\xef\xbb\xbfx\r\nb"),
        ("你好\r\n世界".encode(), "世界", "朋友", "你好\r\n朋友".encode()),
        (b"x[0] = 1\r\n", "x[0] = 1", "x[0] = 2", b"x[0] = 2\r\n"),
    ],
)
def test_patch_byte_contract(tmp_path, raw, old, new, expected):
    path = tmp_path / "file"
    path.write_bytes(raw)
    path.chmod(0o755)
    before_mode = path.stat().st_mode
    tool_patch_file(
        context(tmp_path), {"path": "file", "old_text": old, "new_text": new}
    )
    assert path.read_bytes() == expected
    assert path.stat().st_mode == before_mode


@pytest.mark.parametrize(
    "raw,old",
    [
        (b"same\r\nblock\nsame\nblock", "same\nblock"),
        (b"    indented\r\n", "  indented\nextra"),
        (b"alpha\r\n", "missing"),
        (b"binary\xff", "binary"),
    ],
)
def test_rejected_patch_does_not_write(tmp_path, raw, old):
    path = tmp_path / "file"
    path.write_bytes(raw)
    args = {"path": "file", "old_text": old, "new_text": "new"}
    for operation in (
        lambda: validate_tool(context(tmp_path), "patch_file", args),
        lambda: tool_patch_file(context(tmp_path), args),
    ):
        with pytest.raises(ValueError):
            operation()
        assert path.read_bytes() == raw


def test_patch_revalidates_after_preflight(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"old\r\n")
    args = {"path": "file", "old_text": "old", "new_text": "new"}
    validate_tool(context(tmp_path), "patch_file", args)
    path.write_bytes(b"user-change\r\n")
    with pytest.raises(ValueError, match="found 0"):
        tool_patch_file(context(tmp_path), args)
    assert path.read_bytes() == b"user-change\r\n"


def test_coding_turn_reads_patches_and_executes_check(tmp_path):
    import shlex
    import sys
    from test_tool_gateway import build_agent

    path = tmp_path / "calculator.py"
    path.write_bytes(b"def add(a, b):\r\n    return a - b\r\n")
    command = (
        shlex.quote(sys.executable)
        + ' -c "from calculator import add; assert add(2, 3) == 5"'
    )
    agent = build_agent(
        tmp_path,
        outputs=[
            '<tool name="read_file" path="calculator.py" />',
            '<tool name="patch_file" path="calculator.py"><old_text>    return a - b</old_text><new_text>    return a + b</new_text></tool>',
            '<tool name="run_shell"><command>' + command + "</command></tool>",
            "<final>Fixed and checked.</final>",
        ],
    )
    result = agent.ask("Fix add and execute a check.")
    assert "Fixed" in str(result)
    assert path.read_bytes() == b"def add(a, b):\r\n    return a + b\r\n"
    history = str(agent.session["history"])
    assert "exit_code: 0" in history
