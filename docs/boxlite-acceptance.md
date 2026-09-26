# BoxLite 实机验收

日期：2026-09-26。环境：WSL2 Linux x86_64，BoxLite 0.9.5；当前用户已加入
`kvm` 组，Agent 通过 `sg kvm` 启动测试进程。

本次验证使用真实 VM、真实 MCP 客户端和 VM 内的 JSON-RPC 测试服务，不调用模型。
基础镜像为 `ubuntu:22.04`，MCP 和网络场景使用 `python:3.12-slim-bookworm`。
所有场景共用临时镜像缓存，工作 VM 与工作区各自独立。

## 复现

```bash
REPOAGENT_BOXLITE_LIVE=1 uv run --extra sandbox --extra mcp \
  pytest tests/test_boxlite_live.py -v -s --tb=short -p no:cacheprovider
```

如果当前进程仍使用加入用户组之前的权限，在 `newgrp kvm` 后运行，或使用
`sg kvm -c '上述命令'`。测试需要下载公开镜像并访问公开 HTTPS 测试目标。
未设置 `REPOAGENT_BOXLITE_LIVE=1` 时会跳过，跳过不能算验收通过。

## 全量回归与项目端到端验收

本轮全量回归：**1958 passed，67 skipped，13 warnings，317.39 秒**。
按默认条件跳过的实机/外部依赖测试不能视作通过。新增的 BoxLite 项目端到端
用例在全量运行开始后加入，单独显式执行，不计入 1958。
警告仍为 multiprocessing fork、datetime 弃用和既有 subprocess transport 清理警告。

现有 `scripts/run_offline_demo.py`：**12/12 场景通过**，12 份证据包校验成功。
新增 `tests/test_boxlite_e2e_live.py`：**1 passed，72.99 秒**。

端到端流程使用确定性模型响应驱动真实 `RepoAgent.ask_async`、工具网关、
BoxLite 和 SessionStore；不调用模型服务，不是模型编码能力评测。

| 检查 | 实际证据 |
| --- | --- |
| 读取并修复代码 | 读取 `return a - b`，补丁改成 `return a + b` |
| 修复前后测试 | VM 内 unittest 从退出码 1、断言失败变为退出码 0、`OK` |
| MCP 共用 VM | Shell 写入 `/tmp/from-shell`，MCP 读到并写回另一份临时文件，Shell 再读到 |
| 子 Agent 权限及隔离 | `implementer` 在独立工作区读代码、写笔记；两条工具 trace 均为 `ok`，父工作区没有子笔记 |
| 子 VM 生命周期 | 观察到不同父子 VM ID；委派结束后实际运行列表仅剩父 VM |
| 真正重启恢复 | 创建进程 PID 387585 结束后，由新进程 PID 388002 调用 `RepoAgent.from_session` 恢复同一会话 |
| JSONL 追加 | 历史从 9 条增至 13 条；恢复后的文件保留恢复前的完整字节前缀，各行均可解析 |
| 重启后的执行 | 修复文件仍存在，新 VM 不保留旧 `/tmp` 状态，重新运行 unittest 成功 |
| 退出清理 | 两阶段均验证 `runtime.list_info()` 为空，记录的宿主 shim PID 消失，再清理临时运行目录 |

验收脚本初版误用了子 Agent 没有权限的 `run_shell`，仅检查完成状态未发现拒绝。
补充子 Agent trace 断言后暴露了问题；最终脚本按现有角色权限验证读写，测试由
父 Agent 执行。没有扩大子 Agent 权限，也没有将初版结果当作最终验收依据。

复现：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/run_offline_demo.py
REPOAGENT_BOXLITE_LIVE=1 .venv/bin/python -m pytest \
  tests/test_boxlite_e2e_live.py -v -s -p no:cacheprovider
