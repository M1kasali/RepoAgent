"""命令行入口。

这个模块负责把“用户怎么启动 repoagent”翻译成 runtime 能理解的对象：
解析参数、挑模型后端、构建工作区快照、恢复或新建 session，
最后进入 one-shot 或交互式循环。
"""

import argparse
import asyncio
import os
import shutil
import sys
import textwrap

from .pricing import ModelPricing
from .config import provider_env
from .evaluation.cli import main as evaluation_main
from .providers.clients import AnthropicCompatibleModelClient, OllamaModelClient, OpenAICompatibleModelClient
from .providers.profiles import BUILTIN_MODEL_PROFILES, get_model_profile
from .product_commands import (
    cron_report,
    evolver_report,
    directory_channel_report,
    doctor_report,
    gateway_report,
    print_json,
    provider_report,
    sandbox_report,
    session_report,
    skill_report,
)
from .evolver import LedgerIntegrityError
from .runtime_assembly import assemble_runtime
from .trace_inspection import main as trace_main
from .tui import run_tui
from .workspace import middle

DEFAULT_SECRET_ENV_NAMES = (
    "REPOAGENT_OPENAI_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_API_TOKEN",
    "REPOAGENT_ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "REPOAGENT_DEEPSEEK_API_KEY",
    "DEEPSEEK_API_KEY",
    "REPOAGENT_RIGHT_CODES_API_KEY",
    "PICO_OPENAI_API_KEY",
    "PICO_ANTHROPIC_API_KEY",
    "PICO_DEEPSEEK_API_KEY",
    "PICO_RIGHT_CODES_API_KEY",
    "RIGHT_CODES_API_KEY",
    "GITHUB_PAT",
    "GH_PAT",
)

WELCOME_ART = (
    "        /\\___/\\\\",
    "       (  o o  )",
    "       /   ^   \\\\",
    "      /|       |\\\\",
)
WELCOME_NAME = "repoagent"
WELCOME_SUBTITLE = "local coding agent"
WELCOME_STATUS = "calm shell, ready for work"
HELP_DETAILS = textwrap.dedent(
    """\
    Commands:
    /help    Show this help message.
    /memory  Show the agent's distilled working memory.
    /session Show the path to the saved session file.
    /reset   Clear the current session history and memory.
    /exit    Exit the agent.
    """
).strip()


DEFAULT_OLLAMA_MODEL = BUILTIN_MODEL_PROFILES["ollama"].model
DEFAULT_OLLAMA_HOST = BUILTIN_MODEL_PROFILES["ollama"].base_url
DEFAULT_OPENAI_MODEL = BUILTIN_MODEL_PROFILES["openai"].model
DEFAULT_OPENAI_BASE_URL = BUILTIN_MODEL_PROFILES["openai"].base_url
DEFAULT_ANTHROPIC_MODEL = BUILTIN_MODEL_PROFILES["anthropic"].model
DEFAULT_ANTHROPIC_BASE_URL = BUILTIN_MODEL_PROFILES["anthropic"].base_url
DEFAULT_DEEPSEEK_MODEL = BUILTIN_MODEL_PROFILES["deepseek"].model
DEFAULT_DEEPSEEK_BASE_URL = BUILTIN_MODEL_PROFILES["deepseek"].base_url
DEFAULT_PROVIDER = "deepseek"
PROVIDER_CHOICES = tuple(BUILTIN_MODEL_PROFILES)
SECRET_ENV_NAMES_VAR = "REPOAGENT_SECRET_ENV_NAMES"
LEGACY_SECRET_ENV_NAMES_VAR = "PICO_SECRET_ENV_NAMES"


