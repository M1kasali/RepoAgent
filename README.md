# RepoAgent

`RepoAgent` 是一个面向代码仓库的轻量本地 coding agent。它直接跑在终端里，先看当前工作区，再用一组受约束的工具去读文件、改文件、跑命令，并把会话状态保存在本地 `.repoagent/` 目录里。

它更像一个能在仓库里持续工作的命令行助手，不是纯聊天窗口。你可以拿它做代码排查、测试修复、仓库分析，或者让它在当前项目里执行一次性的工程任务。

内部 Python 包、CLI 和新配置统一使用 `repoagent` / `REPOAGENT_*`。旧版 `.pico/` 状态目录和 `PICO_*` 环境变量仍可读取，新的工作区只会创建 `.repoagent/`。

## 适合做什么

- 在本地仓库里排查测试失败
- 读取当前代码结构并给出修改建议
- 基于现有文件做小步迭代，而不是脱离仓库空想
- 在会话中保留上下文，支持继续上一次工作

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
