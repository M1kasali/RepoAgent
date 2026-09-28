# 主运行时迁移与兼容边界

## 入口

`repoagent` 和 `python -m repoagent` 默认进入 `repoagent.entrypoint`，然后调用 `repoagent.harness.cli.commands.run`。TUI、CLI run 和 gateway 使用迁入的共同运行时组装函数。没有启动失败后自动回退旧运行时的逻辑。

- `repoagent`：Node / React / Ink TUI，Python 父进程启动 Node 子进程，以临时回环 TCP 端口和随机 token 认证 JSON-RPC。
- `repoagent run --workspace PATH -m MESSAGE`：新 Python CLI。
- `repoagent compat ...` / `repoagent-compat ...`：原 Python 运行时与 Textual TUI、同步 delegate、目录持久交付及旧 Evolver。
- `repoagent issue ...`：现有仓库维护 Case/worker 业务应用，仍使用原运行时。迁移业务执行链不是这六项契约替换的一部分。

Python 直接导入 `repoagent.runtime`、`repoagent.cli` 等旧模块仍解析到旧能力，以保留既有业务和脚本；新实现的 Python namespace 为 `repoagent.harness`。

## 六项契约

| 项目 | 新主路径 | 旧显式兼容路径 |
| --- | --- | --- |
| TUI | Node / React / Ink，独立 Python 进程，带认证 RPC | Python Textual，同进程服务调用 |
| Subagent | spawn 后台执行；会话级小时配额与并发限制；结果通过 Origin.SUBAGENT 新 Turn 回流 | delegate 等待完成，结果进入当前工具历史 |
| Outcome | tracing 中 completed、completed_with_tool_failure、provider_failed、error、cancelled | Spine completed、failed、cancelled 状态及单独证据 |
| CallEfficiency | off / observe / optimize，Provider decorator，Anthropic 显式缓存规划 | 原逐调用 Usage 与费用记录 |
| DeliveryHub | 每 outlet 内存队列、隔离慢渠道、有限重试，不落盘 | 目录渠道 SQLite 回执与持久恢复 |
| Evolver | 保留 skill、prompt、policy、runtime、model_profile、route 枚举，只有 runtime 有完整可执行链 | prompt、skill、tool_policy、routing、benchmark |

“五种终态”准确指 tracing outcome 分类，不能解释为 Scheduler 有五个终态枚举。Evolver 也不是枚举中只有 runtime 一个值。

主要源码：`repoagent/harness/cli/_runtime_assembly.py`、`agent/subagent/`、`tracing/semconv.py`、`call_efficiency/`、`spine/delivery.py`、`evolver/candidate_manifest.py`。子任务沿参考实现共享父 workspace 语义；旧 delegate 的独立工作区副本策略只在兼容路径保留。

## 来源与最小适配

参考固定为本地 Pico commit `c3a7a1d9032b539ca7a7cc52e46c9c0e29d5cdc3`。为避免把六项核心逻辑拼接到行为不同的旧调度器，主路径迁入其完整依赖闭包，旧业务通过显式入口保留。

适配集中在可重现导入脚本：Python namespace、产品名和状态目录、环境变量前缀、包资源位置、测试夹具路径、Evolver 核心路径保护名单；BoxLite 网络参数映射到已固定的 SDK 0.9.5 NetworkSpec。协议字段和行为算法保持参考实现。来源、许可和哈希见 `LICENSES/`。

```bash
python scripts/import_reference_runtime.py /path/to/pico-harness-reference --check
```

导入脚本不读取参考仓库中的本地配置、密钥或未跟踪产物。升级参考版本需显式修改固定 commit 并重新审阅差异。

## 配置和数据

主路径使用 `~/.repoagent-harness/`，或 `REPOAGENT_HARNESS_HOME`。旧配置／Session 不会自动迁移，避免把两套 schema 混用。通过 `repoagent onboard` 配置；尚未部署外部 Myna 时，在引导中选择跳过记忆，配置对应 `memory.backend=null`。这保留会话和本地 Skills，但不宣称有长期记忆效果。

主路径 Session 使用 channel/chat 身份和 JSONL 管理器。旧 `cli:<session_id>` 映射及 JSON 迁移适配仍在 compat。BoxLite 默认不启用，主路径 `sandbox.backend=none` 为宿主执行；`boxlite`/`auto` 不可用时明确失败。

飞书、企业微信等参考适配器随源码保留，但本次未配置或验收真实平台，不能据此声称线上可用。历史 SQLite 记忆、Docker 业务执行与历史 benchmark 仍属 compat；并未迁移成 Myna 或新主路径效果证据。