def _effective_provider(args):
    # Provider 选择优先级：
    # 1. 用户显式传入 --provider
    # 2. 项目 .env / shell 里的 REPOAGENT_PROVIDER
    # 3. 代码里的默认 provider
    explicit_profile = getattr(args, "profile", None)
    explicit_provider = getattr(args, "provider", None)
    if (
        explicit_profile
        and explicit_provider
        and explicit_profile != explicit_provider
    ):
        raise ValueError(
            "--profile and --provider must select the same built-in profile"
        )
    provider = (
        explicit_profile
        or explicit_provider
        or provider_env("REPOAGENT_MODEL_PROFILE")
        or provider_env("REPOAGENT_PROVIDER", default=DEFAULT_PROVIDER)
    )
    if provider not in PROVIDER_CHOICES:
        choices = ", ".join(PROVIDER_CHOICES)
        raise ValueError(f"unknown provider: {provider}. expected one of: {choices}")
    return provider


def _effective_model(args, provider):
    # 模型选择优先级：
    # 1. 用户显式传入 --model
    # 2. provider 对应的环境变量
    # 3. 代码里的默认值
    explicit_model = getattr(args, "model", None)
    if explicit_model:
        return explicit_model
    if provider == "openai":
        model = provider_env("REPOAGENT_OPENAI_MODEL", ("OPENAI_MODEL",))
        if model:
            return model
        return DEFAULT_OPENAI_MODEL
    if provider == "anthropic":
        model = provider_env("REPOAGENT_ANTHROPIC_MODEL", ("ANTHROPIC_MODEL",))
        if model:
            return model
        return DEFAULT_ANTHROPIC_MODEL
    if provider == "deepseek":
        model = provider_env("REPOAGENT_DEEPSEEK_MODEL", ("DEEPSEEK_MODEL",))
        if model:
            return model
        return DEFAULT_DEEPSEEK_MODEL
    model = provider_env("REPOAGENT_OLLAMA_MODEL", ("OLLAMA_MODEL",))
    return model or DEFAULT_OLLAMA_MODEL


def _profile_base_url(args, provider):
    if provider == "ollama":
        return getattr(args, "host", None) or DEFAULT_OLLAMA_HOST
    explicit = getattr(args, "base_url", None)
    if explicit:
        return explicit
    if provider == "openai":
        return provider_env(
            "REPOAGENT_OPENAI_API_BASE",
            ("OPENAI_API_BASE",),
            DEFAULT_OPENAI_BASE_URL,
        )
    if provider == "anthropic":
        return provider_env(
            "REPOAGENT_ANTHROPIC_API_BASE",
            ("ANTHROPIC_API_BASE",),
            DEFAULT_ANTHROPIC_BASE_URL,
        )
    return provider_env(
        "REPOAGENT_DEEPSEEK_API_BASE",
        ("DEEPSEEK_API_BASE",),
        DEFAULT_DEEPSEEK_BASE_URL,
    )


def _resolve_model_profile(args):
    provider = _effective_provider(args)
    profile = get_model_profile(provider)
    timeout = (
        getattr(args, "ollama_timeout", profile.timeout_seconds)
        if profile.protocol == "ollama"
        else getattr(
            args,
            "openai_timeout",
            getattr(args, "ollama_timeout", profile.timeout_seconds),
        )
    )
    return profile.with_overrides(
        model=_effective_model(args, provider),
        base_url=_profile_base_url(args, provider),
        timeout_seconds=timeout,
        max_output_tokens=getattr(
            args, "max_new_tokens", profile.max_output_tokens
        ),
        context_window_tokens=(
            getattr(args, "context_window_tokens", None)
            or profile.context_window_tokens
        ),
        context_window_source=(
            "cli-override"
            if getattr(args, "context_window_tokens", None) is not None
            else profile.context_window_source
        ),
        temperature=getattr(args, "temperature", profile.temperature),
        top_p=(
            getattr(args, "top_p", profile.top_p)
            if profile.protocol == "ollama"
            else None
        ),
        pricing=_resolve_model_pricing(args, provider),
    )


def _profile_api_key(profile):
    for name in profile.credential_envs:
        value = provider_env(name)
        if value:
            return value
    return ""


