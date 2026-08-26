"""工具定义与执行辅助逻辑。

可以把这个文件看成 agent 的能力白名单：模型能申请哪些动作、这些动作
如何做参数校验，以及最终如何执行，都是在这里定义的。
"""

import shutil
import textwrap
import re
from functools import partial

from .tool_contracts import (
    ToolDefinition,
    ToolEffect,
    validate_tool_arguments,
)
from .tool_execution import (
    ToolExecutionControl,
    ToolRunnerOutput,
    run_bounded_process,
)
from .workspace import IGNORED_PATH_NAMES


def _object_schema(properties, required):
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


BASE_TOOL_DEFINITIONS = {
    "list_files": ToolDefinition(
        name="list_files",
        description="List files in the workspace.",
        parameters=_object_schema({"path": {"type": "string", "default": "."}}, []),
        effect=ToolEffect.READ,
        concurrency_safe=True,
    ),
    "read_file": ToolDefinition(
        name="read_file",
        description="Read a UTF-8 file by line range.",
        parameters=_object_schema(
            {
                "path": {"type": "string", "minLength": 1},
                "start": {"type": "integer", "minimum": 1, "default": 1},
                "end": {"type": "integer", "minimum": 1, "default": 200},
            },
            ["path"],
        ),
        effect=ToolEffect.READ,
        concurrency_safe=True,
    ),
    "search": ToolDefinition(
        name="search",
        description="Search the workspace with rg or a simple fallback.",
        parameters=_object_schema(
            {
                "pattern": {"type": "string", "minLength": 1},
                "path": {"type": "string", "default": "."},
            },
            ["pattern"],
        ),
        effect=ToolEffect.READ,
        concurrency_safe=True,
    ),
    "run_shell": ToolDefinition(
        name="run_shell",
        description="Run a shell command in the repo root.",
        parameters=_object_schema(
            {
                "command": {"type": "string", "minLength": 1},
                "timeout": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 120,
                    "default": 20,
                },
            },
            ["command"],
        ),
        effect=ToolEffect.EXECUTE,
        requires_approval=True,
        timeout_seconds=120,
    ),
    "write_file": ToolDefinition(
        name="write_file",
        description="Write a text file.",
        parameters=_object_schema(
            {
                "path": {"type": "string", "minLength": 1},
                "content": {"type": "string"},
            },
            ["path", "content"],
        ),
        effect=ToolEffect.WRITE,
        requires_approval=True,
    ),
    "patch_file": ToolDefinition(
        name="patch_file",
        description="Replace one exact text block in a file.",
        parameters=_object_schema(
            {
                "path": {"type": "string", "minLength": 1},
                "old_text": {"type": "string", "minLength": 1},
                "new_text": {"type": "string"},
            },
            ["path", "old_text", "new_text"],
        ),
        effect=ToolEffect.WRITE,
        requires_approval=True,
    ),
    "git_status": ToolDefinition(
        name="git_status",
        description="Show concise Git branch and working-tree status.",
        parameters=_object_schema({}, []),
        effect=ToolEffect.READ,
        concurrency_safe=True,
    ),
    "git_diff": ToolDefinition(
        name="git_diff",
        description="Show the unstaged Git diff, optionally for one workspace path.",
        parameters=_object_schema({"path": {"type": "string", "default": ""}}, []),
        effect=ToolEffect.READ,
        concurrency_safe=True,
    ),
    "git_worktree_list": ToolDefinition(
        name="git_worktree_list",
        description="List Git worktrees in porcelain format.",
        parameters=_object_schema({}, []),
        effect=ToolEffect.READ,
        concurrency_safe=True,
    ),
    "git_worktree_create": ToolDefinition(
        name="git_worktree_create",
        description="Create a named Git worktree on a repoagent branch.",
        parameters=_object_schema(
            {"name": {"type": "string", "minLength": 1, "maxLength": 40}},
            ["name"],
        ),
        effect=ToolEffect.EXECUTE,
        requires_approval=True,
        timeout_seconds=60,
    ),
    "git_worktree_remove": ToolDefinition(
        name="git_worktree_remove",
        description="Remove a clean RepoAgent-managed Git worktree.",
        parameters=_object_schema(
            {"name": {"type": "string", "minLength": 1, "maxLength": 40}},
            ["name"],
        ),
        effect=ToolEffect.EXECUTE,
        requires_approval=True,
        timeout_seconds=60,
    ),
}

