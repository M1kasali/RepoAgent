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

目录渠道 Gateway 可在前台运行：

```bash
repoagent gateway run --directory /path/to/queue --allow-from local -- --cwd /path/to/repo
```

默认 `--approval never`，高风险工具会被拒绝；需要自动批准时，在 `--` 后显式添加
`--approval auto`，并配合适当的 sandbox。服务不接受交互式 `ask` 审批。
消息生产方应先写临时文件，再原子重命名为 `inbox/<unique-id>.json`：

```json
{"chat_id": "room", "sender_id": "local", "message_id": "request-1", "text": "检查当前仓库的测试入口"}
```

回复写入 `outbox/`，已接收消息移入 `processed/`，格式错误或发送者被拒绝的消息移入
`rejected/`；暂时无法提交的消息保留在 `inbox/`。使用 Ctrl+C 退出并清理资源。
发送者白名单不是身份认证，队列目录必须由操作系统权限保护。此适配器仅用于可信本地
集成。目录渠道使用持久回执去重，已完成 Turn 重启后只补发回复；中断且执行结果
不确定的任务进入人工检查，不自动重跑。投递失败按退避重试，最多 8 次，之后可检查并重试：

```bash
repoagent channel receipts --cwd /path/to/repo
repoagent channel retry-delivery turn_id --cwd /path/to/repo
```

手动重试只重新投递已保存的回复，不重新执行任务。回复文件包含稳定的 `delivery_id`；
下游消费者也应按此 ID 去重。此机制不承诺工具副作用或下游消费的恰好一次语义。

QQ 渠道需要安装可选依赖 `uv sync --extra qq`，并在本地环境中配置
`REPOAGENT_QQ_APP_ID` 和 `REPOAGENT_QQ_SECRET`，不要把凭证写入启动参数或仓库。

```bash
repoagent gateway run --channel qq --allow-from YOUR_PLATFORM_USER_ID -- --cwd /path/to/repo
```

支持 QQ 私聊、群聊 @ 消息及频道私信，白名单应填写对应平台事件中的发送者 ID。
入站附件仅提供元数据提示，不下载文件；出站仅支持文本。每条回复绑定原始消息，
平台权限或回复时限可能导致投递失败。QQ 去重仅在当前进程有效，不具备目录渠道的
持久回执与重启补发能力。目前已通过离线 SDK／Runtime 集成测试，尚未进行真实 QQ 联调。

本地终端前端可以通过标准输入输出接入 JSON-RPC：

```bash
repoagent tui --rpc -- --cwd /path/to/repo
```

每行一个 JSON 对象，先初始化获取当前会话，再订阅事件并提交请求：

```json
{"jsonrpc":"2.0","id":1,"method":"system.initialize"}
{"jsonrpc":"2.0","id":2,"method":"turn.subscribe"}
{"jsonrpc":"2.0","id":3,"method":"turn.send","params":{"submission_id":"request-1","content":"检查测试入口"}}
```

`turn.event` 通知区分请求接收与最终完成；`confirm.request` 通过
`confirm.respond` 回答，`approved` 必须是布尔值。关闭输入管道会取消连接拥有的任务，
待确认操作默认拒绝。支持 `session.list`、`session.history`、`session.create` 和
`session.resume`；只有当前任务结束后才能切换会话，切换成功后需要重新订阅事件。
列表与历史支持 `offset`、`limit` 分页。这是单客户端、每次一个活动会话的本地 RPC
入口。

可选全屏终端界面：

```bash
uv sync --extra tui
uv run --extra tui repoagent tui --native -- --cwd /path/to/repo
```

支持多行输入、Send 提交、流式预览、Cancel 取消、工具审批、新建及恢复会话。
Ctrl+Enter 也可提交，Ctrl+Q 退出；审批默认拒绝，超时与退出不会批准操作。
会话历史显示最近 100 条，完整历史仍保留在存储中。运行中禁止切换会话。
`repoagent tui` 继续使用原有行输入入口，`--native` 和 `--rpc` 互斥。
全屏界面是可选的 Python 前端。模型选择器在任务空闲时切换配置档，同步更新输出额度、
上下文窗口和 Token 计数；本次连接内新建或恢复会话后继续使用所选模型。
该选择不写入全局配置，也不代表远端模型或凭证已验证可用。

RPC/全屏入口支持 `ask_user` 工具：每次最多 3 个问题，建议选项可点选，也可输入自己的
回答；按 Send 才提交，Skip 或超时按未回答处理，不会授权写文件等操作。
RPC 使用 `clarify.request` 通知和 `clarify.respond` 回答；`model.options` 列出配置档，
`model.select` 接收 `{"profile":"deepseek"}`。普通 CLI 和 Gateway 不默认启用问答工具。
Settings 可保存 API key/API base、删除本地凭证、添加或移除自定义模型，并用 Use 应用
模型配置。密钥保存在用户配置目录的 `repoagent/providers.json`，不是仓库文件；POSIX
新文件权限为 `0600`。文件未加密，仍需保护本机账号与备份。环境变量中的凭证优先于
保存的凭证。保存或断开不会替换已经构建的客户端，需点击 Use 或重启后生效；断开也不
撤销远端凭证或清除环境变量。配置接口不发起远端验证或模型请求。

Manage 支持会话改名、导出和删除非当前会话。删除需二次确认且校验版本，旧进程不能
重新保存已删除的会话；已有运行证据、导出文件和外部备份不会一起删除。导出包含脱敏
历史与内容摘要校验，不代表任务结果已通过验收。会话管理支持清空、按轮撤销和分支；
清空与撤销需要二次确认，并重建当前运行时。上述操作重置会话检查点与派生记忆，
为外部记忆分配新会话轨道，但不回滚工作区文件、不删除共享长期记忆或远端旧记录。

需要临时文本预览时，用 `{"stream":true}` 调用 `turn.subscribe`。
`turn.text.delta` 携带 Turn ID、递增 `sequence` 和 `provisional:true`；客户端按序追加
预览，收到 `turn.terminal` 后用最终回答替换预览，不要再追加一次最终回答。
默认订阅仍只接收接收/终态事件，兼容已有客户端。

架构、迁移、安全和发布证据分别见：

- [当前架构](docs/architecture/current-architecture.md)
- [迁移指南](docs/migration.md)
- [威胁模型](docs/security/threat-model.md)
- [发布与评测证据](docs/release.md)

代码编辑工具 `patch_file` 优先精确匹配，找不到时才尝试逐行忽略首尾空白，
不做字符级模糊替换。多处匹配必须显式传入 JSON 布尔值 `replace_all: true`；
重叠匹配仍会拒绝。替换文本应提供完整的预期缩进，工具结果会报告匹配方式和替换次数。
编辑保留未修改区域的原始换行，但不提供与外部编辑器之间的事务锁。

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