def _resolve_model_pricing(args, provider):
    prefix = f"REPOAGENT_{provider.upper()}"
    input_rate = getattr(args, "input_cost_per_1m_usd", None)
    output_rate = getattr(args, "output_cost_per_1m_usd", None)
    if input_rate is None:
        value = provider_env(
            f"{prefix}_INPUT_COST_PER_1M_USD",
            ("REPOAGENT_INPUT_COST_PER_1M_USD",),
        )
        input_rate = float(value) if value else None
    if output_rate is None:
        value = provider_env(
            f"{prefix}_OUTPUT_COST_PER_1M_USD",
            ("REPOAGENT_OUTPUT_COST_PER_1M_USD",),
        )
        output_rate = float(value) if value else None
    if input_rate is None and output_rate is None:
        return None
    if input_rate is None or output_rate is None:
        raise ValueError(
            "input and output pricing rates must be configured together"
        )
    cache_read_rate = getattr(args, "cache_read_cost_per_1m_usd", None)
    cache_write_rate = getattr(args, "cache_write_cost_per_1m_usd", None)
    if cache_read_rate is None:
        value = provider_env(
            f"{prefix}_CACHE_READ_COST_PER_1M_USD",
            ("REPOAGENT_CACHE_READ_COST_PER_1M_USD",),
        )
        cache_read_rate = float(value) if value else None
    if cache_write_rate is None:
        value = provider_env(
            f"{prefix}_CACHE_WRITE_COST_PER_1M_USD",
            ("REPOAGENT_CACHE_WRITE_COST_PER_1M_USD",),
        )
        cache_write_rate = float(value) if value else None
    source = (
        getattr(args, "pricing_source", None)
        or provider_env(
            f"{prefix}_PRICING_SOURCE", ("REPOAGENT_PRICING_SOURCE",)
        )
        or "user-configured"
    )
    return ModelPricing(
        input_per_1m_usd=input_rate,
        output_per_1m_usd=output_rate,
        source=source,
        cache_read_per_1m_usd=cache_read_rate,
        cache_write_per_1m_usd=cache_write_rate,
    )


def _configured_secret_names(args):
    configured_secret_names = set(DEFAULT_SECRET_ENV_NAMES)
    configured_secret_names.update(str(name).upper() for name in args.secret_env_names)
    extra_names = os.environ.get(SECRET_ENV_NAMES_VAR) or os.environ.get(LEGACY_SECRET_ENV_NAMES_VAR, "")
    if extra_names.strip():
        configured_secret_names.update(
            item.strip().upper()
            for item in extra_names.split(",")
            if item.strip()
        )
    return sorted(configured_secret_names)


def _build_model_client(args):
    profile = _resolve_model_profile(args)
    if profile.protocol == "ollama":
        client = OllamaModelClient(
            model=profile.model,
            host=profile.base_url,
            temperature=profile.temperature,
            top_p=profile.top_p,
            timeout=profile.timeout_seconds,
        )
    elif profile.protocol == "openai":
        client = OpenAICompatibleModelClient(
            model=profile.model,
            base_url=profile.base_url,
            api_key=_profile_api_key(profile),
            temperature=profile.temperature,
            timeout=profile.timeout_seconds,
        )
    else:
        client = AnthropicCompatibleModelClient(
            model=profile.model,
            base_url=profile.base_url,
            api_key=_profile_api_key(profile),
            temperature=profile.temperature,
            timeout=profile.timeout_seconds,
        )
    client.profile = profile
    return client