```

这证明上述正常流程可运行，不撤销下文记录的 SDK 启动波动、超时子进程残留
及特殊宿主网关限制；它们没有通过本次验收被修复或覆盖。

## 原版对照与迁移修复

随后在同一 WSL 主机、同一 BoxLite 0.9.5、同一镜像下，直接导入干净的原版
Pico checkout，完成三组对照。参考提交为
`c3a7a1d9032b539ca7a7cc52e46c9c0e29d5cdc3`，执行器 SHA-256 与源码清单一致；
没有修改原版源码、模拟 SDK 或自动重试失败命令。对照的是执行器及原始 Shell
工具，不是调用模型后的完整 Agent 任务。原版和迁移版依次使用同一临时镜像缓存，
每组结束后检查实际 VM 列表为空，再关闭 runtime。

| 组别 | 第一轮 40 条命令 | 修复后第二轮 40 条命令 | 超时后子进程写入 |
| --- | --- | --- | --- |
| 原版 Pico 执行器 | 39 成功，1 次 `spawn_failed` | 40 成功 | 两轮均发生 |
| 迁移执行器，直接异步调用 | 39 成功，1 次 `spawn_failed` | 39 成功，1 次 `spawn_failed` | 两轮均发生 |
| RepoAgent 同步适配器 | 40 成功 | 38 成功，2 次 `spawn_failed` | 两轮均发生 |

第二轮还通过原版 `ExecTool.execute(timeout=1)` 验证上层 Shell 工具：返回
`Command timed out after 1s`、`failed=True`，子进程随后仍写入文件。源码中
Shell 工具将命令错误转为工具结果，不会因此关闭执行器。此证据确认本地固定
参考版本存在相同边界，不代表所有 Pico 版本或所有部署环境。

**本次修复的迁移差异：** RepoAgent 原先在任意执行异常后关闭整个 VM，原版
Shell 工具会保留 VM。现在执行器已启动时仅上报命令错误；启动尚未完成、用户
取消及退出时仍清理 VM。第二轮适配器实际遇到两次启动命令错误，VM ID 保持
不变，没有自动重试。已增加“命令失败保留 VM”和“延迟启动失败仍清理”回归。
相关离线回归为 82 passed，1 条已有 subprocess transport 清理警告。

网络兼容补丁也有直接证据：原版的 `network='none'` 在固定 SDK 中报类型错误，
`allow_net=[...]` 报未知参数。现有 `NetworkSpec` 映射仍保留，没有升级 SDK。

复现三组对照：

```bash
python scripts/compare_pico_boxlite.py \
  --reference /path/to/pico-harness-reference \
  --output /tmp/pico-boxlite-comparison.json