## 验收范围

迁移阶段验收包括参考 Python 契约回归、新旧入口路由、真实 TypeScript 客户端与 Python 生产 RPC 服务通信、前端测试和类型检查、真实 PTY 中的 UI 加载、安装包独立加载，以及原有离线回归。2026-09-27 的模型响应与外部平台采用离线测试；后续真实模型验收单独记录如下。

历史 HAL Mini 36/50、配对实验和 Evolver 小样本成绩保持原冻结口径。本次是执行架构迁移，不能描述成新主路径已经复现这些成绩。

### 2026-09-27 本地验收记录

- 合并全量 Python：**2870 passed、68 skipped**，309.32 秒。跳过的是条件测试；不将其计为成功。另有 13 条 warning，包含原有 datetime.utcnow 弃用提醒和 DirectExecutor 子进程析构时事件循环已关闭的提醒。
- 最终主路径专项：**912 passed**，22.53 秒（包含在全量范围内，不与全量相加）。
- 前端：**75 个文件、830 tests passed**；TypeScript 类型检查和 bundle 构建成功。
- 新增验收：实际生产组装，脚本 Provider 触发 spawn；后台子任务完成后以 SUBAGENT 新 Turn 回到同一 Session，调用装饰器生效且 JSONL 落盘。无真实模型请求。
- 实际 TypeScript RpcClient 连接独立 Python 生产 RPC 服务，验证 token 握手、hello、ping、version、setup、resize。
- wheel 与 sdist 构建成功；wheel 包含 UI bundle、模板和第三方许可证。在 `/tmp` 脱离源码目录、复用现有 Python 依赖，分别通过主入口、compat、issue 的 help 检查，并在真实 PTY 中通过 `--check`。这不是一次全新机器的依赖安装测试。
- 导入校验：**881 个源文件映射一致**。`git diff --check` 通过。
- 默认模型测试原先继承当前环境的模型设置，现隔离环境后验证内置默认值；未因此修改业务默认配置。

按照 `agent.md`，工作区中的本次 UI `node_modules/` 与 `dist/` 已清理。源码启动时先按 README 执行 `npm --prefix ui-tui ci` 和 `npm --prefix ui-tui run build`。包含已构建 UI 的本次验收 wheel 保存在 `/tmp/ra-six-dist/`，它是本地临时验收产物，不是发布版本。本次未提交或推送 Git。

### 2026-09-28 真实模型交互验收

验收版本为 `e89dc8547032ab6b3e80650837fba000b45a7479`。从该提交导出独立工作区，在真实 PTY 中启动默认 Node / React / Ink TUI，连接 `deepseek/deepseek-v4-flash`。使用临时配置，关闭外部记忆，禁用 shell、写文件和网页工具，保留读取与后台子任务工具。日常配置未修改。

| 场景 | 实际结果 |
| --- | --- |
| 仓库问答 | 模型实际读取 `pyproject.toml` 和 `repoagent/entrypoint.py`，回答项目用途、默认入口、兼容入口和测试命令。 |
| 重启恢复 | 退出 TUI 后重新启动，用 `/sessions resume tui:20260928_133432_5f2b03` 恢复 JSONL 会话；询问上一轮标记，模型准确返回 `ORCHID-928`，该轮未调用工具。 |
| 后台子任务 | 实际调用一次 `spawn`，父任务先返回启动回执；子任务读取 `repoagent/harness/tracing/semconv.py`，结果通过同一会话的 `Origin.SUBAGENT` 新 Turn 回流，父任务列出五种 outcome。 |

- 调用记录包含 **8 次运行时模型调用，全部成功**；另有一次最小 Provider 连通性请求，HTTP 200。
- 审计记录包含 **4 个已完成 Turn**：3 个 user、1 个 subagent，属于同一会话；工具调用为 3 次 `read_file`、1 次 `spawn`。
- 逐文件比较独立工作区与提交中的 Git blob，**受版本控制的文件无内容变化**。本轮没有修改运行时代码。
- 8 次运行时调用中，5 次有费用估算，已知小计约 **0.001231 美元**；另 3 次缺少费用估算。该小计不含独立连通性请求，不能当作本次完整费用或账单金额。
- 本次验证的是主路径真实交互，不是新 benchmark，也未验收 BoxLite 虚拟机、外部平台或缓存优化效果。

验收结束后退出 TUI，删除含凭据的临时配置、工作区副本和原始日志，并清理本次前端依赖与构建产物。仅保留脱敏验收记录；本轮记录未自动提交或推送。