DELEGATE_TOOL_DEFINITION = ToolDefinition(
    name="delegate",
    description="Ask a bounded read-only child agent to investigate.",
    parameters=_object_schema(
        {
            "task": {"type": "string", "minLength": 1},
            "max_steps": {
                "type": "integer",
                "minimum": 1,
                "default": 3,
            },
            "role": {
                "type": "string",
                "enum": ["implementer", "reviewer", "red-team-verifier"],
                "default": "reviewer",
            },
        },
        ["task"],
    ),
    effect=ToolEffect.READ,
)


def legal_tool_names():
    return set(BASE_TOOL_DEFINITIONS) | {"delegate"}


def tool_definition(name):
    if name == "delegate":
        return DELEGATE_TOOL_DEFINITION
    return BASE_TOOL_DEFINITIONS.get(name)


def normalize_tool_arguments(name, args):
    definition = tool_definition(name)
    if definition is None:
        raise ValueError(f"unknown tool: {name}")
    return validate_tool_arguments(definition, args or {})


TOOL_EXAMPLES = {
    "list_files": '<tool>{"name":"list_files","args":{"path":"."}}</tool>',
    "read_file": '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":80}}</tool>',
    "search": '<tool>{"name":"search","args":{"pattern":"binary_search","path":"."}}</tool>',
    "run_shell": '<tool>{"name":"run_shell","args":{"command":"uv run --with pytest python -m pytest -q","timeout":20}}</tool>',
    "write_file": '<tool name="write_file" path="binary_search.py"><content>def binary_search(nums, target):\n    return -1\n</content></tool>',
    "patch_file": '<tool name="patch_file" path="binary_search.py"><old_text>return -1</old_text><new_text>return mid</new_text></tool>',
    "delegate": '<tool>{"name":"delegate","args":{"task":"inspect README.md","max_steps":3}}</tool>',
    "git_status": '<tool>{"name":"git_status","args":{}}</tool>',
    "git_diff": '<tool>{"name":"git_diff","args":{"path":"repoagent/runtime.py"}}</tool>',
    "git_worktree_list": '<tool>{"name":"git_worktree_list","args":{}}</tool>',
    "git_worktree_create": '<tool>{"name":"git_worktree_create","args":{"name":"feature-check"}}</tool>',
    "git_worktree_remove": '<tool>{"name":"git_worktree_remove","args":{"name":"feature-check"}}</tool>',
}


def build_tool_registry(context):
    # 工具不是动态发现的，而是显式注册的。
    # 这样模型看到的是一个有边界、可审计的动作集合。
    tools = {
        name: {
            "definition": definition,
            "run": partial(_TOOL_RUNNERS[name], context),
        }
        for name, definition in BASE_TOOL_DEFINITIONS.items()
    }
    # 子 agent 是刻意做成受限能力的：一旦深度耗尽，
    # 就连 delegate 这个工具都不再暴露给模型。
    if context.depth < context.max_depth:
        tools["delegate"] = {
            "definition": DELEGATE_TOOL_DEFINITION,
            "run": partial(tool_delegate, context),
        }
    return tools


def tool_example(name):
    return TOOL_EXAMPLES.get(name, "")


