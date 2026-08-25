# RepoAgent

`RepoAgent` 是一个面向多轮代码仓库任务的本地 coding-agent runtime。它把模型接入、任务调度、上下文与长期记忆、受约束工具、恢复、链路追踪、评测和受控策略演进放在同一套可审计运行时中。

它不是用来和 Claude Code、Codex 比拼模型本身，而是把 coding agent 在长任务中的工程问题做成可测试基础设施：同一 Turn 可从 CLI、TUI、channel 或 cron 进入；工具调用经过权限和隔离边界；每次执行保留可复核证据；策略候选只有通过 sealed 评测和人工确认后才能激活。

内部 Python 包、CLI 和新配置统一使用 `repoagent` / `REPOAGENT_*`。旧版 `.pico/` 状态目录和 `PICO_*` 环境变量仍可读取，新的工作区只会创建 `.repoagent/`。

## 适合做什么

- 在本地仓库里排查测试失败
- 读取当前代码结构并给出修改建议
- 基于现有文件做小步迭代，而不是脱离仓库空想
- 在会话中保留上下文，支持继续上一次工作
- 通过 TUI、目录 channel 和 cron 复用同一调度运行时
- 对工具安全、恢复、上下文、记忆、红队和策略候选做可复现评测

## 核心能力

- 多 provider 协议、fallback、路由和统一 usage/cost 记账
- 会话有序、跨会话并发、前后台容量隔离和取消传播
- token-aware 上下文、压缩、长期记忆检索与 consolidation
- ToolGateway 参数校验、effect approval、capability、sandbox 和并发冲突控制
- task state、checkpoint、trace、call ledger、evidence bundle 和离线 replay
- Skill、MCP、声明式插件、隔离 subagent 角色
- paired evaluation、fault injection、red team、SWE-bench adapter 和 release evidence
- evidence-gated Evolver：候选 worktree、sealed grader、人工激活与 append-only rollback

## 安装

需要 Python 3.10+。

如果你用 `uv`，直接安装依赖：

```bash
uv sync
```

如果你已经在自己的 Python 环境里工作，也可以直接装成可编辑模式：

```bash
pip install -e .
```

模型配置统一保存在用户目录 `~/.config/repoagent/.env`，因此不需要在每个被分析仓库中复制配置：

```dotenv
REPOAGENT_PROVIDER=deepseek
REPOAGENT_DEEPSEEK_API_KEY=your-api-key
REPOAGENT_DEEPSEEK_MODEL=deepseek-v4-pro
```

Shell 环境变量优先于用户级配置；目标仓库自身的 `.env` 可以进一步覆盖配置。

## 快速开始

```bash
uv run repoagent
```

进入其他代码仓库后，可以直接运行 `repoagent` 分析当前目录；也可以通过 `--cwd` 显式指定目标仓库：

```bash
repoagent --cwd /path/to/other-repo
```

无需模型密钥即可运行完整的本地 runtime-contract demo：

```bash
uv run python scripts/run_offline_demo.py
```

它会执行 12 个确定性场景并校验每个 Turn 的状态、trace、report 和 evidence bundle。该结果只证明运行时合同，不代表通用 coding benchmark 水平。

常用运维入口：

```bash
repoagent doctor
repoagent provider list
repoagent session list
repoagent gateway status
repoagent cron list
repoagent evolver status
repoagent-eval --help
```

架构、迁移、安全和发布证据分别见：

- [当前架构](docs/architecture/current-architecture.md)
- [迁移指南](docs/migration.md)
- [威胁模型](docs/security/threat-model.md)
- [发布与评测证据](docs/release.md)
