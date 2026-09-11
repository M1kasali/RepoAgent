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

## MCP 服务

安装可选依赖后，通过显式配置文件连接可信的 stdio、SSE 或 Streamable HTTP MCP 服务：

```bash
uv sync --extra mcp
repoagent --cwd /path/to/repo --mcp-config /path/to/trusted-mcp.json
repoagent mcp check --config /path/to/trusted-mcp.json --cwd /path/to/repo
```

```json
{
  "mcpServers": {
    "docs": {
      "command": "/path/to/server-python",
      "args": ["/path/to/server.py"],
      "startup_timeout": 10,
      "tool_timeout": 30
    }
  }
}
```

配置会启动本地进程，必须由使用者审阅；不会自动发现或加载仓库中的 MCP 配置。
工具仍经过 ToolGateway 审批与校验，服务声明的只读提示不会自动授予并行权限。
发现阶段关闭临时连接；实际调用期间复用会话，退出 Runtime 时关闭并回收进程。
重新连接会核对工具目录，目录变化需要重新构建 Agent。

HTTP 配置示例（SSE 将 `type` 改为 `sse`，并填写实际 SSE 入口）：

```json
{
  "mcpServers": {
    "docs": {
      "type": "streamableHttp",
      "url": "https://mcp.example.com/mcp",
      "headers": {"Authorization": "Bearer REPLACE_WITH_TOKEN"},
      "required": true,
      "startup_timeout": 10,
      "tool_timeout": 30
    }
  }
}
```

鉴权使用显式静态请求头，尚不支持自动 OAuth。包含凭据的配置不要提交到 Git。
默认服务必须连接成功；显式设置 `required: false` 后，连接/鉴权失败可以降级，
失败服务不注册工具。配置错误、地址策略拒绝不能通过该选项绕过。
`mcp check` 不调用模型，只检查连接和工具发现并关闭会话；输出服务状态与错误码，
任一已检查服务不可用时退出码为 2。必需服务失败会中止后续发现。

HTTP 请求限定在配置的同源地址，拒绝重定向，不继承环境代理；每次连接校验 DNS
结果并固定目标 IP，同时保留 HTTPS 主机名校验。默认拒绝私有地址；可信本地服务
可显式设置 `allow_private: true`，但仍受调用者显式传入的 Runtime 网络策略限制，
且始终禁止链路本地/元数据等特殊地址。超时/取消会关闭客户端会话，不能保证远端
已开始的副作用被撤销。

Docker stdio MCP 的进程生命周期已接入，并通过本地真实 Docker 验收。
Docker 预检同时检查版本与容器控制接口，避免版本号可读但实际无法管理容器时误报可用；
预检通过仍不替代镜像、挂载和 MCP 服务的真实验收。
选用 Docker 后，服务命令是镜像内的可执行文件，参数中的文件路径也必须是容器路径；
镜像需预装服务依赖，不会自动拉取镜像或安装依赖。`docker` 后端每个 MCP 会话拥有独立容器，
发现后销毁，实际调用时重建并复用，关闭/取消时强制回收整个容器。
现有 `run_shell` 仍每次新建容器，与 MCP 只共享工作区挂载，不共享 `/tmp` 或进程。
这是长驻 MCP 进程支持，不是完整的共享持久沙箱。

可显式选择 `--sandbox-backend docker-persistent`，让 shell 和 stdio MCP 共用一个
持久容器；`mcp check` 和 `sandbox status` 对应使用 `--backend docker-persistent`。
该模式要求镜像内有 Linux Python 3（`python` 命令、`fcntl`、`os.waitid`），启动时校验，
不满足则拒绝执行。工作区和 `/tmp` 在运行期共享，但各次 shell 的 `cd`、`export` 不继承。
关闭或取消一个执行会回收它的进程组，不销毁其他 MCP 会话；正常结束后也会回收组内子进程。
不支持通过脱离进程组长期运行后台服务。进程回收失败会令整个环境失效，需关闭后显式重启。
Runtime 关闭会移除容器，工作区文件保留，`/tmp` 清空；同步 `ask()` 每次结束都会关闭 Runtime。
此模式不是多租户安全边界。创建前会在宿主用户状态目录写入所有权记录，位置为
`${XDG_STATE_HOME:-~/.local/state}/repoagent/sandbox-owners/`，不放入工作区挂载。
下次启动自动核对同一工作区的记录，也可手动运行：

```bash
repoagent sandbox reconcile --cwd /path/to/repo
```

Windows Docker CLI 可指定 `--docker-executable /path/to/docker.exe`，此操作不需要工作区路径转换。
回收会跳过仍持有文件锁的活跃实例，仅删除引擎身份、名称及所有权标签匹配的容器。
创建超时后即使暂时查不到容器，记录仍保留为 `watching`，供后续核对；未确认项返回
`pending` 和退出码 2。没有后台回收守护进程，应用退出期间不会主动清理；这也不是
恢复原有进程或 `/tmp` 数据。不要手动删除所有权记录，否则将失去对应的自动回收依据。

```bash
repoagent mcp check --config /path/to/trusted-mcp.json --cwd /path/to/repo \
  --backend docker --image your-prepared-mcp-image
```

WSL 使用 Windows Docker CLI 时可加 `--docker-executable /path/to/docker.exe`
和 `--wsl-windows-path`。默认 `mcp check` 仍是 direct，容器配置必须明确选择 Docker。
工作区位于 WSL 内时，还需在 Docker Desktop 中开启对应发行版的 WSL 集成；
仅能返回 Docker 版本号不代表工作区挂载服务可用。
不支持进程启动的其他隔离适配器仍明确拒绝 stdio，不会退回宿主机；
HTTP/SSE 使用受上述策略约束的宿主网络客户端。
本地服务以当前用户权限运行，工具审批不能约束服务启动代码本身。