def validate_tool(context, name, args):
    args = normalize_tool_arguments(name, args)

    if name == "list_files":
        path = context.path(args.get("path", "."))
        if not path.is_dir():
            raise ValueError("path is not a directory")
        return args

    if name == "read_file":
        path = context.path(args["path"])
        if not path.is_file():
            raise ValueError("path is not a file")
        start = int(args.get("start", 1))
        end = int(args.get("end", 200))
        if start < 1 or end < start:
            raise ValueError("invalid line range")
        return args

    if name == "search":
        pattern = str(args.get("pattern", "")).strip()
        if not pattern:
            raise ValueError("pattern must not be empty")
        context.path(args.get("path", "."))
        return args

    if name == "run_shell":
        command = str(args.get("command", "")).strip()
        if not command:
            raise ValueError("command must not be empty")
        timeout = int(args.get("timeout", 20))
        if timeout < 1 or timeout > 120:
            raise ValueError("timeout must be in [1, 120]")
        return args

    if name == "write_file":
        path = context.path(args["path"])
        if path.exists() and path.is_dir():
            raise ValueError("path is a directory")
        return args

    if name == "patch_file":
        # patch_file 故意做得很严格：old_text 必须精确命中且只能出现一次，
        # 这样修改行为才是确定的，失败原因也更容易解释。
        path = context.path(args["path"])
        if not path.is_file():
            raise ValueError("path is not a file")
        old_text = str(args.get("old_text", ""))
        if not old_text:
            raise ValueError("old_text must not be empty")
        text = path.read_text(encoding="utf-8")
        count = text.count(old_text)
        if count != 1:
            raise ValueError(f"old_text must occur exactly once, found {count}")
        return args

    if name == "git_diff":
        if args.get("path"):
            context.path(args["path"])
        return args

    if name in {"git_status", "git_worktree_list"}:
        return args

    if name in {"git_worktree_create", "git_worktree_remove"}:
        worktree_name = str(args.get("name", ""))
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,39}", worktree_name):
            raise ValueError("invalid worktree name")
        target = context.path(f".repoagent/worktrees/{worktree_name}")
        if name == "git_worktree_create" and target.exists():
            raise ValueError("worktree already exists")
        if name == "git_worktree_remove" and not target.exists():
            raise ValueError("managed worktree does not exist")
        return args

    if name == "delegate":
        task = str(args.get("task", "")).strip()
        if not task:
            raise ValueError("task must not be empty")
        if context.depth >= context.max_depth:
            raise ValueError("delegate depth exceeded")
        return args


def tool_list_files(context, args, control=None):
    path = context.path(args.get("path", "."))
    if not path.is_dir():
        raise ValueError("path is not a directory")
    entries = [
        item
        for item in sorted(
            path.iterdir(), key=lambda item: (item.is_file(), item.name.lower())
        )
        if item.name not in IGNORED_PATH_NAMES
    ]
    lines = []
    for entry in entries[:200]:
        kind = "[D]" if entry.is_dir() else "[F]"
        lines.append(f"{kind} {entry.relative_to(context.root)}")
    return "\n".join(lines) or "(empty)"


def tool_read_file(context, args, control=None):
    path = context.path(args["path"])
    if not path.is_file():
        raise ValueError("path is not a file")
    start = int(args.get("start", 1))
    end = int(args.get("end", 200))
    if start < 1 or end < start:
        raise ValueError("invalid line range")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    body = "\n".join(
        f"{number:>4}: {line}"
        for number, line in enumerate(lines[start - 1 : end], start=start)
    )
    return f"# {path.relative_to(context.root)}\n{body}"


def tool_search(context, args, control=None):
    pattern = str(args.get("pattern", "")).strip()
    if not pattern:
        raise ValueError("pattern must not be empty")
    path = context.path(args.get("path", "."))
    control = control or ToolExecutionControl(
        timeout_seconds=30,
        max_output_chars=4000,
    )

    if shutil.which("rg"):
        # 优先用 rg，因为搜索会非常频繁，搜索延迟会直接影响 agent 控制循环。
        outcome = run_bounded_process(
            ["rg", "-n", "--smart-case", "--max-count", "200", pattern, str(path)],
            cwd=context.root,
            env=context.shell_env(),
            shell=False,
            control=control,
        )
        content = outcome.stdout.strip() or outcome.stderr.strip() or "(no matches)"
        return ToolRunnerOutput(content, outcome.metadata())

    matches = []
    files = (
        [path]
        if path.is_file()
        else [
            item
            for item in path.rglob("*")
            if item.is_file()
            and not any(
                part in IGNORED_PATH_NAMES
                for part in item.relative_to(context.root).parts
            )
        ]
    )
    for file_path in files:
        if control is not None and control.status() != "running":
            return ToolRunnerOutput(
                "search interrupted before completion",
                {"execution_status": control.status()},
            )
        for number, line in enumerate(
            file_path.read_text(encoding="utf-8", errors="replace").splitlines(),
            start=1,
        ):
            if pattern.lower() in line.lower():
                matches.append(f"{file_path.relative_to(context.root)}:{number}:{line}")
                if len(matches) >= 200:
                    return "\n".join(matches)
    return "\n".join(matches) or "(no matches)"