def build_welcome(agent, model, host):
    width = max(68, min(shutil.get_terminal_size((80, 20)).columns, 84))
    inner = width - 4
    gap = 3
    left_width = (inner - gap) // 2
    right_width = inner - gap - left_width

    def row(text):
        body = middle(text, width - 4)
        return f"| {body.ljust(width - 4)} |"

    def divider(char="-"):
        return "+" + char * (width - 2) + "+"

    def center(text):
        body = middle(text, inner)
        return f"| {body.center(inner)} |"

    def cell(label, value, size):
        body = middle(f"{label:<9} {value}", size)
        return body.ljust(size)

    def pair(left_label, left_value, right_label, right_value):
        left = cell(left_label, left_value, left_width)
        right = cell(right_label, right_value, right_width)
        return f"| {left}{' ' * gap}{right} |"

    line = divider("=")
    rows = [center(text) for text in WELCOME_ART]
    rows.extend(
        [
            center(WELCOME_NAME),
            center(WELCOME_SUBTITLE),
            center(WELCOME_STATUS),
            divider("-"),
            row(""),
            row("WORKSPACE  " + middle(agent.workspace.cwd, inner - 11)),
            pair("MODEL", model, "BRANCH", agent.workspace.branch),
            pair("APPROVAL", agent.approval_policy, "SESSION", agent.session["id"]),
            row(""),
        ]
    )
    return "\n".join([line, *rows, line])


def build_agent(args):
    """Compatibility facade over the parser-independent runtime assembler."""
    return assemble_runtime(
        args,
        model_client_factory=_build_model_client,
        secret_names_factory=_configured_secret_names,
    )


def build_arg_parser():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description=(
            "Local coding-agent runtime with persistent Turns, constrained tools, "
            "evidence, evaluation, and multiple model protocols."
        ),
    )
    parser.add_argument("prompt", nargs="*", help="Optional one-shot prompt.")
    parser.add_argument("--cwd", default=".", help="Workspace directory.")
    parser.add_argument(
        "--profile",
        choices=PROVIDER_CHOICES,
        default=None,
        help="Validated built-in model profile. Compatible with --provider when both match.",
    )
    parser.add_argument(
        "--provider",
        choices=PROVIDER_CHOICES,
        default=None,
        help="Compatibility alias for a built-in model profile. Defaults to REPOAGENT_MODEL_PROFILE, REPOAGENT_PROVIDER, or deepseek.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name override. Defaults to qwen3.5:4b for Ollama, REPOAGENT_OPENAI_MODEL for openai, REPOAGENT_ANTHROPIC_MODEL for anthropic, and REPOAGENT_DEEPSEEK_MODEL for deepseek when set.",
    )
    parser.add_argument("--host", default=DEFAULT_OLLAMA_HOST, help="Ollama server URL.")
    parser.add_argument("--base-url", default=None, help="Provider API base URL for deepseek, openai, or anthropic.")
    parser.add_argument("--ollama-timeout", type=int, default=300, help="Ollama request timeout in seconds.")
    parser.add_argument("--openai-timeout", type=int, default=300, help="OpenAI-compatible request timeout in seconds.")
    parser.add_argument(
        "--input-cost-per-1m-usd",
        type=float,
        default=None,
        help="Explicit input-token price snapshot in USD per one million tokens.",
    )
    parser.add_argument(
        "--output-cost-per-1m-usd",
        type=float,
        default=None,
        help="Explicit output-token price snapshot in USD per one million tokens.",
    )
    parser.add_argument(
        "--pricing-source",
        default=None,
        help="Human-readable provenance for explicitly configured model rates.",
    )
    parser.add_argument(
        "--cache-read-cost-per-1m-usd",
        type=float,
        default=None,
        help="Explicit cache-read price snapshot in USD per one million tokens.",
    )
    parser.add_argument(
        "--cache-write-cost-per-1m-usd",
        type=float,
        default=None,
        help="Explicit cache-write price snapshot in USD per one million tokens.",
    )
    parser.add_argument("--resume", default=None, help="Session id to resume or 'latest'.")
    parser.add_argument("--approval", choices=("ask", "auto", "never"), default="ask", help="Approval policy for risky tools.")
    parser.add_argument(
        "--secret-env-name",
        dest="secret_env_names",
        action="append",
        default=[],
        help="Extra environment variable names to treat as secrets for trace/report redaction.",
    )
    parser.add_argument("--max-steps", type=int, default=20, help="Maximum tool/model iterations per request.")
    parser.add_argument(
        "--max-parallel-tools",
        type=int,
        default=4,
        help="Maximum concurrent calls in a concurrency-safe read batch.",
    )
    parser.add_argument(
        "--mutation-conflict-policy",
        choices=("serial",),
        default="serial",
        help="Conflict policy for side-effecting tools.",
    )
    parser.add_argument(
        "--require-isolation",
        action="store_true",
        help="Reject execute/external tools unless an isolated sandbox is active.",
    )
    parser.add_argument("--max-new-tokens", type=int, default=4096, help="Maximum model output tokens per step.")
    parser.add_argument(
        "--context-token-budget",
        type=int,
        default=3000,
        help="Maximum pre-request prompt tokens before output reservation.",
    )
    parser.add_argument(
        "--context-window-tokens",
        type=int,
        default=None,
        help="Configured model context window used for request admission.",
    )
    parser.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature sent to Ollama.")
    parser.add_argument("--top-p", type=float, default=0.9, help="Top-p sampling value sent to Ollama.")
    return parser


