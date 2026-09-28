# Pico 面试逐字话术稿

# Pico 面试逐字话术稿

<!-- REPOAGENT-NOTE-START -->
## 本项目阅读说明与业务开场（新增）

更新于 2026-09-27。保留原稿正文、问题顺序和通用原理；新增的 **“本项目实现”** 备注用于替换原稿中的具体实现说法。原文仍描述 Pico，不是对 RepoAgent 所有功能的逐项承诺。面试介绍自己的项目时，以备注和本项目证据为准。

本文把你所说的 RepoCode 项目统称为“本项目”；当前代码仓库和命令名仍是 RepoAgent。源码核对基于本地当前工作区，最近提交为 `869d76c`；主路径迁移、测试框架、修复反馈和部分评测脚本还有未提交内容。历史实验使用各自冻结快照，不能说全部成绩来自这个最新提交。

**阅读方式：** 原理和取舍继续沿原稿学习；遇到备注，改用备注中的实现描述。原稿中的个人经历、版本、用户规模和实验数字不自动迁移到本项目。后面的[本项目业务与 Benchmark 补充](#本项目业务与-benchmark-补充新增)提供自己的开场、成绩和追问话术。

### 本项目 30 秒介绍

我的项目是面向代码仓库和开源维护的 Coding Agent。我在现有 Agent Harness 基础上，围绕问题复现、代码调查、候选修复和独立验收做了一条完整链路。底层负责工具执行、上下文、会话恢复、权限和模型预算，业务层负责绑定仓库版本、隔离修改并保存补丁与测试证据。我还做了公开代码修复任务自测：在 HAL SWE-bench Verified Mini 固定 50 题上，使用当时冻结配置的 `deepseek-flash`，经官方评分器验收通过 36 题。这是固定题单上的自测结果。

### 本项目两分钟介绍

我做这个项目，是想解决代码仓库维护中从“收到问题”到“拿到可审核修复”的过程。模型给出一段解释并不够，维护者还需要知道问题是否真的复现、补丁基于哪个版本、修改了什么，以及测试证据是否可信。

我把项目分成两层。底层是 Agent Runtime，负责会话调度、上下文预算、模型与工具循环、权限、会话持久化和追踪。当前主路径使用 Node / React / Ink 与 Python 双进程，支持后台 spawn，JSONL 会话支持恢复和追加；执行后端为宿主直执行或 BoxLite。旧业务运行时的 Docker 能力保留在 compat。上层是仍使用旧业务运行时的 Issue 调查与修复应用：先保存问题和不可变仓库版本，运行隔离调查；维护者明确执行 fix 后，Agent 才生成候选补丁，再交给干净环境中的独立检查。

这里我特别关注三个边界：模型提出动作，Runtime 决定能不能执行；Agent 宣称完成，独立验收决定是否通过；通过验收的补丁只成为待审核候选，代码不会自动推送或合并。Issue 应用和历史 SWE-bench 实验使用 Docker；后来补齐的 BoxLite 是另一条已验收运行链路，不能把历史成绩说成 BoxLite 跑出来的。

验证方面，我先完成了 python-dotenv 历史问题从调查到单文件补丁的闭环，随后做了 SWE-bench 子集评测。HAL Mini 固定 50 题全部运行并评分，通过 36 题，修复率 72%。我也保留了改动后未获收益的实验：10 组新旧配对，旧版通过 8 题，新版通过 7 题。因此我会把运行机制、修复能力和优化收益分开讲，后两者必须有各自的实验支持。

**开场核对入口：** [业务流程](docs/issue-maintainer-demo.md)、[Issue 工作流](repoagent/issue_agent/workflow.py)、[HAL 实验报告](artifacts/swebench-hal-mini-20260917/REPORT.md)。

**本次迁移边界：** 六项核心差异已切换到参考主路径；历史业务与 benchmark 未重跑、不改变成绩归属。详情见 [迁移说明](docs/runtime-migration.md)。
<!-- REPOAGENT-NOTE-END -->

## 一、项目定位与整体介绍

### 如果让你完整介绍一下 Pico，你会怎么讲？

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 当前主路径迁入统一 Harness：Node / React / Ink TUI、CLI、Gateway 和后台 spawn 通过同一运行时契约执行。旧 Textual、SQLite 记忆及目录持久交付保留在 compat。Issue Case 应用仍使用原业务运行时，不能说历史 benchmark 已改用新主路径。
<!-- REPOAGENT-NOTE-END -->

我先给 Pico 一个明确定位：它是一个用 Python 实现、以本地运行优先的**通用 Agent Harness**。它关心的不是"模型会不会调用工具"，而是模型开始长期运行、开始修改真实环境以后，这次委托还能不能被**控制、恢复、解释和验证**。

一个最小 Agent Demo 通常很简单：入口收到消息，调用模型；模型要工具就执行；最后把文本返回。单入口、短任务、只读工具时，这套已经够用。任务一旦变长，入口一旦扩展到 CLI、TUI、消息渠道和定时任务，问题就变成系统问题：同一会话的新请求是排队、补充还是中断；模型说完成时，工具、会话、长期记忆和消息发送是否都成功了；进程崩溃之后，是只能继续聊天，还是能恢复工作区。这些都不是多写几句 Prompt 能解决的，我的工作就从这个问题开始。

围绕它，我做了**三个关键决策**。

**第一个，让所有入口最终走同一条 Turn 执行路径。** CLI、TUI、Gateway、Channel、Cron 和 Subagent 回流都会变成 TurnRequest，经过 Spine、每会话一条 Lane、用户与系统任务池，再进入同一个 Agent Loop。入口可以有不同交互方式，但不能各自维护第二套 Agent 语义。这么做会增加调度、事件和交付的复杂度，换来的保证是：同一任务换了入口，排队、取消、Context、Session 和终态的含义不应该变化。

**第二个，把状态的责任分清楚。** Session 保存这段对话真实发生过什么；Context 只是本次推理临时看到的窗口；Checkpoint 保存本地 Workspace 的恢复点；Myna Memory 面向跨任务、仓库作用域的长期复用；Trace 和 Artifact 保存证据；Delivery 回答结果有没有真的送到外部平台。这些状态之间会传递数据，但不是同一个事务。Session 已经保存、Memory 写入却失败，Turn 已经结束、Channel 发送却失败，都是真实可能出现的状态。把部分失败当成设计前提，而不是当成不会发生的异常，这个立场贯穿了整个架构。

**第三个，划定模型与 Runtime 的权力边界。** 模型负责提出不确定的选择：选 Tool、提 ContextPlan、生成 Evolver Candidate。确定性 Runtime 负责权限和验收：参数校验、预算、并发、风险边界、终态、Verifier 和 Activation，都不能由模型自我授权。理由很直接：模型和 Provider 都会变，这些不变量必须留在模型之外。

横向对比我也认真想过。LangGraph、OpenAI Agents SDK 这类系统更适合很多快速交付的场景，如果目标是尽快把 Agent 跑起来，它们是对的答案。Pico 选择自己实现更底层的契约，是因为它把多入口统一执行、按会话调度、跨状态域失败，以及"什么证据允许得出什么结论"本身当成研究对象。这里要澄清一点：本地运行优先只是部署和所有权上的选择，不是安全证明，Direct Sandbox 仍然在宿主机上执行，Provider、MCP 和 Channel 也可能访问网络。

所以收个尾：Pico 的价值不在功能数量，而在于它试图给多入口、长任务、有副作用的模型执行建立一套统一契约，让"从哪个入口进来、调度、恢复、验证、状态的含义"都不随入口和模型漂移。它最有辨识度的就是这三条：**统一 Turn 路径、状态分域、模型与 Runtime 的权力边界**。

### 如果给你两分钟，你会怎样完整介绍 Pico？

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 开场继续采用前面的代码仓库业务介绍。主路径的异步 Subagent 回流现已具备对应实现；外部 Myna 和真实平台收发未验收，原稿有关部署和实验数字仍不能照搬。
<!-- REPOAGENT-NOTE-END -->

我会**从一条请求开始讲**，跟着它走完整条链路。用户可能从 CLI、原生 TUI，或者飞书、QQ、企业微信发来任务。入口层只负责交互和平台适配，任务会被归一化成 TurnRequest，交给 Spine。Spine 用每个会话独立的 Lane 保证同一会话内的顺序和取消语义，再用用户任务池和系统任务池做容量隔离。接下来 Turn Runner 进入 Agent Loop：Context Engine 先按预算组装身份、项目引导信息、长期 Memory、Skills、历史和当前请求；Provider 返回文本或 Tool Call；Runtime 校验并执行工具，把结果送回模型，直到正常完成、失败、取消，或者达到迭代上限后做一次不带工具的收束。

**但任务执行完，不等于一切都成功。** 模型已经给出答案，工具里却可能有一环失败；Turn 已经完成，渠道发送却可能失败；Session 已经落盘，Memory 后端写入却可能失败。这些都不能压成一个 success=true。所以 Pico 把会话保存、工作区 Checkpoint、长期 Memory 写入、Trace、Turn 终态和平台 Delivery 拆成不同的状态域，各自回答各自的问题。我认为 Pico 最有辨识度的地方，不是"也能调用 Shell 或 MCP"，而是把多入口执行、状态所有权、失败语义和证据链放在**一条可解释、可定位的 Runtime 路径**上。

再往外一层，Pico 提供 PicoBench 和一个 opt\-in 的 Evolver Beta。PicoBench 用任务、Trial、确定性 Verifier、成对实验和 Claim Gate，把"测量是否有效、结论是否成立"变成流程而不是口头承诺。Evolver 可以生成候选修改，在训练集和 sealed evaluation 上验证，但候选只会停在 pending\_human，不会自动改动生产 Runtime。

#### 如果只给你 30 秒，你会怎么介绍 Pico？

Pico 是一个用 Python 实现、以本地运行优先的**通用 Agent Harness**。它不只封装一次模型调用，而是把 CLI、TUI、消息渠道、定时任务和 Subagent 等入口统一成同一种 Turn，再由同一套 Runtime 负责上下文组装、模型与工具循环、会话持久化、恢复、追踪、成本记录和结果交付。

它解决的是：Agent 从单轮 Demo 走向多步、长时、可执行任务以后，**状态由谁拥有、失败怎么恢复、工具副作用怎么约束、结果怎么验证**这些工程问题。

#### Pico 真正解决的核心问题是什么？

核心问题不是"怎么让大模型调用一个函数"，而是怎样**把一个概率性的模型放进一个有状态、有副作用、会失败的执行系统里，并且还能解释它做了什么**。单轮聊天只需要处理输入和输出。Agent 一旦能读写文件、执行命令、连接外部服务、持续多轮运行，系统就必须回答四类问题。

**第一是控制权**：谁决定一次任务何时开始、暂停、取消和结束，同一会话的新消息应该排队、注入还是中断。**第二是状态**：对话记录、当前工作状态、跨任务知识、工作区文件和外部副作用分别由谁保存，崩溃后能恢复到什么程度。**第三是风险**：哪些工具可以并行，哪些动作需要串行、审批或隔离，重试会不会重复产生副作用。**第四是证据**：最终文本说"完成了"是否可信，能否由文件、测试、回执和 Trace 独立验证。

Pico 的选择，是把这四类问题**集中到 Harness 层统一回答**，而不是分散在每个入口或 Prompt 里各自为政。

#### 你认为 Pico 的项目价值最终体现在哪里？

我认为它的价值有两层。**第一层是可迁移的工程模型**：把 Agent 看成"概率决策器加确定性 Harness"，用 Runtime 管理控制循环、上下文、工具、状态、恢复、交付和证据。这套模型不依赖 Pico，换成其他框架或自研系统仍然成立。**第二层是可核验的实现**：面试官可以沿着 `TurnRequest -> Lane -> OriginPools -> Turn Runner -> Agent Loop -> Deliverable -> DeliveryHub` 去看源码，也可以检查 Session、Context、Tool、Trace 和 PicoBench 的事实边界。

它不是靠功能数量取胜，而是试图回答一个更难的问题：当 Agent 给出结果时，我们是否知道它基于什么上下文、执行过哪些动作、哪些状态真正落盘、失败发生在哪一层、结论是否有资格对外宣称。对 Agent Infra 岗位来说，这种**可解释、可恢复、可评测的 Runtime 思维**，比"接了多少模型和工具"更有价值。

### Pico 和普通的聊天机器人或者 Function Calling Demo 有什么本质区别？

普通 Demo 的典型结构是"收到一句话，调用模型，模型需要时调用函数，再把最终文本返回"。这个结构能证明模型会使用工具，却没有证明系统能承担长期运行。Pico 多做的不是几层包装，而是建立了一套**执行语义**。

具体来说，Pico 中一次用户任务是 Turn，而不是某次模型请求。一个 Turn 内可以有多次 Provider 调用和 Tool 迭代。不同入口都经过同一条 Spine 和 Agent Loop，因此会话顺序、取消、上下文、工具安全和终态定义不随着入口改变。Session、Checkpoint、Memory、Trace 和 Delivery 也被分开管理，避免把"模型说完了"误认为"任务完成并成功送达"。

所以我的判断标准不是代码行数，而是系统有没有清晰的**控制循环、持久状态、失败边界、动作约束和可验证结果**。缺少这些时，它更接近带工具的聊天应用；具备这些并形成稳定运行契约后，才进入 Agent Runtime 或 Harness 的范畴。

#### Hermes、nanobot 等本地 Agent 也能调工具、存 Session，Pico 最有辨识度的能力到底是什么？

我不会把"能调工具、能存 Memory、能接飞书"当成差异点，因为很多 Agent 都能做到。Pico 更有辨识度的地方是**执行和证据的统一**。

**第一，执行路径统一。** 不同入口不是各写一条 Agent 链，而是统一成 Turn，由 Spine、Lane、OriginPools、Turn Runner、Agent Loop 和 DeliveryHub 形成稳定骨架。同一任务从 CLI 换到 TUI 或 Channel，核心语义不应该漂移。**第二，证据可以沿着系统追溯。** Pico 不把最终回答当作唯一真相。Provider attempt、Tool execution、Session、Context path、Turn 终态、Delivery outcome 和 Trace lineage 可以关联起来，面试时可以沿证据解释"模型调用成功，但任务为何仍失败"，或者"任务完成，但为什么用户没有收到"。**第三，评测结论分层治理。** 评测层把 ship\_complete、measurement\_valid 和 positive\_claim\_eligible 分开，负结果也可以是一次完整且有效的实验，避免只挑好看的指标。

这种定位的代价是**实现复杂度更高**。它不是唯一正确的架构，但在多入口、本地执行、工程可审计这几个约束下，这是 Pico 最值得讲的设计选择。

#### 为什么不直接使用 LangGraph、OpenAI Agents SDK 或其他成熟框架？

我不会回答"这些框架不够好"，因为它们各自已经覆盖了 Agent Loop、工具、持久化、HITL、Tracing 或多 Agent 编排中的很多能力。**是否自研，取决于目标。**

如果业务目标是快速交付一个流程明确的 Agent，我会优先使用成熟框架，避免重复建设。Pico 选择自己实现，是因为项目要研究并控制更底层的运行契约：多种 Host 怎样共用一条 Turn 路径；同一会话的并发消息怎样排队、注入或中断；Session、Checkpoint、Memory 和 Delivery 怎样保持独立终态；工具并行和副作用边界怎样由 Runtime 执行；评测怎样把 Provider 故障、产品失败和测量无效区分开。这些问题若完全交给上层框架，项目就很难展示 Harness 设计本身。

更准确地说，Pico 不是为了证明"从零写框架一定更优"，而是一个**可审计的工程样本**。实际生产选型时，我会比较团队维护成本、生态集成、持久化语义、可观测性、安全隔离和定制深度。能复用成熟组件的地方应当复用，只有真正需要控制的契约才值得自研。

### 为什么强调 local\-first？它和云端 Agent 有什么取舍？

local\-first 的主要价值，是**离代码、文件和开发工具更近，用户对数据路径和执行环境拥有更直接的控制**。Coding Agent 经常需要读取仓库、执行测试、修改文件，本地 Runtime 可以减少把完整工作区上传到远端的需求，也便于用户观察和干预。

但本地不等于离线，也不等于天然安全。Pico 的模型 Provider、Web、MCP 或 Channel 仍可能访问网络；默认的直接执行后端是在 Host 上运行命令，只有显式选择 BoxLite 时才进入微虚拟机隔离。本地运行还要面对依赖安装、凭据管理、跨平台差异、进程生命周期和资源限制，这些在云服务中通常由平台承担。

所以我会把它定位成一种**部署和所有权选择，而不是安全特性**：Runtime、Workspace 和状态优先靠近用户，网络策略、最小权限、Sandbox、日志脱敏和失败关闭这些风险控制仍然要单独设计和落实。

#### Pico 面向什么用户？它是 Coding Agent 吗？

Pico 更接近**通用的本地 Agent Harness，而不是专门的 Coding Agent**。它内置文件、搜索、Shell、Web、消息、询问用户、Subagent 等工具，因此可以承载代码任务。但 Runtime 的核心抽象是 Turn、Context、Tools、Session、Memory、Tracing 和 Delivery，并没有把补丁、Pull Request、编译流水线当成唯一任务模型。

它适合两类人。一类是希望在本地或消息渠道运行可扩展 Agent 的开发者；另一类是希望研究 Agent Runtime 工程问题的人，例如上下文管理、故障恢复、工具治理和评测。

如果面试官把它和 Claude Code、Codex 这类 Coding Agent 比较，我会说它们在任务层有交集，但产品层级不同：Coding Agent 优化的是软件工程任务体验，Pico 更关注承载不同 Agent 应用的运行时契约。

#### Pico 支持多模态吗？

当前可以处理一定的媒体输入、附件解析和 MediaOut 交付，但这不等于完整的多模态生成平台。实际能力还取决于 Host、Channel adapter 和 Provider 是否支持相应媒体格式；Channel 通常也不会像 TUI 那样流式展示所有中间事件。媒体生成、Deep Research 这类能力不在当前功能清单里，我也不会去宣称。

面试时我会落到具体路径：**明确说"支持哪条输入或输出路径"**，而不是笼统地说"支持多模态"。

### 这个项目现在是什么成熟度？能说“生产级”吗？

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 包版本仍为 0.1.1，本次是工作区迁移，未发布新版本。此前 1958 passed / 67 skipped、离线演示 12/12 和真实 BoxLite E2E 属于迁移前验收；2026-09-27 新旧合并回归 2870 passed / 68 skipped；其中最终主路径专项 912 passed，前端另有 830 passed。详见 [迁移说明](docs/runtime-migration.md)。原稿的用户规模、SLA 与模型收益不适用。
<!-- REPOAGENT-NOTE-END -->

**不能，我不会说它是生产级。** 冻结版本的包是 pico\-harness 0\.1\.7，状态是 Alpha、pre\-RC，没有正式 release tag，完整的发布验收也没有通过。项目里有大量确定性测试、契约 Gate、候选实验和部分真实 Provider 或渠道证据，但这些证据各自只支持特定提交、工作负载和场景。

Pico 目前证明的是**架构边界、部分运行契约和评测方法**，而不是生产规模。具体说：出站 Delivery 队列不持久，多个状态域之间没有跨域事务，默认 Host 执行不是强隔离，Tracing 是本地 best\-effort，只有受约束的 runtime candidate label 有完整 Evolver 证据链，企业级多租户和运营能力也还没有建立。

我会把"代码存在""测试通过""实验有效""生产可用"当成**四个不同层级**来表述，每一层对应不同的证据强度。这既是这个项目的现状，也是我评估任何 Agent 系统时都会用的标准。

#### Pico 允许哪些扩展，又明确不支持什么？

当前支持这些扩展点：Provider 与 OpenAI\-compatible endpoint、MCP Server、贡献 Memory backend 或 Tool 的 Plugin、本地 Skills、注册的 Evolver benchmark bundle 和 Host\-specific Outlet。不支持的有：远程 Plugin/Skill 市场、任意 Candidate Label 的通用评估、自动生产激活、企业多租户和隐式外部状态迁移。

回答扩展性时要同时讲**扩展点和不变量**：扩展点决定能插什么，不变量决定插进来后必须遵守什么。一个插件不能绕过 Spine 拥有 Turn 终态，MCP 不能替代 Tool policy，新的 Memory backend 也必须遵守 fail\-closed 生命周期。**扩展不是允许任何模块任意写 Runtime 状态。**

## 二、Agent、Runtime 与 Harness

### 你怎么理解 Agent、Runtime 与 Harness 的边界？

我不会只按名字区分这三个概念，而会看**谁拥有控制权**。

Agent 是**受约束的动态决策**。模型根据当前观察选择下一步，Runtime 执行动作并把结果送回来，系统反复推进，直到满足终止条件。关键不在有没有一个叫 `Agent` 的类，也不在是不是用了 ReAct，而在模型能否在运行时改变下一步，同时权限、状态和正确性没有全部交给模型。

最小实现可以是一个 `while True`：模型给出 Tool Call 就执行，没有就退出。这个写法能演示闭环，却不能表达真实运行语义：模型不再调用工具，可能是正常完成，也可能是空响应、截断或误判；工具连续失败时，要区分参数错误、瞬时限流和不可恢复失败；取消发生时，要知道副作用是否已经产生；达到预算以后，要明确是失败、返回部分结果，还是做一次不带工具的收束。所以工程上的 Agent Loop 更像一个**有限状态机，而不是无界循环**。

Runtime 更偏执行内核，负责模型调用、工具迭代、状态推进、取消和终止。Harness 的范围更大，它把 Runtime 放进由 Host、Session、Context、Memory、权限、恢复、Tracing、Evaluation 和 Delivery 组成的运行环境里。SDK 提供能力，Framework 提供组织方式，Agent 应用定义业务目标。现实产品经常横跨几层，所以我不争标签，而是问四个问题：控制循环在哪里，状态由谁保存，动作由谁批准，终态由谁裁决。

这也是为什么同一个模型放进不同 Harness，效果会差很多：Harness 决定模型看到什么、能调用什么、错误怎样返回、历史怎样压缩、是否允许并行、失败是否重试、什么时候请求人工、最后怎样验证。模型相同，它面对的观察、动作和反馈环境却不同。所谓 agent\-native，也不是简单加上 Tool Calling，而是系统从一开始就承认任务会多步、非确定、跨窗口，还可能产生真实副作用。

规划方式没有唯一答案。短、可逆、反馈快的任务适合边做边规划；高成本、强依赖、不可逆的任务更需要显式 Plan、审批或图状态。模型可以提出计划，Runtime 仍要控制预算、允许的动作和验证。Pico 的主 Loop 行为上接近 ReAct：模型和 Tool 多轮交替，没有强制每个任务先经过固定 Planner；达到最大迭代后关闭工具做一次 Synthesis，汇报当前进展和阻塞，这不等同于任务正常完成。

完成也要分层。模型说"完成"只是语言信号：Loop 是否停止是一层，Turn 终态是一层，领域 Verifier 是否通过是另一层，结果是否送达用户又是另一层。Pico 能区分正常完成、带工具失败的完成、Provider 失败、一般错误和取消，但普通 Turn 并不自动拥有每个领域的 Verifier，高风险应用仍要在 Harness 之上定义机器可检查的验收合同。

在 Pico 里，Host 负责与用户或平台交互，Spine 负责顺序、取消和 Turn 生命周期，Agent Loop 负责一次 Turn 内的模型与工具工作，Context Engine 负责模型窗口，Session 保存 Transcript，Delivery 负责外部呈现。TUI 通过 RPC 使用 Python Runtime，而不是在前端再写一套 Loop。这些边界让入口和模型可以替换，控制语义却不会跟着漂移。

所以我的理解是：Agent 负责在受限范围内做动态决策，Runtime 负责推进动作与状态，Harness 负责把这套执行放进会话、权限、恢复、追踪、评测和交付环境。**Agent 的智能来自模型，可靠性来自模型之外的执行契约。**

### 你怎么定义 Agent？它和普通 LLM 应用有什么区别？

我会把 Agent 定义为：由模型在运行过程中反复观察状态、选择动作、接收结果并继续决策，直到满足终止条件的系统。真正关键的是**闭环**，而不是有没有一个名叫 `Agent` 的类。

普通 LLM 应用通常是输入一次、推理一次、输出一次；固定 Workflow 由开发者预先规定步骤和分支。Agent 允许模型在局部范围内决定下一步，比如先搜索文件还是先运行测试、工具失败后是否换一种方法。与此同时，模型不是整个系统，它只负责不确定性的决策部分；工具执行、权限、状态持久化、预算、重试、取消和验证，由确定性 Runtime 管理。

因此，一个可靠 Agent 不是"LLM 加 while 循环"，而是**模型决策 \+ 有边界的动作空间 \+ 可恢复状态 \+ 明确终止和验证**。Pico 的 Agent Loop 只是其中的决策循环，Spine、Context、Session、Tools、Tracing 和 Delivery 共同构成可运行的 Harness。

#### Agent Runtime 和 Agent Harness 有什么区别？

这两个词没有完全统一的法定定义，我先说明我采用的边界。Agent Runtime 更偏执行内核，负责一次或一组 Agent Run 如何推进，例如模型调用、Tool Call、迭代、状态转换和终止。Agent Harness 的范围更大，它包住 Runtime，并提供入口适配、会话、上下文、恢复、权限、可观测、评测、交付等运行环境。

拿 Pico 举例：在项目自己的术语里，Pico Runtime 并不等于 Agent Loop，它还包括 Spine、Context、Memory、Providers 和状态所有权，Agent Loop 只是一次 Turn 的核心执行器。把 CLI、TUI、Gateway 等 Host，以及交付、评测和操作面放进完整产品范围，Pico 才以 general Agent Harness 定位。实际行业里两词经常混用，所以面试时我不争术语，而是问：**谁拥有控制循环，谁拥有跨 Turn 的系统责任**。

判断的重点不是名字，而是责任是否清楚。如果所谓 Runtime 只转发一次模型请求，它承担的其实更像 Provider SDK；如果 Harness 自己又偷偷维护第二套 Agent Loop，就会出现状态和终态不一致。

#### Harness、Framework、SDK 和 Agent 应用之间是什么关系？

我按控制权来区分。SDK 提供可调用的能力，比如模型接口、工具声明、MCP 客户端或 Tracing API；Framework 提供开发结构和扩展点，比如图编排、Agent Runner 或持久化抽象；Harness 是 Agent 真正运行时所在的环境，把模型、工具、状态、安全和生命周期组织起来；Agent 应用则定义具体用户目标、领域工具和交互体验。

同一个产品可能同时包含几层。OpenAI Agents SDK 既是 SDK，也内置 Loop、Sessions、HITL 和 Tracing，可以承担一部分 Harness 职责。LangGraph 是偏低层、状态化、可持久执行的编排 Framework，也可以成为 Harness 的执行骨架。Pico 则把自己定位成一个具体可运行的 Harness，而不是供所有团队嵌入的通用库生态。

面试时我会避免靠标签做优劣判断，而是问：控制循环在哪里，状态由谁持久化，副作用由谁批准，崩溃后从哪里恢复，结果由谁验证。回答完这些，系统层级自然就清楚了。

#### 什么叫 agent\-native？是不是加了 Tool Calling 就算？

**不是。** Tool Calling 只是 Agent 获得动作能力的一种协议。agent\-native 更像一种系统设计原则：从一开始就假设执行会多步、非确定、可能失败、可能跨越上下文窗口，也可能产生外部副作用，系统围绕这些事实设计状态和控制面。

我会用五个判断点。第一，**任务有显式 Run 或 Turn**，而不是只存一条聊天消息。第二，**模型动作通过受控工具执行**，并带有 schema、身份、权限和结果。第三，**状态能跨迭代或进程恢复**。第四，**区分模型输出、任务终态、验证结果和交付结果**。第五，**有面向轨迹而不只是最终文本的 Trace 和 Eval**。

Pico 的多入口统一 Turn、Lane 调度、Context 预算、Session/Checkpoint 分层、Tool Contract、Turn 终态与 Delivery Outcome 分离，就是 agent\-native 的体现。但这不代表固定 Workflow 落后：对强合规、步骤稳定的任务，确定性 Workflow 往往比开放式 Agent 更合适。

### Agent Loop 一般怎么设计？Pico 用的是 ReAct 吗？

从行为上看，Pico 接近 [ReAct](https://arxiv.org/abs/2210.03629) 式的观察与行动闭环：模型读取当前上下文，输出文本、Reasoning 或 Tool Call；Runtime 执行 Tool、追加结果、再发起下一次模型调用。它没有把固定 Planner 作为所有任务的强制前置阶段。

但我不会把实现简单贴成"ReAct"就结束。工程上的 Loop 至少还要处理工具参数校验、并行与串行边界、上下文溢出、空响应、重复失败、模型超时、最大迭代、取消和终态。Pico 达到最大迭代后会关闭工具再做一次 Synthesis，让模型汇总现有进展：这是受预算限制的收束，不等于正常完成。

是否需要显式 Planner 要看任务结构。短、可逆、反馈快的任务边做边计划更灵活；高成本、强依赖、不可逆的任务先生成可审查计划或图更安全。一个成熟 Harness 应允许任务类型决定规划粒度，**而不是迷信一种 Prompt 模式**。

#### 为什么不能直接写一个 `while True`，模型不再调用工具就退出？

这个写法适合最小 Demo，但不足以表达真实运行语义。第一，"不再调用工具"可能是正常回答，也可能是空响应、Provider 截断、模型误判完成或被上下文污染。第二，无界循环会产生无限 Token、重复副作用和无法取消的问题。第三，工具调用之间还可能要处理用户插入消息、审批、超时、失败分类和状态落盘。

一个工程化 Loop 至少要有迭代预算、时间预算、工具预算、取消信号、重复失败检测、上下文恢复策略和显式终态。Pico 对空响应使用有界的 prefill、nudge 或 retry；相同工具的确定性重复失败触发有界的 loop\-breaking nudge；超过最大迭代进入无工具 Synthesis；Provider terminal error、取消、一般异常和带工具失败的完成分别记录。

所以，`while` 只是语法结构，**Runtime 真正要设计的是有限状态机和失败语义**。

#### Agent 怎么判断任务完成？模型说完成了可以吗？

模型的完成声明只能算一个信号，不能直接等于任务成功。完成判断最好分三层。

**第一层是 Loop 终止**：模型没有继续请求工具，或触发明确的结束动作。**第二层是 Runtime 终态**：这次 Turn 是正常完成、带工具失败完成、Provider 失败、错误还是取消。**第三层是任务验证**：目标文件是否存在、测试是否通过、数据库状态是否满足约束、外部系统是否返回可核验回执。需要送达用户的任务，还要再看 Delivery Outcome。

Pico 当前会区分这些层次，但并非每个普通 Turn 都自动绑定领域 Verifier；PicoBench 中的任务才明确使用由外部控制面拥有的 deterministic Verifier。实际业务里，我会把高风险任务设计成"完成条件可机器检查"，并要求模型提交证据，而不是自评。**没有独立验证时，最多只能说"Agent 结束了执行"，不能说"业务目标已经完成"。**

#### 怎样防止 Agent 跑偏、遗漏要求或者产生"幻觉式完成"？

我不会只靠在 Prompt 里写"请不要跑偏"，更可靠的办法是让目标、进度和验证形成闭环。

开始时把用户要求转成可检查的约束和验收项；执行中保留 Working State，记录目标、已做决定和未解决问题，避免历史压缩后目标丢失；每次动作前后限制工具范围和预算，高风险动作请求用户确认；结束时由外部 Verifier 检查产物，而不是让模型复述自己做了什么；长任务还需要增量里程碑和干净 Checkpoint，防止一次大改把系统带到不可恢复状态。

Pico 的 Curator Working State、Session、Checkpoint、Tool failure、Trace 和 PicoBench Verifier 分别覆盖其中一部分。我的原则是：**Prompt 负责引导，Runtime 负责约束，Verifier 负责裁决，三者不能互相替代。**

#### 为什么同一个模型放在不同 Harness 里，效果会差很多？

因为模型看到什么、能做什么，都由 Harness 决定。即使模型权重相同，System Prompt、可见工具、工具描述、上下文选择、文件导航方式、错误反馈、并行策略、重试、预算、Sandbox、状态恢复和最终验证不同，模型实际面对的决策问题就不同。

工具描述含糊时，模型可能选错工具；上下文塞入太多无关历史，注意力被稀释；Shell 返回几万行日志，后续推理被噪声淹没；工具失败只抛异常而不给结构化原因，模型无法修正；没有独立 Verifier，模型一句"测试通过"就可能被误当作完成。这些都不是换模型参数能自动解决的。

所以我把模型看作"脑"，Harness 看作**"感知、手、记事本、工作台和纪律"**。Pico 的价值也要靠成对实验来判断：固定模型、任务和 Verifier，一次只改一个 Harness treatment axis，才能说明差异来自哪里，而不是把一次成功归因给整个系统。

### Host 和 Runtime 的边界应该怎么划分？

我的划分是：**Host 拥有"怎样和用户或平台交互"，Runtime 拥有"任务怎样执行"。** 前者包括读取命令行输入、TUI 渲染、平台鉴权、消息格式、流式展示、启动关闭和渠道发送；后者包括 Turn、调度、Context、Provider/Tool Loop、Session、Memory、Trace 和终态。

Pico 中 CLI、TUI 和 Gateway 都通过共享 Runtime Assembly 组合 Agent，但各自仍决定是否流式、Scheduler 池大小、Outlet、交互策略和生命周期。TUI 用 TUI\-RPC 与 Python Runtime 通信，而不是在 Node 前端再写一套 Agent Loop；Channel adapter 也只做平台 intake/outbound，不直接拥有 Agent 语义。

边界划清的好处是入口可以演进而不改变任务语义。反过来，如果 Host 为了"方便"直接写 Session、绕过 Scheduler 或解析最终文本判断成功，就会形成第二套控制面，最终出现难以复现的入口差异。

#### 上下文到底属于 Runtime 还是 Harness？

从执行路径看，上下文组装发生在 Runtime 的每次模型调用前；从系统责任看，它通常属于 Harness。原因是上下文不只是 Prompt 字符串，还依赖 Session、Memory、Skills、工具 schema、预算、当前用户请求和安全策略，这些都跨越单次模型调用。

Pico 把 `ContextAssembler` 放在共享 Runtime Assembly 中，所有 Host 进入同一套 Context Engine：Agent Loop 请求上下文，Context Engine 决定哪些 segment 和历史进入窗口，Provider 只接收最终消息与工具定义。这样 Host 不会各自拼 Prompt，Provider 也不拥有业务状态。

如果某个产品把 Context Builder 嵌在 Runner 里，也不算错误。关键是不能让 CLI、Channel、Subagent 分别维护互相漂移的上下文规则，**更不能让模型自己拥有不可审计的长期状态**。

## 三、Turn、Spine、调度与交付

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 主路径现在使用原稿对应的 Scheduler、Lane、OriginPools 和 Origin.SUBAGENT，APPEND／INJECT／INTERRUPT 语义保留。旧 WorkPools 位于 compat。讲当前主路径可沿用本章调度逻辑。[源码](repoagent/harness/spine/scheduler.py)
<!-- REPOAGENT-NOTE-END -->

### 为什么 Pico 要用 Turn、Spine、Lane 和独立 Delivery，而不是让各个入口直接调用 Agent Loop？

因为多入口 Agent 真正难的不是"怎样调用模型"，而是**并发请求和生命周期由谁解释**。消息只是输入，Turn 才是一项有开始、执行、取消、终止和交付关系的任务。如果 CLI 直接调用 Agent、TUI 另写流式逻辑、Gateway 再单独排队，三个入口都能跑 Demo，却很容易在 Session 顺序、取消、重试和终态上形成三套真相。

最朴素的方案，是每收到一条消息就开一个协程。不同会话之间这样做没有问题，同一会话却可能同时读取旧 Session、修改同一 Workspace，最后出现历史乱序、覆盖写和重复副作用。反过来，如果所有请求全局串行，一个长任务又会阻塞所有用户。这里真正要解决的矛盾是：**同一状态所有权域内要有顺序，不同域之间要允许隔离并发**。

Pico 用 Turn、Lane 和 OriginPools 把这件事显式化。所有 Host 先构造 `TurnRequest`；同一 conversation 映射到一个 Lane，一次最多运行一个 Turn，Lane 同时负责排队、注入和取消；不同 Lane 之间可以并行。用户请求与 Cron、Subagent 使用独立任务池，系统任务不能耗尽全部用户容量。它牺牲了同一会话内的并行，优先换取当前 Session 和 Workspace 模型下可解释的顺序。

忙碌时也不只是"排队或丢弃"。`APPEND` 表示等当前 Turn 结束后执行；`INJECT` 允许用户补充约束，但只在工具循环的安全间隙交给当前 Turn，没被消费再排队；`INTERRUPT` 会取消当前用户 Turn，让新请求优先。重点不是三个名字，而是**注入点和取消语义**：系统不能在不可逆工具执行到一半时随意改变模型认知，也不能让 Cron 或 Subagent 凭来源身份偷偷抢占用户任务。

统一 Runtime 不代表所有 Host 完全相同。CLI 可以非流式输出，并等待渲染队列清空；TUI 通过经过认证的本机 JSON\-RPC 接收文本、推理、Tool 和终态事件；Gateway 负责飞书、QQ、企业微信的消息接入和发送。差异放在 Turn 提交之前和事件呈现之后，中间仍共用 Runtime Assembly、Spine 和 Agent Loop。TUI 不是 Channel，Channel adapter 也不能绕过 Spine 自己运行 Agent。

Turn 终态和 Delivery 必须分开。Runner 只发送类型化的执行事件，Spine 拥有 `TurnStarted`、`TurnEnded` 和 `TurnFailed`；`TurnOutcome` 保存 Usage、工具失败、Memory 命中和 Context 路径等执行事实；之后 DeliveryHub 再按 Outlet 排队发送。模型已经输出最终文本，Session 或 Memory 的收尾仍可能失败；Turn 已经完成，平台发送也可能耗尽重试。发送失败不能倒过来改写已经结束的 Agent Turn，平台接受发送也不能证明用户已经看到。

Cron 同样要守住边界：它是用户预先安排的定时执行，不是 Runtime 自己发现目标的主动 Agent。消息平台还可能重复投递事件，因此接入层需要稳定身份和去重；但跨平台的 exactly\-once 仍依赖幂等键、回执和外部协议，不能只靠本地队列承诺。冻结证据中，飞书是 `live-gated`，QQ 和企业微信只有确定性契约，因此标为 `beta`：共同代码路径和真实平台证据不是一回事。

确定性 Runtime benchmark 曾记录 2,000 个 Scheduler 请求全部被接受，零丢失、零意外重复执行、零未解决 handle。这个数字支持的是冻结工作负载下的本地调度契约，我不会把它说成线上吞吐 SLO。

所以这套设计可以收束成一条路径：`Host -> TurnRequest -> Lane/OriginPools -> Turn Runner -> Agent Loop -> Turn terminal -> DeliveryHub`。它同时解释了同一会话为什么要顺序执行，不同入口为什么能共享语义，以及为什么 Turn 完成不等于消息已经送达。

### 你能沿着源码讲一遍，一条用户请求在 Pico 里怎样执行吗？

我会抓住一条稳定主链，而不是从入口文件逐行背。请求首先由 CLI、TUI 或 Channel Host 转成 `TurnRequest`，其中包含来源、会话身份、文本或媒体、Origin 和 BusyPolicy。请求进入 Spine 的 Scheduler，同一会话映射到一个 Lane：Lane 决定排队、注入或中断，并在可执行时从对应的 OriginPool 获取容量。

随后 Turn Runner 调用 Agent Loop。Loop 先准备 Sandbox 和 MCP，加载或创建 Session，请 Context Engine 组装当前窗口，再调用 Provider。模型如果返回 Tool Call，Runtime 完成 schema 校验、风险与执行策略判断，执行工具并把 `ToolResult` 追加回消息，进入下一次 Iteration。模型不再调用工具、发生终止错误、收到取消，或者达到迭代上限后，Loop 保存 Session，执行 Context 的 after\-turn 处理，并按配置写入长期 Memory。Runner 把 typed `TurnOutcome` 交回 Spine。

最后，Spine 产生 Turn 终态事件，并把 `Text`、`MediaOut` 等 Deliverable 送给 DeliveryHub 或 TUI 事件桥。平台发送有独立结果，不能反向改写已经完成的 Agent Turn。面试时我通常用一句话收束：`Host -> TurnRequest -> Lane/OriginPools -> Turn Runner -> Agent Loop -> Deliverable -> DeliveryHub`。

#### 为什么要设计 Turn？直接把每条消息传给 Agent 不行吗？

**Turn 是一次有生命周期的任务执行，而消息只是数据。** 没有 Turn，系统很难回答：某条用户输入究竟触发了多少模型调用、用了哪些工具、何时开始和结束、被谁取消、产生了什么终态，以及结果是否成功交付。

Pico 的一个 Turn 可以包含多次 Iteration，也可以包含多条中间 Tool 消息，但它只产生一个受 Spine 管理的生命周期。这样 Scheduler 可以对 Turn 排队，Trace 可以关联 Provider attempt 和 Tool Call，Session 可以知道哪些消息属于同一次执行，Delivery 也能把产物绑定到来源。

这个抽象还避免把不同入口的"消息"误认为同一种东西。用户消息、Cron 触发、Subagent 回流在交互上不同，但进入 Runtime 后都能成为 Turn；反过来，流式 Token、工具进度和平台回执只是 Turn 的事件或交付结果，不应该再创建一套执行语义。

#### Turn、Iteration、Session 和 Run 分别是什么？

我会先把四个尺度拆开。**Iteration** 是 Agent Loop 内的一次模型决策及其后续工具处理，一个 Turn 通常包含多次 Iteration。**Turn** 是由一次输入触发、由 Runtime 管理到终态的执行单元。**Session** 是一段按顺序持久化的对话记录，可以包含多个 Turn。**Run** 是行业里比较泛的词，在评测或 Evolver 中可能指一次 Trial、一次完整工作流或一次 Evolution Run，所以使用时必须说明范围。

Pico 的核心执行术语是 Turn 和 Iteration。Session 不保存 Python 调用栈，也不等于正在运行的 Turn，它保存的是对话事实。PicoBench 里的 Trial 则是"某个任务在某个实验条件下的一次执行"，不能和交互 Session 混用。

面试中区分这些概念很重要，因为很多恢复和指标错误都来自尺度混淆：Provider call 成功不等于 Iteration 成功，Iteration 成功不等于 Turn 完成，Turn 完成不等于 Session、Memory 和 Delivery 都成功。

### Spine 是什么？为什么不让各个入口直接调用 Agent Loop？

Spine 是 Pico 的**统一控制面入口**。它定义 `TurnRequest`、Scheduler、Lane、OriginPools、Turn lifecycle 和交付队列，但通过依赖倒置不直接依赖 Agent 的具体实现；Agent 侧用 `AgentTurnRunner` 实现 Turn Runner 契约。

如果每个入口直接调用 Agent Loop，CLI 可能按自己的方式处理并发，TUI 用另一套取消，Gateway 又单独保存 Session。功能看起来都能跑，但同一请求换入口后语义会漂移，Trace 也无法统一。Spine 把入口差异收敛到提交前和交付后，中间执行路径保持一致。

它的代价是多了一层事件和生命周期设计，但这层复杂度是有目的的：统一排队、取消、终态和证据，而不是为了抽象而抽象。一个新的 Host 只要能构造 `TurnRequest`、选择 Runner 和 Outlet，就不需要重写 Agent Loop。

#### Lane 的作用是什么？为什么是 per\-conversation？

Lane 是**同一会话的顺序与取消域**。Agent 在一个会话里会读取前一 Turn 保存的消息和工作状态，如果两个 Turn 同时改同一 Session 或 Workspace，很容易出现历史乱序、覆盖写、重复副作用和无法解释的最终状态。因此 Pico 让一个会话同一时刻最多执行一个 Turn，并把待执行请求、可注入消息和取消都放在 Lane 内管理。

per\-conversation 的好处是隔离而不是全局串行：某个会话里有长任务，不应阻塞其他会话；Lane 空闲一段时间后可以回收。

如果业务允许同一会话中的任务并行，不能简单删掉 Lane，而要进一步定义分支状态、工作区隔离和合并协议。顺序性不是性能损失的偶然结果，而是当前状态模型下的正确性选择。

#### APPEND、INJECT 和 INTERRUPT 三种 BusyPolicy 有什么区别？

`APPEND` 最保守：当前 Turn 结束后再执行新请求，适合独立补充任务。`INJECT` 会在 Agent Loop 的安全间隙把新消息提供给正在运行的 Turn，例如用户补充"不要修改数据库"；如果消息没在当前 Turn 被消费，再作为新 Turn 排队。`INTERRUPT` 则取消当前用户 Turn，把新请求放到前面，适合明确的停止或方向切换。

关键不是三种枚举，而是**中断点**。Pico 不会在 Tool 正执行到一半时任意把消息塞进去，因为那会让工具副作用和模型认知失去一致性，注入只发生在工具循环的安全边界。系统来源任务也不能借 Origin 身份偷偷抢占用户工作，Scheduler 会把策略归一到支持的安全路径。

实际产品还需要 UI 明确反馈"已排队、已注入、已中断"。否则用户以为修改立即生效，Runtime 却还在执行旧计划，会形成危险的时序误解。

![12\-busy\-policy\.png](<原始文件包/Pico 面试逐字话术稿/图片和附件/12-busy-policy.png>)

#### OriginPools 为什么要把用户任务和 Cron、Subagent 分开？

这是**容量隔离**。Cron 和 Subagent 都可能突发地产生很多后台任务，如果它们与用户请求共用一个全局 semaphore，就可能把所有执行槽占满，导致交互请求排队很久。Pico 使用独立的 user pool 和 system pool，而且当前不相互借用，保证后台工作不会吞掉全部用户容量。

不借用的代价是某个池空闲时不能自动提高另一池吞吐，但它换来更可预测的服务质量。是否允许借用，需要进一步定义优先级、配额和抢占，否则"动态利用率"很容易变成饥饿问题。

这个设计也说明调度不是简单 `asyncio.gather`。Agent Runtime 要考虑不同来源的公平性、会话顺序、取消传播和资源预算；并发越高，越不能只看平均吞吐，还要看队列等待、尾延迟和用户任务是否被系统任务挤压。

#### Pico 的 Cron 算不算"主动 Agent"？

**不算。** Cron 是用户预先创建的定时任务：系统按 IANA 时区计算下一次触发，claim 到期任务，再生成 `TurnRequest(origin=CRON)` 进入 Gateway Scheduler，执行后记录结果并按配置 Delivery。它的目标和时间都来自用户。

主动 Agent 通常意味着系统自己发现需要做的事、生成目标并决定何时行动，这还涉及权限、预算、噪声和责任。Pico 没有把 Sentinel 或自主任务发现作为保留能力，因此不能用 Cron 证明"Agent 会主动思考"。

这一区分很重要：**自动触发不等于自主决策**，把计划任务包装成 Proactive Agent 会夸大自治。

### CLI、TUI 和 Gateway 真的走同一套 Runtime 吗？它们有哪些差异？

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 主路径已经改为 **Node / React / Ink TUI + Python 独立进程**。Python 启动 Node，通过回环 TCP、随机 token 和 JSON-RPC 交互。旧 Textual 同进程服务调用仅保留在显式 compat 入口。[入口](repoagent/harness/cli/tui_commands.py) · [客户端](ui-tui/src/rpc/client.ts)
<!-- REPOAGENT-NOTE-END -->

它们共享 Runtime Assembly 和核心 Turn 路径，但 Host 行为并不完全相同。CLI 的一次性模式或 REPL 会构造 TurnRequest，经 Scheduler 和 `AgentTurnRunner` 执行；REPL 还会等待 Turn 完成和 DeliveryHub 渲染队列都清空。TUI 由 Node/React/Ink 负责界面，Python 进程持有 Runtime，双方只通过经过认证的本机 TUI\-RPC 通信，TUI 使用流式 Runner 和订阅事件。Gateway 则负责 Channel 生命周期、Intake、Cron、Outlet 和平台发送，通常不把 Token 流逐条编辑到聊天平台。

所以，"同一 Runtime"指的是**Session、Context、Agent Loop、Tools 和 Turn 语义一致**，而不是所有 Host 配置完全一样。流式能力、Scheduler 池大小、Outlet、启动关闭和交互方式仍由 Host 拥有。

源码核验时要特别防止一个误区：看到每个 Host 都有组装代码，就说它们是三套 Runtime。应当看它们是否最终使用同一套 `assemble_runtime` 组合和 Spine 契约，以及是否存在绕过路径。

#### 为什么 TUI 要通过 RPC，而不是直接让前端调用 Python 对象？

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 当前主路径可按这段跨语言架构讲解，源码运行需要 Node.js 22 或以上。界面负责交互，Python 负责 Agent 运行时，通信经过带认证的本地 RPC；旧 Textual 是兼容实现。
<!-- REPOAGENT-NOTE-END -->

因为 TUI 的前端是 Node/React/Ink，Runtime 是 Python，它们属于**不同进程和语言边界**。TUI\-RPC 让这个边界显式化：请求响应负责 `turn.send`、Session 操作等方法，Notification 负责流式事件和确认请求。Python 持有权威状态，前端只持有渲染和交互状态。

这样做有三个好处。第一，TUI 不需要导入 Runtime 内部模块，减少耦合。第二，协议可以测试：提交 ID、Turn 终态、Tool event 和确认往返都有明确结构。第三，前端崩溃或重启时，不会因为共享内存对象而让 Runtime 状态变得不可解释。

代价是要处理握手、认证、协议版本、进程启动和事件订阅，TUI 还依赖本机 Node\.js 22 和内置 bundle，不能理解成纯 Python 零依赖。

#### TUI 是 Channel 吗？

不是。Channel 指飞书、QQ、企业微信等外部聊天平台适配器，由 Gateway 的 ChannelManager 管理；TUI 是本地终端前端，通过 TUI\-RPC 连接 Python Runtime。消息中可能出现 `channel="tui"` 这样的 routing tag，但那只是收件路由，不等于 TUI 实现了 Channel contract。

区分这个术语能避免错误理解生命周期：TUI 有本地 RPC、订阅和确认往返；Channel 有平台 SDK、Intake、Outbound 和平台重试。它们共享 Turn Runtime，不共享前端适配层。

#### 飞书、QQ 和企业微信三个 Channel 当前成熟度一样吗？

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 参考渠道适配器已随主运行时迁入，但未配置或验收真实飞书／企业微信／QQ 收发，不能把“源码存在”说成“平台已上线”。原目录渠道和原 QQ 适配保留在 compat。
<!-- REPOAGENT-NOTE-END -->

不一样。冻结版本把 Channel 成熟度定义为**证据级别**，而不是代码质量打分。飞书是 `live-gated`：除了确定性 Channel 契约和安全隔离检查，还存在绑定具体提交与场景的真实机器人收发证据。QQ 和企业微信当前是 `beta`，只有确定性契约与安全检查，没有可用于宣称真实平台收发成功的 live gate。

所以我会说"三个平台共享同一 Channel contract 与 Gateway 路径，但当前最强证据等级不同"，而不会说"三个平台都已生产验证"；也不会把 `beta` 理解成代码一定粗糙。任何 live 结论都只能绑定通过 Gate 的适配器、提交和测试场景。

#### 消息平台会重复投递事件，Pico 怎样避免重复执行？

Channel Intake 需要根据平台事件 ID、会话和时间窗口做去重，再归一成 TurnRequest；发送侧则需要稳定 Delivery identity 和平台错误分类。确定性 Channel contract 可以验证重复抑制和 retryable/terminal 错误，但真正跨重启的 exactly\-once 还依赖持久去重状态和平台语义。

Pico 当前 Cron 和部分 Channel 路径有各自的 claim 或 dedup 机制，但 Gateway 出站队列仍在内存中，不能把所有消息都概括成端到端 exactly\-once；遇到网络不确定时，必须依赖幂等 ID 或查询回执。

### Pico 怎样定义一次 Turn 的终态？

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 主路径现在保留 **completed、completed_with_tool_failure、provider_failed、error、cancelled 五种 tracing outcome 分类**。这里准确说法是追踪中的 Turn 结果分类，不是 Scheduler 或 TurnRequest 有五个终态枚举。旧三个状态仍属于 compat。[分类定义](repoagent/harness/tracing/semconv.py)
<!-- REPOAGENT-NOTE-END -->

当前明确区分**五种终态**：`completed`、`completed_with_tool_failure`、`provider_failed`、`error` 和 `cancelled`，这比一个布尔 `success` 更有信息量。

`completed` 表示 Loop 正常结束且没有记录到工具失败。`completed_with_tool_failure` 表示 Agent 仍给出了收束结果，但执行过程中至少有 Tool 失败，不能把它悄悄抹成正常成功。`provider_failed` 表示模型调用在 Provider 边界终止失败。`error` 表示其他未归入 Provider 的执行错误。`cancelled` 表示取消。达到最大迭代后做的无工具 Synthesis 是受预算限制的中断式收束，不应包装成"任务已经验证完成"。

这些状态仍然只是 Turn 终态：任务 Verifier、Session/Memory 持久化细节和 Delivery Outcome 各有自己的语义。只有把层次分开，系统才能做正确的重试、告警和用户提示。

![13\-turn\-terminal\.png](<原始文件包/Pico 面试逐字话术稿/图片和附件/13-turn-terminal.png>)

#### 模型已经输出最终文本，为什么 Turn 还可能失败？

因为**模型文本只是一个事件，不拥有 Runtime 的最终裁决权**。比如模型先生成了一段看起来完整的回答，但随后 Session 持久化失败；或者 Provider 在流式过程中断，只留下部分文本；又或者后处理需要写入必选 Memory 后端而失败。此时把已有文本直接当成成功结果，会让用户收到一个无法恢复、无法追踪的半成品。

反过来，工具有一次非致命失败，模型也可能调整方案并交付有用结果，所以不能看到任何 Tool error 就把整个 Turn 归为 `error`，Pico 用 `completed_with_tool_failure` 保留这种差异。

工程上最好让生命周期所有者产生终态，而不是让 Runner 随意发 `TurnEnded`。Pico 的 Spine worker 拥有 TurnStarted、TurnEnded 和 TurnFailed，Runner 只能发文本、媒体、流式增量、Reasoning、Notice 和 ToolEvent，这可以防止多个组件竞争终态所有权。

#### Turn 成功和消息送达有什么区别？

Turn 成功表示 Agent 执行链在 Runtime 内达到某个完成终态；送达表示某个 Outlet 调用外部平台接口的结果。**两者不在同一个事务里。**

Pico 的 DeliveryHub 为每个 Outlet 维护独立队列和 worker，避免一个慢渠道阻塞其他渠道。平台发送可能成功、被丢弃，或者根本没有可用 Outlet；`delivered` 只表示渠道调用成功，不代表用户真的看见、理解或采纳了消息。反过来，Agent Turn 已完成后，渠道重试耗尽也不会把 Turn 倒改成失败。

这条边界对消息型 Agent 很重要。如果业务要求"必须送达"，就需要持久化 Outbox、幂等消息 ID、回执、补偿和告警，而不是依赖内存队列的运气。

#### 取消和关闭怎样保证不会留下悬挂任务？

正确做法是让**取消有明确所有者，并沿任务树传播**。Pico 中 Lane 是会话取消域：Turn 开始后被取消会产生带 `cancelled=True` 的失败生命周期，尚未开始的请求则不应留下一个没有配对的 `TurnStarted`。等待 `TurnHandle.result()` 的调用方协程被取消，也不会自动取消真实 Turn，因为 Future 被 shield，必须显式走 handle 或会话取消路径。

关闭时，Scheduler 先拒绝新提交，解析队列和 mailbox 中的等待任务，再给运行中任务一个有界清理窗口，之后取消剩余任务并等待资源关闭，最后关闭 Delivery worker。Subagent 也需要被父任务取消，且已取消任务的结果不能再次注入会话。

不过，协程取消不等于外部副作用回滚：Shell 子进程、远端 API 或已经发送的消息可能已经发生，仍需要工具级取消、幂等和补偿。**Runtime 可以保证自己的生命周期尽量闭合，不能承诺撤销世界。**

## 四、Context Engineering：治理有限注意力与信息生命周期

### 如果让你整体讲一下 Pico 的 Context 设计，你会怎么讲？

我会先讲一个反直觉的判断：**Context Engineering 不是尽量把更多信息塞进窗口，而是管理有限注意力和信息生命周期**。它要决定模型此刻应该看到什么、用什么结构看到，以及哪些内容必须留在窗口之外。Context Window 是容量上限，真正稀缺的是有效注意力和正确的信息关系。

最简单的方案，是每次把完整 Session、所有 Memory、全部 Skill、整个仓库和所有 Tool schema 都发给模型。短任务里这样做省事，任务变长以后却会在几个地方失效：历史会线性增长；日志和 Tool Result 会挤压用户目标；旧事实会和新事实冲突；工具数量增加以后，schema 本身也会占满窗口，并提高选错动作的概率。窗口更大只会让这些问题晚一点出现，不会让它们消失。

我会用四组矛盾来理解 Context。**第一，完整性和选择性**：漏掉关键约束会失败，保留所有内容也可能因噪声失败。**第二，可逆和有损**：Summary 节省空间，但以后才重要的细节可能已经丢失；Archive 占用外部存储，却允许回查原文。**第三，模型判断和确定性验收**：LLM 擅长提出相关性计划，却不能自己证明预算、顺序和协议闭包正确。**第四，Token 效率和任务效果**：压缩率、schema token、缓存命中都只是代理指标，最终要服从任务 Verifier。

状态角色也必须分清。Session 是完整 Transcript 的事实记录；Working State 是从历史派生出的目标、决定和开放问题；Archive 保存移出窗口的原文，追求可逆；Memory 面向跨任务复用，需要来源、时效和作用域；Skill 保存做某类任务的方法。**Context 只是这些状态在某次模型调用前的临时投影。** 如果用一个不断覆盖的 Summary 同时替代它们，摘要错误就会变成无法追溯的新真相。

Context 还有一个很容易被忽略的不变量：**协议闭包**。Tool Call 和对应 Tool Result 不能被相关性算法拆开；安全约束、验收条件和当前失败原因需要保护；消息顺序也不能让模型只看到结果，却看不到调用来源。LLM 可以提出 `ContextPlan`，确定性代码必须检查预算、引用、必保消息、Tool 协议对和结构是否合法：模型负责语义选择，Runtime 负责结构正确。

Pico 把这些判断落实成统一的 `ContextAssembler`。Phase A 并行构建身份、Workspace 引导信息、Memory、始终激活的 Skills 和按需 Skills，先形成固定的 system prefix；Phase B 再把固定开销、当前用户消息、Tool schema 和完整 Session history 一起交给 Curator 做预算。压力低时走 Fast Path；压力高时，Curator 读取 Manifest、Archive 和 Working State，提出结构化计划；超时、Provider 失败或计划非法时，进入确定性 Fail\-Safe。路径和 fallback reason 会进入 Turn metadata，降级不会被伪装成成功的 LLM 策划。

大型仓库也不能只靠"切块后向量化"。更稳的做法是先建立仓库地图，再用文件名、符号、grep 或 BM25 缩小范围，按行和引用读取，最后只把当前决策需要的片段送进窗口。工具输出也要先结构化、截断并保留引用，避免一条命令的几万行日志污染后续推理。Prompt Cache 复用的是稳定前缀计算，通常不改变模型看到的信息；Context 压缩会改变逻辑输入，风险更高，两者不能混为一谈。

Pico 当前最重要的证据其实是一条负面结果。Agent 应用 campaign 有 216/216 个 Trial 到达终态，260/260 个 Retrieval Case 可测，但 120 个 Context Pair 只有 119 个有效，主 campaign 因 Usage 缺口 `measurement_invalid`。单独可测的 Tool disclosure treatment 把六个任务的可见 Schema Token 估算降低 93\.4513%，task pass 却从 23/24 退到 20/24。所以正确结论不是"Context 优化成功"，而是**代理成本下降了，但任务效果退化，而且主测量并不完整**。这些数字绑定更早的证据提交，不是冻结 commit 的重跑。

所以 Context 的目标不是最小 Token，也不是最大信息量，而是让模型在当前决策点看到**最少但足够、结构正确、协议闭合、来源可追溯**的信息。压缩、检索、缓存和 Curator 都只是手段，最后仍要由任务通过率和有效测量裁决。

### Context Engineering 和 Prompt Engineering 的边界在哪里？

Prompt Engineering 主要关心指令怎样写，Context Engineering 关心**一次推理时模型到底能看到什么**，这与 Anthropic 公开的 [Context Engineering 实践](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) 一致。对 Agent 来说，上下文不仅包含 System Prompt，还包括会话历史、当前用户请求、工具定义与结果、项目文件、Memory、Skill、工作状态和安全约束。随着 Loop 推进，这些内容持续变化，因此 Context Engineering 是一个每次调用前都要重新做的选择问题。

我会把目标概括为：在有限的注意力和 Token 预算内，提供**能够最大化当前动作正确率的最小高信号集合**。这里不是越短越好，也不是越多越好：少了会丢目标和证据，多了会带来噪声、位置效应、成本和缓存失效。

Pico 的做法是由统一的 `ContextAssembler` 负责六个 segment 和历史选择，所有 Host 共用，而不是让每个入口自己拼 Prompt。这个实现只是设计空间里的一种选择，核心原则是：**上下文必须有所有者、预算、来源和降级语义。**

#### Pico 一次模型调用的上下文由哪些部分组成？

当前 Context Engine 按固定顺序组装六个 segment：Pico 身份、Workspace bootstrap files、Memory、始终激活的 Skills、由 SkillForge 选择的 Skills，以及 Curator 提供的历史和 Working State。当前用户消息是结构化的末尾输入，不算第七个 segment；工具 schema 通过 Provider 的工具侧通道传递，但同样占预算。

这样分段的意义是所有权和优先级明确：身份与安全约束保持稳定，项目引导文件提供当前仓库规则，Memory 提供跨任务可复用事实，Skill 提供操作方法，Session 历史提供本次对话的因果链。它们不能用同一种检索或压缩策略。

需要强调，Pico 当前只有一个 Context Engine。历史上的 legacy、default、curator 选择已经收敛，**Curator 只是第六个 segment，不是和 ContextAssembler 并列的第二套引擎。**

#### 为什么 ContextAssembler 要分成 Phase A 和 Phase B？

因为**历史能用多少预算，取决于前缀已经占了多少**。如果同时独立构建所有段，每个段都假设自己拥有完整预算，最后很容易总量超限，或者不得不做不可解释的尾部截断。

Pico 先并行构建彼此独立的 Phase A：身份、bootstrap、Memory、active Skills 和检索到的 Skills，形成固定 system prefix。Phase B 再把这个真实前缀、当前用户消息、Tool schema 预算和完整 Session 历史放在一起，让 Curator 计算可用历史空间并选择内容。这样历史预算是 prefix\-aware 的，而不是写死一个百分比。

代价是 Phase A 的必选组件失败可能直接让组装失败。比如选中的 Memory backend recall 失败，不会悄悄变成空内容。这是 fail\-closed 选择：既然用户配置了一个持久化后端，就不能在它失效时假装"没有记忆也一样"。

#### 上下文预算具体怎么计算？只看字符数吗？

**不应该只看字符数。** 预算至少要覆盖模型上下文上限、预留输出 Token、System prefix、当前用户消息、历史、工具 schema 和可能的 Provider 格式开销。不同模型的 Tokenizer 不同，同一段中英文、代码或 JSON 对应的 Token 数也不同，所以最好用目标模型或兼容 Tokenizer 估算，并保留安全余量。

Pico 的 ContextAssembler 会先确定固定前缀，再给历史分配剩余空间。HistoryTrimmer 还要保证 Tool Call 与 Tool Result 闭合，不能留下一个工具调用却丢掉对应结果；发生上下文溢出时，Agent Loop 只有有限次应急缩减，不会无限重试。

工程上我还会记录"估算输入""Provider 实际 Usage""缓存命中"和"是否完整"四类数据。估算用于提前控制，实际 Usage 用于复盘；两者不一致时，不能把缺失字段当成零。

#### 工具定义为什么也是 Context 问题？

因为**模型要先看到工具名称、描述和参数 schema，才能决定调用哪个工具**。工具越多、schema 越复杂，固定前缀越大，而且相似工具会增加选择歧义。工具输出同样进入后续上下文，若返回整页 HTML 或几万行日志，会快速污染窗口。

因此工具设计和 Context Engineering 是一体的：工具描述要清楚说明适用条件、输入语义、失败方式和返回结构；参数应使用明确名称；输出应默认紧凑、可分页、带引用或句柄，详细内容按需读取。工具规模很大时，应考虑 namespace、动态发现和延迟加载，而不是把所有 schema 一次性塞入。

#### 为什么不能把完整会话历史每次都发给模型？

短会话里可以，Pico 的 Curator 也有 Fast Path：压力较低时直接保留完整历史，不为压缩而压缩。但会话变长后，完整发送会出现四个问题。第一，成本和延迟随历史增长。第二，旧工具输出、重复日志和已解决分支会稀释当前目标。第三，即使模型支持很长窗口，也不代表对每个位置都同样敏感，相关事实可能被噪声淹没。第四，工具 schema、Memory 和项目说明还要占空间，历史不能独占全部预算。

所以正确策略通常是分层：**权威 Session 保留完整事实，当前上下文只选相关、受保护和最近内容，被移出的内容放入可检索 Archive，目标和决策另存 Working State。** 这样"存下所有事实"和"本次让模型看到什么"成为两个不同的问题。

#### Curator 是做什么的？为什么需要一个内部 Agent？

Curator 是一个**只负责选择上下文的内部有界 Agent**，不直接回答用户。它在历史压力较高时读取消息 Manifest，决定哪些消息继续保留、哪些进入 Archive，并提交结构化 `ContextPlan`。它的价值是能根据语义和当前目标做选择，而不只按"最近 N 条"截断。

但让模型管理模型的上下文也有风险：它可能超时、产生无效计划、错误丢弃关键事实，还会增加一次 Provider 调用。因此 Pico 设计了三条路径：Fast Path 在低压力时不调用 Curator；Slow Path 在时间和步骤预算内生成计划；计划无效或 Provider 失败时进入确定性 Fail\-Safe，优先保留 protected、相关和最近消息。最终使用哪条路径、fallback reason 是什么，都进入 Turn 元数据。

所以我不会说"LLM 压缩一定优于规则"。更稳妥的架构是：**模型做高语义选择，Runtime 做结构验证和安全兜底。**

#### Archive、Summary 和 Working State 有什么区别？

Archive 保存被当前窗口移出的原始消息引用和内容，是**无损**的选择辅助；Summary 把多条历史压成较短叙述，属于**有损**转换；Working State 则是对当前任务最需要持续携带的信息做结构化记录，例如目标、决定、开放问题和下一步。

三者解决的问题不同：Archive 让系统以后能找回细节，Summary 节省 Token 但可能丢失信息，Working State 保证任务主线不随着历史裁剪而消失。权威事实仍在 Session，**不能把任何一种辅助状态当成完整 Transcript**。

Pico 当前由 Curator 维护 Manifest、Archive 和 Working State，历史上的 consolidation summary 不是主历史所有者。这个分层还能帮助恢复：Resume 时先有完整 Session，再根据预算重建上下文，而不是把上次模型看到的某个摘要当成不可更改的真相。

![14\-context\-lifecycle\.png](<原始文件包/Pico 面试逐字话术稿/图片和附件/14-context-lifecycle.png>)

#### 处理大型代码仓库或超大文件时，你会怎么做？

我不会先把整个仓库向量化，也不会让模型从头读取所有文件。更稳妥的是**渐进式导航**：先看目录、项目说明、依赖和入口；用 grep、符号搜索、文件元数据或测试失败定位候选范围；只读取相关片段；把关键架构决定和已验证事实记入 Working State；需要时再扩展搜索。

对于超大文件，工具应支持行范围、分页、结构化摘要和稳定引用，模型先读取索引或局部上下文，再决定下一段。代码是高度动态和精确的，**实时文件导航经常比陈旧向量索引更可靠**；设计文档、历史 Issue 等较稳定语料才适合用 BM25、向量或混合检索补充。

Pico 的文件、grep、find 和 Context 分层能够承载这种策略，但"模型一定会高效导航"仍取决于工具描述、Prompt 和评测。不能因为有搜索工具就声称解决了大仓库理解。

#### 压缩上下文最容易丢什么？怎样降低风险？

最容易丢的是**当下看起来不重要、后来却成为约束的信息**：用户的否定要求、一次失败原因、接口兼容条件、未解决的边缘场景、工具返回中的精确标识。通用摘要倾向于保留"发生了什么"，却可能丢掉"为什么不能这样做"。

降低风险要靠结构，而不是只换更强模型。第一，标记 protected 消息，例如安全要求和明确验收标准。第二，Tool Call 和结果成对保留。第三，把目标、决策、开放问题分离为 Working State。第四，Archive 保留原文并可回查。第五，压缩失败要有确定性兜底。第六，用长任务回放评测"恢复后是否还能完成"，而不是只比较 Token 减少多少。

Pico 的 Curator 采用这些思路，但当前证据中的 Tool disclosure treatment 虽然显著减少了可见 Schema Token，任务通过数却从 23/24 退到 20/24。这正说明压缩不是天然优化项：**节省 Token 如果让成功率下降，就不是有效改进。**

![15\-tool\-protocol\-closure\.png](<原始文件包/Pico 面试逐字话术稿/图片和附件/15-tool-protocol-closure.png>)

#### 现在模型上下文窗口已经很大，Context Engineering 还重要吗？

**仍然重要。** 更大窗口缓解了"放不下"，没有消除"放什么"的问题。Agent 运行时间越长，工具日志、文件片段、错误分支和重复内容增长越快；上下文中无关信息会增加成本、延迟和决策噪声。大窗口也不能替代持久化，因为进程重启、跨 Session 复用和审计都需要窗口之外的状态。

不过，大窗口会改变最优策略：以前可能激进摘要，现在可以更长时间走 Fast Path；以前依赖复杂预检索，现在可以让模型先看更完整的项目地图。Harness 不应把某个模型时代的压缩阈值硬编码成永恒真理，而应通过回放和成对实验重新校准。

我更倾向"先用最简单可工作的选择策略，再根据失败轨迹增加复杂度"。Context 系统本身也会制造错误，不能为了展示架构而过度处理。

### Prompt Cache 和上下文压缩是什么关系？

两者优化目标不同。**压缩减少逻辑上发送给模型的 Token，可能改变模型看到的信息；Prompt Cache 让 Provider 复用稳定前缀的计算，通常不改变逻辑输入。** 缓存命中可以降低计费或延迟，但不会自动解决上下文噪声，也不等于 Pico 自己保存了模型的 KV Cache。

要提高缓存命中，Harness 会尽量保持稳定内容和顺序：把稳定 System、工具定义和 bootstrap 放在前面，把高变化历史放在后面，并根据 Provider 支持选择 cache breakpoint。与此同时，不能为了缓存稳定而把过期内容长期固定在前缀。

Pico 的 CallEfficiency 可以观测 Provider Usage，并在特定模式下管理 Anthropic 显式缓存断点；DeepSeek 和 OpenAI 等缓存行为仍由 Provider 自动决定。未知 Usage 或价格必须标记不完整，不能把没有上报的 cache token 当成零成本。

#### Pico 的 Context 优化实验结果怎么样？

这个问题要把"当前主评测""更早候选实验"和"实现存在"分开说。冻结证据索引里，Agent 应用主 campaign 记录了 216/216 个真实 Provider Trial 到达终态、260/260 个确定性 Retrieval Case 可测，120 个 Comparison Pair 中有 119 个有效 Pair。它已经 `ship_complete`，但因为有一个 Context Pair 缺少完整 Usage 证据，整体是 `measurement_invalid`，不能据此发布主 campaign 的正向指标。

在可测的 Tool disclosure 轴上，treatment 把六个任务的可见 Tool Schema Token 估算降低了 93\.4513%，但任务通过数从 baseline 的 23/24 下降到 20/24。这个结果最重要的含义不是"压缩了很多"，而是**压缩代理指标改善时，端到端任务仍可能退化**。只要任务效果没有守住，Schema Token、Tool Call 或延迟的下降都不能自动升级为优化结论。

所以我会把这组证据表述为：Pico 已经把 Context 处理放进可审计的成对实验里，并得到了一次有价值的负结果。主 campaign 因测量不完整而关闭正向 Claim，Tool disclosure 也因任务回归不具备正向资格。所有数字都绑定证据记录声明的较早实验提交，并不是在 `aedcaf2c` 上重新运行的结果。

#### 你会怎样评测一个 Context 系统？

我会把评测拆成四层。**第一是结构正确性**：预算不溢出、顺序稳定、工具调用闭合、受保护消息不丢、Fail\-Safe 可复跑。**第二是检索与选择质量**：关键事实召回、无关信息抑制、freshness 和冲突处理。**第三是端到端任务效果**：相同模型和任务下，Verifier 通过率是否变化。**第四是经济性**：输入、输出、缓存 Token、Provider 调用数、Tool 调用数、延迟和成本。

实验最好成对进行，一次只改变 Context 策略，并保留完整 Trace。不能只看摘要压缩率或检索命中，因为真正目标是让 Agent 更可能完成任务。还要分任务类型：短会话可能不应压缩，长代码任务需要保留决策链，研究任务可能适合 Subagent 隔离。

如果结果像 Pico 候选实验一样出现"工具调用变少但通过率下降"，结论应是 treatment 不合格，而不是挑一个好看的子指标宣传。

#### 如果让你继续改 Pico 的 Context Engine，你会优先做什么？

我会先降低策略风险，而不是增加更多模型调用。第一，建立按任务类型和压力分桶的回放集，把 Fast Path、Slow Path 和 Fail\-Safe 分别评测。第二，强化 protected 语义，把用户验收条件、不可逆动作约束和未解决错误作为结构化状态，而不是依赖摘要。第三，为每次 ContextPlan 记录"为何保留或淘汰"，方便从失败 Trace 定位选择错误。第四，针对大文件和工具输出改进分块、引用和按需展开，减少进入窗口前的噪声。

之后才考虑更复杂的检索、模型路由或学习式策略。任何优化都要通过 one\-axis 成对实验，并设置任务通过率的非劣门槛。**Context 是 Agent 最容易"优化了指标却伤害任务"的地方，发布条件应该比普通 Prompt 修改更严格。**

## 五、Session、Memory、Skill 与恢复

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 本章状态分层原理可沿用。主路径保留 Myna 插件契约，但本项目未完成外部 Myna 部署验收；未部署时显式设置 memory.backend=null。原本地／SQLite 记忆仍属于 compat，不能用来证明 Myna 效果。
<!-- REPOAGENT-NOTE-END -->

### Pico 为什么要把 Session、Context、Memory、Skill 和恢复状态分开？

因为这些状态解决的是不同问题。如果都叫 Memory，系统看起来什么都记住了，真正出故障时却不知道哪一份是权威、哪一份能删除、哪一份可以用来恢复。分层不是多建几个目录，而是**给每种状态指定唯一责任、更新规则和失败承诺**。

Session 回答"这段对话真实发生了什么"，所以它保存按顺序排列的 Transcript，并允许追溯。Context 回答"下一次推理让模型看到什么"，它只是临时派生视图。Working State 保存目标、决定和开放问题，帮助任务跨窗口继续。Archive 保存移出窗口的原文，追求可逆。长期 Memory 回答"哪些事实或经验值得跨任务复用"，因此还要处理来源、作用域、时效、冲突和替换关系。Skill 保存做某类任务的方法。Checkpoint 保存本地 Workspace 的恢复点。Artifact 保存一次运行留下的可核验证据。这些对象可以关联，但不能互相冒充。

最常见的朴素方案有两种。第一种只保留聊天摘要：摘要一旦写错，Tool 的精确参数、用户否定条件和失败原因就可能永远丢失。第二种把所有历史切块放进向量库：临时日志、旧计划、过期事实和方法说明会混在一起，相似度高却不一定正确。向量检索只解决近邻搜索，不解决真值、更新、删除和作用域。

恢复也必须分层解释。Session Resume 通常只是语义续聊，不会恢复 Python 调用栈，也不代表能从一个未完成 Tool Call 的中间指令继续；Checkpoint 可以恢复工作区文件，但不能撤销已经发送的消息、创建的云资源或数据库写入；Artifact 可以帮助新进程判断进度，却不是可执行状态机。外部副作用很难做到 exactly\-once，因为服务端可能已经成功，客户端却没有收到回执。更可靠的做法是记录 intent、幂等键和 receipt，再通过状态查询或补偿决定下一步，而不是崩溃后把整轮直接重跑。

Pico 的 Session 用 `<channel>:<chat_id>` 标识会话，以 JSONL 追加消息和 metadata；文件锁、epoch/content fence 和原子替换分别处理并发写、陈旧写入和部分损坏。完整 Transcript 是事实权威，Curator 不会取代它。Checkpoint 使用独立的 shadow Git 保存 Workspace，不触碰用户自己的 `.git`，但它仍只是本地文件安全网。Session、Curator、Myna、Trace 和 Evolution Artifact 属于不同持久化域，一处成功不能推导其他地方同时成功。

长期 Memory 也不能简单理解成"每轮自动总结"。一段经历进入 Memory 前，至少要判断它是否有跨任务价值，能否追溯可靠来源，是否和现有事实重复或冲突，以及是否包含敏感或一次性信息。召回以后，相似度也只是候选信号：当前仓库文件、外部系统回执等实时事实应优先于过期 Memory；高风险事实还要重新验证，不能因为被召回就自动变成系统真相。

Pico 与 Myna 的边界体现了这种所有权。Pico 只拥有公开的 `MemoryBackend` 接口和调用时机，把 Session 已接受的标准化 Turn slice 交给 `store()`；Myna 作为独立 Plugin，拥有仓库绑定、Source Journal、索引、排序、打包和持久化。目标仓库需要显式执行 `myna init`。选定的 Memory backend 如果缺失、无法启动、召回失败或写入失败，Pico 会失败关闭，而不是静默假装没有 Memory。

证据也要分层。安装 Gate 只能证明插件发现和生命周期接线。确定性 Myna Pack 完成 144/144 个 Trial，在冻结任务上把仓库读取次数降低 50\.0%，95% 区间为 29\.166667% 到 70\.833333%，且 stale 或跨仓库 Memory 事件为 0。真实 24\-Pair Pack 完成 24/24 个有效 Pair，观察到 20\.8333 个百分点的 pass delta，但 95% 区间为 0 到 41\.6667 个百分点，capability、efficiency、general\-Agent 和正向 Claim Gate 全部失败。所以只能说"有契约和局部效果证据，真实任务正向点估计仍不足以形成结论"，不能说"Memory 普遍提升了 20\.8%"。

删除语义最终检验状态所有权是否真实。删除 Session 不会自动删除 Curator Archive、Myna Journal 和索引、Trace、评测 Artifact 或外部副作用；真正的隐私删除必须枚举每个派生域，定义传播、失败重试和可核验回执。否则界面上"会话已删除"，可能只是删除了一个入口文件。

所以这套分层服务于一个很朴素的目标：发生崩溃或冲突时，系统能指出**最后一个可信提交边界**，并说明哪些事实仍然有效、哪些派生状态可以重建、哪些外部副作用只能核验而不能回滚。可恢复的 Agent 不是存得多，而是每种状态都知道谁拥有、何时提交、失败后承诺到哪里。

### Session、Context、Memory、Skill 和 RAG 到底有什么区别？

我会用五个问题区分它们。Session 回答"这次对话实际发生了什么"，是按顺序保存的 Transcript。Context 回答"下一次推理让模型看到什么"，是从多种状态中临时组装的窗口。Memory 回答"哪些跨任务经验或事实值得以后复用"。Skill 回答"某类任务应该怎样做"，更接近可执行的方法说明。RAG 是一类检索增强机制，可以被 Memory、知识问答或 Context 系统使用，但它本身不等于长期记忆。

在 Pico 里，Session 是权威对话记录，Curator 只选择历史而不替代 Session；Myna 作为可选的长期 Memory 后端；本地 Skill 由 SkillForge 检索；ContextAssembler 在每次 Turn 把这些内容按预算放进模型窗口。这样的分层避免一个向量库同时承担历史、知识、方法和状态，最终谁都说不清。

面试中最容易犯的错误，是把"存过"当成"模型记得"。**只有内容被正确召回、通过 freshness 和冲突检查、进入当前 Context，并在任务中产生可验证作用，才能说它影响了后续行为。**

#### Pico 的 Session 是怎么持久化的？

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 主路径直接使用 channel/chat 身份规则与 JSONL SessionManager，追加、重写、锁和 epoch 语义沿用参考实现。旧 cli:<session_id> 映射及旧 JSON 迁移适配保留在 compat；两套状态不自动互相转换。[主路径](repoagent/harness/session/manager.py) · [旧适配](repoagent/session_store.py)
<!-- REPOAGENT-NOTE-END -->

Session 以 `<channel>:<chat_id>` 作为组合身份，默认写在 Workspace State 下的 JSONL 文件，包含 metadata 记录和追加式 message 记录：正常保存追加新消息和最新 metadata，undo、clear 这类缩减或重写操作使用原子替换。

为了防止并发和损坏，Session Manager 使用文件锁、epoch/content fence，并区分尾部半条记录和中间损坏：进程崩溃导致的最后一条不完整 JSONL 可以被忽略并修复，中间出现无法解析内容则视为存储损坏，不会静默跳过。Session 身份还必须和请求 key 及文件路径一致，模糊前缀解析也不能悄悄选错会话。

这种设计适合 local\-first 和单机进程，但它不是分布式数据库。面向多实例、高并发或跨机器时，需要重新设计事务、锁、复制和一致性，而不能只把 JSONL 放进共享目录。

#### 为什么 Session 要保存完整 Transcript，而不是只存摘要？

因为**Transcript 是可追溯的事实来源，摘要只是一个有损视图**。用户曾经否定过什么、工具具体返回了什么、模型在哪一步改了计划，这些细节在当下可能不重要，后续恢复、审计或冲突判断时却可能成为关键证据。

Pico 让 Session 保存消息真相，Curator Archive、Manifest 和 Working State 负责当前窗口选择，长期 Memory 负责跨任务复用。这样可以重新构建 Context，也可以检验某条 Memory 是否真来自历史。若只剩一个不断覆盖的摘要，摘要错误会成为无法回溯的"新真相"。

代价是存储和隐私管理更复杂，因此还需要保留策略、脱敏和删除协议。完整保存不是"永远保存"，而是先把事实和视图区分开，再为各状态域定义生命周期。

#### Session 的 fork、undo 和 portable export 分别有什么用？

fork 从现有 Session 复制一条带 lineage 的新分支，适合探索另一方案而不污染原对话。undo 撤回或重写最近状态，通常需要原子替换而不是追加。portable export 把 Session 转成稳定 schema、Markdown 视图和 digest，便于跨进程审查和验证。

它们仍只操作 Session 域：fork 不会自动复制 Myna 索引或外部资源，undo 不能撤销已发送消息和 Tool 副作用，delete Session 更不会自动删除 Trace 和 Memory。**每个操作的承诺必须限定到权威状态域。**

#### 删除一个 Session，为什么不一定等于删除所有相关 Memory 和 Trace？

因为它们是不同的持久化域。Session 文件由 Session Manager 拥有；Curator Archive 和 Working State 位于 Context 状态域；Myna 拥有自己的 Journal 和 Index；Tracing 位于本地 Trace 目录；Evolver 和评测还可能复制任务 Artifact。删除一个源文件不会自动跨系统传播。

这对隐私设计是一个重要提醒：**所谓"删除用户数据"必须列出所有派生状态、缓存、备份、索引和日志，并定义删除顺序、失败重试和可核验回执**。否则 UI 显示 Session 消失了，语义 Memory 或 Trace 里仍然存在内容。

### Resume 到底恢复了什么？能不能从上一次 Tool Call 中间继续？

Pico 的 Session Resume 恢复的是**对话连续性**：重新加载已持久化的消息和 metadata，随后由 Context Engine 重新组装窗口。它不恢复 Python 调用栈，不保证恢复一个正在等待返回的 Tool invocation，也不自动撤销或重放外部副作用。

如果进程在 Tool 执行后、Session 保存前崩溃，外部动作可能已经发生但 Transcript 没有完整记录；如果 Session 保存后、Memory 写入前崩溃，则会有对话事实但没有新的长期 Memory。要实现真正的 durable execution，需要为每个步骤保存状态机、调用 ID、幂等键、结果和重放规则，而不仅是"继续同一个 session\_id"。

所以我会用**"语义续聊"而不是"精确恢复整个执行现场"**来描述 Pico Resume。这条边界说清楚，讨论 exactly\-once 时才不会过度承诺。

#### Session、Checkpoint 和 Artifact 三层恢复分别解决什么？

Session 恢复对话事实；Checkpoint 恢复本地 Workspace 文件；Artifact 保存一次运行产生的可核验产物或证据，例如报告、补丁、测试输出、回执和 Trace 摘要。三者的对象和生命周期不同。

Pico 的 Checkpoint 可以按配置在 Turn 边界把 Workspace 状态提交到独立的 shadow Git 仓库，不修改用户自己的 `.git`：本地文件改坏时有机会回滚。但它只覆盖被 Checkpoint 管理的文件状态，不覆盖数据库、远端 API、已经发出的消息或进程内对象。Artifact 则更适合跨进程查看"做到了哪一步"和"结果是什么"，但不一定能直接恢复执行。

可靠恢复通常需要组合使用：Session 告诉新进程用户目标和历史，Checkpoint 提供可回退的 Workspace，Artifact 提供验证和重建依据。**把三者压成一个"Memory"概念，会让恢复承诺失真。**

![16\-recovery\-layers\.png](<原始文件包/Pico 面试逐字话术稿/图片和附件/16-recovery-layers.png>)

#### 如果 Agent 执行到一半进程崩溃，你会怎么恢复？

第一步不是立刻重跑，而是确定**最后一个可信提交边界**。我会读取 Session 终点、Checkpoint、Tool 调用记录和外部回执，判断哪些动作"准备了"、哪些"执行了"、哪些"确认完成了"。第二步检查 Workspace 是否与 Checkpoint 基线一致，有没有用户在崩溃后手工修改。第三步根据工具的幂等性决定重试、查询状态、补偿还是请求人工确认。

对于只读工具，通常可以安全重放；写文件可通过 content hash 和 Checkpoint 判断；远端创建订单、发消息等动作必须使用幂等键或先查询回执。恢复后的模型还应获得一段 re\-anchoring context，明确目标、已完成步骤、未知项和禁止重复动作。

Pico 提供 Session 和本地文件 Checkpoint，能降低本地任务恢复成本；外部动作的精确恢复，则要靠幂等键、状态查询和补偿一条条落实。

#### 为什么很难保证外部副作用 exactly\-once？

因为"系统只执行一次"和"世界只发生一次"不是同一件事。网络可能在服务端成功后、客户端收到响应前断开：Runtime 不知道动作是否完成，简单重试可能重复创建资源，不重试又可能漏做。进程崩溃、消息重复投递和第三方接口缺少幂等能力都会放大这个问题。

常用方案是 **at\-least\-once 加幂等**：为动作生成稳定的 idempotency key，服务端按 key 去重；或者使用 Outbox、事务消息、状态查询和补偿。高风险动作还应在执行前保存 intent，执行后保存 receipt，并让恢复流程先查状态再决定。

所以我会这样表述：本地会话和文件有持久化保护，外部动作只能用幂等键、回执与补偿去逼近，而不是宣称通用 exactly\-once。

#### 恢复时如果 Workspace 已经被用户改了，怎么办？

这叫 Workspace drift。**不能把旧 Checkpoint 机械覆盖到新状态**，否则可能删掉用户修改；也不能忽略漂移继续运行，因为 Agent 原来的计划基于旧文件。

我会在任务开始或 Checkpoint 时记录基线 hash、关键文件 digest 和可能的 Git 状态。恢复时比较当前 Workspace 与基线：无漂移可以继续；只涉及无关文件可以重新组装 Context 并继续；涉及 Agent 已修改或即将修改的文件，则需要三方合并、重新验证，或者让用户选择。任何自动 apply 都应检查 base hash，失败时停下而不是盲目覆盖。

Pico 的 shadow repo 与用户 Git 隔离是好基础，但"可回滚"不等于"可无冲突恢复"。在长任务里，re\-anchoring、hash 检查和人类决策同样重要。

### 一段经历怎样才应该进入长期 Memory？

我会把 Memory 写入看成一次**受控晋升**，而不是每轮对话都向量化。候选内容至少要经过四个判断：它是否跨任务仍有价值；是否能追溯到可靠 Source；是否与已有 Memory 重复或冲突；是否包含敏感、短期或未经确认的信息。

例如"这个仓库的测试命令是 `pytest tests/x`"可能是 Repo Knowledge，"用户偏好所有写操作先 dry\-run"可能是 User Preference；一次临时错误日志通常不值得长期保存，除非它被提炼成可复用故障模式。写入后还需要版本、时间、范围和 supersession 关系，未来召回时要优先使用更新且作用域匹配的信息。

Pico 主 Runtime 只定义 MemoryBackend 生命周期和 recall/store 时机，具体 Source Journal、索引、排序和打包由 Myna 拥有。它不会把模型自述自动当成已验证事实。

#### Pico 和 Myna 的边界是什么？

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 主路径使用迁入的 MemoryBackend／插件契约；无外部 Myna 时显式禁用隐式记忆。旧本地／SQLite 后端、FTS5 和来源处理仍可从 compat 使用，但不在新主路径默认启用。[主接口](repoagent/harness/memory_engine/__init__.py) · [旧 SQLite](repoagent/sqlite_memory.py)
<!-- REPOAGENT-NOTE-END -->

Pico 拥有稳定的 `MemoryBackend` 接口和调用时机：启动、停止、按 query 召回，以及 Turn 结束后把已被 Session 持久化接受的标准化消息片段交给后端 store。Myna 作为独立安装的 Plugin Adapter，拥有仓库绑定、Source Journal、导入、索引、检索、排序、打包和持久化。

当前 Host 主要使用 user lane，但 Myna 把它解释为当前 Workspace 所绑定仓库中的 repository\-scoped recall，`user_id` 本身不负责选择仓库。目标仓库需要显式 `myna init`，初始化、绑定或索引不合法时应 fail closed。Pico 不自动扫描、迁移或删除 Myna 私有状态。

这个边界的意义是 **Runtime 不耦合某一种向量库或 Memory 实现**，同时也避免拿接口存在去替 Myna 的任务效果背书：安装契约通过只能证明接线正确，不能证明记忆让任务更好。

#### Memory 召回时怎样处理 freshness、冲突和作用域？

检索相似度只回答"像不像"，不能回答"现在还对不对"。我会让每条 Memory 携带 Source、时间、作用域、版本和状态：召回时先过滤仓库、用户或任务范围，再做相关性排序；遇到同一事实的多个版本，根据 supersession、Source 权威性和当前 Workspace 验证；高风险事实最好通过工具读取真实状态，而不是直接相信 Memory。

例如旧 Memory 说项目使用 Python 3\.11，但当前 `pyproject.toml` 已经改成 3\.12，文件事实应覆盖旧 Memory，并触发旧条目过期或被替代。用户偏好也要区分"稳定偏好"和"一次性指令"，后者不能无限外推。

**Runtime 应把 Memory 当候选上下文，不应把它提升为无需核验的系统真相。**

#### 为什么不直接把所有历史放进向量数据库，当作 Memory？

因为向量数据库只是索引和近邻检索设施，**不会自动解决语义层级、更新、冲突、删除和证据问题**。把所有消息切块后入库，常见结果是模型反复召回旧计划、临时错误和相互矛盾的答案，而且不知道哪条是权威事实。

合理的 Memory 系统需要 Source capture、结构化类型、去重、supersession、freshness、作用域、召回打包和反馈。Session 仍应保留对话顺序，Skill 仍应保存方法，当前事实仍应通过工具验证。向量检索可以是其中一部分，但不能代替这些生命周期。

Pico 当前本地 Skills 使用 BM25/CJK\-aware 检索，Myna 负责自己的 Memory 检索，两者没有在主路径中被简单融合成"一个万能向量库"。这反而体现了不同知识类型应有不同所有者。

### BM25、向量检索、混合检索和 RRF 应该怎么选？

BM25 依赖词项匹配，对类名、错误码、配置键、中文关键词和精确术语很有效，成本低且可解释。向量检索擅长语义改写和同义表达，但可能错过精确标识，也需要 Embedding 服务和索引更新。混合检索把两者候选集合结合，适合同时需要精确与语义召回的场景。RRF 是一种按排名融合的方法，不要求不同检索器分数同尺度。

选择不能脱离语料。代码符号和 Skill 名称通常 BM25 很强；自然语言经验可能需要 Embedding；动态仓库事实还应优先实时读取。融合也不是越多越好，多个弱检索源会增加噪声和 Token。

判断能力要看**当前启用路径**，而不是源码里有没有组件：代码里有通用 RRF、rewriter 和 LLM gate，当前 SkillForge 主路径仍是 Local BM25/CJK\-aware source，并不代表 Runtime 正在做多源 Memory\+Skill 融合。

![17\-memory\-retrieval\.png](<原始文件包/Pico 面试逐字话术稿/图片和附件/17-memory-retrieval.png>)

#### Pico 的 Memory 效果有怎样的证据？

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 这里的 144/144、读取下降 50% 和通过率增幅属于原项目证据。本项目有本地／SQLite 功能验收和受控召回案例；下文两批 SWE-bench 正式自测关闭了 Memory，不能拿 72% 证明记忆有效。研究记忆评测的方法可以保留，数字不要照搬。
<!-- REPOAGENT-NOTE-END -->

我会先区分三种证据，因为它们回答的不是同一个问题。

**第一层是安装和契约证据**：证明 Pico 能通过公开的 `MemoryBackend` 接口发现、启动和停止 Myna，也能完成仓库作用域内的存储与召回。但这只能说明链路接通了，不能直接推出任务效果更好。

**第二层是确定性任务包**：Myna Pack 完成了 144/144 个 Trial。在这组冻结任务中，开启 Memory 后仓库读取次数下降 50\.0%，95% 区间是 29\.166667% 到 70\.833333%；过期 Memory 和跨仓库 Memory 事件都是 0。这说明在固定实现、固定任务和固定验证器下，Memory 确实减少了重复读取，而且没有破坏作用域隔离。

**第三层是真实 Agent 成对实验**：24/24 个 Pair 都有效，任务通过率的点估计提高 20\.8333 个百分点，但 95% 区间是 0 到 41\.6667 个百分点，仍然包含 0；能力、效率、通用 Agent 和正向结论 Gate 也都没有通过。

所以准确说法是：安装契约和确定性读取收益已经有证据，真实任务中出现了正向观察，但统计不确定性还不足以支持普遍提升。所有结论都绑定当时的 Pico、Myna 版本和任务包，不能改写成当前冻结源码的生产表现。

#### 怎样评测长期 Memory，而不只是看检索准确率？

我会设计"记住以后能否帮助未来任务"的任务级评测。首先构造需要跨 Session 信息的任务，同时加入 decoy、过期条目、冲突版本和跨仓库干扰；然后固定模型、工具、任务和 Verifier，做 Memory 关闭与开启的成对 Trial。Verifier 检查最终产物，检索指标则诊断为什么成功或失败。

具体指标可以包括：关键 Memory recall、stale recall、cross\-scope leakage、重复仓库读取次数、任务通过率、Provider 与 Tool 成本，以及对无关任务的负迁移。还要记录完整 Source 链，防止 Memory 直接泄漏答案或 benchmark 标签。

**检索 top\-k 命中高不代表任务更好**；反过来，Agent 可能不用显式召回也能完成简单任务。主指标应当是端到端 Verifier，检索指标和 Token 只是解释变量。

### Pico 的 SkillForge 当前怎样选 Skill？

LocalSkillCatalog 从 Workspace Skills、配置目录和源码中的包内参考 Skill 中发现候选，检查 frontmatter 以及所需 binary 或环境是否可用。需要说明：冻结版本的 wheel 构建清单并不保证包内参考 Skill 一定被安装，因此不能把源码目录存在直接写成 installed\-wheel 能力。SkillForge 用自包含的 BM25/CJK\-aware 索引检索，经过可选 query rewrite、bounded ranking 和可选 LLM gate，最后注入 Skill 正文或摘要。

失败时，query rewrite 回退原 query，LLM gate 回退确定性排名，某个 Skill source 失败只产生诊断并允许其他 source 继续。当前 Runtime 主路径只有 Local source，并不是 Memory、远程 Hub 和多个市场的统一融合。

![18\-skillforge\-selection\.png](<原始文件包/Pico 面试逐字话术稿/图片和附件/18-skillforge-selection.png>)

#### Active Skill 和检索出来的 Skill 有什么区别？

Active Skill 是每次 Turn 都需要的稳定方法或规则，直接进入固定 segment；检索 Skill 只在当前 query 相关时由 SkillForge 选择，避免把所有方法一次性塞入 Context。

始终激活太多 Skill 会增加 Token 和冲突，检索又可能漏召回，所以 **active 集合应非常小，只放普遍且高风险的规则**，其余按需披露。是否 active 是产品策略，不是"重要 Skill 越多越好"。

Pico 把两者分成不同 segment，便于预算和诊断，也能记录实际注入的 Skill ID。

#### 关闭 Memory 后，Session 和 Skills 还能工作吗？

能。Pico 把 Memory backend、Session 和 Local Skills 分开：`memory.backend = null` 会关闭长期 Memory 的 recall、store 以及相关个性化能力，但 Session 仍保存对话，SkillForge 仍可从 Local catalog 检索方法。

这正是分层的价值：**长期记忆不可用，不应让基本会话和本地 Skill 一起失效**。反过来，如果用户明确选择了 Myna 而后端启动或 recall 失败，Pico 会 fail closed，不会静默当作 Memory 关闭。

#### Pico 的 Skill 会不会自动从每次任务中学习并更新？

当前不能把它描述成完整的自动 Skill 进化。配置和源码中可能保留统计、自动检测、草稿激活或退休等 future\-facing 字段，但主 Runtime 没有形成完整的反馈驱动生命周期，Local Skill 仍以显式文件和检索为主。

要实现自动 Skill 学习，需要候选提炼、Source、去重、版本、权限、train/sealed eval 和人工激活，和 Evolver 类似。**仅让模型写一个 ****`SKILL.md`**** 并在下次加载，容易把错误经验固化。**

## 六、Tools、Function Calling 与 MCP

### 如果让你整体讲一下 Pico 的 Tool 设计，你会怎么讲？

我会把 Tool 看成**模型把语言判断变成现实动作的能力契约**。Function Calling 解决模型怎样用结构化参数表达"我想调用什么"；Tool 是 Runtime 里真正会被执行、约束和观察的动作；MCP 解决外部工具、资源和 Prompt 怎样通过统一协议接入。三者相关，但都不能替代权限和执行治理。

最简单的 Tool 只有名称、JSON schema 和一个 Python 函数。它能跑起来，却没有说明调用者身份、前置条件、读写范围、风险、超时、幂等性、并行条件、错误类别和结果大小。模型看到了 schema，也不等于获得授权。更可靠的分工是：**模型提出动作，Runtime 做 schema 校验、能力检查、参数规范化、风险判断和执行**；Tool 返回结构化 `ToolResult`；模型再根据真实结果继续。

Schema 的价值也不只是让 JSON 能解析。字段名、枚举、默认值和说明共同定义了模型的动作空间：一个含糊的 `run(command: string)`，通常比多个目的明确的只读 Tool 更容易误用；一个布尔 `success` 也会丢失"可重试、权限不足、目标不存在、已经产生部分副作用"等重要语义。好的 Tool 接口应该尽量窄，结果应该可解释，不能自动恢复的状态要明确返回。

失败处理要先看错误类型和副作用。参数错误可以反馈模型修正；限流可以在预算内退避；相同输入的确定性失败反复发生时，应触发 loop\-breaking，而不是无限重试；已经可能产生外部写入的调用，除非有幂等键、回执或状态查询，不能自动重放。所谓 retry policy，本质上依赖动作的副作用语义，不是统一的"最多三次"。

并行也不是越多越好。只读、互不依赖、结果顺序不影响后续的调用可以并行；写同一文件、存在先后依赖、共享速率限制、需要审批，或者一个结果会改变下一步参数的调用，应当串行。更成熟的 Tool 系统最好声明读写集合、依赖关系和风险等级。Pico 当前采用保守规则和运行时边界。

Tool 太多以后，问题还会进入 Context。把几百个 schema 一次性发给模型，会增加 Token 和选错动作的概率，所以需要**按任务、权限和能力逐步披露**：ToolCard 先告诉模型"有什么能力"，真正执行前再给完整 schema；Skill 描述"怎样组合工具完成一类任务"，两者不能混在一起。Pico 的负面实验也说明，schema 少很多不代表任务一定更好：可见 Schema Token 下降 93\.4513%，task pass 却从 23/24 退到 20/24。

MCP 的价值是降低集成成本，但它不是信任协议。远程 MCP Server 可能返回恶意内容、暴露过宽 Tool，也可能自己持有凭据和网络权限，Runtime 仍然要校验 Server 身份、Tool allowlist、参数、数据出站和结果大小。普通 REST API 更直接，契约由业务控制；MCP 更适合动态工具生态；Plugin 可以在进程内贡献 backend 或 Tool，性能更直接，供应链和隔离风险也更高。Pico 会先读取 manifest 和分发元数据，只有真正选择 factory 时才延迟导入。这降低了耦合，不等于安全沙箱。

人类审批也不是万能开关。真正需要审批的是具体动作：目标对象、参数、预期影响和当前版本，而不是一个抽象 Tool 名字；审批以后到执行前，状态可能已经变化，所以 Runtime 还要重新校验参数、base hash、权限和目标，避免"检查时安全，执行时已变"。模型不确定用户意图、动作不可逆、影响范围大或者缺少必要数据时，应当请求用户，而不是把"自主"理解成永不提问。

Pico 当前具备 Tool Registry、schema 校验、timeout、结构化 ToolResult、MCP 延迟连接、Shell 与文件 guardrail、ask\-user、Tool event 和重复失败 nudge：它让错误回到模型，让终态不由最终文本决定，也把可选 Tool 故障和关键后端故障分级。

评测 Tool 也不能只看模型有没有成功发起调用，还要看选择准确率、参数有效率、错误恢复、重复副作用、结果信噪比、任务通过率和成本。**Function Calling 只是提案协议，MCP 只是连接协议，Tool 才是被 Runtime 执行的能力契约。** 模型可以决定"想做什么"，只有 Runtime 有资格决定"能不能这样做、怎样安全地做，以及做完如何证明"。

### Function Calling、Tool 和 MCP 有什么区别？

Function Calling 是模型与应用之间表达"我想调用某个函数以及参数是什么"的协议；Tool 是 Runtime 真正能够执行的能力；[MCP](https://modelcontextprotocol.io/specification/2025-11-25) 是把外部工具、资源和 Prompt 以标准客户端与服务端协议接入应用的一种方式。三者不在同一层。

模型发出 Function Call 以后，Runtime 仍要做 schema 校验、权限检查、超时、执行、结果格式化和 Trace。一个 Tool 可以是本地 Python 函数、Shell 封装、远程 API，也可以来自 MCP Server。MCP 解决的是能力发现和连接标准化，并不替代 Agent Loop、Session、恢复、Verifier 或安全策略。

Pico 把内置工具、Plugin 工具和 MCP 工具归一到 Tool Registry 中执行。我不会说"接了 MCP 就有了 Agent"：**协议只提供连接面，真正的控制权仍在 Harness。**

#### Pico 的 Tool 抽象是什么样的？

核心抽象很小：Tool 有名称、描述和参数 schema，并提供异步 `execute`。Registry 负责按名称解析，Runtime 在超时范围内执行，结果用显式 `ToolResult` 表示，失败不是靠解析字符串猜出来的。执行上下文还携带 call id、session、iteration、origin 和 parent 等信息，便于追踪和取消。

一个好的 Tool Contract 至少应包含：明确的输入类型、确定的错误结构、是否只读、是否可能产生外部副作用、能否并行、是否需要网络或审批，以及结果如何分页或引用。**schema 只是语法约束，不能代表业务权限。**

#### 为什么 Tool schema 很重要？模型不是能理解自然语言吗？

schema 把自然语言意图收敛为**可验证输入**。没有 schema 时，模型可能漏字段、传错类型、把用户可见名称当内部 ID，或者构造一个工具根本不支持的组合。严格 schema 能在副作用发生前拒绝明显无效请求，也让工具描述、日志和测试更稳定。

但 schema 不能解决所有问题：`delete_file(path: str)` 即使类型正确，也可能越权；`send_email(to, body)` 字段合法，也可能发给错误的人。还需要路径约束、身份解析、业务规则、审批和幂等。

工具设计时我会使用语义明确的参数名，例如 `repository_id` 而不是 `repo`，把枚举、范围和互斥关系写清，并让失败返回"哪个字段为什么不合法"。Anthropic 的 [Tool Engineering 实践](https://www.anthropic.com/engineering/writing-tools-for-agents) 也强调，工具接口本身会直接改变 Agent 轨迹。对模型而言，清晰的工具接口就像给新工程师的 API 文档，含糊描述会直接转化成错误轨迹。

### Tool 调用失败以后，Agent 应该怎么处理？

首先要分类。参数错误、权限拒绝、资源不存在、确定性命令失败、超时、限流、网络中断和未知异常的恢复策略不同。Runtime 应把结构化错误返回给模型，同时记录 Tool Event：可修正的参数错误可以让模型重试，限流可以按策略退避，不可逆或权限问题应停止或询问用户。

Pico 不会让 Tool 异常直接丢失上下文，而是生成失败的 `ToolResult` 供模型观察，Turn 最终可能成为 `completed_with_tool_failure`。同一工具以相同参数反复出现确定性失败时，Loop 会发出有界 nudge 阻断循环；合法的空搜索或瞬时 rate limit 不应被误判为死循环。

关键是避免两种极端：任何失败都终止 Turn，会损失模型自我修正能力；无限交给模型重试，又会浪费预算或重复副作用。**恢复策略必须由错误类型和工具风险共同决定。**

#### 怎样防止 Agent 重复调用同一个工具？

不能只按工具名计数，因为连续读取不同文件可能是正常探索。我会构造**调用指纹**：工具名、规范化参数、相关状态版本和错误类别。只有相同意图在状态没有变化时反复失败，才更像无效循环。

应对方式按风险升级：第一次把明确错误返回模型；第二次提醒它不要原样重试，并建议替代动作；达到阈值后阻断该指纹或结束当前分支。对于有副作用工具，在第一次不确定响应后不应直接重试，而应查询远端状态或使用幂等键。

Pico 有针对相同确定性失败的 loop\-breaking 机制，但这不是通用规划器。未来可以用 Trace 统计重复读、重复 Shell 和无进展迭代，把"是否产生新信息"作为更强的循环判断。

#### Tool Call 可以并行执行吗？你怎么判断？

只有**彼此独立且并发安全**的调用才应该并行。Pico 当前采取保守策略：只有连续出现、风险被标为 READ 并且 `concurrency_safe` 的调用可以并发；未知、写入、执行和外部动作都作为串行屏障，结果最终按原始调用顺序返回模型。

这比"模型一次给出多个调用就全部 gather"安全。两个读取可以并行，但"写文件后运行测试"有依赖；两个写不同文件看似独立，也可能共享格式化配置或 Git 状态；两个外部 API 调用可能受配额和顺序影响。并行化前需要考虑读写集、共享资源、幂等和取消。

性能优化的原则是**先证明独立性，再并行**。Agent 系统的错误往往不是平均延迟高，而是一个看似无害的并发改变了世界状态，模型随后还按照串行假设推理。

#### 工具结果应该返回多少内容？越完整越好吗？

默认不应该越完整越好。Tool Result 既是机器证据，也是下一次模型调用的 Context：返回过少，模型无法判断；返回过多，会淹没目标并增加 Token。理想结果应包含状态、关键字段、可操作错误、稳定引用和必要摘要，详细内容通过分页、范围读取或 artifact 句柄按需展开。

例如 Shell 工具不应无上限返回完整日志，可以保留 exit code、stdout/stderr 摘要、截断标记和 artifact 路径；搜索工具返回命中位置和 snippet，模型需要时再 read；远端写操作返回资源 ID、幂等键和服务端回执。

压缩不能破坏证据：Runtime 可以截断展示，但原始结果应有受控存储或 digest，Trace 中也要注意敏感信息。**工具输出设计往往比 Prompt 微调更直接地影响 Agent 表现。**

#### Tool 重试怎样避免重复副作用？

首先给每次逻辑动作稳定的 call identity 或 idempotency key，Runtime 记录 intent、attempt 和 receipt。遇到超时或连接断开时，先查询远端状态，只有确认未执行才重试；服务端支持幂等键最好，不支持时需要应用层去重、补偿或人工确认。

重试策略也要按错误分类：参数错误不应重试；限流按 `Retry-After` 退避；网络错误可以有限重试；权限拒绝应停止；未知结果的写操作不能像 GET 一样自动重试。每个物理 attempt 都应独立记录，否则成本和故障率会被低估。

Provider 层会区分重试和 fallback attempt，Tool 层有超时和失败 bit；具体第三方写工具仍要实现自己的幂等和 receipt 协议。

![19\-idempotent\-retry\.png](<原始文件包/Pico 面试逐字话术稿/图片和附件/19-idempotent-retry.png>)

### 工具越来越多时，怎么避免把几百个 schema 都塞给模型？

常见方案是**分层披露**。先给模型少量高层 namespace 或 ToolCard，描述能力范围；模型选择后，Runtime 再加载详细 schema。也可以先用一个 Tool Search 检索相关工具，或者由应用根据任务、权限和当前状态过滤工具集。对于多 MCP Server，还要把 Server 身份、信任级别和可用性纳入选择。

动态披露的风险是模型可能因为没看到工具而无法选中，检索器也可能错召回。因此要保留稳定的核心工具、提供 fallback 发现路径，并评测"工具可见率、schema token、任务通过率"的权衡。

![20\-tool\-disclosure\.png](<原始文件包/Pico 面试逐字话术稿/图片和附件/20-tool-disclosure.png>)

#### ToolCard 和 Skill 有什么区别？

ToolCard 通常是对某个可执行能力的紧凑描述，用来帮助模型发现或选择工具；Skill 是完成一类任务的方法知识，可能包含步骤、注意事项、示例和所需工具。**ToolCard 回答"我能调用什么"，Skill 回答"这类事通常怎么做"。**

Skill 本身不一定有执行权：它可能指导模型调用 `grep`、`read` 和 `shell`，真正的动作仍由 Tool Contract、权限和 Sandbox 控制。反过来，一个 Tool 没有 Skill 也能被调用，但在复杂领域里模型可能不知道正确顺序或业务约束。

Pico 的本地 Skill 可以始终激活或被 SkillForge 检索后注入 Context，Tool 仍由 Registry 执行。两者分离能防止把一段自然语言教程误当成安全边界，也便于独立评测"检索到了正确方法"和"工具执行正确"。

### MCP 接入 Pico 的完整链路是什么？

运行时根据配置连接 MCP Server，读取其可用 Tools，把外部 schema 适配到 Pico 的 Tool Registry。模型在 Provider 协议中发出对应 Tool Call 后，Pico 仍负责名称解析、调用上下文、超时、错误归一、结果回填和 Trace；MCP Client 再通过 stdio 或网络 Transport 把请求发给 Server。

这条链路里有两个**信任边界**。Server 可能提供错误或恶意 schema、读取敏感上下文或执行高风险动作；Server 返回的数据也可能包含 Prompt Injection。因此 MCP 工具不能因为"遵循标准协议"就自动获得高权限：应当限制可连接 Server、使用最小凭据、审核工具注解、对敏感动作审批，并把远端内容当不可信输入。

**MCP 的价值是减少每个集成重复定义协议，不是消除运行时治理。** Pico 的 MCP 只是一种工具来源，不拥有 Turn 和 Verifier。

#### MCP 和普通 REST API、Plugin 有什么取舍？

REST API 定义的是业务服务接口；MCP 在其上提供面向模型应用的能力发现、Tool/Resource/Prompt 语义和统一 Transport；Plugin 则通常是应用内部的扩展机制，可以贡献 Tool、Memory backend 或其他组件，并受应用版本和加载规则控制。

已有稳定 API 时，可以直接写 Tool 封装，最少依赖；希望多个 Agent 客户端复用、动态发现或统一授权时，MCP 更有价值；需要深入 Runtime 生命周期、贡献本地组件时，Plugin 更合适。三者可以组合：Plugin 注册一个连接内部 MCP Server 的 Tool Provider，MCP Server 内部再调用 REST API。

选型时要看部署、延迟、版本、权限和故障域。引入 MCP 多了一层进程或网络边界，获得标准化的同时也增加了安全和可用性问题，**不应为了"支持 MCP"而把一个简单本地函数复杂化**。

#### Plugin 的发现和加载为什么要延迟？

如果启动时直接导入所有 Plugin，一个缺失依赖、恶意副作用或版本冲突就可能让整个 Runtime 崩溃。Pico 先读取 manifest 和 distribution 元数据，验证身份、版本和贡献冲突，只有真正选择某 backend 或 Tool 时才解析 factory 并导入。

延迟加载降低启动耦合，也让未启用的 Channel 或 Plugin 不要求安装其 SDK。但一旦加载进主 Python 进程，仍然不是安全隔离：**可用性优化与信任边界要分开。**

### Shell、文件读写这类本地工具怎样做安全控制？

我会从五层做：能力最小化、路径边界、命令分析、执行隔离和审计。文件工具只能访问 Workspace 允许范围，规范化路径并防止 `..`、绝对路径、symlink 逃逸；Shell 尽量使用参数化命令或 allowlist，识别删除、提权、网络下载等高风险模式；敏感动作需要确认；执行环境限制 CPU、内存、时间和网络；每次调用记录输入摘要、结果和身份。

不过，字符串规则永远不是强 Sandbox：复杂 Shell 可以绕过黑名单，代码仓库也可能包含恶意脚本。Pico 默认 Direct backend 在 Host 执行，Workspace 约束和危险命令检查只是 guardrail；显式选择 BoxLite 时才进入 microVM，并且不可用时 fail closed，不回退到 Host。

所以正确表述是"**有多层防护和可选强隔离**"，而不是"有路径检查所以安全"。

#### 什么时候应该让 Agent 询问用户，而不是自己继续？

当缺失信息会显著改变不可逆动作、成本、权限或结果语义时，应询问用户。典型情况包括收件人歧义、删除范围不清、生产环境写入、付费资源、法律或财务承诺，以及多种方案取舍没有默认依据。

低风险、可逆、可通过工具验证的问题不必频繁打断：Agent 可以先做只读探索、生成 dry\-run 或提出带证据的建议，再在真正提交前请求确认。询问也应具体，说明拟执行动作、影响范围和可选项，而不是笼统说"可以吗"。

Pico 有 ask\-user 和 TUI Confirm Round\-Trip 等交互入口。应用层需要定义哪些动作必须 HITL：**人类确认是权限决策，不应被模型用措辞绕过。**

#### 审批通过以后，为什么还要重新校验参数？

因为审批和执行之间可能发生 **TOCTOU 变化**。用户批准的是"删除临时目录 A"，但模型或状态在等待期间可能把参数变成目录 B；文件也可能被替换成 symlink；远端资源版本可能变化。如果 Runtime 只记一个 `approved=true`，就会把旧授权应用到新动作。

更安全的方式是把审批绑定到具体 call id、规范化参数、目标版本或 digest，并设置有效期；恢复后执行前重新做 schema、权限、路径和状态检查，参数变化则需要重新审批。OpenAI Agents SDK 的 [HITL 机制](https://openai.github.io/openai-agents-python/human_in_the_loop/) 也把审批与具体工具调用和可序列化 RunState 关联，而不是给 Agent 永久通行证。

#### 怎样评测一个 Tool 设计得好不好？

我会先建立真实任务，而不是只测试函数本身。指标分为四类：**选择正确性**，模型是否在该用时选到它；**参数正确性**，schema 错误和重试次数；**执行质量**，成功率、延迟、幂等和副作用；**任务效果**，Verifier 是否通过。还要看 Token 开销、结果是否造成后续 Context 污染，以及与相似工具的混淆。

实验时固定模型和其他 Harness 条件，只修改工具名称、描述、schema 或输出格式，做成对 Trial。Trace 可以显示模型为什么选错、是否反复读取、错误是否可修正；确定性单元测试负责工具本身，真实 Provider 任务负责模型与工具接口。

**工具质量是接口与模型共同作用的结果，必须用 Agent 任务验证，不能只靠单元测试证明契约。**

## 七、安全、权限与可靠性：模型不能成为信任根

### 一个能够执行 Shell、文件和外部 API 的 Agent，安全与可靠性应该怎么设计？

我会先把问题说清楚：一个能执行 Shell、读写文件、调用外部 API 的 Agent，本质上是一个**代理权限问题**。模型会同时读取用户输入、网页、仓库、邮件和 Tool 返回的非可信内容，又代表用户持有真实权限。Prompt Injection 危险，不只是因为模型可能"被说服"，而是**非可信数据可能借模型的身份调用高权限能力**。因此模型、检索内容、Tool 输出、MCP Server 和 Plugin 都不能成为信任根，权限必须由模型之外的确定性边界决定。

最常见的朴素方案，是在 System Prompt 里写"不要泄露密钥，不要执行危险命令"。这只能形成行为倾向，不能提供强制保证。另一个误区是"只允许在 Workspace 内操作"：Workspace 里仍可能有 `.env`、SSH 配置、符号链接、可执行脚本和敏感代码，Shell 也可能通过网络或子进程越界。本地运行优先同样不天然安全，因为本地进程往往拥有比云端沙箱更高的用户权限。

我会把安全设计拆成四个控制面。**第一是能力最小化**，只向当前任务披露必要 Tool，按用户、会话和任务发放短期能力。**第二是执行隔离**，通过文件边界、进程、容器或 microVM 限制 CPU、内存、网络、挂载和系统调用。**第三是数据流控制**，Secret 不进入模型 Context，敏感数据出站要经过目的地 allowlist、脱敏和用户授权。**第四是高风险动作治理**，要有明确 intent、审批、执行前重校验、幂等和回执。这四层不能互相替代。

Sandbox 的保证必须说具体。路径检查和危险命令过滤只是应用层 guardrail，不是操作系统隔离；容器提供 namespace 和 cgroup 级隔离，但共享宿主机内核；microVM 的内核边界更强，启动和资源成本也更高；Kubernetes 解决调度、租户资源和生命周期，不会自动让容器里的 Tool 变安全。Pico 的 Direct backend 在宿主机执行；`auto` 当前表示必须有可用的 BoxLite，而不是 BoxLite 不可用后静默回退宿主机。这种失败关闭比"尽量沙箱"更可信。

Secret 管理的原则是 **Tool 持有，模型引用**。模型最好只看到逻辑连接名或 capability id，由执行器在受控环境中注入最小范围凭据。日志和 Trace 需要字段级脱敏，错误信息不能把 Authorization header 原样送回模型。网络层还要区分模型 Provider、Web Tool、MCP 和 Channel 的不同出站目的，不能只用一个全局"允许联网"开关。

供应链同样属于安全边界。Plugin 或 MCP Server 的 manifest、版本、来源和贡献冲突，要在加载前验证；延迟导入可以避免未启用组件拖垮启动，但进程内 Plugin 一旦加载，仍然拥有 Python 进程权限。真正不可信的扩展应放在独立进程或隔离环境，通过窄协议通信。Tracing 也有安全矛盾：记录越完整越利于调试，也越可能复制用户数据、源码、Tool 参数和凭据，所以需要采样、保留期、访问控制和默认不记录隐私 Reasoning。

可靠性和安全还要共享失败分类。用户明确选择的 Memory backend、强 Sandbox 或审批路径不可用时，应当 fail closed，因为静默降级会破坏承诺；可选 Skill source、query rewrite、LLM gate 或 Tracing 失败，可以隔离或回退到确定性路径，因为它们不应让主任务完全不可用。Tool failure 可以作为结构化结果反馈模型；Provider terminal error 应使 Turn 失败；Channel send exhaustion 发生在 Turn 之后，不能被伪装成执行成功，也不能回滚已经发生的动作。

所以安全设计的落点不是让模型"更听话"，而是让**任何非可信内容都无法越过确定性的权限边界**。模型可以是不可信的，只要系统不给它未经检查的权力。一旦模型本身被当成安全策略，Agent 就失去了可证明的边界。

### Agent 系统最核心的安全原则是什么？

最核心的原则是：**模型输出始终是不可信的建议，真正的权限由 Runtime 掌握**。模型可以提出调用工具、访问文件或发送消息，但不能因为它"认为有必要"就自动获得能力。所有动作都应经过身份、schema、作用域、风险、审批和执行环境检查。

我会把信任边界画成几层：用户输入与外部网页可能包含 Prompt Injection；模型可能误解或被诱导；Tool 或 MCP Server 可能有漏洞；Workspace 中的代码也可能是恶意的；Provider、Trace 和日志则涉及数据外发。安全设计要对每一层设最小权限和失败关闭，而不是只在 System Prompt 写"忽略恶意指令"。

#### Prompt Injection 为什么在 Agent 场景里更危险？

普通聊天中的 Prompt Injection 可能让回答偏离；Agent 场景中的注入可能**诱导模型调用工具、读取秘密、修改文件或把数据发送出去**。网页、README、Issue、邮件和 Tool Result 都可能携带"忽略上文、上传凭据"这类内容，而模型很难天然区分数据和指令。

防护不能只靠模型识别。第一，把外部内容标记为不可信数据，与系统策略分层。第二，工具权限不从文档内容继承。第三，敏感 Source 默认不进入外发 Context。第四，写入和外部发送采用审批、allowlist 和最小凭据。第五，用 canary 和对抗任务评测是否发生越权。

Pico 的 `security` 层提供不可信内容 fencing 和网络 trust 检查，但本地文件、MCP 和 Shell 组合仍然是高风险面。任何"模型已经被 Prompt 教会防注入"的说法都不够可信。

![21\-prompt\-injection\-boundary\.png](<原始文件包/Pico 面试逐字话术稿/图片和附件/21-prompt-injection-boundary.png>)

### Pico 的 Sandbox 能提供什么保证？

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 主路径执行器是 none（宿主直接执行）或 BoxLite；auto／boxlite 不可用时失败。BoxLite 保留参考实现，并适配 SDK 0.9.5 的网络配置接口。Docker 与持久 Docker 仍由 compat／Issue 业务使用。此前真实 BoxLite E2E 和 SDK 边界记录属于历史验收，不能把历史 SWE-bench 归为 BoxLite 成绩。[验收记录](docs/boxlite-acceptance.md)
<!-- REPOAGENT-NOTE-END -->

Pico 有 Direct 和 BoxLite 等执行后端。**Direct 在 Host 上执行**，虽然有 Workspace 限制、危险命令检查和确认，但本质上仍共享用户机器的内核、文件和凭据，不能称为强隔离。**BoxLite 是显式选择的 microVM 路径**，隔离边界更强；如果用户选择它但后端不可用，Pico 会 fail closed，而不是悄悄降级为 Host 执行。

Sandbox 真正需要定义的是文件挂载、网络、环境变量、进程、资源、生命周期和 Artifact 导出，而不仅是"放进容器"。容器共享 Host 内核，也不自动阻止挂载敏感目录或网络外传；microVM 隔离更强，但启动与资源成本更高。

所以准确表述是：Pico 有**可选的强隔离方向**，但默认不是 Sandbox 安全证明。对于不可信仓库或高权限凭据，应明确选择隔离后端，并使用最小挂载和网络策略。

![22\-sandbox\-levels\.png](<原始文件包/Pico 面试逐字话术稿/图片和附件/22-sandbox-levels.png>)

#### 为什么"限制在 Workspace 内"还不够安全？

路径字符串在 Workspace 内，不代表最终访问对象一定在里面。攻击者可以利用 `..`、符号链接、硬链接、挂载点、大小写和路径规范化差异，或者让允许路径内的脚本再访问外部资源。即使文件访问被限制，Shell 进程仍可能读取环境变量、用户 Home 或网络凭据。

因此路径检查要在规范化后验证，并处理 symlink；最好在 OS 或 Sandbox 层只挂载允许目录，而不是依赖应用层字符串判断。写入前还应检查目标类型、权限和 base hash，避免替换用户文件。

Pico 的 Workspace path constraints 是必要 guardrail，但它和 Direct backend 一起不能形成恶意代码隔离。正确表述是"减少误操作范围"，不是"保证不越权"。

#### 你会怎样设计工具风险分级和权限模型？

我会至少按四个维度打标签：读还是写；作用域是本地、网络还是外部账户；动作是否可逆；影响面和成本多大。由此形成类似 `read`、`write_local`、`execute`、`external_write`、`destructive` 的等级，再叠加用户身份、Workspace、目标资源和时间限制。

低风险只读动作可以自动执行；本地可逆写入可以先 Checkpoint；外部写入、删除、付费和权限变更必须审批；未知工具默认按高风险处理。审批应绑定 call id 与规范化参数，Runtime 执行前重新校验；Plugin 或 MCP 贡献工具时必须声明风险，**声明缺失不能默认安全**。

#### 模型能看到 API Key 或用户凭据吗？怎样避免泄露？

理想情况下模型不应看到原始凭据。Runtime 应把 Secret 保存在环境、Keychain 或 Secret Manager 中，工具按最小权限使用，模型只获得不敏感的资源句柄。日志、Trace 和错误信息要在写盘前脱敏，Tool Result 也不能把完整 Header 或环境变量回传给模型。

对 Provider 还要做数据分类：哪些仓库文件可发送到远端模型，哪些必须用本地 Provider 或先脱敏；MCP Server 和 Plugin 使用独立凭据，不能继承整个 Host 环境；执行 Shell 时采用 env allowlist，而不是默认传递所有变量。

**安全结论必须覆盖 Context、Tool、Trace、Memory 和 Delivery 全链路，单看某一个入口都会漏。**

#### 怎样防止 Agent 把敏感数据发到外部网络？

需要做**数据流控制**，而不只是限制一个 `web_fetch` 工具。首先分类敏感 Source，例如 `.env`、SSH key、客户数据和私有代码，ContextAssembler 和 Tool 层都要知道哪些内容不能进入远端 Provider。其次，网络 Tool 和 MCP Server 使用目标域 allowlist、请求大小限制和内容检查。第三，外部写入前显示目标、数据摘要和敏感性，必要时审批。

还要防止间接外传，例如把 Secret 编码进 URL、DNS、提交信息或错误日志。强场景应在 Sandbox 层默认禁网，只开放代理，并由代理记录和过滤出站流量。

**不能因为单个 Tool 不允许任意 URL，就声称系统不存在外泄通道。**

![23\-data\-egress\.png](<原始文件包/Pico 面试逐字话术稿/图片和附件/23-data-egress.png>)

### 如果接入的 MCP Server 或 Plugin 是恶意的，怎么办？

首先不要把扩展发现等同于信任。Plugin 在加载前应验证 manifest、分发身份、版本兼容和贡献冲突，并延迟导入；MCP Server 应有显式 allowlist、传输认证和最小凭据。其次，扩展声明的 Tool 风险只能作为输入，Runtime 仍要施加独立策略。最后，执行应放在隔离进程或 Sandbox 中，并限制网络、文件和环境变量。

Pico 的 Plugin Registry 会在激活前检查 manifest、distribution 和 compatibility，贡献冲突 fail closed；正常 Host 也不会自动把 Workspace 里的任意目录当可执行 Plugin。这个边界能降低供应链风险，但不能证明第三方代码安全，因为一旦导入 Python Plugin，它仍可能在进程内执行。

更高安全级别下，我会优先使用**进程外 MCP 或受限服务接口**，配合签名、版本固定和行为审计，而不是让不可信 Plugin 直接进入主 Runtime。

#### Tracing 记录得越完整越好吗？安全上有什么矛盾？

Trace 越完整越容易调试和评测，但也越可能包含用户输入、源代码、Tool 参数、凭据片段和模型 Reasoning。全部原样保存，会把可观测系统变成高价值敏感数据仓库；过度脱敏又可能失去定位价值。

我会采用**分级采集**：默认记录结构化元数据、状态、时延、Token、digest 和截断摘要；原始 Prompt、Tool 输入输出只在受控调试模式保存，并设置访问控制、保留期和删除路径。敏感字段使用 allowlist 而非事后黑名单；向第三方 Observability 导出时还要做二次审查。

Pico 的 Trace 强调非干扰，即 Trace 写入失败不应让 Turn 失败，这是正确的可用性边界。但 best\-effort 也意味着不能把 Trace 当作唯一审计账本，高合规场景需要更强的不可抵赖日志和数据治理。

### 可靠性设计应该怎样给 Agent 故障分类？

我会按**责任边界分类**，而不是都叫"Agent 失败"。至少包括：输入或权限失败；Context 与 Memory 失败；Provider 超时、限流或终止失败；Tool schema、执行或副作用失败；Session 和 Checkpoint 持久化失败；调度与取消失败；Delivery 失败；Verifier 失败；Trace 缺失。每类故障的重试、用户提示和告警不同。

Pico 在 Turn 内区分 Provider failure、一般 error、cancelled 和带 Tool failure 完成；Session、Memory 和 Delivery 又有独立边界。例如 Session 保存后 Memory store 失败，会形成持久 Transcript 但 Turn 失败；Channel 发送耗尽不会回滚 Agent Turn；Tracing 失败被吞掉，避免 Observability 打断业务。

这套分类比"catch Exception 后重试三次"可靠。**重试只有在错误暂时、动作幂等且预算允许时才合理。**

#### 有哪些错误应该 fail closed，哪些应该降级？

如果组件的存在改变了用户对安全或持久性的承诺，就应该 fail closed。例如用户明确选择 BoxLite，后端不可用不能回退 Host；配置了 Myna 作为 Memory 后端，启动或 recall 失败不能假装 Memory 关闭；审批信息不完整时不能执行敏感动作。

可选的增强组件可以降级：一个 Plugin 贡献的非必需 Tool 构造失败，可以记录并跳过；Skill query rewrite 或 LLM gate 失败，可以回退到确定性排名；Tracing 失败不应让业务 Turn 失败；Curator 计划无效可以走 Fail\-Safe。

判断依据是**"静默降级会不会违反用户预期或改变安全语义"**。Pico 在 Memory 和可选 Tool 上的不同处理，正体现了这个原则。

### Pico 当前最重要的安全与可靠性缺口有哪些？

我会主动讲四类。第一，**Direct Sandbox 仍在 Host 执行**，guardrail 不是 OS 隔离。第二，Session、Curator、Myna、Trace、Delivery 和 Evolver 属于多个持久化域，**没有跨域事务**，外部副作用更没有通用 exactly\-once。第三，Gateway 的出站队列当前不持久，关闭时也不保证在途发送完成。第四，通用工具风险分级、数据流控制和企业多租户权限仍不完整。

此外，Tracing 是本地 best\-effort，不是不可篡改审计账本；Lane 队列没有硬容量；只有部分 Channel 和 Evolver 路径具有特定证据；项目状态仍是 Alpha、pre\-RC。

可靠性设计本来就是在明确失败边界，所以这些缺口我会主动讲，而不是等对方追问。真正危险的是把"有测试"说成"生产不会失败"，或者把"本地"说成"天然私密"。

#### 如果要把 Pico 推向真实生产，你会先补哪些能力？

我会先补**可恢复性和权限**，而不是继续增加工具数量。第一，引入持久 Outbox 和幂等 Delivery，明确消息回执与补偿。第二，为高风险 Tool 建立统一 Policy Engine 和 call\-bound 审批，默认最小权限。第三，把不可信执行默认放到强 Sandbox，配额、网络和 Secret 隔离成为配置契约。第四，为 Session、Memory、Trace 和 Artifact 建立数据血缘、retention 和删除编排。

随后补多实例所需的持久 Scheduler、分布式锁或租约、队列容量与背压、租户隔离、凭据服务、指标告警和发布回滚；最后才是在实际流量上建立 SLO、故障演练和容量模型。

这不是说所有场景都必须一次做到企业级，而是按**"风险 × 影响 × 不可恢复性"排序**。一个能调用更多工具但无法证明动作、恢复和删除边界的 Agent，不适合先扩自治范围。

## 八、Subagent 与多 Agent：并行不是免费的智能

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 主路径已经改为 **spawn 后台执行、会话级小时配额、Origin.SUBAGENT 新 Turn 回流**。可以沿本章解释执行语义。旧同步 delegate 只在 compat。[源码](repoagent/harness/agent/subagent/manager.py)
<!-- REPOAGENT-NOTE-END -->

### Pico 的 Subagent 设计为什么不只是"多开几个模型"？

因为多 Agent 的价值不来自数量，而来自**任务分解、上下文隔离、角色分工和可利用的并行**。把同一个任务复制给三个模型，再让第四个总结，通常只会增加 Token、重复 Tool 调用和结果冲突。只有子任务相对独立、需要不同信息或能力，而且并行收益大于协调成本时，多 Agent 才可能优于一个更强的单 Agent。

最直接的方案，是让主 Agent 随意 `spawn`，每个子 Agent 继承全部上下文和工具，完成后返回一段自然语言。这个方案很快会失效：递归扩散让成本失控；多个 Agent 同时改同一文件；敏感信息被无必要复制；父任务取消以后，子任务还在产生副作用；不同结果没有证据和冲突规则；最后主 Agent 仍要在更长 Context 里重新理解所有输出。

所以多 Agent 的核心不是 Prompt 里的角色名，而是一组**执行合同**：子任务的目标和验收条件是什么；允许看到哪些 Context、Tool 和 Secret；输出是自然语言还是结构化 Artifact；谁拥有 Workspace、Session 和 Memory；并发预算、深度、超时和取消怎样传播；结果冲突由谁仲裁；子任务失败是局部失败还是父任务终止。没有这些合同，多 Agent 只是把单 Agent 的不确定性复制多份。

常见模式分别优化不同目标。Manager\-worker 适合可拆分并汇总的任务；Planner\-executor 把计划和执行分开，但计划可能过时；Reviewer 或 debate 增加独立检查，也会增加成本，并可能形成一致性幻觉；Pipeline 适合稳定阶段，更接近 Workflow；Blackboard 允许多角色共享状态，但需要严格并发和版本控制。选择模式前，应先看**任务图、状态所有权和失败传播**，而不是先起角色名字。

Pico 的 Subagent 是受控的 Runtime 能力。主 Agent 通过 `spawn` Tool 提交工作，`SubagentManager` 限制并发和每 Session 小时额度；子 Agent 有自己的执行 Context，不应无条件继承完整父历史；最终结果会作为 `Origin.SUBAGENT` 的新 `TurnRequest` 重新进入 Spine，而不是直接回调主 Agent 内存。这样它仍然服从 Lane 顺序、OriginPool 容量、取消和统一生命周期，代价是结果回流不是零成本函数调用。

结果为什么要重新经过 Spine？因为直接回调会绕过调度和终态，在父 Turn 已取消或 Session 已切换时仍然修改状态。经过 Spine 后，子结果会被当成新的系统来源工作，并与用户消息形成确定顺序。父子取消也必须显式传播：取消只能阻止尚未发生的动作，已经执行的外部副作用仍要靠幂等、回执或补偿处理。

Context、Memory 和 Workspace 的共享要更保守。子 Agent 只应拿到完成子任务所需的目标、输入、约束和 Tool，避免复制所有私人历史；共享长期 Memory 可能提升连续性，也会带来跨任务污染、并发写和权限泄漏；多个 Agent 写同一 Workspace 时，应该使用 worktree、分支、文件租约或明确 owner，再由 review 和 merge 阶段处理冲突。

结果冲突不能靠"主 Agent 凭感觉选"。每个子结果应带 Source、Artifact、检查结果、置信边界和未解决项：能确定性验证的，先交给 Verifier；无法机器判断的，再由主 Agent 或人工按 rubric 仲裁。如果两个答案都来自同一个错误 Source，多数投票没有意义。**多 Agent 增加的是搜索和独立检查机会，不保证真相。**

评测时必须保留单 Agent baseline，固定模型、任务、工具和总预算，再比较 task pass、延迟、Provider attempts、Tool 重复、冲突率和人工介入。只看 wall\-clock 会奖励无限并发，只看 pass 又会忽略三倍成本；还要把真正可并行的任务单独分桶，否则在不适合的任务上退化，并不能说明整个模式无效。

所以我不把 Agent 数量当作能力指标。多 Agent 的实质，是把复杂任务拆成多个**有输入、预算、权限、终态和回流协议的故障域**。只有拆分边界清楚、协调成本可控，并且在同等预算下通过成对评测证明收益，它才比单 Agent 更合理。

### 为什么需要 Subagent？一个更强的主 Agent 不够吗？

Subagent 主要解决**上下文隔离和并行探索**，而不是把一个模型复制几份就自动更聪明。主 Agent 可以把范围明确、彼此独立的子任务交给干净上下文，例如分别调查不同模块、检索多个信息源或运行独立验证。子 Agent 内部可以产生大量中间过程，只把压缩后的结果回给主 Agent，避免污染主上下文。

但 Subagent 会增加 Provider 成本、协调复杂度和错误传播。任务高度串行、共享状态频繁或结果必须全局一致时，单 Agent 通常更简单。**多 Agent 最适合可分解、并行收益明确、每个子任务有边界和验收的场景。**

Pico 的 `spawn` 是受限后台任务机制，强调独立工具集、并发和每 Session 限额。

#### 多 Agent 常见的编排模式有哪些？应该怎么选？

我会讲三类最常用模式。第一，manager 或 orchestrator\-worker：一个主 Agent 保留控制权，把子任务当工具调用，适合统一用户体验和最终决策。第二，handoff：当前 Agent 把对话控制权交给专家 Agent，适合客服路由等明确领域切换。第三，显式图或 Workflow：节点和边由开发者定义，适合依赖、审批和恢复要求强的流程。

选择取决于控制权、状态共享和任务结构：需要主 Agent 综合多个证据时用 orchestrator\-worker；用户后续都应由专家处理时用 handoff；步骤可预定义且合规要求高时用图。开放式研究可能混合使用：主 Agent 动态分解，但每个子任务在受控 Workflow 中执行。

Pico 当前更接近有界的 orchestrator\-worker：主 Agent 用 spawn 派发，结果经 Spine 回流，而不是让对等 Agent 自由接管整个会话。

#### 什么时候不应该使用多 Agent？

当任务无法清晰分解、子任务强依赖同一可变状态、协调成本大于并行收益，或者单 Agent 已经能稳定完成时，不应该使用。多 Agent 还会成倍增加 Token、延迟、Trace 量和失败点：多个 Agent 可能重复搜索、相互矛盾，最终主 Agent 仍要承担合并责任。

例如修复一个小函数，启动三个 Agent 通常只是浪费；多个 Agent 同时编辑一个文件会引入冲突；需要严格顺序的数据库迁移更适合确定性 Workflow。**多 Agent 不是能力勋章，而是一种用资源换取探索广度和上下文隔离的方案。**

我会先用单 Agent 基线，通过 Trace 定位瓶颈：是 Context 污染、搜索范围太大，还是任务确实可并行。只有评测显示 subagent treatment 改善任务效果或成本收益合理，才保留它。

### Pico 的 Subagent 从创建到结果回流，链路是什么？

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 可直接说：“父 Agent 调用 spawn 后先得到任务回执，子任务在后台运行，受并发与会话小时配额约束；完成后结果经调度器作为 SUBAGENT 来源的新 Turn 回流。”工作区和工具限制沿参考实现；不要把旧 delegate 的独立工作区副本与角色权限套到新 spawn 上。[源码](repoagent/harness/agent/subagent/manager.py)
<!-- REPOAGENT-NOTE-END -->

主 Agent 调用 `spawn` Tool，SubagentManager 根据当前 Session、任务描述、工具配置和限额创建后台 Agent。它独立运行自己的模型与工具循环，并受并发、每 Session 小时级 spawn 配额和取消约束。完成后，不是直接修改主 Agent 当前调用栈，而是把清理后的结果重新封装成 `TurnRequest(origin=SUBAGENT)`，通过 Spine 进入同一会话 Lane。

这种回流方式有两个意义。第一，Subagent 结果仍服从同一会话的顺序、取消和 Turn 生命周期，不会绕过 Session 和 Trace。第二，主 Turn 与后台结果在时间上解耦，Host 不用为 Subagent 再写一套交付路径。

代价是它不是同步函数调用语义：主 Agent 可能先结束，Subagent 结果随后成为新 Turn。应用必须在 Prompt 和 UI 里解释这种异步性，避免用户误以为所有子任务已经包含在当前答案中。

#### 主 Agent 和 Subagent 的 Context 应该怎样隔离？

主 Agent 只应传递子任务完成所需的**最小上下文**：目标、约束、相关文件或证据引用、允许工具和期望输出。把完整 Session 和所有秘密复制给每个 Subagent，会扩大 Token、隐私和攻击面，也让子 Agent 被无关历史带偏。

Subagent 内部可以有独立工作记忆和工具轨迹，返回时应提供结论、证据、未解决项和置信边界，而不是把整段隐藏推理倒回主 Context。若涉及共享 Workspace，还要定义只读、独立 worktree 或合并策略，避免并行写冲突。

面对并行代码修改，我会优先分配独立 worktree 或限制为只读探索，而不是让多个 Agent 同时改同一目录。

#### Subagent 结果为什么要重新经过 Spine？直接回调主 Agent 不行吗？

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 这段异步回流设计现在适用于主路径。结果作为新 Turn 进入会话 Lane，调度器决定顺序与资源池；父任务取消、子任务取消和结果归属有独立测试。同步工具结果返回是 compat 的旧语义。
<!-- REPOAGENT-NOTE-END -->

直接回调看起来更快，但会绕过会话顺序和生命周期。假设主 Agent 已经完成、用户又发了新消息，此时 Subagent 回调直接写 Session，可能插到错误位置；如果会话已取消，结果仍可能"死而复生"；不同 Host 也要各自处理回调。

重新经过 Spine 以后，结果拥有明确 Origin，进入对应 Lane，接受同样的排队、取消、Trace 和 Delivery 规则。这把异步后台工作变成系统可理解的 Turn，而不是一段任意回调。

代价是多了一次调度和可能的模型处理，但换来了状态一致性。Pico 还规定取消传播后不得把已取消 Subagent 的结果回注，这正是统一控制面的价值。

#### 怎样处理主 Agent 取消以后仍在运行的 Subagent？

父任务取消应**沿任务树传播到所有子任务**：SubagentManager 要停止接受新 spawn、向运行任务发送取消、等待有界清理，并标记终态。即使底层 Provider 或 Tool 无法立刻中断，最终结果也必须检查 parent cancellation token，禁止再次注入 Session。

还要区分计算取消和外部副作用。取消协程不能撤销已经发出的 API 请求或文件写入，因此子工具仍需要幂等、Checkpoint 或补偿。对于长时子任务，最好定期保存可重建 Artifact，而不是把所有进度留在内存。

第三方 Tool 不一定都可中断，所以正确的承诺是：**Runtime 不再接纳已取消任务的结果，而不是世界状态自动回滚。**

### 怎样限制 Subagent 数量，避免指数级扩散？

限制应该由 Runtime 执行，不能只在 Prompt 里写"少开几个"。常见控制包括全局并发、每 Session 并发、单位时间 spawn 次数、最大嵌套深度、总 Provider 预算、每子任务超时和允许工具。父 Agent 还应给出任务复杂度与期望产物，避免子 Agent 继续无边界 spawn。

Pico 有自己的并发和每 Session 小时级 spawn 限额，系统 Origin 也使用独立容量池；这能防止一个任务吃光所有资源，但真正的预算还应把 Provider 成本、Tool 资源和 Host 负载统一计算。

更高级的策略可以按任务复杂度动态分配，但必须有硬上限。**多 Agent 系统一旦失去资源控制，错误不只是回答变差，还可能成为费用、网络和外部副作用的放大器。**

![24\-bounded\-subagent\-tree\.png](<原始文件包/Pico 面试逐字话术稿/图片和附件/24-bounded-subagent-tree.png>)

#### 多个 Subagent 的结果冲突时，主 Agent 怎么办？

主 Agent 不应按"多数票"盲目选择，而应比较 Source、方法、时间和可验证证据。返回协议最好包含结论、引用、执行的检查、假设和不确定项：对可机器验证的问题，运行统一 Verifier；对事实冲突，回到原始 Source；对设计分歧，明确优化目标和权衡。

如果子 Agent 修改代码，合并前应在独立分支或 worktree 中检查 diff、测试和 base hash。若冲突无法自动解决，主 Agent 应把分歧暴露给用户，而不是生成一个看似一致的综合答案。

Pico 提供的是可追踪的协作管道：子结果清理后回流，具体合并逻辑仍由任务和应用定义。

#### Subagent 能不能共享 Memory 和 Session？

技术上可以共享部分状态，但不能把"可访问"理解成"应该全部共享"。Session 是主会话的权威记录，后台 Agent 如果并发追加很容易破坏顺序，因此 Pico 让结果经 Spine 回流。长期 Memory 可以按用户或仓库作用域召回，但要防止不同任务、仓库或权限之间泄漏；工具凭据也应按子任务最小化。

我更倾向传递引用和显式 Context 包，而不是共享可变内存对象。子 Agent 读取同一 Memory 时应记录版本，写入则经过独立晋升和冲突处理；高风险场景甚至只允许只读。

**共享得越多，协调越难；隔离得越强，复制成本越高。** 这个权衡要用任务数据决定，不能用 multi\-agent 标签替代状态设计。

### 怎样评测多 Agent 比单 Agent 更好？

做成对实验：固定模型家族、任务、总预算、工具、Workspace 和 Verifier，基线使用单 Agent，treatment 启用 Subagent。主指标仍是任务通过率或质量，辅以墙钟时间、总 Token、Provider calls、重复工具调用、协调失败、峰值并发和成本。

必须控制预算，否则多 Agent 用三倍 Token 得到一点提升，很难说是系统能力。还要分任务类型：独立并行研究可能受益，单文件修复可能退化。Trace 应标出父子关系、每个子任务的实际贡献和无用分支。

非确定性任务需要多 Trial，并同时看 pass@k 与一致性。**若子 Agent 只让一次幸运样本成功，不能直接推广为可靠提升。**

#### 你怎么看"Agent Team"会替代单 Agent 这一说法？

我认为不会简单替代。更可能的形态是**单一用户\-facing controller 加按需专家、工具和后台 Worker**。很多任务的瓶颈不是 Agent 数量，而是工具质量、上下文、状态、Verifier 和权限：把同一缺陷复制到多个 Agent，只会并行地产生更多错误。

多 Agent 会在搜索广度、组织分工和隔离上下文方面持续有价值，但调度、共享状态、成本和责任归属会成为主要工程问题。模型越强，一些原先需要多 Agent 的任务也可能重新收敛到更简单的 Harness。

所以我的设计原则是**按需派生，而不是默认组队**：由评测决定并行度，让主控制面保留预算、权限和最终验证。Pico 的有界 Subagent 比"自治 Agent 社会"更接近当前可控的工程路径。

## 九、Provider、模型路由、缓存与成本

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 主路径已接入 CallEfficiency 的 off／observe／optimize 与 Anthropic 显式缓存规划。unknown 不等于 zero 的原则保留。没有因此获得原稿 72 个 Trial 或 72.0750% 降本结论；自己的历史费用数据见末尾 benchmark。
<!-- REPOAGENT-NOTE-END -->

### Pico 怎么治理 Provider、模型路由、缓存和调用成本？

我把 Provider 层当成模型世界和 Runtime 世界之间的协议边界，同时也是调用成本的记账边界。一次逻辑上的"问模型"，实际可能包含请求改写、真正的 Provider attempt、重试、fallback、缓存读写，还有 Usage 缺失。如果系统只记录最终模型名和一个总 Token，后面就没法解释实际调用了什么、为什么失败、成本到底花在哪，也没法比较不同 Harness 策略的差别。

直接绑死一个模型 API 是最省事的方案，做原型完全够。但不同 Provider 在 Tool schema、流式输出、Reasoning、Usage、缓存、错误码、限流和模型身份上并不一致。统一抽象如果只做字段映射，反而会制造一种"模型可以无损替换"的幻觉。真正要保留的，是 requested model、attempted model、实际模型、计费模型、错误类别、缓存语义，以及 Usage 是否完整，而不是把 Provider 差异全部抹平。

**重试和 fallback 首先是语义问题**。网络连接在请求发出去之前就失败，通常可以安全重试；但 Provider 是不是已经计费了、是不是已经返回了部分流，这些要靠独立的 attempt 记录才能说清楚。一旦模型输出已经触发 Tool 副作用，就不能再把整个 Turn 当纯函数重跑。fallback 也不等于"主模型失败就换一个"，因为模型能力、Context 长度、数据地域、Tool 支持和安全政策都可能跟着变。切换之后，要重新检查预算、Prompt 与 Tool 的兼容性、用户允许的 Provider 链，并把最终实际身份记录下来。

路由是一个多目标决策。任务难度、Context 长度、Tool 能力、延迟、价格、隐私、Provider 健康状态和历史成功率都会影响选择。模型能给出语义信号，但它看不到完整的价格、健康与合规策略，最终控制还是应该留在 Runtime。历史成功率还要当心选择偏差：小模型总被分到简单任务，成功率高不代表它适合复杂任务。所以路由评测要按任务桶和探索策略来校准。

Prompt Cache 优化的是稳定前缀的重复计算，它不等于 Context 压缩，也不等于 Runtime 自己保存了 KV Cache。可信的缓存统计，必须按每个物理 attempt 分别记录 fresh input、cache read、cache creation、output 和 reasoning Token，而且命中率的分母要明确。还要分清哪些是 Provider 自动缓存，哪些是 Pico 显式打的断点。**Usage 缺失是 unknown，不是 0**；价格未知就是 `None`，不能把它塞进一个看起来精确的总成本里。

回到实现层面。Pico 现在用 `CallEfficiency` 在最终 Provider 请求边界拥有策略和证据；历史模块 `TokenWise` 只保留兼容 schema 和 benchmark lineage。共享的 Runtime Assembly 安装一个稳定的 Provider decorator，让主 Agent、Subagent、Context、Personalizer 这些调用都走同一个记录层。模式分 `off`、默认的 `observe` 和 `optimize`。`optimize` 能管理 Anthropic 的显式 breakpoint，而 DeepSeek、OpenAI 的缓存仍然交给 Provider 自动处理。

当前 DeepSeek live campaign 给了一条边界很清楚的成本证据：72 个 Trial，两臂各 36/36 通过，累计 504 条完整 Call Record。稳定前缀下的保守命中率是 74\.0478%，按任务聚类的估算成本降低 72\.0750%，95% 区间是 68\.8471% 到 75\.0961%。它说明的是：在冻结工作负载加 DeepSeek 自动缓存的前提下，调用记录完整，估算成本有显著变化。但它不是发票核对，不证明通用生产节省，也不能外推到 Anthropic 显式断点。Pico 管理的 Anthropic、OpenAI 付费 canary，目前也没有形成正向成本证据。

这类实验的分母必须是"每个 Verifier 通过的任务"。如果一个策略把单次调用变便宜，却让 task pass 掉下来，真实的单位成果成本反而更高。Pico 的 Context 与 Tool disclosure 负结果就是个提醒：Schema Token 减少了 93\.4513%，通过数却从 23/24 退到 20/24。成本、延迟和缓存，只能在任务非劣、Usage 完整之后，才有资格拿来讨论。

说到"做得怎么样"：把每次 retry 和 fallback attempt 分开记录、把 Usage 归一和完整性显式化、把模型身份和 Trace 关联、保留 unknown 语义，这些是我认为做对的地方。没做到的也摆清楚：还没有真实账单核对，付费 canary 覆盖的 Provider 还少，跨 Provider 统一但不失真的缓存实验还没成，路由也缺真实工作负载的验证。

所以我的结论是：**Provider 抽象的价值不是抹平差异，而是让差异可见、可治理**。成本优化也不是最后算一次 Token 就完事，而是从每个物理调用开始，保存可归因、可比较、不会把未知当成零的证据。

### 为什么要做 Provider 抽象？直接调用一个模型 API 不够吗？

单 Provider 的项目直接调 API 就好，**抽象只有在需要统一运行语义时才有价值**。Agent Harness 在意的远不止请求和文本，还有 Tool schema、流式事件、Reasoning 字段、Usage、缓存 Token、错误分类、重试、fallback 和模型身份。不同 Provider 的字段与能力并不完全一致，如果让这些差异泄漏到 Agent Loop 里，主循环会塞满厂商分支。

Pico 的做法是让 Provider adapter 负责协议转换，Agent Loop 只面向统一的消息和响应。`CallEfficiency` 再在最终调用边界记录 requested、attempted、actual 和 accounting model 这些身份。这样一旦发生模型路由或 fallback，Trace 就能知道真正被调用的到底是谁。

抽象同样不能抹平真实差异。有的 Provider 支持显式 cache breakpoint，有的只能自动缓存；Tool Calling、Reasoning、Usage 的完整度也不一样。**好的抽象统一共同语义，同时保留能力探测和 Provider\-specific policy，而不是制造"所有模型完全可互换"的假象**。

#### 本地模型和远程模型应该怎么选？

选本地还是远程，我会先看数据敏感性、模型能力、延迟、成本、可用硬件和运维能力这几样。远程前沿模型通常在复杂推理、工具使用和长 Context 上更强，接入也快，代价是数据外发、网络和价格依赖。本地模型的数据控制和离线能力更好，固定负载下成本可控，但你要养显存、量化、推理服务和模型更新这些运维。

任务也可以分层来放：敏感代码的初筛、Embedding 或简单分类放本地，复杂规划交给远程模型；或者干脆所有内容留在本地，只把脱敏摘要发到远端。无论哪种摆法，Harness 都要记录实际模型和能力，不能路由之后还声称用的是用户最初选的那个模型。

Pico 支持不同 Provider 和自定义 OpenAI\-compatible endpoint，但能接本地模型，**不等于小模型在所有 Agent 任务上都可用**。是不是真能用，要用相同的任务和 Verifier 去测工具遵循、长任务稳定性和成本。

#### 模型切换时，怎样保证行为不会突然变化？

先说句实话：完全不变我保证不了，**能做的只有把变化显式化，再用契约去约束**。不同模型对 System Prompt、Tool description、并行调用、结构化输出和错误反馈的敏感度不一样。切换之前应该先做 capability probe，确认 Context 上限、Tool Calling、流式、Reasoning、JSON schema 和缓存支持，然后跑一组有代表性的回归任务。

运行时要把 requested model 和 actual model 都记下来，**fallback 不能静默**。Prompt 和工具 schema 尽量遵循通用格式，同时给 Provider adapter 留出能力分支。对高风险流程还要限定允许模型，不是任何 fallback 都能接受。

Pico 的 Call Record 区分 requested、attempted、actual 和 accounting 身份，就是为了避免"配置模型"和"实际执行模型"混为一谈。不过行为兼容仍然要靠评测来证明，抽象层本身证明不了模型等价。

### Provider 超时、限流和错误应该怎样重试？

重试之前先分类。连接重置、部分 5xx 和明确的限流，通常可以有限重试；认证失败、配额耗尽、无效请求和内容策略拒绝，一般不应该原样重试。退避要有抖动并尊重 `Retry-After`，同时受 Turn 总时间和成本预算的约束。

**每次物理 attempt 都要独立记录**，不能把三次失败加一次成功合并成"一次成功调用"。这样才能算出真实的延迟、费用和故障率。流式中断还要判断是不是已经产生了用户可见的内容，避免重复输出。

Pico 在 Provider attempt 层记录重试和 fallback，失败最终可以进入 `provider_failed`。不过能不能换模型，还要看任务语义：**用一个低能力 fallback 换来一段文本，不一定比明确失败更好**。

#### Fallback 模型应该自动切换吗？

我的结论很直接：**低风险、结果可验证的任务可以自动 fallback；安全、合规或质量要求高的任务，要谨慎得多**。因为 fallback 一换，Context 上限、工具支持、推理能力、价格和数据地域都可能变，而且用户可能只授权了某一个 Provider。

所以我倾向为每个任务声明最低能力和允许的 Provider 链，fallback 之前检查剩余预算、工具兼容和数据策略，结果里记录实际模型。如果切换之后必须重新格式化 Prompt 或 Tool schema，最好从安全的 Checkpoint 重启当前 Iteration，而不是盲目续接半个流。

Pico 支持 Provider routing 和 fallback 记录，但不会把"最终有一个模型回答"自动定义为成功。**任务 Verifier 和用户策略仍然拥有最终判断**。

#### 模型路由应该依据什么？能不能让一个模型自己选模型？

路由可以依据任务类型、敏感性、Context 长度、工具需求、延迟 SLO、价格和历史成功率。简单提取用小模型，复杂规划用强模型，长 Context 或多模态任务就选对应能力的模型。**模型自选可以作为一个信号，但最终策略应该由 Runtime 控制**，因为模型看不到完整的成本、权限和健康状态。

路由器还要防两件事：训练数据泄漏和自我强化。某模型历史上老被分到简单任务，成功率自然高，不代表它适合复杂任务。应该用分桶评测和探索比例来校准，并且记录 requested 与 actual 身份。

Pico 有可选的 model Router 和 Provider fallback，可以把上面这些约束和记录落实到位。

![25\-model\-routing\.png](<原始文件包/Pico 面试逐字话术稿/图片和附件/25-model-routing.png>)

### CallEfficiency 是什么？为什么不继续叫 TokenWise？

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 主路径通过运行时组装统一安装 CallEfficiencyProvider decorator，调用观测和缓存计划进入实际 Provider 路径。旧 CallEfficiencyEntry／Summary 仍在 compat。源码中保留历史 token_wise 兼容模块，不代表个人经历过原稿所述迁移。[组装](repoagent/harness/cli/_runtime_assembly.py) · [实现](repoagent/harness/call_efficiency/__init__.py)
<!-- REPOAGENT-NOTE-END -->

`CallEfficiency` 是当前 Runtime 在 Provider 调用边界上的策略和证据层。它在 Tool 过滤之后准备最终请求，按 Provider 语义归一 Usage、估算成本，并为每个物理 attempt 追加一条 Call Record。共享的 Runtime Assembly 安装一个稳定的 Provider decorator，主 Agent、Subagent、Context 这些 Runtime 组件可以共用它。

`TokenWise` 是历史兼容表面：旧 Strategy、schema 和 benchmark lineage 都保留着，但它不再拥有当前 Runtime 的请求。区分名称，是为了避免旧模块和当前职责互相混淆。

这里其实藏着一条工程原则：**成本治理不能只在事后统计 Token**，它必须知道最终实际请求、缓存策略、retry/fallback 和真实模型。否则聚合数字看着精确，实际连"一次逻辑调用到底包含多少物理请求"都说不清。

#### Pico 的 CallEfficiency 有哪些模式？

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 主路径现在支持三种模式和 Anthropic 显式 cache breakpoint；observe 只观测，optimize 按实现条件规划请求。Provider 自动缓存与显式策略仍需分别说明，缓存效果需要独立实验。
<!-- REPOAGENT-NOTE-END -->

现在是三种模式：`off`、`observe` 和 `optimize`。**`observe`**** 是默认模式**，它记录标准化 Usage 和 Call Record，但不改变请求；`optimize` 在 observe 基础上，可以管理 Anthropic 的显式 cache breakpoint；`off` 就是关掉相关策略。

DeepSeek、OpenAI 这些 Provider 的缓存，仍然由 Provider 自动处理，Pico 不会假装自己创建了 KV Cache。即使在 `optimize` 模式下，Cache policy 也只影响请求布局或显式标记，不能改变任务语义，也不能绕过 Context 预算。

我个人更认同先 observe、再 optimize 的节奏。**没有完整 Usage、模型身份和任务 Verifier 就直接宣传成本优化，容易把 Provider 价格变化、缓存暖机和任务失败混进收益里**。

#### Prompt Cache 的命中率和节省金额怎样计算才可信？

先定义清楚什么叫可缓存前缀、Provider 上报的 cache read/write 是什么语义，再按每个物理 attempt 记录 fresh input、cache read、cache creation、output 和 reasoning Token。命中率可以按缓存 Token 占稳定前缀或总输入的比例来算，**但分母必须说清楚**。成本则使用当时的模型价格表，未知的价格标成 `None`，不能当作零。

实验要控制请求顺序、前缀稳定性、冷启动和并发。更关键的是，**成本要按"每个 Verifier 通过的任务"来比较，不能只看单次调用**，因为一个便宜但失败的 treatment 没有任何业务价值。真实账单和估算也要区分开。

Pico 可以重估成本、记录 Provider Usage，但它不是 Provider 账单系统。任何节省数字都要绑定模型、日期、工作负载和定价，不能外推成通用生产降本。

#### 为什么 Usage 缺失不能按 0 处理？

因为语义完全不一样：**0 表示确定知道没有消耗，缺失表示不知道**。把缺失按 0 处理，会系统性低估失败调用、fallback、缓存写入，还有那些不支持 Usage 的 Provider，最后某个 treatment 会看起来比实际更省。

Pico 的 Call Record 用完整性标志和 `None` 来表达未知价格或不完整 Usage；聚合的时候，只要里面含未知项，就不能给出一个确定的总成本。评测层发现关键 Usage 缺失，应该降低 `measurement_valid`，而不是照常出正向 Claim。

这其实是数据工程里最基本的语义：**unknown 不等于 zero**。Agent 评测里尤其容易踩坑，Provider 恰好报错、Usage 恰好缺失时，按 0 处理等于在"奖励失败"，所以必须把完整性放进 Gate。

### 同一个模型在 Pico、Claude Code 或其他 Harness 里表现不同，怎么证明差异来自 Harness？

只能做受控实验：固定模型版本、Provider 参数、任务、Workspace、工具能力、预算和 Verifier，基线和 treatment 之间只改一个 Harness 轴，比如 Context 策略、工具 schema 或缓存策略。每个条件要跑足够的 Trial，并把 Provider 故障与产品失败分开记录。

**如果同时换了模型、Prompt、工具和 Sandbox，结果就算变好也无法归因**。还要小心别把 Harness 自带的隐藏工具或预载知识当成模型能力。Trace 要能重建模型实际看到的 Context 和调用轨迹。

PicoBench 的 Comparison Block 和 Treatment Axis 就是为这种 one\-axis 对比设计的。它没法自动消除所有随机性，但至少能让"差异到底来自哪里"比一次 Demo 更可辩护。

#### Pico 的 Provider/Tool 契约实验能证明什么？

先说能证明什么。材料里允许使用的一组候选版本证据，是 4 个离线成对 Trial：使用相同的 Plan、任务、Verifier 和确定性 Fake Provider，treatment 只改变自定义 Provider/Tool 契约。基线 4/4，treatment 4/4，总计 8/8 通过。

它证明的是：在那套固定离线任务里，新增契约没有破坏功能，实验结构和 Verifier 能跑起来。**它不能证明 treatment 优于基线**，因为两边通过率相同；也不能证明真实网络模型或生产工具的行为。另外，真实 Provider 的证据现在还是 provisional，里面包含 blocked\-provider 和 Provider failure，不能拿来下正向性能结论。

所以我把这类结果叫作 **contract/non\-regression evidence，而不是"性能提升"**。负责任的实验报告，结论可以写成"接线正确，但尚无优越性证据"。

#### 怎样降低 Agent 成本，又不伤害任务效果？

我按优先级做四件事。第一，减少无效调用：工具定义写清楚、做循环检测、一次拿够信息。第二，优化 Context：稳定高信号的前缀、按需检索、紧凑的 Tool 输出，但前提是任务不能劣化。第三，用 Prompt Cache 和模型路由。第四，才是压缩输出、降低采样或减少 Trial。

**所有优化都以 Verifier 通过的任务为分母**，同时记录总的 Provider attempts 和 Tool 成本。不能只比较 Token，因为某个策略少调用一次模型却导致任务失败，真实的 retry 成本可能更高。

Pico 的 Context/Tool disclosure 证据就是警示：可见的 Schema Token 降得很多，任务通过数却从 23/24 退到 20/24；主 campaign 还因为一个 Pair 缺少完整 Usage 而 measurement invalid。**成本优化必须和质量 Gate、证据完整性绑定，而不是独立 KPI**。

## 十、Tracing、Artifact 与 Delivery

### 当用户说“任务完成了”时，Pico 怎么证明这句话？

我一般会把“完成”拆成好几层来看。模型自己说完成，那只是语言信号。Turn 是不是正常结束，是一层；工具和状态是不是真的成功，是一层；任务 Verifier 有没有通过，是一层；结果有没有送到外部平台，又是另一层。所以 Pico 要证明任务完成，不是靠一段漂亮的最终文本，**而是把这些事实连接起来，让它们能够互相印证**。

这里要先分清楚：普通日志回答的是“某个进程打印了什么”，Agent Observability 要回答的是，一个用户目标经过了哪些模型判断和工具动作，改变了哪些状态，最后在哪一层成功或失败。一次 Agent 执行会跨 Provider、Tool、Session、Context、Memory、Checkpoint 和 Delivery，而这些域不在同一个事务里。所以**可观测性的核心不是日志数量，而是能不能重建一次 Turn 的因果链**。

日志、Trace、Metric 和 Artifact 也要分工。日志更接近某段代码打印的文本；Trace 表达跨组件的因果关系；Metric 用来聚合跨运行的趋势；Artifact 是可以独立检查的产物，比如 diff、测试报告、发送回执或评测 Record。如果只把 Prompt、模型回复和异常写进文件，就分不清逻辑调用和物理重试，也解释不了“Agent 回复了，用户却没收到”。

一条有用的 Agent Trace，至少要有稳定的 Turn、Session 和 Trace 身份。它要记录请求模型与实际模型、每次 Provider attempt、Context 路径和回退、Tool 名称与参数摘要、执行结果、Session 提交、Memory 命中或写入、Turn 终态和 Delivery outcome。敏感内容不一定全量进 Trace，但关联键和完整性标志必须保留。这样既能从用户目标往下排查，也能从某个 Tool 或 Provider 的故障反向找出受影响的 Turn。

状态域的分离会决定诊断方式。用户看到最终文本，不代表任务 Verifier 通过；`completed_with_tool_failure` 不是“完全正常完成”；Session 已保存但 Myna store 失败，会留下部分持久成功；Delivery 的 send exhaustion 发生在 Agent Turn 之后，不能改写成模型失败。所以 Observability 要让这些部分成功可见，而不是用一个绿色状态把它们盖过去。

Reasoning 的记录也要克制。完整的私有思维链不是可靠的解释，里面还可能有敏感内容、Provider 不稳定字段，以及很高的成本。更实用的做法是记录可公开的 reasoning summary、decision event、Tool proposal、拒绝原因和外部证据。追踪的目标是重建系统的决策边界，不是无条件保存所有隐藏推理。

Tracing 本身有资源和隐私成本，所以要取舍：失败和高风险动作优先保留，成功的短任务可以采样或只留摘要；字段要脱敏；Artifact 要带 digest 和访问控制；保留期也按数据类型区分。我们有一份冻结证据：本地 overhead campaign 包含 1,000 个 Pair；启用臂保留 1,000 条 Trace 和 6,000 个 span，关联率是 100%；禁用臂写入 0 字节。P95 从 2\.912ms 升到 5\.157ms，每个启用 Turn 写入 25,717\.2 字节。需要说清楚，这是冻结工作负载下的本地开销测量，不是“生产低开销”的保证。

Delivery 也有独立的语义。Pico 每个 Outlet 有自己独立的有界队列和 worker，避免一个慢的 Channel 拖住其他 Channel；`wait_idle()` 提供渲染屏障。但 `delivered` 最多表示平台 send API 按当前适配器语义接受了，不能证明用户已读。当前出站队列不持久，关闭时不能保证在途 retry 完成，Host 也没有完整接线 terminal delivery failure notice sink。这些缺口必须在 Trace 和产品承诺里明确可见。

成熟平台比如 LangSmith、OpenAI Tracing 和 OpenTelemetry 都很有价值，生产系统应该尽量接标准生态。Pico 保留本地 Trace，是因为它 local\-first，还需要在没有外部服务时表达 Spine Turn、Lane、CallEfficiency、Myna 和 Delivery 这些领域语义。更成熟的方向不是二选一，而是保留 Pico 自己的语义约定，再导出到 OTel 或外部平台。这里也要讲清楚边界：当前 Pico Trace 只是本地、尽力记录但不阻塞业务的机制，不是分布式、不可篡改的审计账本。

最后还要记住，Observability 不能替代 Evaluation。Trace 说明模型看到了什么、调用了什么、在哪一步失败；Eval 判断任务目标有没有满足、treatment 是不是优于 baseline、结论有没有发布资格。可以从失败 Trace 里归纳新的 Task 或候选规则，但改动必须回到独立 Verifier 和成对实验，不能“看了几条轨迹觉得更好”就上线。

所以一份可靠的执行证据，应当让人从用户目标追到 Provider attempt、Tool 结果、Session 提交、Turn 终态和 Delivery outcome，同时守住隐私、采样和开销边界。**Agent 的可解释性来自可关联的外部事实，不来自模型对自己行为的一段漂亮叙述。**

### Agent 系统为什么特别需要 Tracing？普通日志不够吗？

普通日志是按时间输出局部事件，查异常很好用。但 Agent 任务里有很多次模型调用、工具动作、Context 选择、后台任务和交付步骤，你首先得知道它们属于哪一次 Turn、父子关系是什么、每一步用了什么状态。光靠关键词搜索，**很难把因果链重建出来**。

**Trace 用稳定的 trace id 和 span 关系表达一次执行**：谁触发的、什么时候开始的、调用了哪个 Provider、模型要求了什么 Tool、Tool 有没有失败、Context 走了哪条路径、最后 Turn 和 Delivery 各自怎么样。日志仍然有价值，但它是补充文本，不该承担所有关联语义。

具体到 Pico，我们以 `spine.turn` 作为根侧生命周期，Agent Loop 里有 `session.turn`，Provider、Tool、Memory、Skill 和 Delivery 这些 span 共享 lineage。这样就能从用户请求一路追到平台发送。不过要说明：当前 Tracing 是本地 best\-effort，不是分布式生产 APM，也不是不可篡改的审计系统。

#### Trace、Log、Metric 和 Artifact 有什么区别？

我的区分是这样的：Trace 描述一次请求或任务内部的因果路径；Log 记录离散事件和诊断文本；Metric 聚合大量运行的趋势，比如成功率、延迟和 Token；Artifact 保存可供人或 Verifier 检查的产物，比如补丁、测试报告、回执、Manifest 和最终报告。

**这四者不能互相替代**。Trace 告诉我哪一步失败，Log 提供错误细节，Metric 说明这类失败是不是在增加，Artifact 证明任务实际生成了什么。**Agent 的最终文本最多算一个 Artifact 或事件，不应该成为唯一证据**。

Pico 把本地 span、Call Record、Session、PicoBench records 和 Evolver artifacts 分开管理。工程上要用共同 ID 关联它们，但不能假设它们在一个事务里同时成功。

#### Pico 的一条 Trace 里你最关心哪些 span 和字段？

我最关心六类。根 Turn span，记录 session、origin、source、终态和时长；Context span，记录 Fast/Slow/Fail\-Safe 路径、预算、fallback reason 和 Memory/Skill 命中；Provider span，记录模型身份、attempt、Usage、缓存和错误；Tool span，记录 tool name、call id、风险、时延和显式失败；持久化 span，记录 Session、Memory 或 Checkpoint 的结果；Delivery span，记录 Outlet、重试和 Outcome。

字段的要求是**低基数、有稳定语义**，原始 Prompt 或 Tool 内容不应该默认全部进属性。需要详细内容时，用受控 Artifact 加 digest 关联。父子关系还要覆盖 Subagent，保证一个后台结果能追溯到它的 spawn 来源和后续回流的 Turn。

目前 Pico 已经有自己的 semantic conventions，但没有声称实现完整的 OpenTelemetry exporter。未来可以把这些字段映射到 [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)，同时保留 Pico 特有的 Turn 与 Delivery 语义。

#### 为什么不直接用 LangSmith、OpenAI Tracing 或 OpenTelemetry，还要自己做？

外部平台很有价值，我不否定它们。自己维护本地 Trace，理由主要是所有权和语义：**Pico 是 local\-first，需要在没有外部服务时也能记录**；同时它要表达自己的 Spine Turn、Lane、CallEfficiency、Myna 和 Delivery 状态，通用平台未必天然有这些字段。

但自定义不应该变成封闭生态。合理的路径是**内部先定义稳定语义，再提供 OpenTelemetry 或第三方 processor 导出**。OpenAI Agents SDK 的内置 Tracing、LangSmith 的轨迹分析、OpenTelemetry 的标准属性，都可以作为集成对象。

也要摆正定位：Pico 当前的本地 Tracing 适合调试和评测关联，不是“比所有成熟平台更好”。如果进入生产，我会优先补标准导出、采样、访问控制和多进程安全，而不是继续扩展私有 Viewer 的功能。

### 怎样把 Provider、Tool、Session、Turn 和 Delivery 关联起来？

做法是：先有一个端到端 trace id，再为每个逻辑对象保留稳定 ID，也就是 Turn id、session key、Provider attempt id、Tool call id、Delivery id 和 parent id。Provider 每次重试或 fallback 都创建独立 attempt，但仍然挂在同一个逻辑调用或 Turn 下面；Tool Result 携带原来的 call id；Session 保存记录 Turn 边界；Delivery 保存源 Turn 和 Outlet。

**关联的重点是不要靠时间去猜**。举个例子：同一会话里连续两次调用同一个 Tool，如果没有 call id，结果就可能错配；Turn 完成之后平台再重试，如果没有 Delivery id，你就分不清是 Agent 重复发送还是渠道在重试。

Pico 的执行上下文和 Call Record 已经携带 Session、Trace 和 attempt 信息，Spine 与 Delivery 也有独立事件。**它仍然不等于跨状态域的事务，但至少能把部分成功和失败还原出来**。

#### 如果用户说“Agent 明明回复成功，为什么任务没完成”，你怎么排查？

我先确认“回复成功”到底是指哪一层。第一看 Turn terminal，是 `completed`、`completed_with_tool_failure` 还是其他状态；第二看任务有没有 Verifier，最终文件、测试或回执是否满足验收；第三看 Tool 轨迹，有没有参数错误、超时、被拒绝，或者结果被模型误读；第四看 Session 和 Memory 有没有持久化，避免文本只出现在流式 UI 里；第五看 Delivery，平台调用成功不代表用户收到，也可能发出去的是错误版本。

然后回放 Context：检查关键要求有没有进入窗口、Curator 有没有 fallback、工具 schema 是否可见。如果模型文本声称“测试通过”，而 Trace 里根本没有那次测试 Tool Call，就可以直接判定这是自述，不是证据。

这种排查方法的核心，是**按状态所有权逐层验证，而不是只读最终自然语言**。

### Tracing 会不会产生太多数据？怎么控制？

会，而且这是真问题。Agent 一次 Turn 可能包含多次 Provider 请求、几十个 Tool 事件和大批输入输出，原样记录会迅速放大存储，也放大隐私风险。控制手段包括采样、分级、截断、digest、按需 Artifact 和保留期。

我的默认策略是：**全量保留结构化生命周期和错误元数据，对成功的低风险内容做采样**；失败、取消和安全事件提高采样率；大的 Tool 输出存成 Artifact，span 里只记大小、digest 和引用；原始 Prompt 和 Reasoning 默认不保存，调试模式要显式开启。

另外还要评测 Tracing 自身的开销和失败隔离。Pico 选的是 non\-interfering：**Trace 写失败不影响 Turn，这保护了可用性**。但也意味着，关键审计不能只依赖这一份 best\-effort 数据。

#### Reasoning 或思维链应该进入 Trace 吗？

默认不应该把完整的隐藏思维链当成生产 Trace 的要求。它可能含敏感信息、未经验证的猜测，还消耗大量 Token，而且不同 Provider 对 Reasoning 的暴露方式不同。调试真正需要的是可观察的决策证据：模型选了哪个 Tool、参数是什么、看到了哪些结果、状态怎么变化的。

可以记录 Provider 返回的公开 reasoning summary，或用户可见的说明，但要遵循数据政策和 Provider 条款。对评测来说，最终产物和动作轨迹通常比内部推理文本更稳定、更可验证。

Pico 可以流式处理 Reasoning 事件，但这不意味着所有 Reasoning 都要永久写盘。**可观测的目标是解释行为，不是收集模型的每一个内部 Token**。

### DeliveryHub 为什么要给每个 Outlet 单独队列？

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 主路径 DeliveryHub 是 **非持久化的每 outlet 内存队列与有限重试**，可按原稿讲慢渠道隔离和进程退出后的交付边界。目录 SQLite 回执与 DurableDirectoryDelivery 仅属于 compat，不能混作新主路径的保证。[主路径](repoagent/harness/spine/delivery.py) · [旧回执](repoagent/channel_receipts.py)
<!-- REPOAGENT-NOTE-END -->

核心是故障隔离：**一个慢的或出故障的渠道，不能拖住其他 Outlet**。同一个 Outlet 内部保持 FIFO，跨 Outlet 各自承受自己的 backpressure 和重试。对 CLI 来说，还可以等队列 idle，形成渲染屏障；Gateway 则根据 Channel 能力发送文本和媒体。

这是故障隔离，但队列本身有边界，要讲清楚：容量有限；出站状态不持久，关闭可能取消在途重试；平台若永久拒绝，消息会被 drop；retryable 错误按策略重试。而且这些往往发生在 Agent Turn 早已完成之后，**不能靠回滚模型执行来解决**。

对比生产消息系统，通常还需要持久 Outbox、dead\-letter、幂等 key、监控和人工补发。Pico 当前的实现说明了 Delivery 应该被独立建模，但还没有完成全部可靠消息语义。

![26\-delivery\-queues\.png](<原始文件包/Pico 面试逐字话术稿/图片和附件/26-delivery-queues.png>)

#### `delivered` 能说明用户已经看到结果吗？

不能。`delivered` 最多说明 Outlet 调用平台发送 API 成功，或者说平台接受了消息。平台可能异步失败，用户可能根本没打开，可能被权限过滤，可能收到但没理解，而且它不包含业务结果的确认。

如果业务需要更强的语义，就应该区分 accepted、server\-delivered、client\-received、read 和 acknowledged，再按平台实际能力去实现。不是所有 Channel 都提供已读回执，系统不能伪造它没有的东西。

Pico 当前的 Delivery Outcome 只描述 Runtime 可观测的边界。我坚持一个原则：**观测边界决定承诺边界**。看不到用户已读，就不能把 API 2xx 说成用户已完成处理。

### Trace 怎样反过来推动 Agent 优化？

第一步是把失败轨迹聚类：Context 缺失、工具选择错误、参数错误、重复调用、Provider 故障、权限拒绝、Verifier 失败、Delivery 失败，归好类。然后挑占比高、又可控制的失败，提出单一 treatment，比如改 Tool 描述、调整 Context 保护规则、增加幂等查询。**用相同的任务集做成对实验，看任务通过率和成本，而不是凭几条 Trace 就去改 Prompt**。

Trace 还能发现“成功但低效”的模式，比如重复读文件、Subagent 开得过多、缓存前缀不稳定。不过任何自动建议都要先进候选和评测流程，不能直接改 Runtime。

具体到 Pico，PicoBench 和 Evolver 就是在尝试把轨迹、候选、Verifier 和 Claim Gate 连起来。当前 Evolver 还是 Beta，activation 也待人工确认。我的判断是：**反馈闭环的价值在于可验证的改进，而不是系统会自己改几处，就叫“自进化”**。

#### Observability 和 Evaluation 的边界是什么？

**Observability 回答的是“这次运行发生了什么”，Evaluation 回答的是“在预定义标准下，它做得好不好”**。Trace、Log、Metric 提供观察数据；Eval 还需要任务、环境、Trial、Verifier、对照和统计规则。

一条 Trace 显示 Agent 调用了测试工具，不能证明修复是对的；Verifier 检查测试和文件状态，才是结果判定。反过来，Eval 只给一个 fail 也不够定位，要靠 Trace 找原因。**两者应该通过同一个 trace id 和 Artifact 引用连起来，但不能互相替代**。

Pico 里，Tracing 属于 Runtime 能力，PicoBench 属于 checkout 中的评测模块。把两者分开，可以避免 Runtime 为了跑分去硬编码任务，同时让评测消费真实的执行证据。

![27\-observability\-evaluation\.png](<原始文件包/Pico 面试逐字话术稿/图片和附件/27-observability-evaluation.png>)

## 十一、Agent Evaluation：先验证任务，再治理结论

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 本章的固定任务、独立 Verifier、成对实验、失败分类和证据边界可以学习。正文的 PicoBench、Context、Myna、Provider 实验数值是原项目记录；介绍自己的成果时统一使用末尾“本项目业务与 Benchmark 补充”。自己的 HAL Mini 50 题已逐份核对官方报告，不是只凭 Agent 最终文本计分。
<!-- REPOAGENT-NOTE-END -->

### PicoBench 怎样从一次 Trial 走到一条有资格发布的评测结论？

我会把 PicoBench 的工作拆成两步：第一步，验证任务是不是真的完成了；第二步，判断手头这些证据够不够支撑一条对外结论。我理解的 Agent Evaluation，不是给最终回答打个分，而是在明确任务、环境、预算和失败条件的前提下，检查系统到底有没有完成目标，以及我们有没有资格说它变好了。

最简单的评测，是人工看一眼答案“像不像对”，或者让另一个 LLM 打分。它做早期体验可以，但问题不少：被测 Agent 可以自述“已经完成”；Judge 可能被答案里夹带的指令操纵；一次成功也反映不了非确定性和 Provider 波动。可审计的评测，第一步得定义一份 Task contract，把初始 Workspace、用户目标、允许的 Tool、预算、终止条件、隐藏验收和不可退化项都写清楚。

再厘清几个对象。Task 定义问题；Trial 是某个条件下的一次执行；Trace 解释过程；Verifier 读取 Trial 结束后的外部状态并裁决结果；Eval Harness 负责隔离环境、运行多次、保存 Record、汇总并执行 Gate。这里最关键的一条是：Verifier 必须由 Agent 外部的**父控制面拥有**。换句话说，Agent 不能修改验收代码、标准答案或原始 Artifact，否则它就不只是在被评测，而是在参与改分。

Verifier 怎么设计？我的原则是确定性优先，先检查能用机器判断的事实，比如文件、测试、schema、digest、数据库状态或发送回执。LLM\-as\-a\-Judge 更适合风格、解释完整性、开放研究质量这些维度，但必须放在硬约束之后，还要冻结 rubric、Judge 模型和输入，再用人工标注去校准偏差。最终文本可以评价沟通质量，但不能当主要完成证据。

因为结果有非确定性，所以要看分布，不能只看一次。一个条件要跑多次 Trial，报告 pass rate、失败类型和置信区间。这里 `pass@k` 和 `pass^k` 的含义不一样：pass@k 看重试 k 次里至少成功一次，衡量探索能力；pass^k 看连续 k 次都成功，衡量可靠性。比较 Harness treatment 时，最好做成对实验：Task、Workspace、Provider 设置、预算和 Verifier 全都一样，只改变一个 Treatment Axis。要是模型、Prompt、Tool、Context 一起换，结果就没法归因了。

失败分类决定统计分母。模型选错 Tool、产物不符合验收，这类 Product failure 留在任务分母里；Provider failure 和 Infrastructure failure 可能污染比较，但不能事后选择性删除。还有一点：`skipped`、`inconclusive`、`provider_failure`、`infrastructure_failure` 是结果状态，不是证据等级。一次实验可以完整运行并得到负结果，也可以结果看起来很好，却因为证据缺失而无法比较。

PicoBench 用三个状态把结论治理拆开。`ship_complete` 回答任务、Record 和报告有没有按合同生成；`measurement_valid` 回答 Usage、Pair 和环境够不够解释结论；`positive_claim_eligible` 回答结果有没有达到预设门槛，有没有资格写成正向提升。实验完整、测量有效，不代表一定有正向收益；反过来，结果看起来很好，只要测量不完整，也不能发布。

当前的 Context campaign 就是典型案例：216/216 个 Trial 到达终态，260/260 个 Retrieval Case 可测，说明执行层基本完整；但 120 个 Context Pair 里只有 119 个有效，主 campaign 因为 Usage 缺口判了 `measurement_invalid`。单独可测的 Tool disclosure 轴呢，可见 Schema Token 减少了 93\.4513%，task pass 却从 23/24 退到 20/24，所以正向 Claim 必须关闭。Myna 的证据也要克制：确定性 Pack 完成 144/144 个 Trial，并减少 50\.0% 的仓库读取；真实 24\-Pair Pack 的 pass delta 点估计是 20\.8333 个百分点，但区间是 0 到 41\.6667，所有正向 Gate 都失败。评测的价值，正是允许结论是“**不足以证明**”。

报告必须从不可变 Record、Manifest、Artifact digest 和 Verifier 输出重建，不能重新跑一次任务再假装是原报告，因为模型、Provider 和环境都会漂移。Benchmark 还要防答案泄漏：sealed 数据和 Agent 隔离，每个 Trial 重置 Workspace，Memory namespace 隔离，Verifier 不可写。半开放任务适合分层 Gate：功能、安全和兼容是硬条件；性能做重复测量；维护性、解释质量这类再用 rubric 打分。

PicoBench、Eval Engine、Evolver 也要分清。PicoBench 是 checkout 里的评测 Harness；Eval Engine 源码存在，但当前 Runtime Assembly 没有把它挂成公开运行功能；Evolver 消费 benchmark contract 来治理候选。目录存在，不等于 Runtime 已经启用。

所以我的完整答案是一句话：Agent Eval 从 Task contract 和外部状态出发，由 Agent 外部的 Verifier 检查结果，用多次 Trial 描述非确定性，把 Product、Provider、Infrastructure 三类 failure 分开，再到报告层判断实验完不完整、测量有没有效、正向结论有没有资格发布。它的产物不是一个孤立分数，而是一条从任务合同到可发布结论的**证据链**。

### Agent 应该怎么评测？为什么不能只看最终回答？

先说为什么不看最终回答。Agent 会调用工具、修改环境、产生副作用，所以最终文本很可能和真实世界状态不一致。模型可以说“文件已修改、测试已通过”，但真正该检查的是文件内容、命令退出码、数据库状态、回执或 digest。因此我理解的 Agent Eval，主对象是**任务结果和执行轨迹**，不只是语言质量。

怎么评测？我会把一个 Eval 定义为：冻结任务和初始环境，运行一个或多个 Trial，保存 Trace 与 Artifact，用独立 Verifier 判定结果，再按预先声明的指标和 Gate 汇总。开放式答案可以加 LLM grader，但只作为补充，不能替代可确定检查的部分。

PicoBench 落地的对象是 Plan、Task Pack、Trial、Comparison Block、Treatment Axis、Trial Record、Deterministic Verifier 这一套，作用就是**把模型自述和任务证据分开**。这套方法不限于 Pico，可以迁移到任何 Agent Harness。

#### Task、Trial、Verifier、Trace 和 Eval Harness 分别是什么？

我一个个说。Task 描述要完成的目标、初始环境、约束和验收标准；Trial 是某个 Agent 配置在该 Task 上的一次运行；Verifier 在运行后独立检查结果；Trace 记录过程；Eval Harness 负责准备环境、执行 Trial、收集 Artifact、调用 Verifier 和汇总。一句话概括：Task 说“要做什么”，Trial 说“跑了一次”，Verifier 说“结果算不算数”，Trace 说“过程发生了什么”，Eval Harness 说“整个流程谁来管”。

还要分清两个 Harness：Agent Harness 是被测系统，负责模型、工具、Context 和状态。**两类 Harness 必须分开**，否则被测 Agent 可以修改自己的评分代码，或者评测逻辑只能依赖最终文本自报。Anthropic 公开的 [Agent Eval 实践](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) 也强调 task、trial、grader、transcript 和 outcome 的区分，方向跟这个一致。

PicoBench 在仓库 checkout 的 benchmark 模块里，不是安装 wheel 中的普通 Runtime 功能，也不替代 Agent Loop 或 Evolver。准确地说，它是**测试 Harness 的 Harness**。

#### 什么是确定性 Verifier？为什么说它应该是 parent\-owned？

确定性 Verifier 用可复跑规则检查产物，比如文件是否存在、AST 是否满足约束、测试是否通过、JSON 字段是否正确、回执 digest 是否匹配。说它应该 **parent\-owned**，意思是由评测进程或父控制面拥有，被测 Agent 不能编辑、替换，也不能决定自己的评分。

这是防止 **self\-report 和 reward hacking** 的基本边界。Agent 可以提交证据，但不能说“我认为自己通过了”就拿到分数。实现上，Workspace 最好把任务文件和 Verifier 隔开，或者在只读环境里执行 Verifier。

PicoBench 的 Verifier 检查文件、结构化状态、receipt、digest 和 Runtime evidence；最终文本和 LLM judge 最多是诊断。遇到没法完全确定性判断的任务，就把确定的部分先交给机器验证，剩余的主观质量再让校准过的 grader 评。

#### 为什么最终文本不能作为主要 Verifier？

原因很直接：最终文本由被测 Agent 自己生成，天然带着**自我评分偏差**。它可能漏报失败、误读工具结果，甚至根本没执行动作就声称完成。语言听起来更有信心，不代表环境状态正确。

最终文本能检查的是沟通质量，比如有没有解释限制、有没有给用户明确的下一步。但任务完成，必须**由外部状态判断**：Coding 任务检查 diff、编译和测试；消息任务检查发送 receipt；数据任务检查输出 schema 和数值约束。

Pico 在实现里把模型最终文本、Turn terminal、task verifier、delivery success 这四层分得很开。面试时能把四层分开讲清楚，基本就说明理解了 Agent Eval 的核心。

#### 什么时候可以使用 LLM\-as\-a\-Judge？

当结果包含风格、完整性、解释质量、开放研究综合这些没法完全用规则判断的维度时，可以用。但用法有个顺序：先用确定性 Verifier 把硬约束过滤掉，再让 Judge 给主观维度打分。

操作上，Judge Prompt、rubric、模型版本和输入都要冻结，并用人工标注集校准一致性、偏差和阈值。还要防被测输出操纵 Judge，比如答案里夹带评分指令；做法是隔离数据和评分指令，限制 Judge 能看到的信息。多 Judge 或 pairwise 比较能减小偏差，但成本更高，**不是自动真相**。

Pico 里 LLM judge **只当诊断用，不拥有最终任务裁决**。这个选择偏保守，但可解释。

### FakeModelClient 有什么用？它能证明模型能力吗？

FakeModelClient 的作用，是确定性地脚本化模型响应，用来测 Harness 自己拥有的行为，比如 Tool 分发、重试、Session、流式、取消、终态和错误处理。它把网络和模型随机性都消掉了，失败可以稳定复现。

但它**不能证明真实模型能力**：证明不了真实模型会选择正确 Tool、理解任务或在开放环境中泛化，也证明不了 Provider 协议在真实网络下完全一致。OpenAI Agents SDK 当前也提供 [provider\-neutral scripted model 测试](https://openai.github.io/openai-agents-python/testing/)，并明确要求外部模型、网络和 Sandbox 行为用真实集成环境验证。

所以我给 FakeModelClient 的结论命名为 **runtime contract evidence**，不叫 Agent intelligence evidence。两类测试都需要，只是回答的问题不同。

#### 为什么真实 Provider Eval 仍然需要确定性离线测试？

因为真实 Provider 能覆盖模型行为、网络、计费和协议细节，但它非确定、昂贵、可能限流，还容易把基础 Runtime bug 和模型波动混在一起。离线测试先把状态机、错误边界和 Verifier 验证掉，真实实验就能更聚焦。

我一般用一个金字塔来想：单元和属性测试验证组件；Fake Provider 端到端验证 Harness；少量真实 Provider smoke 验证协议；成对真实任务评测效果；上线后再看受控生产指标。**任何一层失败都不能用另一层掩盖**。

落到 Pico 上：Provider/Tool 候选的离线实验 8/8 通过，**只说明契约路径没有回归**。真实 Provider 的 evidence 如果被 blocked，或发生 Provider failure，要单独记录，不能硬归成产品失败或正向结果。

#### 为什么 Agent Eval 通常需要多次 Trial？

因为模型输出有随机性，工具和网络也会波动。一次通过，说明不了稳定成功；一次失败，也证明不了能力不存在。多 Trial 能估计每个 Task 的成功概率、方差和尾部风险。

Trial 数怎么定？由成本、预期差异和决策风险决定。内部快速回归可以少跑；发布 Claim 前，要**预先算好统计功效和置信区间**。还要固定温度、模型版本、环境和预算，每次 Trial 都保存，不能只留最好的那次。

成对设计能降低任务难度差异的影响，但小样本仍然讲不了宏大结论。Pico 的证据纪律强调完整 Trial Record 和不可变 Manifest，正是为了**防止事后挑样本**。

#### pass@k 和 pass^k 有什么区别？

pass@k 是同一个 Task 尝试 k 次、至少一次成功的概率，适合“允许多次尝试、只要找到一个可用解”的场景，所以 k 越大，它通常越高。pass^k 是 k 次都成功的概率，关注一致性，k 越大通常越低，适合用户每次都希望可靠的产品。

举个数字例子：单次成功率是 75% 的话，三次里至少成功一次的概率很高，但三次全成功只有 `0.75^3`，约 42%。Coding benchmark 常看 pass@1 或 pass@k；客户\-facing 的 Agent 还要关注 pass^k、失败严重度和重试成本。

不能只报 pass@10 去掩盖单次体验差，也不能把多次里最好的结果当成真实线上行为。**指标必须和产品允许的尝试次数一致**。

![28\-pass\-k\.png](<原始文件包/Pico 面试逐字话术稿/图片和附件/28-pass-k.png>)

### 什么是成对实验？为什么一次只能改一个 Treatment Axis？

成对实验，就是给同一个 Task 建立 baseline 和 treatment 两边，模型、Provider、预算、Workspace、工具、Verifier 这些条件保持一致，只改变要研究的那一个轴。这样结果的差异，才更可能**归因到这一个轴**。

如果 Context、Tool schema、模型一起换，treatment 成功了，你不知道是哪一项在起作用；失败了，也不知道怎么修。PicoBench 用 Comparison Block 和 Treatment Axis 把这个约束显式表达出来。Provider 或基础设施污染，应该**阻断这个 pair 的比较**，而不是偷偷补一条有利样本。

现实里完全控制很难，所以要记录模型快照、时间、价格和外部依赖，并用随机化顺序减少缓存和服务状态偏差。

![29\-paired\-experiment\.png](<原始文件包/Pico 面试逐字话术稿/图片和附件/29-paired-experiment.png>)

#### Provider failure、Infrastructure failure 和 Product failure 为什么要分开？

因为三类失败对产品判断的含义不同。Provider failure 是外部模型服务失败；Infrastructure failure 是评测环境、网络或准备阶段失效；Product failure 是 Agent 在有效环境里没有完成任务。

但**分开不等于把不利数据全删掉**。线上产品依赖这个 Provider 的话，Provider failure 对可用性仍有业务意义；在测 Harness 算法效果时，它又可能让成对比较失效。规则必须预先声明：哪些 Trial 重跑、哪些留在分母、哪些使 measurement invalid。

PicoBench 的原则是：产品失败留在任务分母，Provider 或 infra 污染按实验合同阻断比较，不能事后把失败包装成 skipped。证据类别和结果状态也不能互换。

#### `ship_complete`、`measurement_valid` 和 `positive_claim_eligible` 分别是什么？

`ship_complete` 回答实验所需的任务、Trial、Artifact 和报告有没有按合同完成；`measurement_valid` 回答数据够不够完整、成对比较有没有被 Provider 或基础设施污染、指标可不可解释；`positive_claim_eligible` 回答结果有没有达到预先设定的效果和统计门槛，能不能支撑正向对外结论。

**三者必须分开**。一个 treatment 明显退化，但所有 Trial 完整、数据有效，它可以 ship complete、measurement valid，却就是不具备 positive claim 资格。反过来，一次结果看起来提高很多，但 Usage 缺失或 pair 被污染，同样不能发 Claim。

PicoBench 的价值之一，就是允许实验得出“不提升”甚至“退化”的结论，而不把它当评测失败。**科学有效和营销好看不是同一个目标**。

### Pico 的 Context 候选实验为什么是一个好失败案例？

因为它把“实验完整”“测量有效”“指标好看”三件事拆开了。当前证据里的 Agent 应用 campaign：216/216 个真实 Provider Trial 到达终态，260/260 个 Retrieval Case 可测，说明执行和证据收集基本完整。但 120 个 Context Pair 里只有 119 个有效，缺的 Usage 证据让主 campaign 判了 `measurement_invalid`，所以主 campaign 的正向结论不能发布。

单独可测的 Tool disclosure 轴呢：可见 Tool Schema Token 估算下降 93\.4513%，但 task pass 从 23/24 退到 20/24。要是只宣传“Schema Token 少了 93%”，就把效率代理指标放到了真实任务结果前面；要是忽略那个 Usage 不完整的 Pair，又等于把无法比较的数据伪装成完整实验。

我用这个案例讲三件事：第一，**负结果也可以是高质量实验**；第二，任务 **Verifier 必须高于 Token、Tool Call 和延迟**；第三，证据不完整时**关闭 Claim，而不是补一个看起来合理的数字**。还要补一句版本边界：这个结果绑定证据记录里声明的实验提交，不是冻结 commit 上的重跑。

#### Pico 的 Myna 候选实验应该怎样完整表述？

我会把它表述成**两条彼此独立的证据链**。第一条，确定性 Pack：144/144 个 Trial 完成，在冻结任务上仓库读取次数降低 50\.0%，95% 区间是 29\.166667% 到 70\.833333%；并且 stale 与 cross\-repository Memory 事件都是 0。它能支持的结论是：契约可运行、在这个 Pack 里确实减少了读取、作用域检查没发现异常；它不支持真实模型任务的普遍提升。

第二条，live 24\-Pair real\-Agent Pack：24/24 个 Pair 有效，pass delta 的点估计是 20\.8333 个百分点，但 95% 区间是 0 到 41\.6667 个百分点；capability、efficiency、general\-Agent 和 positive Claim 的 Gate 全部失败。正确说法是“观察到正向点估计，但统计和预设 Gate **不足以形成正向 Claim**”，而不是“Memory 已证明提升 20\.8%”。两条链都记录了 0 个 stale 或跨仓库事件。

最后补版本边界：这些结果绑定 candidate evidence index 中声明的 Pico、Myna、Provider、任务和 Artifact 身份，不是对 `aedcaf2c` 当前所有场景的外推，也不是线上用户数据。

#### PicoBench、Eval Engine 和 Evolver 是什么关系？

PicoBench 是 checkout 里的 Agent 应用评测模块，负责准备 Task/Trial、运行 Comparison、调用 Verifier、生成 Claim Gate。Eval Engine 的源码是既有的 hook/judge 实现，但当前 Runtime Assembly 没有把它作为公开运行功能挂载。Evolver 是 opt\-in Beta，消费轨迹和 benchmark contract，生成 candidate，再做 train/sealed 评测和 activation artifact。

所以**三者不能互换**：PicoBench 评“某个配置做得怎么样”；Evolver 用评测治理“候选值不值得保留”；Eval Engine 有代码，不代表生产 Runtime 正在调用。

我在面试里会讲当前的实际接线，而不是根据目录名去推断功能。

#### 为什么 PicoBench 报告要从已保存 Record 重建，而不是重新跑任务？

因为重新跑一次，会重新引入模型随机性、Provider 变化和环境漂移，得到的就不是原实验报告。正确做法是：用不可变 Manifest、Trial Record、Artifact digest 和 Verifier 输出重建汇总，验证报告逻辑，但不改变原始观察。

如果原始证据不完整，结论就标成无法重建或 measurement invalid，**不能补跑几条再和旧数据混在一起**。这样报告才可审计，也能防止事后删掉不利 Trial。

### Benchmark 怎样避免泄漏答案或被 Agent“刷题”？

核心思路是隔离。任务、Verifier 和 sealed 数据都要**和被测 Agent 隔离**。Agent 不能读取隐藏验收代码、答案文件或其他 Trial 的 Artifact。训练或调参只用公开的 train split，最终决策用没见过的 sealed split。Workspace 每个 Trial 重新初始化，避免上一个 Trial 的 Memory 或文件漏过来。

Prompt 和 Memory 的污染也要管：长期 Memory 存过 benchmark 答案的话，就换独立 namespace 或新仓库。还有一层：模型本身可能在训练数据里见过公开 benchmark，所以真实业务回放和新构造的任务同样重要。

Pico 这边，Evolver 区分 train 和 sealed evaluation，PicoBench 用 Manifest 和 Artifact 重建报告。任何“让 Agent 看到 Verifier 以便自我检查”的设计，都要保证它**改不了 Verifier，也读不到预期答案**。

#### 怎样评测半开放任务，比如“优化这个模块”？

先把开放目标拆成可验证的维度：功能正确性、性能、兼容性、安全、可维护性和变更范围。硬条件用确定性测试和静态检查；性能用重复测量加置信区间；代码质量用 rubric 加人工或 LLM review；最后还要检查有没有违反用户约束。

任务 Prompt 不能只说“变好”，要写清优化目标和不可退化项。Agent 交出来的 diff、benchmark 结果和解释都是 Artifact，Verifier 要独立重跑关键检查。多目标时，用**分层 Gate 而不是一个加权总分**，防止性能提升掩盖正确性失败。

PicoBench 的 one\-axis 和 Claim Gate 适合这类场景，但具体 Verifier 必须由领域任务定义。**没有一个通用的 Agent 分数可以替代**。

#### 除了通过率，还应该看哪些 Agent 指标？

至少三类：可靠性、效率和风险。可靠性包括 per\-task 成功率、pass^k、恢复成功率、取消正确性、严重失败率；效率包括 Provider attempts、Token、Tool calls、墙钟时间、成本和缓存；风险包括越权、审批绕过、重复副作用、跨作用域泄漏和无效完成声明。

还要看测量完整性：Usage 缺失、Trace 关联率、Verifier 覆盖、被污染的 pair 数。**平均值容易掩盖长尾**，所以报告里要放 p50、p95 和任务分桶。多 Agent 还要看无用的 spawn 和协调开销；Memory 要看 stale 和 cross\-scope 的 recall。

指标越多，越要**预先指定主指标和 Gate**，否则事后挑选必然产生误导。Pico 的 Context 负结果就是现成的提醒。

#### 一套 Eval 什么时候有资格成为发布 Gate？

我认为要满足几个条件：稳定、可复跑、能代表真实风险、对失败有明确动作。任务集覆盖核心路径和历史回归；环境可重建；Verifier 独立；数据完整性可检查；阈值在运行前就确定。还要设置 Provider 和 infra 的故障策略，以及 Artifact 保留规则。

发布 Gate 通常是分层的：离线确定性 Gate 每次都必须通过；真实 Provider 或 Channel Gate，按凭据和成本在候选发布时运行；性能 Claim 需要专门实验；生产 canary 另有回滚条件。反过来，不能用又贵又易抖动的大 Benchmark 阻塞所有开发，也不能只靠单元测试就发布 Agent 行为变更。

还要记住：**Gate 存在，不等于 Gate 已经满足**。定义一套 Gate，和真正走通它，是两件事。

## 十二、Coding Agent 与长任务

### 一个 Coding Agent 怎样把长任务中的代码生成变成可交付变更？

我会把 Coding Agent 的长任务理解成一笔跨多步、可中断、还可能遇到基线变化的软件变更。模型会写代码只是起点，真正难的是把这些环节串起来：把自然语言目标转成验收，在陌生仓库里建立足够的理解，控制好 diff 的范围，持续做验证，处理失败和用户新提交，最后交付一个可审查、可回滚的结果。

最直接的方案，是把 PRD、仓库和一句"请全部实现并测试"一次性交给 Agent，但常见失败我见过不少：过早选方案，一次改太多文件，为了让测试通过去改测试，忘了最初限制，Context 压缩之后重复探索，遇到失败无限修，还有基线变化后把用户的工作盖掉。所以长任务真正危险的，不是单个 Tool Call 出错，而是**一个错误判断在几十步以后累积成看似完整的大 diff**。

执行上我会拆成一组可验证的提交边界。先澄清目标和不可变约束，把 PRD 转成验收清单。再建立 repo map，定位入口、依赖、测试和风险。然后形成最小计划，把未知项标出来。修改按小批次进行，每一批都先跑最便宜、最相关的检查。最后做独立 review、全量验证，生成 diff、测试和风险 Artifact，再请求合并或交付。阶段可以灵活，但**每一步都要留下可恢复状态，不能只有聊天记录**。

有几条原则我反复用。第一，任务完成条件由外部验收说了算，不是模型说"测试通过"就算完成。第二，变更范围要和目标成比例，大 diff 必须解释为什么必要。第三，测试不是被测 Agent 可以随意改写的答案，只有需求确实变化、旧测试本身有错、而且有独立证据时，修改测试才合理。第四，Workspace 有自己的基线身份，Agent 在 apply 之前要检查 commit 或 content hash，遇到用户新提交时，要 rebase、三方合并，或者停下来问。

任务跨 Context Window 时，要靠 Session、Working State、Artifact 和 Checkpoint 一起维持连续性。Session 保留对话事实，Working State 压缩目标、决定和开放问题，Artifact 保存计划、接口清单、diff、测试和回执，Checkpoint 保存 Workspace 恢复点。只依赖 Summary 会丢精确约束，只依赖 Git 又不知道为什么做这些修改。恢复时要重新确认当前基线、已完成步骤、失败原因、未知副作用和下一项验收，不能从最后一句对话继续猜。

预算既要限制资源，也要保护质量。可以限制总时间、模型调用、Tool 调用、失败重试和最大 diff，但到上限时，不能把半成品包装成完成。Pico 达到最大迭代后会关掉 Tool 做一次 Synthesis，只用来汇报当前进展和阻塞，不代表代码任务已经通过。测试失败以后，按失败类别、剩余预算和改动风险决定继续、回滚还是请求人工，而不是自动修到绿为止。

Checkpoint 的频率也不是固定每 N 步。只读探索不需要频繁提交；完成一个可验证的小阶段、做大范围重构之前、执行高风险 Tool 前后，更适合建立恢复点。Pico 用独立的 shadow Git，不污染用户仓库。这是本地安全网，但它恢复不了数据库和外部副作用，也解决不了用户的并发修改。

Human\-in\-the\-loop 要放在信息价值最高的边界。需求有歧义、可能大范围分叉时，不可逆或高权限动作之前，计划明显偏离用户约束时，以及合并大 diff 或发布生产之前，都适合让人介入。低风险、可逆的操作不必逐步审批，否则会产生审批疲劳。批准的内容也应该是具体的 plan、diff 或 action intent，执行之前还要重新校验基线。

Pico 能承载代码任务，是因为它有文件、grep、Shell、Context、Session、Checkpoint、Subagent、Trace 和 PicoBench 这些能力。但要说明白，它不是专用的 Coding Agent 产品，没有完整的 branch/PR 工作流、仓库专用 Planner、默认 review pipeline，也不是每个任务都有领域 Verifier。固定 CI/CD Workflow 也不会被开放式 Agent 取代。稳定、可审计的步骤应该留在 Workflow 里，Agent 更适合处理路径不确定的分析和修改，再把产物交给确定性 Pipeline。

所以 Coding Agent 的交付能力，不由写代码的速度决定，而取决于能不能把需求、基线、最小变更、测试、Review、Artifact、Checkpoint、预算和人工决策**串成一条可审查的软件变更链**。代码只是交付物的一部分，验证证据、未解决风险和可回滚边界同样重要。

### 从一个 PRD 到代码，Agent 应该经过哪些阶段？

我会分成理解、计划、执行、验证、交付五个阶段。理解阶段，把 PRD 里的用户目标、非目标、约束和验收条件结构化，识别歧义，做只读探索。计划阶段，定位受影响的模块、依赖和测试，形成一份可审查的最小变更计划。执行阶段小步修改，每一步都保持 Workspace 可运行。验证阶段独立跑单元、集成和端到端检查。交付阶段输出 diff、测试证据、已知限制和回滚方式。

关键不是逼模型先写一篇长计划，而是**让需求跟 Verifier 建立映射**。简单任务可以边探索边做，复杂或高风险的任务应该先确认计划。任何阶段发现 PRD 矛盾，都要回到用户那里，**不要自己发明业务规则**。

![30\-coding\-delivery\-chain\.png](<原始文件包/Pico 面试逐字话术稿/图片和附件/30-coding-delivery-chain.png>)

#### 怎样防止 Coding Agent 一次改太多文件？

靠变更预算和增量提交约束。开始前根据任务定位候选文件，设好最大 diff、最大文件数，或者一个超过就要审批的扩散阈值。每次只完成一个可验证的子目标，跑完相关测试再继续。如果发现需要架构级重构，先暂停并解释原因，不要悄悄扩大范围。

Runtime 可以记录已修改文件、base hash 和 Checkpoint，Verifier 检查有没有碰禁区。**Prompt 只负责告诉模型"优先最小改动"，真正的限制要由文件工具和 Patch 应用层来执行**。

### 单元测试通过就能说明 Agent 改对了吗？

不能。单元测试可能覆盖不足，可能被 Agent 错误修改，可能只验证了局部行为，环境也可能跟真实用户路径不一样。至少还要看现有回归测试、静态类型和 lint、构建、集成边界和端到端行为。前端或 API，要从用户入口验证。

还要保护测试所有权。被测 Agent 可以新增测试，但**不能靠删除或弱化原有断言来通过**。关键验收最好由 Agent 外部的控制面拥有的 Verifier，在隔离环境里重跑。涉及性能、安全或数据迁移的变更，还要做专项检查。

PicoBench 强调 Verifier 不受 Agent 控制，正好能说明这一点。而且"所有测试通过"这个结论，**只有在 Trace 和 Artifact 里存在真实执行证据时才可信**。

#### Agent 修改测试算作弊吗？

不一定。需求变了，更新测试是正常的工程工作；新增回归测试也很有价值。真正的问题在于：测试是否属于被测目标、Agent 有没有权改、改动有没有削弱验收。

我会把测试分成三类：公开可改的项目测试，可新增但不能删除的回归测试，以及隐藏或只读的 Verifier。第一类，Agent 可以按需求调整。第二类，改了要说明理由。第三类，没有写权限。评测的时候还要比较变更前后的覆盖和断言强度，**不能把失败的测试直接跳过**。

这不是靠 Prompt 写一句"不要作弊"就能解决的，**必须在 Workspace 权限和 Verifier 隔离上落实**。

#### 你会怎样让 Agent 做代码 Review，而不是只看自己生成的结果？

把生成和审查的角色分开。执行 Agent 提交 diff 和证据之后，Reviewer 用干净的 Context，读 PRD、变更、相关源码和测试结果，从正确性、边界、并发、安全、兼容和可维护性几个角度审查。**Reviewer 不依赖执行 Agent 的自述**，可以要求补充验证，也可以直接拒绝。

这个 Reviewer 可以是人、独立模型，或者两者组合。关键是要有独立的输入和明确的 rubric，而不是让同一个 Agent 在原上下文里说一句"我反思之后觉得没问题"。高风险变更，还要用确定性工具做静态检查和测试。

#### Agent 生成了很大的 diff，你怎么判断是合理重构还是跑偏？

先看变更能不能映射到验收项。每个主要文件为什么改、有没有更小的方案、公共接口变没变、测试和迁移有没有覆盖。再看 diff 统计：无关的格式化、重命名、依赖升级跟业务修改混在一起，会抬高审查风险，应该拆开。

我会要求 Agent 输出变更清单和影响面，但最终靠代码分析、测试和 Reviewer 验证。**base hash 变了，或者 diff 超过阈值，就进入人工审批**。重构和功能变更最好分开提交，不然失败的时候定位不了。

大 diff 并不一定错，但它提高了未知风险。Harness 应该把"变更多大"当成控制信号，**不能只把最终的测试绿灯当成唯一标准**。

### 怎样防止 Agent 忘记用户最初的要求？

把要求从自然语言历史里提升出来，变成结构化的 Working State 和 acceptance list。每一项要求都标记状态、证据和未决问题，Context 压缩时这些内容要受保护。执行前后检查当前动作是不是在服务某个验收项。用户补充要求时，更新版本，并记录跟旧要求的冲突。

长任务里只依赖最初那条消息很危险：历史会被裁剪，模型也容易被最近的错误带跑。Pico 的 Curator Working State 可以保存目标、决定和开放线程，但完整的需求管理还是要靠应用层实现。

结尾时让 Verifier 按原始要求检查，而不是按 Agent 最后的总结检查。**这样即使模型在中途重新解释了任务，也改不掉成功标准**。

#### 需求在执行过程中变化怎么办？

先区分三种情况：补充信息、范围变更和矛盾指令。补充信息可以直接注入当前 Turn。范围变更一般要重新计划和预算。跟已执行的不可逆动作冲突时，必须停下来说明。新要求要有版本，并记录哪些旧决定因此失效。

Pico 的 INJECT 可以在 Tool\-loop 的安全间隙把用户补充交给运行中的 Turn，INTERRUPT 可以取消当前工作。但消息注入不会自动解决已经发生的副作用，所以 Runtime 还要把当前进度、Checkpoint 和外部 receipt 提供给模型。

真正可靠的长任务需要变更控制：评估影响、更新验收、重新确认高风险动作。**把所有新消息都当"再多一句 Prompt"，会让状态和用户预期错位**。

#### 长任务为什么需要阶段性 Artifact，而不只是 Session？

Session 按对话顺序记录发生了什么，但长任务恢复的时候，还需要结构化的产物：当前计划、已完成 feature 的列表、测试结果、diff、失败根因和下一步。Artifact 比自然语言历史更容易被新进程、Verifier 和人检查，也能跨 Context 窗口持续。

Anthropic 公开的[长任务 Harness 实践](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)也强调要初始化环境、维护 feature list、记录增量进展、做 Git 提交、写 progress file，核心目的就是**让下一段 Context 不用靠猜来知道前一段做了什么**。Artifact 要有 schema、版本和 digest，避免被不受控地覆盖。

在 Pico 里，Session、Checkpoint、Trace 和 Evolver/PicoBench Artifact 已经能承载一部分，但普通 Turn 里的 progress artifact 还是要由具体应用来定义。这个分工很明确：**Harness 负责存放和关联，领域任务负责定义内容**。

#### 任务跨越多个 Context Window 时，怎样保持连续性？

常见的组合是 compaction、结构化 note 和 Subagent。快到窗口上限时，不是简单丢掉旧历史，而是保存目标、架构决定、未解决的错误、关键文件和验证状态。原始消息进 Archive，必要时可以回查。下一个窗口用这些状态重新锚定，并且先检查 Workspace 的实际状态。

Coding 任务还应该依赖 Git 或 Checkpoint，而不是靠记忆文件内容。模型进入新窗口后，先读当前 diff、测试和 progress artifact，**确认"事实上的代码"跟摘要一致，再继续**。

Pico 的 Context Engine、Working State 和 shadow Checkpoint 能支撑这套思路。但当前 Context 实验曾出现回归，所以持续性必须用长任务 Verifier 来测，不能只看摘要可读性。

### 怎样避免长任务无限运行？

同时设硬预算和进展检测。硬预算包括最大 Iterations、总时间、Provider Token、Tool 调用、Subagent 数量和外部成本。进展检测看最近几步有没有产生新文件、状态变化、Verifier 改善或新信息。**同一类失败循环，就触发 nudge 或直接终止**。

预算耗尽时，应该生成"受限收束"：当前完成项、失败原因、未验证内容和下一步，而不是声称任务完成。Pico 到 max iterations 之后关掉 Tools 做 Synthesis，就是这种机制，它不是成功判定。

确实需要继续的任务，可以开新的 Turn 或持久 Job，但要重新拿预算和恢复状态，**不能让一个不可见的循环长期占着资源**。

#### Checkpoint 多久做一次合适？

取决于动作的成本和可逆性。每个小读操作都做 Checkpoint 是浪费；**完成一个可验证的里程碑之后再建，更合理**。高风险批量写入的前后，各留一个边界比较合适。Coding 任务一般是测试绿、工作区干净，或者完成一个 feature 的时候提交。

Checkpoint 的内容也要控制，依赖缓存、Secret 和大二进制别无差别地存进去。恢复时验证 digest 和 Workspace drift。Pico 在 Turn 边界提供可选的 shadow Git Checkpoint，是一个比较粗粒度的安全网；更长的单 Turn，可能还需要应用内的阶段 Checkpoint。

**频率不是固定秒数，而是"恢复成本 × 失败概率 × Checkpoint 开销"的权衡**。

#### Agent 运行测试失败以后，应该自动修到什么时候？

先判断失败是不是当前变更引入的，还是环境、依赖或既有红灯。确定性、可修正、又在范围内的问题，可以有限迭代，每次修复都要产生新证据。**如果失败反复出现、原因超出任务、需要大规模重构或碰到安全边界，就停下来报告**。

不能把"测试失败就继续改"当成无限策略，因为 Agent 可能靠删测试、扩大改动或掩盖错误去追绿灯。设最大修复轮次、diff 预算和不可修改的 Verifier，能压住这个风险。

Pico 的 Tool failure 和 loop\-breaking 机制能处理一般性的重复失败；Coding 应用还要加测试基线、failure fingerprint 和修改范围控制。

#### 如果 Agent 改完代码但用户同时提交了新 commit，怎么办？

这跟 Workspace drift 是一类问题。Agent 的 Patch 应该绑定开始时的 base commit 或文件 hash，apply 之前检查当前 HEAD 和目标文件有没有变。没冲突就 rebase 或三方合并；有冲突就重新读新代码、更新 Context，让 Agent 生成新的 Patch。**不能强制覆盖**。

独立 worktree 能减少 Agent 和用户互相踩踏，但最终合并还是要 base 验证。长时间运行时，可以锁分支、用 PR 而不是直接写主工作区，或者在 merge 之前重跑完整 Verifier。

Pico 的 Checkpoint 和 Workspace 边界提供了基础，但对 Coding Agent 来说，**版本锚定比"记住文件内容"更可靠**。

### Human\-in\-the\-loop 应该放在哪些阶段？

我会放在三类边界：目标不明确时确认需求；不可逆或高风险动作之前审批；最终交付或发布之前审查。中间那些低风险、可逆、可验证的动作可以让它自治，不然每一步都问，Agent 就退化成一个遥控脚本。

HITL 还要支持暂停后序列化状态并恢复，审批要绑定具体的动作和参数。OpenAI Agents SDK 的[HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)和 LangGraph 的[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)都提供 pause/resume 或 interrupt 机制，说明这已经是长任务 Harness 的基础能力。

**人类参与的是决策边界，不是代替 Agent 去点每个按钮**。

#### Coding Agent 和固定 CI/CD Workflow 的边界在哪里？

Agent 适合处理开放式诊断、代码探索、方案生成和局部修改。CI/CD 适合执行确定性的构建、测试、扫描、部署和回滚。**最佳组合不是让 Agent 替代 Pipeline，而是让 Agent 产出候选变更，Pipeline 当独立的 Verifier 和发布 Gate**。

举例来说，Agent 分析失败并提交 Patch，CI 跑标准测试和安全扫描；失败后把 Trace 丢回给 Agent 继续修，但部署权限仍然由 Pipeline 和人控制。这样模型负责不确定的推理，Workflow 负责确定的执行。

Pico 提供的是通用 Runtime 和评测思想，不是 CI/CD 系统。把两者接起来的时候，要保留幂等 Job、Artifact 和审批，**不要用 Shell Tool 直接承担完整的发布平台**。

## 十三、Evolver、自进化与反馈闭环：候选修改不等于自动上线

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 主路径迁入原候选契约：枚举含 skill、prompt、policy、runtime、model_profile、route，**只有 runtime 有完整可执行评估链**。不是枚举只有 runtime 一个值。旧 prompt／skill／tool_policy／routing／benchmark 标签位于 compat。Evolver 未自动接入每次 Issue 修复，历史 SWE-bench 关闭了它。[候选契约](repoagent/harness/evolver/candidate_manifest.py)
<!-- REPOAGENT-NOTE-END -->

### Pico Evolver 怎样把候选修改变成一个受控、可验证、可回滚的软件改进流程？

我会先把“自进化”说得克制一些。它不是模型在一次对话里做 Reflection，也不是让 Agent 直接修改自己的代码。更准确的说法是：系统根据历史任务提出候选改动，用独立评测去检查收益和回归，再经过发布与回滚治理，最后决定要不要影响后续运行。这里有个前提我要先点出来：候选一旦会改变未来用户看到的 Runtime、Prompt、Tool 或策略，它就不只是调参，而是**软件变更管理问题**。

最朴素的方案我也想过，就是让模型读几条失败 Trace，改一改 Prompt 或源码，跑几个训练任务，分数一高就自动 Apply。这个方案很快会撞上几堵墙：过拟合、Reward Hacking、Benchmark 泄漏、候选把评测器改坏、选择性报告，还有无法回滚。现在强模型生成一个看似合理的 Patch 已经不难了，真正难的是建立一套评价体系，它要代表真实目标、不能被候选操纵、还能发现长尾回归。

所以有几个状态必须分开。Observation 或失败只是候选来源；Candidate 是有不可变身份的提案，不是已经生效的配置；`train evaluation` 用来快速搜索，`sealed evaluation` 用来检验未见样本；promotion 只表示候选可以成为当前 Evolution Run 的下一基线；human readiness 表示证据够不够让人来审查；activation 才会改变真实 Runtime；rollback 负责恢复。**如果把这些状态压成一个 ****`improved=true`****，你就分不清候选到底是在哪条边界上被接受的。**

Candidate Label 也很重要，因为“允许改什么”决定了“怎样评”。改 Prompt、改 Context 策略、改 Tool schema、改 Router、改依赖，或者改任意源码，风险和 Verifier 都不一样。一个通用 Patch 生成器不能自动拥有通用正确性。每个 Label 都应该有文件 allowlist、禁止项、专属 Task、完整性检查和回滚策略。模型权重、Secret、Verifier 和 sealed 数据，通常都不应该允许候选去改。

train 与 sealed 分开能降低过拟合，但不能自动消除。sealed 集合要和候选生成隔离，还要覆盖安全和非劣两类场景。候选反复尝试之后，还是会间接对 sealed 过拟合，所以还需要轮换数据、真实回放和外部 holdout。Reward 我倾向用分层 Gate：先守住正确性、安全和完整性，再比较成本、延迟或质量。不能用加权总分，让一次严重的安全回归被平均收益抵消掉。

Reward Hacking 的本质，是候选优化了测量方法，而不是目标本身。它可能去改测试、跳过困难任务、减少日志让成本看起来更低、硬编码 benchmark 答案，或者让 Judge 偏爱自己的措辞。防护手段包括：由 Agent 外部控制面拥有的 Verifier、只读的 sealed Artifact、candidate allowlist、从原始 Record 重建结果、完整 Trial 分母、单变量比较和人工抽查。要记住一条原则：搜索能力越强，评测漏洞越危险。

Pico Evolver 现在把这些判断做成了 opt\-in Beta，包含 Run spec、Candidate Manifest、受约束修改、train 与 sealed evaluation、Gate、verdict、activation 和 rollback Artifact。目前只有 `runtime` 这个 Candidate Label 具备完整可执行的证据链。Activation 被单独建模，停在 `pending_human`；候选记录不会自动修改 checkout，也不会移动 `HEAD`。这不是过度保守，而是自治范围必须与证据能力匹配。

现有的真实证据也只能谨慎表述。一条小型 real\-model run 里，候选被选为下一轮 run 的 baseline，train pass rate 从 40% 到 60%，四个 sealed case 从 25% 到 50%，完整性错误是 0。但结果没有达到 two\-sigma 的正向置信门槛，activation 仍然是 `pending_human`。它证明受控候选链能跑通，并且出现了探索性改善；**它不证明持续自我改进，更不证明生产自动升级。**

Evolver 和强化学习也不一样。它主要在 Runtime 仓库或配置上搜索候选，产物是可审查的 Patch 和 Artifact，不更新模型权重。它和 Reflection 也不同：Reflection 在一次 Run 内修正当前答案，Evolver 跨 Run 改变未来的系统，影响范围更大。两者都可以用失败反馈，但发布责任完全不同。

如果继续完善，我会先扩充分层真实 Task 和安全回放；每增加一个 Candidate Label，先建立专属 Verifier，之后再增加多 Trial、跨模型非劣检查、feature flag、canary 和自动回滚告警，而不是先开放“任意代码自改”。候选谱系和负结果也要保留，避免系统反复去探索同一种失败。

所以 Pico Evolver 最值得讲的，不是模型会自己改代码，而是 Candidate、train、sealed evaluation、promotion、activation 和 rollback 被拆成了不同状态。真正难的不是生成修改，而是用独立证据证明它值得影响未来的运行。自治程度只能随着评测、隔离和回滚能力一起增长。

### 你怎么定义“自进化 Agent”？

我的定义是：系统能从运行轨迹和评测结果中产生可持久化的候选改变，并通过独立验证决定是否保留。候选可以是 Prompt、Tool description、Context 策略、路由规则或代码，但它必须有明确作用域、基线、Verifier、sealed evaluation 和回滚。

反过来讲，什么不算。仅仅让模型在一次对话里反思、重试，不算系统级自进化；把历史写进 Memory，也不等于改变了策略；定期重新训练模型，那是另一类学习系统。**真正难的不是“生成一个改进建议”，而是证明它没有过拟合、没有破坏别的任务、没有越权，并且能安全激活。**

Pico 的 Evolver 走的是候选、评测、Gate、待人工激活这条路径，所以我更愿意称它为**受控优化闭环 Beta，而不是自治自我改写系统**。

#### Pico Evolver 和强化学习有什么区别？

先说强化学习：它通常更新模型策略或参数，通过 reward 优化长期期望回报。Pico Evolver 不一样，它主要在 Runtime 仓库或受约束的配置上生成候选补丁，用外部任务和 Verifier 来做选择，**不训练模型权重**。如果要对号入座，它更接近自动程序修复、搜索或受控配置优化。

两者都会面对 reward hacking 和分布外泛化，这点我不回避，但工程载体差很多。Evolver 的候选可以用 Git diff、测试和 rollback 直接审查；模型训练则需要数据、训练基础设施、权重版本，以及更复杂的安全评估。

Pico 有一条明确禁止的表述：不能把当前能力说成 fine\-tuning 或 model\-weight training。**就算未来真的用 RL 来生成候选，Runtime activation 仍然要过同样的发布治理。**

#### Reflection、自我批评和 Evolver 有什么区别？

差别主要在作用域。Reflection 发生在一次 Run 内部：模型回看结果、指出错误、再尝试一次，状态通常随 Context 结束。Evolver 是跨 Run 的，它保存 candidate、评测和 verdict，会改变后续 Runtime 或配置，影响面大得多，所以需要的证据和发布边界也更严格。

Reflection 能提高局部成功率，但会增加 Token，还可能让模型把错误合理化；它不会自动产生可复用的改进。**只有当失败被归因、改动被持久化、在独立任务上验证过并且受控激活，才算是形成了系统级反馈闭环。**

补一个澄清：Pico 的 Agent Loop 里有失败反馈和有界 nudge，但那些不是 Evolver，Evolver 是独立的 opt\-in 模块。把两者混在一起叫“自进化”，会夸大能力。

### Pico Evolver 的完整流程是什么？

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 当前主路径按本章的 runtime 候选范围、训练／sealed gate 和人工操作边界讲。历史训练 8/20→12/20、验证 2/8→4/8 来自旧业务运行时的独立功能实验，已知划分、z=1、未激活；不是新主路径或 SWE-bench 提分证据。
<!-- REPOAGENT-NOTE-END -->

一次 Evolution Run 会先冻结基线、Candidate Label、允许修改范围、任务和 Verifier。系统根据失败轨迹生成 candidate patch，做 lint 和完整性检查，然后在 train 任务上运行；通过了再进入 sealed evaluation，而 sealed 对候选生成过程不可见。Gate 检查功能、完整性、安全、统计和 Artifact，最后形成 verdict、manifest 和 activation bundle。

这里要强调：候选即使在 run 内部胜出，也只能成为后续候选比较的 next baseline，**这不等于修改正在服务用户的 Runtime**。最终的 activation record 保持 `pending_human`，要等人审查之后才能决定应不应用。回滚信息和 candidate identity 也要随 Artifact 一起保存。

当前只有受约束的 `runtime` Candidate Label 具备可执行的完整证据链；其他标签不能因为接口存在就说已经支持。**Evolver 还是 opt\-in Beta。**

#### 为什么要分 train 和 sealed evaluation？

核心原因一句话：候选生成器如果反复看到所有评测任务和失败细节，它很容易对着测试写补丁，而不是学一条可泛化的规则。所以 **train 用来迭代和调参，sealed 用来做最终检验**；候选生成过程不能读 sealed 的答案、Verifier 实现，也不能看结果。

但 **sealed 也不是绝对防过拟合**。任务可能同分布过窄，模型可能见过公开数据，候选也可能靠增加特例来间接匹配。所以还需要多任务、多种 failure mode、完整性检查和人工审查来兜底。

Pico Evolver 把 train 与 sealed 分开，保留 Manifest 和 Gate，我认为这个结构是对的。但边界也要说清楚：小规模一次通过，证明不了长期自进化；证据要绑定到具体的 candidate 和 task pack 上。

#### 为什么 Candidate 生成后不能直接 Apply？

因为生成器和被优化对象可能共享同一个目标函数，最容易找到的就是“分数变高但系统变坏”的捷径。举几个例子：删掉失败检查、放宽安全规则、修改 Verifier、硬编码任务答案、关掉昂贵功能、隐藏错误。而且直接 Apply 还会让线上状态难以复现。

所以候选必须在独立 Workspace 里生成，allowlist 限制能改哪些文件；base hash 保证补丁基于预期版本；lint、测试、sealed eval 和安全 Gate 逐层检查；最后由人审查 diff 和证据。**激活和候选生成要分成两个不同的权限域。**

Pico 的 candidate 到 activation 这条边界，就是为了拦住“模型写完就上线”。如果面试官追问自进化是不是自动的，我会直接回答：**当前不会自动激活**。

#### 为什么要有 Candidate Label？直接让 Agent 改任意代码不行吗？

因为不同改动需要不同的 Verifier。改 Prompt，可以用任务回归加 Token 指标；改 Scheduler，要验证顺序、取消和并发；改 Sandbox，要做安全测试；改 Memory schema，要处理迁移和兼容。一个通用的 “tests passed” 覆盖不了这些风险。

Candidate Label 声明的是：候选作用域、允许文件、所需 Gate、Artifact 和激活方式。**只有标签对应的评估器完整，candidate 才有资格进入后续阶段。**任意代码修改等于把搜索空间和风险同时放大到无法验证。

现在 Pico 只有 `runtime` 标签有完整的候选评估链，其他标签即使有名字，也不能描述成“已支持”。这是证据边界，不是功能列表缺了几个开关。

### Evolver 的 Reward 或 Verifier 应该怎样设计？

我会优先用任务结果和约束，而不是语言风格。Reward 要分层：**正确性是硬 Gate，安全和完整性不能被性能收益抵消**；过了这一关，再比较成本、延迟和可维护性。多任务场景要按任务聚合、给置信区间，避免一个大分值任务主导整体。

Verifier 必须放在 candidate 权限之外，检查它有没有改测试、关功能、泄漏 sealed 数据或绕过安全。还要设非劣门槛：**即使主指标提高，关键子群退化也不能发布。**

PicoBench 的 Claim Gate 和 Evolver 的 train/sealed 结构提供了基础，但通用 Reward 还要按 Candidate Label 逐个实现。目前只有 `runtime` 标签有完整评估链，这本身就说明：**“评什么”必须比“能改什么”先定义。**

#### 怎样防止 Reward Hacking？

我从权限、指标、审查三层来防。权限层：candidate 只能改 allowlist 里的文件，Verifier、sealed 任务和证据生成器只读。指标层：不能只优化单一代理指标，要保留正确性、安全、完整性和成本这些 Gate。审查层：检查 diff、Trace 和异常分布，专门识别删检查、硬编码、跳过失败这类行为。

还要把产品失败留在分母里，禁止 candidate 把异常改成“成功返回空结果”。对那种看起来巨大提升的结果，更要查数据泄漏、Provider 差异和测量缺失。

最后说底线：**没有任何自动 Gate 能完全消除投机**，所以高影响的 activation 仍然需要人。`pending_human` 不是保守过度，而是当前证据能力与影响范围的匹配。

#### 自进化最难的部分是生成修改，还是验证修改？

我的答案是：通常是验证。强模型生成一个看似合理的 Prompt 或 Patch 已经不难了，难的是建立一套 Verifier，它能代表真实目标、不会被投机、还能覆盖回归和安全。反过来讲，**没有可信的评价，搜索越强，就越可能更快找到漏洞**。

还要处理非平稳性：模型、Provider、工具和用户任务都在变，旧 Benchmark 可能失效。候选在 train 上提高，却可能在长尾或新模型上退化；Harness 升级之后，过去那些优化假设也应该重新评估。

所以自进化项目的核心资产不是“让 Agent 写自己”，**而是任务集、证据链、隔离和发布治理**。Pico 把较多设计放在 Gate 和 Claim 纪律上，这比单纯展示自动改 Prompt 更有工程意义。

### Activation 为什么要单独建模？

因为“候选在实验中胜出”和“把它应用到真实系统”，是两种权限和风险完全不同的事。Activation 要确认目标版本、base hash、部署环境、回滚点、审批人和变更窗口；线上还需要 canary、监控和自动回滚。实验进程本身不应该拥有生产写权限。

Pico 会生成 activation artifact，但状态保持在 `pending_human`，不会移动 checkout 的 HEAD，也不会替换正在运行的代码。即使 run 内部 promotion 选择了 next baseline，也只影响那个 Evolution Run。

面试时我会用一句话区分：**promotion 是实验内部选择，activation 是产品变更。前者自动化不代表后者自动化。**

#### Evolver 怎样回滚？

前提是候选有不可变身份、基线 commit、patch digest、评测 Artifact 和 activation record。激活之前先保存当前版本；canary 监控触发阈值之后，恢复旧版本或者关闭 feature flag。涉及数据 schema 时，还要有向后兼容和数据回滚方案，不能只靠 `git revert`。

回滚判断不能只看平均成功率，**还要看安全事件、特定任务退化、成本异常和 Provider 错误**。Trace 要能标明某次 Turn 用了哪个 candidate 版本。

目前的实际状态：Evolver 生成 rollback 相关 Artifact，但没有生产自动激活，所以线上自动回滚我还没有证明过；它现在建立的是候选治理结构。

#### Pico Evolver 当前有什么证据？

一句话总结：**现有证据能证明“受控候选链可以运行”，还不能证明“系统会持续自我改进”**。我分两块讲。

先看确定性 Gate。它覆盖了 Evolution Run、Candidate Manifest、受限修改、train 与 sealed 评测、Gate 判定，还有激活和回滚 Artifact。也就是说，从生成候选到形成一条可审查的结论，**这条链路是机器可检查的**。不过要加一个限定：冻结版本里只有 `runtime` Candidate Label 具备完整的可执行证据链；接口可以扩展，不等于任意模块都已经能安全进化。

再看真实模型实验。有一条小型 Evolution Run 留下了探索性改善的记录：train pass rate 从 40% 提高到 60%，四个 sealed case 从 25% 提高到 50%，完整性错误是 0。这个候选被选成了下一轮 run 的 baseline，但结果没有达到 two\-sigma 的正向置信门槛，activation 仍是 `pending_human`。

所以 Pico 能声称的，是已经建立了候选生成、隔离评测、Gate、人工激活和回滚 Artifact 的工程闭环，也保留了一条真实模型的探索性结果；不能声称的，是实现生产自动上线，更不能据此推出持续、稳定的自主进化。

#### 如果继续做 Evolver，你会优先补什么？

第一，扩大但分层维护真实任务集，加入长期回放、安全和恢复场景，并防止 candidate 读取 sealed 数据。第二，为更多 Candidate Label 建立专属 Verifier，而不是开放任意文件。第三，增加多 Trial、统计功效、子群非劣和跨模型验证。第四，把 activation 接到 feature flag 和 canary 上，但保持人工批准和快速回滚。

还要建立候选谱系：哪个失败生成了什么改动、在哪些任务上改善或退化、有没有被后续候选覆盖。对没达到 Claim Gate 的候选，也要保留负结果，避免重复走弯路。

我不会先去追求完全自动上线。**自治范围应当随评测与回滚能力扩展，而不是反过来。**

## 十四、框架比较与行业判断

### Pico 与 LangGraph、OpenAI Agents SDK、Claude Code 等系统应该怎么比较？

我不会一上来就做功能勾选表，比谁支持 Memory、谁支持 MCP、谁能跑多 Agent。主流系统用不同方式都能实现这些名词，勾选表比不出高下，**真正的差异在控制语义**。我一般从几个问题切入：谁拥有控制循环，状态怎样持久化，动作权限由谁治理，部署边界划在哪，Observability 和 Evaluation 怎么接到真实任务，还有产品本身为哪类任务优化。

先看控制循环。它可能是固定 Workflow、图状态机、内置的 Agent Loop，也可能干脆由应用自己写；还要看支不支持暂停、恢复、取消，以及人类能不能介入。再看状态权威：Session、Checkpoint、Memory 和 Artifact 各自怎么保存，崩溃之后能恢复到哪一层。然后是 Tool 与权限：模型只是提出调用，还是可以直接执行；系统有没有 Sandbox、审批、幂等和数据出站策略。最后比部署形态，本地、托管、多租户还是嵌入式，再加上生态、开发体验和维护成本。

拿这套方法去套各个系统。LangGraph 更像面向开发者的低层状态化图编排框架，适合显式节点、持久化和人机中断；OpenAI Agents SDK 提供轻量 Agent Loop、handoff、guardrail、session 和 tracing，适合围绕它的生态快速构建；Claude Code、Codex 是冲着软件工程体验去的完整 Coding Agent 产品；Hermes、nanobot 这类本地 Agent 在 Tool、Session、Channel、Cron 或 Skill 上跟 Pico 有交集。比较的时候，不能去猜没公开的内部实现，也不能因为 Pico 源码可见，就断言它更先进。

Pico 的位置，是一个独立的 local\-first 通用 Agent Harness，也算一个工程研究样本。它最值得讲的不是哪项功能别人绝对没有，而是一组选择：把多 Host 统一成 Turn，用 Spine 和 Lane 表达调度，把 Session、Context、Memory、Trace 和 Delivery 分域，再用 PicoBench 管住证据边界。这套组合在面试里有辨识度，代价是维护成本更高、生态更小、产品成熟度更低。

模型变强不会让 Harness 消失，只会把复杂度换个位置。强模型可能省掉一部分 Planner、Prompt 和手工分支，但它不会自动获得 Secret 管理、数据库事务、幂等、取消、审计和发布权。模型能自主行动得越久，外部副作用和恢复成本反而越高。所以 Harness 的重点，可能从“帮助模型做对”转向**“限制模型做错，并证明它做过什么”**。

固定 Workflow 也不会被 Agent 全面替代。步骤稳定、合规要求高、错误成本大的流程，适合确定性图或状态机；输入开放、路径未知、需要搜索和综合的部分，才更适合 Agent。我更看好一种产品形态：Workflow 拥有业务状态和 Gate，Agent 在某些节点内处理非结构化的子问题，而不是让一个通用 Loop 吞掉整条编排。

多 Agent 同样不是默认答案。并行研究、独立审查、上下文隔离，会推动它增长；共享状态冲突、Token 成本、重复劳动和责任分散，又会限制它。长时间自治真正的瓶颈，通常不是模型能不能多想一会儿，而是目标漂移、环境变化、状态提交、外部副作用、Context 污染和错误累积。企业落地的主要障碍，也常在数据权限、系统集成、任务验收、责任归属和 ROI，而不是缺一个聊天入口。

人类在更强 Agent 时代仍然握有目标、授权、异常判断和发布责任。人不需要逐个 Tool 去点，但要决定哪些动作可以自治、什么证据够放行、什么结果需要回滚。HITL 不是“模型还不够强时的临时补丁”，它是对组织责任和风险预算的编码。

Pico 相对成熟产品的不足，我会主动说。它还是 Alpha、pre\-RC；企业 IAM、多租户、持久消息基础设施和默认强 Sandbox 不足；专用 Coding 体验不如成熟产品；Tracing 不是生产级 APM；多个状态域之间没有跨域事务；部分实验只有确定性或探索性证据，Context treatment 还有明确的负结果。所以没有哪项功能值得包装成“别人绝对没有”，Pico 的差异更适合表述为设计组合和证据纪律。

往后两三年的工程重点，我更看好按需的 Context 和 Tool 披露、durable execution、身份与 Sandbox、轨迹级 Observability、任务级 Eval、模型路由与缓存，以及受控的候选发布。MCP 会降低连接成本，但不会替代权限。Memory 和 Skill 会更普及，但来源、时效和任务效果会成为分水岭。多 Agent 会选择性增长，不会成为所有任务的默认答案。

所以我比较系统，重点不是背产品名单，而是讲清楚每个系统把控制循环、状态、动作权限、恢复、可观测和评测放在哪里，再判断这些选择适不适合目标任务。产品能力会变化，**谁拥有控制、状态、动作和结论**，这类问题更稳定。

![31\-framework\-comparison\.png](<原始文件包/Pico 面试逐字话术稿/图片和附件/31-framework-comparison.png>)

### Pico 和 LangGraph 有什么区别？

[LangGraph](https://docs.langchain.com/oss/python/langgraph/persistence) 更像一个面向开发者的低层状态化编排框架。它的核心优势是图状态、checkpoint、durable execution、interrupt、subgraph 和 HITL，适合开发者自己显式定义节点、边和恢复边界。Pico 是一个能直接跑起来的 local\-first Harness，CLI、TUI、Gateway、Spine、Agent Loop、Session、Context、Tools、Tracing 和 Delivery 已经组合在一起。

所以**两者不是简单竞品**。Pico 完全可以借用类似的图执行引擎去实现某些长流程，LangGraph 也可以拿来当一个产品 Harness 的内核。真正的差异在控制模型：Pico 当前的主路径是统一 Turn，加一个开放式的模型与工具 Loop；LangGraph 则更适合把确定步骤和 Agent 节点混成一张显式图。

选型的时候，如果业务流程的依赖、审批和恢复点都很明确，我会优先考虑图；如果目标是研究多入口 Agent Runtime 的端到端语义，Pico 的实现更直接。我不会说哪一方更先进，**关键还是看你对状态和控制权的需求**。

#### Pico 和 OpenAI Agents SDK 有什么区别？

先说定位差异。截至我这个回答核验的时间点，[OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) 提供 Agent、Tools、Handoffs（也就是 Agents\-as\-tools）、Guardrails、Sessions、HITL、Tracing 和内置 Loop，也支持 MCP，Sandbox Agent 这类能力还处在 Beta。它适合以 SDK 为中心快速构建 Agent 应用。这里有个关键区分：直接裸用 Responses API 时，Loop、Tool dispatch 和 state 都得开发者自己扛；用了 Agents SDK，**更多运行责任就交给 SDK 管理**。

Pico 覆盖的概念有重叠，但它更偏一个独立的本地 Harness 和研究样本：多 Host 统一到 Spine、per\-conversation 的 Lane、OriginPool、自己的 Context Engine、Myna 边界、CallEfficiency、PicoBench 和 Delivery 语义，都是项目里显式实现的。代价是维护成本更高、生态更小。

我不会去回答“Pico 比官方 SDK 功能更多”。更合理的说法是，面试关心的 Runtime 契约，Pico 暴露得更完整。真实业务里，如果 OpenAI 生态能满足需求，**应该优先评估直接复用 SDK，而不是为了自研而自研**。

#### Pico 和 Claude Code、Codex 这类 Coding Agent 怎么比较？

Claude Code 和 Codex 是冲着软件工程任务优化的成熟 Coding Agent 产品或 Harness，重心在仓库导航、代码修改、Shell、补丁、Sandbox、项目指令和开发者交互上。它们的模型、工具和产品体验是放在一起优化的，单靠公开的模型 API 复刻不出来。

Pico 也能执行代码任务，但**核心抽象不是 Patch 或 PR，而是通用 Turn**，以及围绕它的 Context、Tools、Session、Memory、Trace 和 Delivery。它的优势是源码可见、证据边界可研究，入口也不只有终端。缺点同样要说：专用 Coding 体验、真实规模、工具成熟度和发布质量，都还达不到成熟产品的量级。

面试时我会把它们当行业参照，用来解释为什么 **Harness 会显著影响同一个模型的表现**，但我不会去猜闭源内部实现，也不会说 Pico 在能力上超过它们。

#### Pico 和 Hermes、nanobot 这类本地 Agent 有什么区别？

先说清楚一点，这些本地 Agent 可能都提供终端、消息 Gateway、Session、Memory、Skills、Cron、Subagent 和多 Provider，所以拿功能清单很难比出差异。以公开仓库为例，[Hermes](https://github.com/NousResearch/hermes-agent) 和 [HKUDS/nanobot](https://github.com/HKUDS/nanobot) 已经覆盖多入口、多 Provider、Tools、Memory、Skills、计划任务或 Subagent 这些能力。这正好说明：**这些能力是本地 Agent 的重要产品面，不是 Pico 独占的**。

Pico 更值得讲的是内部契约：所有入口统一成 Turn；Lane 和 OriginPools 表达调度；Session、Checkpoint、Memory、Trace 和 Delivery 分域；Tool 并行采用保守的风险边界；PicoBench 对成对实验和 Claim 资格做显式建模。说白了，它把**“为什么能相信结果”当成核心故事**在讲。

但这不意味着 Pico 整体更强。Hermes 这些项目可能在生态、用户体验和集成范围上更成熟。比较要选同一个维度，**不能把架构洁癖和产品可用性混成一个分数**。

### 为什么说“同一模型，不同 Harness”已经成为 Agent 竞争的关键？

我的判断是，模型能力越来越强之后，**差异更多来自模型被放进的环境**。Context 是不是高信号，工具容不容易用对，文件和 Sandbox 可不可靠，任务能不能跨窗口恢复，失败有没有结构化的反馈，Verifier 是不是真的在验，这些都会改变模型的有效能力。

看行业动向也是这个趋势。Anthropic 公开过 Context、长任务 Harness 和工具工程的经验；OpenAI 的 Agents SDK 也在不断扩展 Loop、Sessions、HITL、Tracing 和 Sandbox。这些都说明 Agent 工程正在从“写一个 Prompt”转向“设计完整的运行环境”。关于多 Agent，Anthropic 也公开了 [Research 系统的编排与评测经验](https://www.anthropic.com/engineering/multi-agent-research-system)。反过来，模型一升级，旧的 Harness 假设可能直接失效，所以要持续重评，而不是把规则越堆越多。

Pico 正好用来展示这个观点：它的 Agent Loop 不复杂，复杂的是周围的状态和证据。真正的竞争不是谁 System Prompt 最长，而是**谁能让模型在有限权限和预算内，稳定完成可验证的任务**。

#### 模型越来越强以后，Harness 会不会变得不重要？

不会。某些复杂的 Prompt 和人工规划确实会变得不必要，但 Harness 的系统责任不会消失。更强模型可能更会选工具、更会管 Context、更会自己修正错误，但它仍然没法凭空提供事务、权限、Sandbox、幂等、持久化、交付回执和独立 Verifier。

Harness 的形态会变简单：减少脆弱的规则，让模型直接拿到文件和 Shell 这类更底层的原语。同时，基础设施边界反而更重要，因为模型能执行的动作越来越复杂。Anthropic 公开的 Harness 研究也提醒过这一点：模型能力一变，旧的过度编排就会失效，最优策略常常是**重新去找最简单可工作的结构**。

所以未来**不是“没有 Harness”，而是“更少干预推理、更多保证环境”**。Pico 也该持续删掉不再必要的策略，而不是把每一次失败都固化成一条新 Prompt。

#### 固定 Workflow 会被 Agent 替代吗？

不会。固定 Workflow 擅长步骤明确、输入输出稳定、合规和可重复的任务；Agent 擅长开放式理解、探索和异常处理。**最好的系统通常是两者组合**：Agent 决定方案或者生成候选，Workflow 负责确定性的检查、审批和交付。

举个例子，报销流程不该让 Agent 自由决定要不要打款，但可以让它解析票据、解释异常、准备审批材料。Coding Agent 可以修代码，CI 仍然负责构建和发布。**自治程度越高，越需要外层确定性控制**。

对应到 Pico：当前 Agent Loop 偏开放，PicoBench 和 Delivery 这类确定性层在提供约束。碰到强业务流程，未来可以把 Turn 嵌进显式 Workflow，而不是要求一个通用 Loop 承担所有编排。

### 你怎么看未来的多 Agent 趋势？

我的看法是，**多 Agent 会成为重要工具，但不会默认取代单 Agent**。它最有价值的地方是并行探索、领域隔离和组织分工；主要瓶颈则是成本、共享状态、责任归属和合并质量。而且单模型能力越强，很多小任务会重新收敛回单 Agent，真正持续受益的还是复杂研究和大型工程。

我预计未来的系统更像**“一个主控制面 \+ 按需派生的有界 Worker”**，而不是自由自治的 Agent 社会。Worker 只拿最小的 Context、工具和预算，结果必须带证据；主控制面负责最终的 Verifier 和权限。

Pico 把 Subagent 的结果回流到 Spine，走的就是这条保守路线。它的价值在可控协作，不在展示 Agent 数量。

#### 长时间自治 Agent 真正的瓶颈是什么？

我的体会是，**真正的瓶颈不是上下文窗口这个单点，而是状态和错误在累积**。任务越长，需求变化、Workspace 漂移、工具偶发失败、外部服务不确定、成本和安全风险都会叠加在一起。就算 Context 无限大，模型也可能被旧分支和重复日志干扰。

所以需要的能力是成套的：结构化目标、增量 Artifact、Checkpoint、幂等动作、恢复、预算、Verifier 和人类升级。公开的长任务 Harness 实践也都强调**小步进展、持久状态和端到端测试**，而不是指望一次 Context 把全部工作做完。

Pico 已经覆盖了一部分基础，但还缺通用 durable workflow 和外部副作用事务，所以我不该把它说成可以无人值守地跑任意长任务。

#### 企业落地 Agent 最大的障碍是什么？

我认为**最大的障碍不是模型会不会回答，而是责任、权限和集成**。企业需要知道：Agent 访问了什么数据、以谁的身份在动作、动作能不能审批和回滚、失败由谁处理、效果怎么评测、供应商和成本怎么治理。很多时候，遗留系统的 API 和数据质量，比模型能力本身更难啃。

所以企业 Agent 往往**先落在边界清晰、结果可验证的场景**，比如研发辅助、客服草拟、文档处理和内部检索，再逐步扩大写权限。一上来就给通用 Agent 跨系统的最高权限，风险通常不可接受。

Pico 能展示 Runtime、Trace、Tool 和 Eval 这套思维，但它没有企业级多租户、IAM、计费和生产的 SLO。面试时我会把它定位成技术原型和工程样本，而不是企业解决方案的成品。

#### 人类在更强 Agent 时代还负责什么？

我的立场是，**人类负责目标、价值判断、权限和责任**。Agent 可以去探索方案、执行可逆动作、汇总证据，但业务优先级、不可逆承诺、法律伦理和异常取舍，还是需要授权主体来拍板。人类还要负责设计 Verifier，并判断它是不是真的代表了目标。

HITL 不应该退化成每步点确认，而应该放在高信息量、高风险的边界上：需求有歧义、会产生重大副作用、候选发布、异常升级。其余步骤让 Agent 在预算内自治。

Pico 的 `pending_human` activation、ask\-user 和确认机制，体现的就是这个方向；但完整的人机协作，还需要产品层的角色、审计和责任设计。

#### 你怎么看 Agent 开发未来两三年的技术重点？

我判断，重点会从“能调用工具”继续转向五件事。一是 Context 和工具的按需披露，把噪声降下来；二是长任务的 durable execution 和 HITL；三是 Sandbox、身份和外部副作用的治理；四是轨迹级 Observability 和任务级 Eval；五是模型与 Harness 的协同优化，包括缓存、路由和候选发布。

MCP 这类标准会降低连接成本，但不会替代权限和 Runtime。Skills 和 Memory 会更普及，但 freshness、来源和评测会成为分水岭。多 Agent 会在有并行收益的任务里增长，同时受成本和协调约束。

当然，这是我基于当前公开实践做的工程判断，不是确定预测。对 Pico 来说，下一步**更该补强可靠性、隔离和证据，而不是继续堆表面功能**。

### Pico 相比成熟 Agent 产品最明显的不足是什么？

我会按重要性直接列。第一，**成熟度和真实使用证据不足**，现在仍是 Alpha、pre\-RC。第二，产品能力不完整：企业 IAM、多租户、持久消息基础设施、默认的完整 Sandbox、Web 端和移动端、运营体系都还缺。第三，专用任务体验不如成熟的 Coding Agent 或平台。第四，一部分复杂模块已经有代码和测试，但正向效果的证据不足，比如 Context 和 Tool disclosure 的 treatment，甚至出现 Schema Token 下降、任务通过数反而回退的结果。

另外，本地多个状态域之间没有跨域事务，Tracing 不是生产 APM，目前只有受约束的 Candidate Label 带了完整的 Evolver 链。我会**主动承认这些缺口，这能说明我清楚项目的边界**，也能顺势引出下一步的工程优先级。

#### 如果面试官问“Pico 有什么是别人绝对没有的”，你怎么答？

我不会硬找一个“绝对没有”，**开源 Agent 的能力变化很快，很多设计最后会趋同**。更稳的回答是讲组合和叙事：Pico 把多 Host 统一成 Turn，把状态分域，把证据关联起来，讲 Claim 纪律。它把最终文本、Turn 终态、Verifier 和 Delivery 分开，而且把负实验也保留下来。

**差异化不一定是独占功能，也可以是选了一个明确的问题，然后把它做成可核验的实现**。面试官真正考察的通常不是市场专利，而是你能不能解释清楚约束、取舍、失败和证据。

如果对方要我讲竞品事实，我会限定到公开版本和具体日期，不凭印象贬低别的项目，这样答案才经得起追问。

## 十五、个人贡献、失败与简历追问

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 这里涉及个人经历、开发时间、用户规模和贡献的内容需要按真实情况回答。项目基于现有 Harness 设计／实现开展，自己的工作可围绕 Issue 应用、集成适配、执行与验收排障、公开任务评测展开；具体归属以实际参与和提交为准。最适合复述的本项目失败案例是“组合修复配对未获收益”和“镜像故障与模型失败分开处理”，见末尾补充。
<!-- REPOAGENT-NOTE-END -->

### 请介绍一下 Pico 这个项目，以及你在里面具体负责什么？

回答之前，我会先在脑子里把两件事分开：项目本身是什么，我个人做了什么。先说项目：Pico 是一个用 Python 实现、以本地运行优先的通用 Agent Harness。CLI、TUI、Gateway、Channel、Cron 和 Subagent 这些入口，最终都会汇入同一条 Turn 执行路径；Runtime 再统一管理 Context、Tools、Session、Memory、Tracing、Evaluation 和 Delivery。

到了个人层面，我不能照搬项目介绍。**仓库里有一项能力，不代表这项能力就是我设计或实现的。**开口之前，我会先确认四件事：我的真实角色是什么；哪个模块或关键决策真正归我负责；哪些实现和验证是我亲手做的；哪些部分来自协作者、既有代码、上游项目或 AI 工具。

现场回答时，我只挑一个自己最熟、也最经得住源码追问的范围来讲。顺序大概是：先讲当时遇到的真实问题，再讲最初的简单方案为什么不够；然后说清楚我负责的边界、我做的关键决定、实际改过的接口或测试，以及用什么证据支撑结论；最后主动把还存在的缺口说出来。项目里的其他能力可以当背景带过，但我不会把每一项都认领成个人贡献。

我这样安排，是因为项目面试本质上在审查责任范围。实验有了结果，不代表实验是我亲自跑的；仓库里有多少代码、多少个 Gate 或 Channel，也不自动等于我的产出。可信的个人叙事，要让每一个"我"都能落到 commit、PR、模块、测试、Trace 或真实的协作记录上。

规模和因果也最容易夸大。GitHub star、测试 Trial、真实用户、线上请求和生产 SLO，口径都不一样。没有 DAU 或日均 Token 的统计，我就直接说没有可靠数据。谈代码量要先说明统计范围，不能把仓库总行数当成个人工作量。实验观察到变化，也要交代基线、处理条件、任务、Verifier、区间和版本。**没有通过 Claim Gate 的结果，不能写成"提升"。**

失败故事不需要编，Pico 里有可以诚实讲的负面证据。比如 Tool disclosure 把可见 Schema Token 减少了 93\.4513%，任务通过数却从 23/24 退到 20/24；主 campaign 还因为一个 Context Pair 缺少完整 Usage，最终是 `measurement_invalid`。如果我只是阅读和复核这条证据，我会讲它让我形成了什么判断；只有我真的参与了设计、运行或修复，我才会接着讲我据此做了什么。

关于 AI Coding，也要讲责任而不是回避。模型可以帮我探索方案、生成候选、补测试或者做 review，但需求澄清、状态所有权、关键接口、diff 审查、测试运行和合入决定，仍然要有人负责。证明自己不只是"让 AI 改代码"，最有效的办法不是声称每一行都手写，而是能沿着入口一路讲到核心状态和测试，还能解释某个候选为什么不能直接采用，以及最后是怎样验证的。

讲"最难的问题"时，我只选一个真正深入的工程冲突，不把 Context、Memory、Tool 和 Eval 一次全讲完。一个好的故事要有朴素方案、失败场景、不变量、关键实现、验证证据和未解决的边界。细节越经得住追问，就越不需要堆概念。

最后补上两项真实信息就行：我的角色是 `[真实角色]`，最能证明能力的负责范围是 `[一个最熟的真实模块或决策边界]`。后面只用一项实现和一条验证证据展开，并明确哪些能力只是项目背景。**信息不足时保留边界，比补一个听起来完整、却无法核验的故事更专业。**

### 你为什么会做 Pico 这个项目？

这道题不能只回答"我对 Agent 感兴趣"，也不要把后来才总结出来的工程问题，倒写成本来的动机。更自然的讲法是：从一段真实的触发经历讲起，再说为什么把问题收敛到 Harness。

我会这样说："我做 Pico，不是因为想再写一个聊天机器人，而是想弄清，Agent 从 Demo 走向真实执行以后，为什么会出现状态不一致、长任务丢上下文、工具失败难定位这些问题。真正触发我的经历是 `[一段真实的项目、实习、课程或使用经历]`。后来我发现，**问题不只在模型 API，更在模型外面的运行环境**，所以才把项目目标收敛到统一 Turn、状态分层、工具治理和可验证执行。"

后面的负结果，可以用来讲项目怎样改变了我的判断，但不能伪装成最初动机。比如 Context 与 Tool disclosure 确实降低了 Schema Token，却让 task pass 退化；主 campaign 还因为 Usage 缺口，失去了有效的主结论。**这让我确认，Agent 工程不能只看功能清单和主观体验，还要把 Verifier、实验合同和结论边界一起做。**没有真实触发经历时，宁可删掉那一句，也不要编一段线上事故。

#### 你在 Pico 里具体负责了什么？

我会先限定范围，再讲细节。**最稳的回答，不是一口气列出五六个模块，而是选一个自己最熟、能沿着源码和测试讲到底的部分。**

我会先说清自己的真实角色，再指出我直接负责的边界。接下来只回答四件事：当时遇到的是什么问题；我做了什么关键决定；我具体改了哪些接口、实现或测试；最后是怎样验证的。**其他能力虽然属于 Pico，但我没有直接参与的部分，我不会算成个人贡献。**

最好提前准备一个源码入口、一个关键测试和一个失败场景。如果只能说"我负责整体架构"，却讲不出不变量、diff 和验证，可信度反而会下降。

### 你在这个项目里最难的技术问题是什么？

我会选一个自己真正深入的冲突来讲，而不是把复杂的模块都堆上去。**回答的重点不是代码量，而是为什么这个问题没有一个显然正确的方案。**

讲的时候可以按这个顺序推进：先说最初的简单方案为什么看起来合理；再说它在哪个场景下失效；接着说明最后必须守住什么不变量；然后落到具体的接口、状态或执行路径上；最后给出测试、Trace 或实验，并主动说清还剩什么缺口。

像"同一会话要顺序执行，但不同会话要并发""Session 和外部副作用不能假装成一个事务""模型文本不能拥有完成裁决"这些，都属于可以深入讲的工程冲突。到底选哪一个，必须取决于我自己的负责范围。**没有直接负责的模块，可以作为项目理解题来回答，但不能包装成"我最难的贡献"。**

#### 你做 Pico 时失败过什么？最后学到了什么？

没有真实事故可以讲的时候，就讲有完整证据的负面实验，但要把项目证据和个人参与分开。

我会这样说："Pico 里一条很有价值的负结果，来自 Context 与 Tool disclosure。可见 Tool Schema Token 的估算下降了 93\.4513%，任务通过数却从基线的 23/24 下降到 20/24。主 campaign 还因为 120 个 Context Pair 中有一个缺少完整 Usage，只得到 119 个有效 Pair。它虽然 `ship_complete`，但仍然 `measurement_invalid`。**这个结果说明，Agent 优化很容易改善代理指标，同时伤害真实任务效果。**证据不完整时，结果再好看，也不能发布正向结论。"

最后只补一句自己在这条证据里的真实参与：是设计、运行、复核，还是仅仅阅读。只有确实参与了后续修改，才继续讲我怎样调整 Gate 或流程。若只是阅读和复核，就说一句："我从这条项目证据得到的判断是\_\_\_\_。"

#### 这个项目最重要的工程取舍是什么？

我会选"用统一控制面换取额外复杂度"这个取舍。Pico 没有让 CLI、TUI 和 Gateway 各自直接调用 Agent，而是让它们统一进入 Spine 和 Turn 语义。这样做多出了 Scheduler、事件和 Delivery 的设计，开发成本更高，**但换来的，是同一会话的顺序、取消、Context、终态和 Trace 都保持一致。**

对单入口、短任务的简单 Agent 来说，这可能算是过度设计；但对多入口、长任务、需要证据的 Harness，这个取舍更合理。

如果我没有亲自参与这个决策，我会明确说：项目采用了这个取舍，而我直接负责或深入验证的，是其中某个具体边界。**我不会把项目的选择，自动改写成我个人的设计。**

#### 你从 Pico 中学到的最大教训是什么？

最大的教训是，Agent 系统的正确性不在最终文本，而在状态所有权和证据。**模型说完成、Tool 返回成功、Turn 结束、Session 落盘、Verifier 通过和消息送达，是不同的事件。**只要把它们压成一个布尔值，恢复、重试和评测都会出错。

第二个教训是，优化必须允许负结果。Context 与 Tool disclosure 的结果说明，局部的 Token、Tool Call 或延迟，不能替代任务通过率。

第三个教训是，**自治范围必须匹配验证和回滚能力。**Evolver 候选留在 `pending_human`，比在证据不足时自动上线更诚实。

最后补一条自己的真实变化就可以，比如以前怎样判断一个系统，现在怎样定义状态或验证。没有亲历的部分，就保留为项目结论，不包装成个人成长故事。

### 这个项目做了多久？有多少代码？

这道题只能使用真实数据。我会先说项目的时间范围和我自己的投入方式，再解释代码量的口径。**仓库总行数不能直接等于个人产出**，因为里面可能包含前端 bundle、测试、生成文件和依赖。

代码量只报可复现的统计：截至哪个 commit、统计哪些语言和目录、有没有排除生成文件，都要说清楚。个人范围用提交、模块和测试来说明，不拿仓库总行数代替个人贡献。如果我没有提前运行统计命令，就直接说没准备可靠口径，**不要现场猜一个好看的整数。**面试官继续追问时，我会尽快回到具体模块的设计、测试和缺口上。

#### Pico 有多少真实用户？线上运行规模多大？

我会先讲清楚定位：Pico 当前是 Alpha、pre\-RC，不是带生产 SLO 的多租户服务。**确定性 Trial、候选实验和 Channel Gate 是工程证据，不等于 DAU、请求量或线上稳定性。**

随后按真实情况说明使用范围：是个人长期使用、少量同学试用、公开用户，还是尚无可验证的外部用户。若确实有用户，还要说明时间窗口、统计来源，以及这里的"用户"到底算安装、试用还是持续活跃。**GitHub star、clone、测试 Trial 和真实活跃用户，不能互换。**

#### 你自己每天使用 Pico 吗？每天大概消耗多少 Token？

我会先给出真实的使用频率和主要任务，再说明有没有连续的统计。没有可靠口径时，就直接说没有，**不能根据账单或感觉反推一个数字。**

有统计的时候，要说明统计的日期范围，以及有没有包含输入、输出、cache、reasoning 和 retry。没有统计，我就不报日均 Token。Pico 的 CallEfficiency 可以记录每次 Provider attempt，但只有 Usage 完整、价格已知时，成本数字才可信。**这道题不只考察我用没用过，也在考察我的数据口径；诚实说"没有连续统计"，比报一个无法解释的数字更好。**

#### 项目是你一个人做的吗？有没有团队协作？

我会按真实的协作形式回答。**不把个人仓库自动说成"全部都是我完成"，也不在团队项目里把成果都揽到自己身上。**

需要讲清三件事：项目是个人开发、团队协作还是开源协作；我拥有的范围是什么；其他参与者、上游代码或 AI 工具各自承担了什么。变更怎样通过 PR、Issue、review、branch 或测试来控制，也只说真实用过的流程。

即使是个人项目，也可以接着说明：我是怎样拆需求、审查 diff、做回归和冻结版本的。**团队协作的重点不是人数，而是接口、责任和交付怎样对齐。**

#### 你用了多少 AI Coding？代码是不是模型生成的？

这道题我会直接说真实情况，不把使用 AI Coding 当成需要回避的事。**关键不是模型有没有生成代码，而是我是否仍然对设计和合入结果负责。**

回答时，我会先说明自己实际用过哪些工具，它们主要参与了哪些环节，比如方案探索、候选实现、补测试、代码审查或文档整理。这里不需要报一个听起来精确、实际上无法核验的"AI 生成比例"。

然后我会讲一个真实例子：模型给出的第一版方案是什么；它为什么没有满足某个并发、状态、兼容性或失败语义的约束；我做了怎样的修改；最后又通过什么测试、Trace 或实验确认了结果。这个例子，比强调"代码是不是我逐行手写"更能证明能力。

所以我的判断是：**AI 可以承担生成工作，但需求边界、关键接口、差异审查、测试执行和合入决定，仍然要由人来承担。**没有亲历过的例子就不编，按真实的使用范围回答即可。

#### 怎样证明你不是只会调用 AI 改代码？

我会用三类证据来回答。第一，我能从第一性原理去解释一个设计，比如为什么 Session、Checkpoint 和 Memory 不能合并。第二，我能沿着真实源码和测试，讲清控制流、失败语义和边界，而不是只复述 README。第三，我能讲一条不符合预期的实验，以及为什么当时没有挑好看的指标来宣传。

现场不需要覆盖整个仓库。**我会选自己最熟的一条链路，从入口讲到核心状态，再用一个测试或 Trace，说明失败是怎样被发现的。**

**AI 可以提高实现速度，但不能替我决定正确性、证据和责任。**能把这三件事讲清楚，比强调"代码都是我手写的"更可信。

### 如果重做一遍 Pico，你会删掉什么、先做什么？

我会先冻结一个更小的 vertical slice：CLI 单入口、统一 Turn、少量高质量 Tools、Session、Trace 和确定性 Eval。等这条链在真实任务上稳定以后，再增加 TUI、Channel、Memory 和 Evolver。这样能更早建立基线，**避免功能增长的速度快于证据积累的速度。**

我也会更早把状态域和 Claim Gate 写成 contract。Context、Memory 和成本优化，先进入 observe，再做单变量的 treatment。历史兼容模块如果不再拥有主路径，就明确标成 compatibility 或者删除，**避免"源码还在"被误解成当前仍在运行的能力。**

这道题不是要求否定项目，而是看我能不能识别出复杂度的来源。如果我真的经历过一次删除或收敛，可以再补那个真实例子；没有的话，就保留项目级的判断。

#### Pico 下一步最值得做什么？

我会按可靠性来排序。第一，完成完整的发布验收，收敛 Alpha 状态。第二，补上持久的 Delivery Outbox、通用的高风险 Tool policy 和更强的默认 Sandbox。第三，为长任务增加可恢复的步骤状态，以及幂等的外部动作协议。第四，扩充代表性的真实任务 Eval，重新验证 Context、Memory 和 CallEfficiency，不沿用旧的候选结论。第五，再扩展 Evolver 的 Candidate Label 和 canary activation。

如果面试官问的是我个人的计划，我会只保留自己真实准备做、也有能力落地的部分，并说明优先级依据。**Roadmap 不是已完成的能力，也不是未经承诺的时间表。**

#### 怎么把 Pico 写进简历才不夸大？

项目定位可以写成："Pico：Python local\-first Agent Harness。将 CLI、TUI、Gateway、Cron 与 Subagent 请求统一为 Turn，围绕 Spine/Lane、Agent Loop、Context、Session/Checkpoint、Tools/MCP、Tracing 与 PicoBench 建立可恢复、可审计的执行链。通过成对 Trial 与独立 Verifier，区分任务结果、测量有效性和正向 Claim 资格。"

个人 bullet 只能写我真实负责的范围。更自然的结构是：先写我负责的模块和我要守住的不变量，再写一项可核验的实现，最后写对应的测试、实验或负结果。**只补入一条能由提交、测试或实验支持的真实个人贡献，不要把项目的公共证据自动算到自己名下。**

**没有完整实验合同的百分比，宁可不写。**候选版本的结果，不能改成当前实现的普遍性能；Myna、Context 这些公共证据，也不能自动算成个人贡献。

#### 为什么这个项目能证明你适合 Agent Infra 岗位？

这个项目能证明的，不是我接过多少模型 API，而是**我能把 Agent 的问题还原成 Runtime 工程**：控制循环、状态所有权、工具副作用、恢复、可观测和评测。我能从一条 Turn 讲到 Provider、Tool、Session 和 Delivery，也能主动说明哪些事情当前做不到。项目保留了负面实验，说明**最终文本和单次 Demo，不会被当成充分证据。**

个人部分，只需要落到一个最能证明能力的真实范围。然后我会讲，这套方法怎样迁移到业务 Agent 上：先定义任务和失败边界，再选择框架与工具，最后用 Verifier 和发布 Gate 闭环。这样既能说明能力，也不会把整套项目架构自动认领成个人经验。

## 十六、Agent Runtime 基础

<!-- REPOAGENT-NOTE-START -->
> **本项目实现备注：** 当前主路径具体例子统一为：Node / React / Ink 与 Python 双进程、spawn 异步新 Turn 回流、五种 tracing outcome、三模式 CallEfficiency、非持久 DeliveryHub、仅 runtime 有可执行评估链。Textual、delegate、SQLite 目录回执和旧标签仅属于 compat。Myna 与真实平台上线仍需额外验收。
<!-- REPOAGENT-NOTE-END -->

### Agent Runtime 看起来很新，它背后真正依赖哪些经典工程原理？

我不会把这些基础题看成和 Agent 项目无关的八股。我的判断是，**Agent Runtime 里很多看起来很新的设计，底层都是经典工程约束**。Transformer 的注意力和 Context 容量，解释了为什么要选择信息；检索模型解释了 Memory 和 Skill 怎样被召回；并发模型决定 Provider、Tool 和多会话怎么调度。存储和分布式语义，决定恢复、幂等和 exactly\-once 为什么困难；隔离技术决定 Sandbox 能承诺什么；状态机和事件驱动，决定复杂生命周期能不能被解释清楚；测试理论决定非确定系统怎么获得可信证据。所以这些题不是换了个壳的八股，把它们串起来，才讲得清 Pico 为什么这么设计。

先看 Context Window。它是模型架构、训练方式和服务系统共同形成的有限资源。就算长上下文一直在进步，输入变长仍然会增加显存、计算、延迟，还会稀释注意力。工程上不能把“窗口上限更大”理解成“所有信息都应该放进去”。这正是 Context Engineering 需要做预算、做选择、依赖外部状态的根本原因。

再看检索。BM25 依赖词频、逆文档频率和长度归一，对类名、错误码和精确关键词来说稳定、便宜、可解释。向量检索擅长语义近邻，但可能漏掉精确符号，也受 embedding 版本、索引时效和作用域影响。Reranker 会对候选重新打分，RRF 则按排名融合多个检索器。RAG 是“先检索到证据、再生成”的模式。Memory 还要额外处理来源、更新、冲突、跨任务作用和写入生命周期。所以技术选型应该由语料和失败模式决定，不该默认等同于向量库。

并发方面，Agent 大部分时间都在等 Provider、网络 Tool 和文件 I/O，Python 的 `asyncio` 很适合管理这种大量等待型任务。CPU 密集、阻塞 SDK 或不可信代码，应该卸载到线程、进程或 Sandbox。协程共享进程状态，切换成本低；线程受 GIL 和共享内存风险影响；进程隔离更强，但通信成本更高。而且真正的系统上限，往往先来自 Provider 配额、Tool 延迟、队列和状态冲突，而不是语言本身。

再讲 Backpressure。它说的是当生产速度超过消费能力时，系统要怎么限制、排队、拒绝或降级。没有背压，Lane pending、Delivery queue、Tool 输出、Trace 和 Subagent 都可能无限增长。固定队列容量、按来源加 semaphore、超时、速率限制、优先级和负载削减都可以用，但被拒绝请求的语义必须对用户可见。Pico 现在有 OriginPools 和每个 Outlet 的有界队列，Lane pending 还没有硬上限，这是一个明确缺口。

持久化方面，原子写防止读到半成品，文件锁用来序列化并发读写，content/epoch fence 防止基于旧状态的 writer 覆盖新状态，它们解决的问题不一样。幂等是说，重复执行和只执行一次，业务效果等价。exactly\-once 是对最终可观察效果的承诺，跨网络和外部系统时，通常要靠幂等键、Outbox、事务消息、receipt 和状态查询来逼近，不能只靠“代码只调用一次”。

隔离方面，容器共享宿主机内核，适合做资源和文件系统隔离；microVM 有独立内核，边界更强，但启动和资源成本更高；Kubernetes 负责集群调度、服务发现和资源治理，它不会自动提供不可信代码安全；Workspace 路径检查只是应用层 guardrail。选哪一层，要看威胁模型、启动延迟、密度和成本。

如果要把 Pico 改成分布式服务，最大的变化不是多部署几个 Pod，而是所有权从本地对象变成租约和持久状态。Lane 需要分布式 claim，Session 进入事务存储，Tool 进独立 worker，Delivery 用持久 Outbox，Runtime 用 heartbeat 处理 stale claim，Trace 集中导出，Secret 和租户身份也要分开。否则两个实例可能同时处理同一个会话，重复产生副作用。Python 是否继续保留，应该由 profiling、团队能力和 SLO 决定，而不是语言信仰。

状态机适合 Agent，是因为 Turn、Tool、Delivery、Approval 和 Evolution 都有互斥的终态和合法迁移；大量布尔变量很容易拼出不可能的组合。事件驱动适合多入口，因为提交、执行和呈现可以通过类型化事件解耦。但事件不等于事务，顺序、重复和持久化仍然要处理。Pico 的 Turn terminal、RunnerEvent 和 Delivery Outcome 就体现了这些原则。

最后看测试。非确定的 Agent 比普通后端难测，因为模型、Provider、网页、时间和 Tool 环境都会漂移。解决办法不是要求每次文本完全一样，而是分层验证：确定性单元和契约测试覆盖 Runtime 不变量，Fake Provider 覆盖控制流，真实 Provider Trial 覆盖端到端行为，Verifier 检查外部状态，多次运行和成对设计估计分布，Artifact 锁定原始观察。越接近真实环境，越要分清楚 Product failure、Provider failure 和 Infrastructure failure。

所以我的结论是：Agent 带来的新问题很多，但可靠答案仍然建立在信息检索、并发、持久化、隔离、状态机和验证这些经典工程原理上。**把这些基础讲清楚，才有能力解释 Pico 为什么这样设计，也能判断换一个框架或部署形态以后，哪些结论仍然成立。**

### Transformer 为什么会有 Context Window 限制？

我理解的 Context Window 限制，本质是物理资源边界。Transformer 一次推理要处理一组 Token 表示，注意力和 KV Cache 都会消耗计算和显存。不同架构、不同推理优化会改变复杂度，但任何模型都存在一个最大可接受的序列长度，以及实际注意力质量的边界。还有一点容易忽略：即使窗口上限很大，长序列里的信息也可能因为位置、噪声和相互干扰，变得难以被稳定利用。

所以 Agent 不能把长期状态只放在模型窗口里。Session 负责完整持久化，Context 按当前目标挑选，Memory 和 Artifact 保存跨窗口的信息，工具按需去读真实环境。更大的窗口会调整压缩的阈值，但不会替代状态的所有权。

Pico 的 Context Engine 正是围绕“窗口有限、高信号更重要”来设计的。不过也要说清楚：它的候选实验表明，过度压缩可能让结果退化，**理论上的原则，不能直接等同于当前策略一定有效**。

#### BM25 的核心原理是什么？为什么适合 Skill 检索？

BM25 是稀疏检索方法，核心是三项：词项频率、逆文档频率和文档长度归一。某个查询词在文档里出现得越有区分度，得分通常越高；高频公共词贡献低；长度归一化避免长文档只因为词多就占便宜。

为什么适合 Skill 检索，我的回答是：Skill 通常有名字、描述和明确的关键词，而 BM25 对类名、命令、错误码、工具名和专业术语很有效，索引简单、可解释，也不需要 Embedding 服务，所以是合理的基线。中文要注意分词或字符级策略，Pico 的 Local Skill 检索做了 CJK\-aware 处理。

局限是同义改写和纯语义匹配比较弱，**所以要不要加向量检索或 LLM rewrite，应该由召回失败的数据决定，而不是默认把方案搞复杂**。

#### 向量检索是怎么工作的？它有什么风险？

工作方式是用 Embedding 模型把 query 和文档都映射到向量空间，再按余弦相似度、内积或距离找近邻。好处是能匹配语义相近但词面不同的内容，适合自然语言知识和经验。

风险方面我会数几个：Embedding 模型和业务语料不匹配、精确标识召回差、索引过期、跨作用域泄漏，还有“相似但错误”。另外要记住，向量分数不代表事实权威，也不代表 freshness。所以召回之后仍然要过滤 scope、去重、rerank、验证 Source。

我自己对动态代码库的做法是，把向量检索当辅助，把实时文件和符号搜索当事实来源。**Memory 也不能只靠向量相似度决定要不要注入**。

#### RAG 的标准链路是什么？它和 Agent Memory 有什么不同？

标准 RAG 链路一般是：先解析和分块数据，建 Embedding 或倒排索引，做候选检索，可选 rerank，最后把结果拼进生成 Context。它解决的是“怎么从外部语料取到相关信息”这个问题。

Agent Memory 不一样，它还要回答很多生命周期问题：写什么、什么时候写、作用域、版本、冲突、遗忘、Source，以及对未来任务的影响。Memory 可以借用 RAG 的检索，但生命周期要完整得多。Session 历史可以被检索，但还算不上长期 Memory。

所以我的判断是：**“用了向量库”只能说明你有了某种检索基础，不能说明你实现了可用的 Memory**。Pico 把 Session、Myna Memory 和 Local Skill 分开管理，就是为了避免这种概念混淆。

#### Reranker 和 RRF 有什么区别？

这两个是不同层面的东西。Reranker 对一组候选重新评分，它可以用 cross\-encoder 或 LLM，把 query 和文档结合起来做更细的语义判断，效果更准，但计算也更重。RRF 不重新理解内容，它只按多个检索列表里的排名做融合，比如一条文档在好几个列表里都靠前，就获得更高分。优点是简单，而且不需要校准不同检索器的分数尺度。

常见的混合检索做法是：先用 BM25 和向量检索拿候选，用 RRF 融合，最后用 Reranker 精排。**要不要上这套，取决于候选量、延迟和任务收益**。

### asyncio 为什么适合 Agent Runtime？

核心原因是：Agent Runtime 的大量时间都在等，等 Provider、等文件、等网络、等 MCP、等 Subagent，属于典型的 I/O 密集。`asyncio` 可以用单线程事件循环管理大量等待型任务，省掉每个请求一个线程的开销，还天然支持超时、取消和流式事件。

但我要补一句：异步不等于自动并发安全。共享的 Session、Workspace 和队列，仍然需要锁、Lane 和所有权来保护；阻塞的 CPU 或同步 SDK 必须挪到线程或进程里，否则会卡住事件循环；取消也要能传到子任务和外部资源。

Pico 的做法是：用 Lane 保证同一会话的顺序，用 OriginPool 限制容量，让异步 Tool 和 Provider 并发做 I/O。**真正的难点是调度语义，不是把函数改成 ****`async def`**** 就完事**。

#### 线程、进程和协程在 Agent 系统里怎么选？

我一般这样切分：协程适合高并发 I/O、还要共享内存的 Runtime 控制流；线程适合封装阻塞库或少量并行 I/O，但要面对共享状态和 GIL 的问题；进程适合 CPU 密集、崩溃隔离或不可信扩展，代价是通信和启动成本更高。真要强安全隔离，通常还要容器或 VM，普通子进程不够。

落到 Agent 系统：Provider 和网络 Tool 用协程，Myna 的同步操作可以 offload 出去，代码执行放进 Sandbox 进程或 microVM，TUI 前端和 Python Runtime 用 RPC 分进程。**选择依据是资源和故障域，不是语言偏好**。

#### 什么是 Backpressure？Pico 哪些地方需要它？

Backpressure 的意思是，当下游处理不过来时，上游要减速、排队、拒绝或丢弃，而不是无限地继续生产。Agent 系统里，用户请求、Cron、Subagent、流式 Token、Tool 输出和 Channel 发送，都可能造成压力。

Pico 现在的实现：OriginPool 限制并发，DeliveryHub 给每个 Outlet 配了有界队列，**但 Lane 的 pending queue 目前还没有硬容量**。后果是：没有背压时，同一会话持续提交会让内存一直涨，慢的 Channel 也会积压消息。可用的策略有容量上限、优先级、429 或繁忙提示、丢弃低价值更新、持久队列。

还有一条原则必须强调：**背压要配上用户可见的语义，悄悄丢任务比明确拒绝更危险**。

#### 为什么用 Python 写 Agent Runtime？高并发会不会不够？

Python 的优势先说清楚：模型 SDK、数据和 Agent 生态都最丰富，异步 I/O 开发快，很适合本地 Harness 和快速验证。Pico 主要就是等 Provider、Tool 和网络，`asyncio` 已经能覆盖大量 I/O 并发。而且性能瓶颈通常先出现在模型延迟、外部工具和状态设计上，而不是 Python 的指令吞吐。

但也要承认边界：如果面向大规模多租户控制面，Python 的进程、GIL、内存和部署模型可能成为限制。应对办法是把 CPU 密集和不可信执行放进进程或 Sandbox，把高吞吐的 Gateway 或队列拆成 Go/Rust 服务，或者水平扩展 Runtime。**是否重写，应该由 profiling 和 SLO 决定，而不是预设“Agent 都应该用 TypeScript 或 Go”**。

### 幂等和 Exactly\-once 有什么区别？

我用一句话先区分：**幂等是说同一操作重复执行，最终效果和执行一次相同**，比如用同一个 key 创建资源，最后只得到一个对象。**exactly\-once 是说系统对外可观察地只发生一次**。跨网络和故障时，exactly\-once 很难直接保证，通常是用 at\-least\-once delivery，再加上幂等、事务和去重来逼近。

对 Agent 来说，重试 Tool 或恢复任务时，幂等是关键。读操作天然幂等；写文件可以用 base hash 加原子替换；远端写入需要 idempotency key 和 receipt。没有这些，Runtime 就不能从“没有收到响应”推断出“没有执行”。Pico 对任意外部副作用不承诺 exactly\-once，这个边界我认为是对的。

#### 原子写、文件锁和内容 Fence 分别解决什么？

我的理解是，这三个东西解决的问题不同。原子写保证读者看到的要么是旧版本，要么是完整的新版本，不会看到写了一半的文件；文件锁用来协调并发的读写，减少同时更新带来的冲突；content 或 epoch fence 确保写入者基于最新版本操作，防止一个迟到的进程把后来的修改覆盖掉。**只上一个锁，不等于持久化就安全了**。

还要注意，进程崩溃、NFS 语义、跨机器锁，都是额外要处理的情况。Pico 的 Session 对追加、重写、尾部半记录和 stale writer 都分别做了处理，比简单 `open(..., "w")` 可靠，但它仍然是本地文件模型，不是分布式事务存储。

### 容器、microVM 和 Kubernetes 在 Agent 场景里分别做什么？

我的分层答案是这样。容器提供进程、文件和资源隔离，启动快，但共享宿主机内核；microVM 提供更强的内核隔离，适合跑不可信代码，成本也更高；Kubernetes 负责大规模调度、服务发现、配额和生命周期，**它本身不是代码安全 Sandbox**。

放到 Agent 场景：可以让每个任务进一个短生命周期的容器或 microVM，只挂载最小的 Workspace 和 Secret，限制网络与资源，由 Kubernetes 负责分配这些执行单元。这里有个常见误解要澄清：**只把 Agent 部署进 K8s Pod，并不会自动防止 Pod 内代码读取挂载的凭据或访问网络**。

#### 如果要把 Pico 改成分布式服务，最大的架构变化是什么？

最大的变化不是部署层面，而是**所有权模型：从本地对象变成可租赁、可恢复的分布式状态**。具体说：Lane 需要持久队列或分布式租约；Session 要从文件迁到事务存储；Tool 执行进独立 Worker 或 Sandbox；Delivery 用持久 Outbox；Trace 导出到集中系统；Secret 和租户权限独立管理；Scheduler 还要处理 worker heartbeat、stale claim 和幂等重放。

所以不能只把现有进程放到多个 Pod，因为两个实例可能同时处理同一个会话、写同一个 JSONL，或者重复执行外部动作。**分布式化首先改变一致性和故障模型，然后才谈部署形式**。

### 为什么使用状态机比大量布尔变量更适合 Agent？

我会先说布尔变量为什么出问题：它很容易产生非法组合，比如 `completed=true`、`cancelled=true`、`provider_failed=true` 同时出现，系统就不知道自己到底处于什么状态。**状态机明确列出允许的状态和转换，能定义每个状态的所有者、进入条件、终态和补偿**。

Agent 里其实有好几套生命周期：Turn、Tool、Delivery、Approval 和 Evolution。我倾向分别建模，再用 ID 关联，而不是堆一个全局 `success`。Pico 的五种 Turn terminal、Delivery Outcome 和 Evolver 状态就是这种思路。

状态机还好测：每条转换、取消时序、重复事件都能覆盖。再补一句原则：模型可以建议动作，但不能任意改写生命周期状态。

#### 事件驱动架构为什么适合多入口 Agent？

多入口 Agent 的场景是：CLI、TUI、Channel 和后台任务都在异步产生请求、流式输出、Tool 进度、Subagent 结果和 Delivery 回执。**事件驱动能解耦生产者和消费者**，CLI、TUI、Channel 各自选择呈现方式，同时共享同一套 Runtime 生命周期。

但也要泼一盆冷水：事件必须有 schema、顺序、幂等和所有者，否则“发事件”只是把调用关系藏起来了。Pico 的做法是限制 Runner 只能发 RunnerEvent，Turn lifecycle 归 Spine 拥有，TUI Notification 也不等于任意广播，都是为了防多个组件抢状态。

代价也要说清楚：**事件架构让调试和一致性更复杂，所以 Trace 和稳定 ID 不可少**。

#### 为什么 Agent 项目里测试非确定性比普通后端更难？

难在哪里：普通后端在相同输入和状态下，通常应该产出确定输出；而 Agent 的模型决策本身有随机性，外部工具和网络也一直在变。所以失败可能来自模型、Harness、Provider、环境或 Verifier，单次复现很难。

解决思路是分层，不要追求每次文本都一样。具体说：确定性地测试 Runtime 状态机；用 Scripted 或 Fake 模型测试 Harness；冻结任务和环境做多 Trial 的真实模型评测；按 failure class 记录；使用独立 Verifier 和置信区间；还要保留轨迹，不能只存总分。**这就是为什么 Agent 工程要同时具备传统测试和实验方法，两者缺一不可**。



<!-- REPOAGENT-NOTE-START -->
## 本项目业务与 Benchmark 补充（新增）

以下是本项目历史业务运行时的可用话术。六项主路径迁移没有重跑这些实验，不能把下列成绩归因于新主路径。数据来自本地保存的报告、汇总和评分凭证；本文整理没有重新运行模型或实验。原始产物位于本地 `artifacts/`，通常不随 Git 提交；链接供本机核对，分享材料时需另行准备脱敏证据。

### 1. 你的业务背景是什么？为什么针对代码仓库？

我的目标用户是需要处理代码问题的开发者和开源维护者。实际工作不只是生成代码，还要复现错误、理解仓库约束、生成有范围的修改，并把补丁和测试证据交给维护者审核。

因此我把通用 Harness 放到代码仓库这个具体场景里：仓库版本是工作基线，文件和测试是事实来源，模型负责调查与提出修改，Runtime 负责工具、权限和预算，独立验收负责判断候选是否满足要求。目前是本地 CLI 原型，没有接成自动回复 Issue、创建 PR 或合并代码的线上机器人。

**依据：** [业务说明](docs/issue-maintainer-demo.md) · [Case 与版本绑定](repoagent/issue_agent/cases.py)。

### 2. 一个 Issue 从输入到候选补丁，具体怎么走？

我先保存 Issue 快照，把目标仓库绑定到不可变提交，再由维护者提供固定验收检查。`investigate` 负责隔离调查和复现；维护者明确执行 `fix`，才进入候选修复。Agent 产生的修改会导出补丁，再在干净环境里应用并执行独立检查。

最终的 `candidate_ready` 表示候选通过了当前检查，适合进入人工 review。模型输出“修好了”、正常退出、通过本地自写测试，都不能替代这个验收状态。整个过程中会保存 Case 状态、模型调用、补丁、基线失败和修复后检查结果，原仓库不会被自动改写或发布。

在这条业务路径里，模型凭据由宿主代理持有，代码执行使用 Docker 隔离。Case 锁管理本地同案例执行；底层会话 Scheduler 是另一层能力，不能把原型说成已经部署的多用户服务。

**依据：** [工作流](repoagent/issue_agent/workflow.py) · [执行与验证](repoagent/issue_agent/execution.py)。

### 3. 有没有一个可以详细讲的真实业务案例？

有，我用 python-dotenv 的一个历史问题验证了从调查到修复的完整链路。原始版本在固定检查下出现预期失败，Agent 完成调查并修改 `src/dotenv/main.py`，导出的单文件补丁在干净环境里通过了五项维护者预设检查。

最终成功这次，调查用了 11 次模型调用，修复用了 11 次，共 22 次；按当时冻结价格口径估算模型费用约 0.0622 美元。这是一个问题的五种行为检查，不是五道题。之前也有失败开发尝试，累计记录了 53 次调用，不能把最终一次说成首次尝试成功。

这个案例最适合证明业务链路能走通。跨仓库修复能力则看下面的公开任务评测，不能只从一个案例推导。

**依据：** [案例报告](docs/issue-maintainer-demo.md#measured-result-2026-09-14) · [本地 Case](artifacts/issue-demo-live/cases/issue_c955ff15d3f244d488b1ae7a/report.json)。

### 4. 你跑了哪些 benchmark？最主要的结果是什么？

我主要讲两批 SWE-bench 自测，另外保留了后续配对诊断和未完成的消融实验。两批都使用原生 RepoAgent 加评测适配层，最终由独立的官方 SWE-bench 评分器判断 resolved。

| 实验 | 固定范围与实际执行 | 结果 | 用途与边界 |
| --- | --- | --- | --- |
| SWE-bench Verified 自选分层子集，2026-09-17 | 固定 50 题、10 个仓库；恢复后 47 题调用模型，3 题镜像下载失败未运行 | 原始 34/50；基础设施恢复后确认 36/50，按计划分母为 72% | 固定子集自测；68%→72% 来自首次尝试覆盖补齐，不是策略提升 |
| HAL SWE-bench Verified Mini，2026-09-18 | 固定 50 题：Django 25、Sphinx 25；50 题全部执行并评分 | **36/50，72%**；Django 21/25，Sphinx 15/25 | 采用 HAL 固定题单和冻结版本官方评分器的自测，不是官方榜单认证 |
| 新旧 Harness 配对诊断，2026-09-18 | 10 组已知任务，两版分别独立尝试并验收 | 旧版 8/10，新版 7/10 | 没有证明组合改动提升能力；不能替换 HAL 原始成绩 |
| 四组消融诊断，2026-09-18 保存记录 | 计划 6 题×4 组，共 24 次；当前汇总只有 16 条终止记录 | `complete=false`，含阻塞与评分超时歧义 | 尚无完整结果，不用于简历提分数字 |

**主要依据：** [自选 50 题报告](artifacts/swebench-verified50-20260917/REPORT.md) · [合并证据](artifacts/swebench-verified50-20260917/combined-evidence.json) · [HAL 报告](artifacts/swebench-hal-mini-20260917/REPORT.md) · [配对报告](artifacts/harness-paired-20260918/REPORT.md) · [消融分析](artifacts/harness-ablation-20260918/analysis.json)。

### 5. 面试时怎么完整介绍 72% 这个结果？

可以这样说：

> 我在 HAL SWE-bench Verified Mini 固定 50 题上做了一次本地自测，25 题 Django、25 题 Sphinx。使用 DeepSeek 官方 API，当时记录的请求模型标识为 deepseek-flash，冻结说明对应 V4.1-Flash；每题本批次一次模型尝试，最多 60 次模型调用。50 题都实际运行并由官方 SWE-bench 评分器验收，其中 36 题 resolved，修复率为 72%。我保留了题单、源码快照、候选补丁、调用用量和逐题评分日志。

追问配置时补充：单次输出上限 8192，序列化输入上限 128000 **字节**，单题推理超时 2400 秒、评分超时 900 秒。模型只接收公开题面字段与待修复源码，参考补丁和评分侧测试补丁不提供给推理端。每题一次尝试指一次完整修复过程，其中可以有多轮模型调用，不是只问模型一次。

这批共 1813 次模型调用，记录的估算模型费用为 **2.780362392 美元**，不等于账单，也不含机器、镜像下载及开发实验费用。Skills、Memory、Evolver 全部关闭，所以结果只归于当时冻结的修复链路。模型别名的含义按实验当时的记录解释，不推断其未来对应版本。

**题单与评分冻结版本：** HAL `16bb03ebc11577fb5ea6dc8bb6c968387085e6aa`；Mini 数据集 `b316c349947c29963fce3f4a65967c9807a4b673`；官方评分器 `02e7a74ffd0b707aab73d203fe87bdc7c76afc8e`。不是完整复刻 HAL 历史榜单环境。

**依据：** [冻结方法](artifacts/swebench-hal-mini-20260917/METHOD.md) · [50 题汇总](artifacts/swebench-hal-mini-20260917/summary.json)。本次整理逐一检查了该汇总对应的 50 份官方 `report.json`，resolved 合计一致为 36。

### 6. 两批都是 72%，能合并成更大样本吗？能和别人的 80% 比吗？

我会分开报告。自选 50 题与 HAL 50 题不是同一题单，彼此重叠 6 题。HAL 这一轮全部独立运行，没有复用旧补丁，但这些题已有接触历史，不能称为全新未见测试集。自选批次还有 3 题没有实际调用模型，更不能把两批直接合并成 100 道独立题。

与外部成绩比较时，也需要统一模型、任务、预算和评分器版本。目前只能陈述我自己的固定条件结果，不能从 72% 与某个截图里的 80% 直接推导 Harness 差距。公开历史题也可能进入模型训练，本次没有排除训练污染。

自选批次如果被问到实际执行比例，我会说明 36/47≈76.60% 是诊断口径，主报告仍使用预先固定的 50 题分母。基础设施故障保留原记录，恢复只补齐零模型调用任务的首次尝试，没有对已失败修复做 best-of-N。

### 7. 有没有优化没有奏效的案例？你怎么判断？

有。完成 HAL 50 题后，我把持久沙箱、测试解释器、结构化测试结果、动态反馈和阶段提醒等改动组合起来，做了 10 组新旧版本配对。有效结果是旧版通过 8 题，新版通过 7 题；新版多通过 1 题，也少通过了 2 题，净少 1 题。有记录的估算费用也从约 0.6812 美元增加到 1.4051 美元。

因此我没有把“功能补齐了”说成“解题能力提高了”。结构化测试报告确实运行了，但新增反馈也可能增加输入、干扰稳定缓存前缀，具体原因还要拆分实验验证。后续把改动拆为 old、infra、feedback、phase 四组；当前只保留部分结果，不能先宣布哪个策略最好。

这轮还暴露过评分调度问题：两版同时评分同一题时，官方容器命名冲突。修正评分调度后，对冻结补丁重新独立验收，没有重做模型修复。最后一组遇到余额不足，也单独保存恢复批次，原始失败记录未覆盖。我的处理原则是把模型结果、评分环境和费用缺失分别记录。

**依据：** [10 组配对结果及恢复说明](artifacts/harness-paired-20260918/REPORT.md) · [消融协议](artifacts/harness-ablation-20260918/METHOD.md)。这些是已知题诊断样本，不是新的 HAL 得分，也不代表最新提交已完成同样的效果重测。

### 8. 自进化实验和 SWE-bench 有什么关系？

它们是两条独立证据。底层 Evolver 有一次小规模真实功能验收，训练结果从 8/20 到 12/20，已知验证划分从 2/8 到 4/8；验证集只有四个独立任务、每题两次试验，配对 z=1，没有统计显著性信用，也没有激活。

它说明受控候选生成、训练、验证和记录链路能运行，不能证明持续自我改进。两批正式 SWE-bench 都关闭了 Evolver，所以不能说 72% 是自进化带来的，也不能把这组已知验证划分说成全新盲测。

**依据：** [实现台账 TECH-175](docs/architecture/implementation-ledger.md#tech-175-fresh-v41-flash-end-to-end-acceptance-2026-09-14) · [独立实验目录](artifacts/upstream-protocol-20260914/evolution-v41-live/)。

### 9. 全量回归和 BoxLite 验收能证明什么？

2026-09-26 工作区全量回归是 1958 通过、67 跳过，离线 runtime-contract 演示 12/12 通过。后来新增的真实 BoxLite E2E 单独运行通过：父 Agent 在 VM 里修复代码并运行测试，MCP 共享 VM 状态，子 Agent 在独立工作区读写；新进程恢复同一 JSONL 会话，历史由 9 条追加到 13 条，退出后回收 VM 和宿主进程。

这条 E2E 使用预设模型响应，证明真实执行链路和存储／清理机制正常；修复能力仍看真实模型 benchmark。SDK 的已知限制另有记录，不能把一次正常流程验收说成全部隔离问题都已解决。全量数字来自当时工作区，包含其他开发中的改动，不是一次独立干净提交的完整效果评测。

**依据：** [BoxLite 验收记录](docs/boxlite-acceptance.md) · [端到端用例](tests/test_boxlite_e2e_live.py)。

### 10. 如果简历只留一句业务介绍和一句实测结果，怎么写？

**业务：** 基于 Agent Harness 构建面向代码仓库维护的 Issue 调查与候选修复原型，串联不可变版本绑定、隔离执行、模型预算和独立补丁验收，保留可审查的执行证据。

**实测：** 在 HAL SWE-bench Verified Mini 固定 50 题上完成单次尝试自测，使用冻结配置的 DeepSeek 模型并经官方 SWE-bench 评分器验收，36 题解决（72%），保留逐题补丁、评分日志与调用成本记录。

面试时补齐模型标识、预算、题单重叠和版本范围；个人贡献按实际参与说明。原稿的 Context、Memory、缓存收益等数字继续作为学习案例，不放进自己的成果描述。
<!-- REPOAGENT-NOTE-END -->
