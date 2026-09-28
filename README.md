# RepoAgent

面向代码仓库任务的本地 Agent 项目。当前主入口使用 Node / React / Ink TUI 与独立 Python 运行时，通过带认证的本地 JSON-RPC 通信。

## 安装与启动

需要 Python **3.12** 和 Node.js **22 或以上**。源码安装需先构建终端界面：

```bash
uv sync
npm --prefix ui-tui ci
npm --prefix ui-tui run build
uv run repoagent onboard
uv run repoagent
```

也可以直接使用 Python CLI：

```bash
uv run repoagent run --workspace /path/to/repo -m "分析这个仓库的测试入口"
uv run repoagent --help
```

主路径配置与状态存放在 `~/.repoagent-harness/`，可以通过 `REPOAGENT_HARNESS_HOME` 指定其他目录。`onboard` 配置模型；模型调用会使用所配置服务的额度。默认执行器 `sandbox.backend=none` 在宿主机运行；需要 BoxLite 时安装 `uv sync --extra sandbox`，在主路径配置中设置 `sandbox.backend=boxlite`，并准备 KVM 权限。BoxLite 不可用时会报错。

旧版 `.repoagent/`、`.pico/` 和用户级 `.env` 不会自动迁入新主路径。请通过 onboarding 配置新环境。

## 当前主路径

- Node / React / Ink 终端界面，独立 Python 进程与认证 RPC。
- `spawn` 后台子任务，结果作为 `Origin.SUBAGENT` 新 Turn 返回调度器。
- tracing 区分五种 Turn outcome；这不是五个调度状态枚举。
- CallEfficiency 提供 `off / observe / optimize`，含 Anthropic 显式缓存规划。
- DeliveryHub 使用非持久化的每 outlet 内存队列与有限重试。
- Evolver 保留候选、训练、sealed gate 和人工操作边界；当前只有 `runtime` 标签有完整可执行评估链。

实现来源、适配边界和验收方法见[运行时迁移说明](docs/runtime-migration.md)。版本仍为 `0.1.1`，本次未发布新版本。

## 旧能力与业务背景

旧 Textual TUI、同步 `delegate`、目录持久化交付及旧 Evolver 标签通过显式兼容入口保留：

```bash
uv run repoagent compat --help
uv run repoagent-compat --help
uv run repoagent issue --help
```

`issue` 继续使用原有 Case/worker 工作流，使用方式见 [Issue Demo](docs/issue-maintainer-demo.md)。历史 SWE-bench、修复验证和成本结果属于这套业务运行时，不能作为新主路径效果的证据。

[旧运行时使用说明](docs/runtime-compatibility.md)保留此前配置、BoxLite、MCP 和会话操作方法。[面试话术备注](Pico%20面试逐字话术稿.md)说明主路径与历史业务实测的区别。

## 验证与打包

```bash
uv run pytest tests -q
npm --prefix ui-tui run type-check
npm --prefix ui-tui test
# 在真实终端中检查 UI 加载
uv run repoagent --check
# 先构建 UI，再构建包含界面的 wheel
uv build
```

涉及真实模型、VM、平台的测试按各自标记或环境变量显式启用。测试通过不等于模型效果提升。第三方源码归属与许可证见 [LICENSES](LICENSES/README.md)。
