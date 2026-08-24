"""命令行入口。

这个模块负责把“用户怎么启动 repoagent”翻译成 runtime 能理解的对象：
解析参数、挑模型后端、构建工作区快照、恢复或新建 session，
最后进入 one-shot 或交互式循环。
"""

import argparse
import os
import shutil
import sys
import textwrap

from .pricing import ModelPricing
from .config import load_project_env, load_user_env, provider_env
from .paths import workspace_state_root
from .providers.clients import AnthropicCompatibleModelClient, OllamaModelClient, OpenAICompatibleModelClient
from .providers.profiles import BUILTIN_MODEL_PROFILES, get_model_profile
from .run_store import RunStore
from .runtime import RepoAgent, SessionStore
from .workspace import WorkspaceContext, middle

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
    """根据 CLI 参数装配出一个可运行的 RepoAgent 实例。

    为什么存在：
    命令行参数只是字符串和开关，runtime 需要的是已经装配好的对象图：
    model client、workspace snapshot、session store、secret 配置等。
    这个函数负责把“启动参数”翻译成“agent 运行现场”。

    输入 / 输出：
    - 输入：`argparse` 解析后的 `args`
    - 输出：一个新的 `RepoAgent`，或一个从旧 session 恢复出来的 `RepoAgent`

    在 agent 链路里的位置：
    它是整个程序启动链路里最靠近 runtime 的装配点。`main()` 先调它，
    得到 agent 后，后面无论是 one-shot 还是 REPL 模式，都会落到 `ask()`。
    """
    # 这里是 CLI 到 runtime 的装配点：
    # 用户级配置让 CLI 能在任意仓库使用；项目配置仍可按仓库覆盖它。
    workspace = WorkspaceContext.build(args.cwd)
    load_user_env()
    load_project_env(workspace.repo_root)
    configured_secret_names = _configured_secret_names(args)
    state_root = workspace_state_root(workspace.repo_root)
    store = SessionStore(state_root / "sessions")
    run_store = RunStore(state_root / "runs")
    recovered_turn_ids = run_store.recover_incomplete_turns()
    model = _build_model_client(args)
    model_profile = model.profile
    session_id = args.resume
    if session_id == "latest":
        session_id = store.latest()
    if session_id:
        agent = RepoAgent.from_session(
            model_client=model,
            workspace=workspace,
            session_store=store,
            run_store=run_store,
            session_id=session_id,
            approval_policy=args.approval,
            max_steps=args.max_steps,
            max_new_tokens=model_profile.max_output_tokens,
            secret_env_names=configured_secret_names,
        )
    else:
        agent = RepoAgent(
            model_client=model,
            workspace=workspace,
            session_store=store,
            run_store=run_store,
            approval_policy=args.approval,
            max_steps=args.max_steps,
            max_new_tokens=model_profile.max_output_tokens,
            secret_env_names=configured_secret_names,
        )
    agent.recovered_turn_ids = tuple(recovered_turn_ids)
    return agent


def build_arg_parser():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Minimal coding agent for DeepSeek, OpenAI-compatible, Anthropic-compatible, or Ollama models.",
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
    parser.add_argument("--max-new-tokens", type=int, default=4096, help="Maximum model output tokens per step.")
    parser.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature sent to Ollama.")
    parser.add_argument("--top-p", type=float, default=0.9, help="Top-p sampling value sent to Ollama.")
    return parser


def main(argv=None):
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