PRODUCT_COMMANDS = frozenset(
    {
        "channel",
        "cron",
        "doctor",
        "eval",
        "evolver",
        "gateway",
        "provider",
        "sandbox",
        "session",
        "skill",
        "trace",
        "tui",
    }
)


def build_product_parser():
    parser = argparse.ArgumentParser(
        prog="repoagent",
        description="Operate and inspect the RepoAgent runtime.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor", help="Check the local runtime environment.")
    doctor.add_argument("--cwd", default=".")

    provider = commands.add_parser("provider", help="Inspect model profiles.")
    provider_commands = provider.add_subparsers(dest="provider_command", required=True)
    provider_commands.add_parser("list", help="List configured model profiles.")
    provider_show = provider_commands.add_parser("show", help="Show one model profile.")
    provider_show.add_argument("name", choices=PROVIDER_CHOICES)

    session = commands.add_parser("session", help="Inspect persisted sessions.")
    session_commands = session.add_subparsers(dest="session_command", required=True)
    session_list = session_commands.add_parser("list", help="List workspace sessions.")
    session_list.add_argument("--cwd", default=".")
    session_show = session_commands.add_parser("show", help="Show a session summary.")
    session_show.add_argument("session_id")
    session_show.add_argument("--cwd", default=".")

    sandbox = commands.add_parser("sandbox", help="Inspect sandbox enforcement.")
    sandbox_commands = sandbox.add_subparsers(dest="sandbox_command", required=True)
    sandbox_status = sandbox_commands.add_parser("status", help="Show sandbox status.")
    sandbox_status.add_argument("--require-isolation", action="store_true")

    gateway = commands.add_parser("gateway", help="Inspect the local gateway.")
    gateway_commands = gateway.add_subparsers(dest="gateway_command", required=True)
    gateway_status = gateway_commands.add_parser("status", help="Show gateway health.")
    gateway_status.add_argument("--cwd", default=".")

    channel = commands.add_parser("channel", help="Inspect channel adapters.")
    channel_commands = channel.add_subparsers(dest="channel_command", required=True)
    directory_status = channel_commands.add_parser(
        "directory-status", help="Show directory-channel queue counts."
    )
    directory_status.add_argument("--root", required=True)

    cron = commands.add_parser("cron", help="Inspect scheduled work.")
    cron_commands = cron.add_subparsers(dest="cron_command", required=True)
    cron_list = cron_commands.add_parser("list", help="List persisted cron jobs.")
    cron_list.add_argument("--cwd", default=".")

    trace = commands.add_parser("trace", help="Inspect one Turn trace.")
    trace.add_argument("trace_args", nargs=argparse.REMAINDER)

    evaluation = commands.add_parser("eval", help="Run evaluation operations.")
    evaluation.add_argument("eval_args", nargs=argparse.REMAINDER)

    evolver = commands.add_parser("evolver", help="Inspect controlled evolution state.")
    evolver_commands = evolver.add_subparsers(dest="evolver_command", required=True)
    evolver_status = evolver_commands.add_parser(
        "status", help="Verify the evolution ledger and show active routes."
    )
    evolver_status.add_argument("--cwd", default=".")

    tui = commands.add_parser("tui", help="Run the scheduler-backed terminal UI.")
    tui.add_argument("agent_args", nargs=argparse.REMAINDER)

    skill = commands.add_parser("skill", help="Inspect local Skills.")
    skill_commands = skill.add_subparsers(dest="skill_command", required=True)
    skill_list = skill_commands.add_parser("list", help="List discovered Skills.")
    skill_list.add_argument("--cwd", default=".")
    skill_show = skill_commands.add_parser("show", help="Show one Skill manifest.")
    skill_show.add_argument("skill_id")
    skill_show.add_argument("--cwd", default=".")
    return parser