def tool_run_shell(context, args, control=None):
    command = str(args.get("command", "")).strip()
    if not command:
        raise ValueError("command must not be empty")
    timeout = int(args.get("timeout", 20))
    if timeout < 1 or timeout > 120:
        raise ValueError("timeout must be in [1, 120]")
    control = control or ToolExecutionControl(
        timeout_seconds=timeout,
        max_output_chars=4000,
    )
    if context.sandbox_adapter is None:
        raise RuntimeError("shell execution has no sandbox adapter")
    outcome = context.sandbox_adapter.execute(
        command,
        cwd=context.root,
        # 这里传入的是过滤后的环境变量，而不是直接继承整个父 shell 环境，
        # 目的是减少敏感信息被意外带进命令执行环境的风险。
        env=context.shell_env(),
        control=control,
    )
    content = textwrap.dedent(
        f"""\
        execution_status: {outcome.status}
        exit_code: {outcome.exit_code if outcome.exit_code is not None else "unknown"}
        stdout:
        {outcome.stdout.strip() or "(empty)"}
        stderr:
        {outcome.stderr.strip() or "(empty)"}
        """
    ).strip()
    return ToolRunnerOutput(
        content,
        {
            **outcome.metadata(),
            "sandbox_identity": context.sandbox_adapter.identity,
            "sandbox_isolated": context.sandbox_adapter.is_isolated,
        },
    )


def tool_write_file(context, args, control=None):
    path = context.path(args["path"])
    content = str(args["content"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"wrote {path.relative_to(context.root)} ({len(content)} chars)"


def tool_patch_file(context, args, control=None):
    path = context.path(args["path"])
    if not path.is_file():
        raise ValueError("path is not a file")
    old_text = str(args.get("old_text", ""))
    if not old_text:
        raise ValueError("old_text must not be empty")
    if "new_text" not in args:
        raise ValueError("missing new_text")
    text = path.read_text(encoding="utf-8")
    count = text.count(old_text)
    if count != 1:
        raise ValueError(f"old_text must occur exactly once, found {count}")
    path.write_text(text.replace(old_text, str(args["new_text"]), 1), encoding="utf-8")
    return f"patched {path.relative_to(context.root)}"


def tool_delegate(context, args, control=None):
    if context.depth >= context.max_depth:
        raise ValueError("delegate depth exceeded")
    task = str(args.get("task", "")).strip()
    if not task:
        raise ValueError("task must not be empty")
    if control is None:
        return context.spawn_delegate(args)
    return context.spawn_delegate(args, control=control)


def _run_git(context, command, control, operation):
    control = control or ToolExecutionControl(timeout_seconds=60, max_output_chars=4000)
    outcome = run_bounded_process(
        command,
        cwd=context.root,
        env=context.shell_env(),
        shell=False,
        control=control,
    )
    content = outcome.stdout.strip() or outcome.stderr.strip() or "(empty)"
    return ToolRunnerOutput(
        content,
        {
            **outcome.metadata(),
            "exit_code_is_error": True,
            "git_operation": operation,
        },
    )


def tool_git_status(context, args, control=None):
    return _run_git(
        context, ["git", "status", "--short", "--branch"], control, "status"
    )


def tool_git_diff(context, args, control=None):
    command = ["git", "diff", "--no-ext-diff"]
    if args.get("path"):
        path = context.path(args["path"])
        command.extend(["--", path.relative_to(context.root).as_posix()])
    return _run_git(context, command, control, "diff")


def tool_git_worktree_list(context, args, control=None):
    return _run_git(
        context, ["git", "worktree", "list", "--porcelain"], control, "list"
    )


def tool_git_worktree_create(context, args, control=None):
    name = args["name"]
    target = context.path(f".repoagent/worktrees/{name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    return _run_git(
        context,
        ["git", "worktree", "add", "-b", f"repoagent/{name}", str(target), "HEAD"],
        control,
        "worktree_create",
    )


def tool_git_worktree_remove(context, args, control=None):
    target = context.path(f".repoagent/worktrees/{args['name']}")
    return _run_git(
        context,
        ["git", "worktree", "remove", str(target)],
        control,
        "worktree_remove",
    )


_TOOL_RUNNERS = {
    "list_files": tool_list_files,
    "read_file": tool_read_file,
    "search": tool_search,
    "run_shell": tool_run_shell,
    "write_file": tool_write_file,
    "patch_file": tool_patch_file,
    "git_status": tool_git_status,
    "git_diff": tool_git_diff,
    "git_worktree_list": tool_git_worktree_list,
    "git_worktree_create": tool_git_worktree_create,
    "git_worktree_remove": tool_git_worktree_remove,
}