```

该脚本会检验原版执行器哈希，并输出每条失败记录、超时副作用、原版工具结果
和 VM 是否保持不变。统计次数有限，不用于估计失败概率。

公网探测现需额外设置 `REPOAGENT_BOXLITE_PUBLIC_NETWORK=1`，与本机可控服务
的策略测试分开报告。另一次本机探测发现，`allowNet=['192.0.2.1']` 时仍能通过
特殊网关 `192.168.127.254` 访问宿主回环 HTTP 服务；不能声称当前 SDK 的白名单
能隔离宿主回环服务。这项边界单独保留，不通过扩大白名单掩盖。

修正后的普通外连测试将服务绑定到宿主实际网卡地址，用同一个服务验证四种
配置：开放网络通过、禁网不可达、目标 IP 在白名单时通过、不在白名单时拒绝。
该测试 1 passed，9 deselected，84.18 秒，不依赖外站或公共 DNS。
它验证的是 IP 白名单；域名/SNI 仍由单独的公网测试覆盖，不能混称为同一项。
本轮 MCP 正常、取消、崩溃及启动失败回收四项实测也通过；同轮原回环网关
测试失败，已在上文单独记录。没有将选择性通过的项目合并成“整套验收通过”。

## 前一轮完整验收结果

**完整验收未通过。** 最终整套运行：6 passed，3 failed，115.69 秒。
失败用例保留为失败，没有通过重试、跳过或预期失败消除。

| 场景 | 最终一轮 | 说明 |
| --- | --- | --- |
| 基础 VM、挂载、复用、简单超时 | 通过 | 真实命令及工作区文件断言 |
| MCP 与 Shell 共用 VM | 通过 | 两者读写同一份 VM 临时文件 |
| MCP 取消后清理 | 通过 | 无延迟写入，VM 保留 |
| MCP 崩溃后清理 | 通过 | 本次适配层修复已实测 |
| 父子 VM 隔离 | 失败 | 前一轮通过；最终轮 SDK 在子 VM 执行命令时报 `spawn_failed` |
| 超时派生子进程清理 | 失败 | 两轮均出现超时后的延迟写入 |
| Shell 取消后 VM 回收 | 通过 | 实际 VM 列表为空，无延迟写入 |
| VM 启动验证失败后回收 | 通过 | 真 VM 创建后令验证超时，检查部分启动清理 |
| 禁网与域名白名单 | 基线失败 | 最终轮开放网络访问 `www.python.org` 超时，不能据此判断策略失效 |

前一轮针对 MCP 三项及网络策略的运行：4 passed，4 deselected，88.69 秒。
其中开放网络、禁网、白名单允许/拒绝，以及直接 IP 的 HTTPS 探测全部通过。
最终轮网络基线失败表明外网测试存在波动，不覆盖前一轮的通过记录，也不能
把前一轮通过当作整套最终通过。

## 覆盖与已取得的证据

- 工作区挂载写入、同一 VM 的 `/tmp` 复用、简单命令超时后仍能执行下一条命令。
- MCP 服务与 Shell 共享 VM 内的临时文件；关闭 MCP 服务后共享 VM 仍然可用。
- MCP 调用取消、服务主动崩溃后，服务进程停止，后续 Shell 命令仍能执行。
- 父子 VM 的临时文件及挂载工作区各自独立；关闭子 VM 后父 VM 仍能执行。
- 未挂载的宿主测试文件在 VM 内不可见。此项不代表穷尽所有隔离攻击面。
- Shell 任务取消后，VM 从 BoxLite 的实际列表中消失，延迟写入未发生。
- 真实 VM 创建后令启动验证超时，部分启动清理后实际 VM 列表为空。
- 网络开放时先验证目标可达，再验证禁网和白名单：允许 `example.com`，拒绝
  `www.python.org`，绕过 DNS、直接使用已验证 IP 的 HTTPS 请求也被拒绝。
  测试要求收到 HTTPS 响应，不能仅凭 TCP 握手判断能绕过策略；未覆盖所有协议。
- 各扩展场景关闭后查询 `runtime.list_info()`，并检查已记录的宿主 shim PID 消失。
  检查发生在测试环境的兜底清理之前，不能靠兜底清理把残留错误变成通过。

## 本次修复

真实 MCP 崩溃时，BoxLite 0.9.5 对已退出进程执行 `kill()` 会报告
`Failed to send signal`。旧适配层把它当成未能清理进程，连共享 VM 一起关闭，
并使 MCP 调用抛出清理异常。

`BoxliteSandboxAdapter` 现在在 `kill()` 报错后等待退出确认，最多 1 秒。
确认进程已退出时完成流与任务清理；无法确认时仍回收 VM。没有按错误字符串
直接忽略异常。两项离线回归分别覆盖这两种结果，真实崩溃场景复测通过。

## 未通过的边界

最初这些失败仅来自 RepoAgent 实测。随后已完成上述原版执行器及原版 Shell
工具对照，确认超时残留和偶发启动错误在固定参考中也出现。未调用模型运行
原版完整 Agent；对照结论限于已测试路径。

**超时不能保证清理派生子进程。** 测试启动一个父进程，父进程再派生一个
延迟写文件的子进程。父进程超时被终止后，子进程仍写出了 `timeout-late`。
所以“返回 timeout、下一条命令能运行”不能证明原任务所有进程都已停止。

固定参考中的局部调用也相同：Pico 执行器超时调用 `execution.kill()`；固定 SDK
0.9.5 的 guest `ExecHandle::kill` 对单个 PID 发信号，不处理整个进程树。
来源：[BoxLite 0.9.5 exec_handle.rs](https://github.com/boxlite-ai/boxlite/blob/v0.9.5/src/guest/src/service/exec/exec_handle.rs)。
本次保留严格对照的执行器和 SDK 版本，没有用自动重建 VM 掩盖残留，也没有
把这个失败用例改成跳过或预期失败。关闭 VM 后，残留子进程随 VM 回收。

第一轮还出现过 SDK 内部 `spawn_failed`：
`received unexpected message: InitReady, expected: IntermediateReady(0)`。
后续 MCP 复测通过，但最终一轮父子 VM 场景再次出现同一错误。SDK 没有修改，
这项稳定性问题仍未解决；没有用自动重试掩盖它。错误由 SDK 内部报出，
且原版单事件循环执行器也已复现。适配器不是该错误出现的必要条件；没有据此
推断不同调用方式的失败概率相同。

## 回归与源码一致性

- 适配层及 Pico 执行器回归：64 passed，1 条已有 subprocess transport 清理警告。
- MCP 传输回归：16 passed。
- Pico 源码映射及哈希：11/11 一致；原先记录的 `NetworkSpec` API 兼容补丁仍是
  执行器与固定参考源码之间的唯一额外差异。本次产品改动位于 RepoAgent 适配层。
- 实机结果独立记录，不并入上述离线通过数量。