def run_product_command(argv):
    args = build_product_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            payload = doctor_report(args.cwd)
        elif args.command == "provider":
            payload = provider_report(
                args.name if args.provider_command == "show" else None
            )
        elif args.command == "session":
            payload = session_report(
                args.cwd,
                args.session_id if args.session_command == "show" else None,
            )
        elif args.command == "sandbox":
            payload = sandbox_report(require_isolation=args.require_isolation)
        elif args.command == "gateway":
            payload = gateway_report(args.cwd)
        elif args.command == "channel":
            payload = directory_channel_report(args.root)
        elif args.command == "cron":
            payload = cron_report(args.cwd)
        elif args.command == "trace":
            return trace_main(args.trace_args)
        elif args.command == "eval":
            return evaluation_main(args.eval_args)
        elif args.command == "evolver":
            payload = evolver_report(args.cwd)
        elif args.command == "tui":
            agent_args = build_arg_parser().parse_args(args.agent_args)
            if agent_args.prompt:
                raise ValueError("tui does not accept a one-shot prompt")
            return asyncio.run(run_tui(build_agent(agent_args)))
        else:
            payload = skill_report(
                args.cwd,
                args.skill_id if args.skill_command == "show" else None,
            )
    except (OSError, ValueError, LedgerIntegrityError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print_json(payload)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in PRODUCT_COMMANDS:
        return run_product_command(argv)
    args = build_arg_parser().parse_args(argv)
    agent = build_agent(args)

    model = getattr(agent.model_client, "model", getattr(args, "model", DEFAULT_OLLAMA_MODEL))
    host = getattr(agent.model_client, "host", getattr(agent.model_client, "base_url", getattr(args, "host", DEFAULT_OLLAMA_HOST)))
    print(build_welcome(agent, model=model, host=host))

    if args.prompt:
        # one-shot 模式：只跑一次 ask，不进入 REPL 循环。
        prompt = " ".join(args.prompt).strip()
        if prompt:
            print()
            try:
                print(agent.ask(prompt))
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return 1
        return 0

    while True:
        # 交互模式：每次读取一条用户输入，交给同一个 agent，
        # 因此 session history 和 working memory 会跨轮延续。
        try:
            user_input = input(f"\n{WELCOME_NAME}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("")
            return 0

        if not user_input:
            continue
        if user_input in {"/exit", "/quit"}:
            return 0
        if user_input == "/help":
            print(HELP_DETAILS)
            continue
        if user_input == "/memory":
            print(agent.memory_text())
            continue
        if user_input == "/session":
            print(agent.session_path)
            continue
        if user_input == "/reset":
            agent.reset()
            print("session reset")
            continue

        print()
        try:
            print(agent.ask(user_input))
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
