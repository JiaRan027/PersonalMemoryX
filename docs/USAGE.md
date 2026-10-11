# PersonalMemory 功能与使用指南

> 适用版本：v0.2.10 Alpha，Windows 10/11 x64。界面标题以中文显示；英文界面可在“设置”中切换。

## 功能一览

| 页面 | 用途 |
| --- | --- |
| 首页（Overview） | 查看本机记忆数量、连接状态、近期活动和需要你判断的条目。 |
| 接入（Connections） | 为支持的 Agent 配置持续连接；查看最近处理状态；跳转到本机来源扫描。 |
| 记忆识别（Memory recognition） | 只读扫描本机 Agent 记忆或会话文件，预览提取结果后再导入。 |
| 记忆（Memories） | 检查内容、来源和证据；处理待判断条目、纠正内容或删除记忆。 |
| 图谱（Graph） | 浏览记忆之间的关系，选择节点查看对应记忆。 |
| 活动（Activity） | 查看来源处理和记忆治理记录；部分成功导入可撤销。 |
| 迁移（页面标题“数据与安全”） | 导入/导出受支持的记忆数据，或为另一个 Agent 生成短期 Continuity Capsule。 |
| 设置（Settings） | 切换界面语言，调整 Windows 登录后启动和启动时显示方式。 |

## 安装并连接 Agent

1. 按 [README](../README.md) 中的安装说明下载 Alpha 安装包，并校验随包提供的 SHA-256。
2. 启动 PersonalMemory，打开“接入”，选择“添加持续来源”，按向导选择支持的 Agent 和连接范围。
3. 按页面说明审核 MCP / Hook 配置，然后新建一个 Agent 会话，让新配置生效。
4. 返回“接入”或打开“活动”，检查是否出现新的处理记录。仅看到“已配置”不代表 Hook 已经送达。

MCP 用于按当前任务提供有限的相关记忆；Hook 用于支持的集成中的触发和采集路径。Claude Code 的集成与选择性采集已在真实会话中使用过。Codex Desktop / CLI 的 MCP 与 Hook 路径已实现，但 Hook 是否到达、是否能自然召回，仍需在你的环境中验证。任何集成都不保证捕获每条消息或每次都召回到正确记忆。

## 通过 MCP 让 Agent 读取和写回记忆

MCP（Model Context Protocol）可以理解为 PersonalMemory 与 Agent 之间的标准工具接口：Agent 在需要时调用本地工具读取相关记忆，也可以提交一条长期记忆候选。它不替代 Agent 的模型，也不会自动让另一个模型分析每轮对话。

PersonalMemory 为 Agent 提供本机 MCP stdio 服务。桌面版会管理当前支持的 Claude Code / Codex 接入；其他 MCP 客户端需要自行配置，尚不能据此推断兼容性。桌面托管模式不要求你在 Agent 配置中填写固定端点、会话 Token 或模型 API Key：Agent 使用自己的账号和授权；MCP Host 通过本机回环连接 Service，桌面端管理运行时 Token。若显式启用单独的历史数据批处理模型端点，那是另一项可选功能，不是 MCP 接入的前提。Service 需要运行，关闭主窗口只会缩到系统托盘，从托盘“退出”才会停止它。

| MCP 工具 | 适用场景 | 作用 |
| --- | --- | --- |
| `get_user_context` | 简单的个人背景、目标、能力或偏好问题 | 无参数；返回每类最多 5 条经过治理的稳定记忆。 |
| `memory_context` | 项目任务、历史原因、决策、经验或需要综合背景的问题 | 必填 `query`；可选 `namespaces`、`token_budget`（128–4096，默认 1600）和 `limit`（1–10，默认 10）。按任务编译有界上下文，并附来源、版本和选择原因；不会默认发送整个记忆库。 |
| `search_memories` | 精确查找曾记录的关键词或主题 | 必填 `query`；多词按 AND 检索，可选 `limit`（1–50，默认 10）。这是管理/查找入口；搜索结果不一定符合普通 Agent 上下文的交付条件。 |
| `remember` | 明确要求记录一条长期有效的信息 | 必填 `type`（`fact` / `goal` / `skill` / `preference` / `event` / `insight` / `decision`）和 `statement`，建议提供 `reason`。它提交 Candidate，仍经过重复、冲突、敏感度和删除状态检查，不会绕过治理直接写数据库。 |

使用时像平常一样向已接入的 Agent 提问，MCP 工具由 Agent 根据问题决定是否调用：

- “结合之前关于这个项目的决策和失败经验，分析下一步方案。”适合由 Agent 调用 `memory_context`。
- “我以前记录过哪些关于本地优先的结论？”适合由 Agent 调用 `search_memories`。
- “请记住这条长期决定：……；原因是……”可让 Agent 判断是否调用 `remember`。
- “我有哪些长期目标或偏好？”适合由 Agent 调用 `get_user_context`。

工具已配置不代表模型每次都会调用。可以在“接入”页检查本机 MCP 读取状态；发起相关问题后，再在“活动”或“记忆”中核对调用是否产生了变化。MCP 读取和 `remember` 写回是工具路径；Hook 自动采集是另一条独立路径，接入 MCP 本身不会把每轮对话自动保存为记忆。

## 扫描并导入已有记录

1. 在“接入”页面点“扫描本机来源”，进入“记忆识别”。扫描会只读访问本机发现的文件，不修改原始文件。
2. 在支持的来源旁点“提取预览”，检查识别出的候选、来源依据和提示。当前不支持的来源会单独列出，不能预览或导入。
3. 确认后逐项导入，或选择导入全部可用来源。导入候选还会经过重复、冲突和可信度检查。
4. 到“活动”查看处理结果；已接纳的内容可在“记忆”中检查、纠正或删除。

识别结果可能遗漏、误解或未能提取出记忆。预览和导入后的治理结果都应结合原始来源核对。

## 检查和治理记忆

在“记忆”中选择条目，查看内容、形成原因、来源位置和证据。首页或列表会标出需要你决定的条目；系统会把需要判断的敏感、冲突或高影响内容单独呈现。

- 对需要判断的条目，选择它应如何使用，例如作为长期记忆、项目经验、提炼后保留、仅保留来源、测试记录或暂缓判断。选择会影响它是否交付给 Agent，不会替代你对证据的检查。
- 发现表述不准确时，使用“纠正”修改内容和形成原因。修订保留版本脉络。
- 使用“删除”将不需要的记忆移出当前记忆。删除状态会用于防止相同内容被再次导入；它不等同于删除整个本机数据目录。

图谱页适合浏览已建立的记忆关系。关系图是探索入口；需要核对具体含义时，打开对应记忆详情并检查证据。

## 查看活动和撤销

“活动”记录来源处理、记忆变化和治理操作，可按类型筛选。对于已完成且符合条件的来源导入，记录旁会显示撤销操作。只有这类可撤销的导入会显示该按钮；其他操作不一定能撤销。如果相关记忆随后已发生变化，导入也可能不再可撤销。

## 把任务背景交给另一个 Agent

“迁移”页面中的 Continuity Capsule 会针对一个任务编译短期上下文，不会把整个记忆库原样发给 Agent：

1. 输入当前问题或任务；选择全部可用范围，或勾选需要的记忆范围。
2. 按需填写当前任务、已验证状态和下一步行动；设置有效期与 Token 上限。
3. 点“生成并预览”，检查实际引用的记忆、有效期和被预算排除的字段；需要时先验证或按当前记忆重建。
4. 检查交付文本后复制给 Agent，或保存为 Markdown 文件。

Capsule 是有有效期和预算限制的任务材料，不是长期记忆本身。交给云端 Agent 前，请检查文本是否包含你不想发送的内容；接收方的处理规则仍适用。

## 导入和导出数据

在“迁移”页面可导出 `.pmemory` 迁移包和可阅读的 Markdown 副本，也可选择 `.pmemory` 文件导入。导入前会验证包的完整性，并保留已删除记忆的状态。

当前 Alpha 的 `.pmemory` 协议只覆盖受支持的对象子集（包括记忆空间、记忆对象、版本、关系和删除状态），并非数据库镜像或整台电脑的备份；协议未覆盖的资料类型、来源证据和签名不会随包迁移。请保留原始数据的独立备份，不要把此功能当作唯一备份。

## 可选：记录少量经历痕迹

“接入”页中的 Claude 个人目录选项可开启“被动经历痕迹”，默认关闭。开启后只处理新对话，尝试保留少量主题短语/代码标识符与角色、时间信息，不保存完整对话，也不会自动形成正式记忆；痕迹保留 90 天。过滤规则不能识别所有敏感内容，短语本身仍可能敏感。开启前请检查说明；之后可在该连接下查看或清空痕迹。

## 当前限制

- 记忆和服务默认保存在本机，没有云同步、移动端或多用户服务。
- PersonalMemory 不以存储完整聊天记录为目标；自动采集和召回都可能遗漏或出错。
- 发送给云端 Agent 的上下文会受相应服务商的数据处理规则约束。
- Alpha 不是稳定版，也不应作为重要资料的唯一备份。

---

# PersonalMemory Feature and Usage Guide

> Applies to v0.2.10 Alpha on Windows 10/11 x64. Screen names below use the English UI labels.

## Feature map

| Screen | What you can do |
| --- | --- |
| Overview | Check local memory counts, connection status, recent activity, and items needing a decision. |
| Connections | Configure persistent connections for supported agents, inspect recent processing status, and open local source recognition. |
| Memory recognition | Read-only scan of local agent memory or session files; preview extracted candidates before importing. |
| Memories | Inspect content, provenance, and evidence; review, correct, or delete memories. |
| Graph | Explore relationships between memories and open a memory from a graph node. |
| Activity | Review source processing and governance history; undo is available for some completed imports. |
| Transfer (page title: Data & security) | Import/export supported memory data or compile a short-lived Continuity Capsule for another agent. |
| Settings | Change the UI language and Windows startup behavior. |

## Install and connect an agent

1. Follow the installation instructions in [README_EN.md](../README_EN.md) to download the Alpha installer and verify its bundled SHA-256 file.
2. Start PersonalMemory, open **Connections**, select **Add persistent source**, and follow the wizard for a supported agent and connection scope.
3. Review the MCP / Hook configuration shown by the app, then start a new agent session so the new configuration takes effect.
4. Return to **Connections** or open **Activity** and check for a new processing record. A configured connection alone does not prove a Hook was delivered.

MCP provides bounded context relevant to the current task. Hooks provide trigger and capture paths for supported integrations. The Claude Code integration and selective capture path have been used in real sessions. Codex Desktop / CLI MCP and Hook paths are implemented, but Hook delivery and natural recall still need validation in your environment. No integration guarantees capture of every message or correct recall every time.

## Use MCP to read and write memories

MCP (Model Context Protocol) is the standard tool interface between PersonalMemory and an agent: when needed, the agent can call local tools to read relevant memories or submit a long-term memory candidate. MCP does not replace the agent's model or automatically run a second model over every conversation.

PersonalMemory provides a local MCP stdio server. The desktop app manages its current Claude Code and Codex integrations; other MCP clients require manual setup and are not thereby guaranteed to work. In desktop-managed mode, you do not enter a fixed endpoint, session token, or model API key in the agent configuration. The agent uses its own account and authorization; the MCP host connects to the local Service over loopback, and Desktop manages the runtime token. An optional model endpoint for explicit historical-data batch processing is a separate feature, not a prerequisite for MCP. The Service must be running: closing the main window minimizes it to the system tray, and choosing **Exit** from the tray stops it.

| MCP tool | When it fits | What it does |
| --- | --- | --- |
| `get_user_context` | Simple questions about personal background, goals, skills, or preferences | No arguments; returns up to five governed stable memories per category. |
| `memory_context` | Project work, historical rationale, decisions, experience, or questions needing broader context | Requires `query`; optional `namespaces`, `token_budget` (128–4096, default 1600), and `limit` (1–10, default 10). Compiles bounded task context with source, revision, and selection reasons; it does not send the whole store by default. |
| `search_memories` | Finding a known keyword or topic | Requires `query`; multiple terms use AND matching. Optional `limit` is 1–50 (default 10). This is a lookup/management tool; search results are not necessarily eligible for ordinary agent context. |
| `remember` | Explicitly recording something durable | Requires `type` (`fact`, `goal`, `skill`, `preference`, `event`, `insight`, or `decision`) and `statement`; `reason` is recommended. It submits a Candidate that still passes duplicate, conflict, sensitivity, and deletion-state checks instead of writing directly to the database. |

Talk to a connected agent normally; the agent decides whether the question calls for an MCP tool:

- “Use the decisions and failed approaches we discussed before to analyze the next step for this project.” This is a good fit for `memory_context`.
- “What have I recorded about local-first design?” is a good fit for `search_memories`.
- “Remember this durable decision: … The reason is …” can prompt the agent to consider `remember`.
- “What are my long-term goals or preferences?” is a good fit for `get_user_context`.

Having the tools configured does not mean the model will call them on every turn. Check local MCP-read status in **Connections**; after asking a relevant question, inspect **Activity** or **Memories** to see whether anything changed. MCP reads and `remember` writes are tool calls. Hook-based capture is separate, and enabling MCP alone does not automatically save each conversation as a memory.

## Recognize and import existing records

1. In **Connections**, select **Scan local sources** to open **Memory recognition**. The scan reads discovered local files and does not modify the originals.
2. Select **Extract preview** beside a supported source. Review the candidates, source evidence, and warnings. Unsupported sources are listed separately and cannot currently be previewed or imported.
3. Import selected sources, or import all available sources. Candidates go through duplicate, conflict, and credibility checks.
4. Check the outcome in **Activity**. Accepted content can then be inspected, corrected, or deleted in **Memories**.

Recognition can miss, misunderstand, or fail to extract useful memories. Compare both the preview and the resulting governance decisions with the source material.

## Inspect and govern memories

Select an item in **Memories** to inspect its statement, reason, source location, and evidence. The Overview and list mark items that need a decision; sensitive, conflicting, or high-impact content that the system cannot safely decide is presented for review.

- For an item needing a decision, choose how it should be used—for example, as a long-term memory, project experience, rewritten memory, source-only material, test artifact, or an item to revisit later. This affects delivery to agents; it does not replace checking the evidence.
- Use **Correct** to revise an inaccurate statement or its reason. Revisions preserve version history.
- Use **Delete** to remove a memory from the active set. Deletion state helps prevent the same content from being imported again; this does not delete the entire local data directory.

Use **Graph** to explore relationships. Open the corresponding memory detail and review its evidence when you need to verify what a relationship means.

## Review activity and undo

**Activity** records source processing, memory changes, and governance actions, and can be filtered by type. A completed source import may show an undo action when eligible. Other operations are not necessarily undoable; an import may also become ineligible for undo after related memories change.

## Give another agent task context

The Continuity Capsule in **Transfer** compiles short-lived context for a task; it does not copy the whole memory store verbatim:

1. Enter the current question or task. Choose all available scopes or select specific memory scopes.
2. Optionally enter the current task, verified status, and next action; choose a time-to-live and token budget.
3. Select **Generate and preview**. Inspect the memory references, expiry, and any fields dropped to meet the budget; validate or rebuild against the current memory if needed.
4. Review the delivery text, then copy it to an agent or save it as Markdown.

A Capsule is task material with an expiry and token budget, not a long-term memory. Before sending it to a cloud agent, inspect the text for information you do not want to share; the recipient's data-handling rules still apply.

## Import and export data

In **Transfer**, export a `.pmemory` migration package and a readable Markdown copy, or select a `.pmemory` file to import. The app checks package integrity before import and preserves deletion state.

The current Alpha `.pmemory` protocol covers only a supported subset of objects (including memory spaces, memories, revisions, relations, and deletion state). It is not a database image or a backup of the entire computer; unsupported data types, source evidence, and signatures are not carried in the package. Keep an independent backup of your original data and do not rely on this as your only backup.

## Optional: retain a small trace of experiences

The Claude personal-directory option in **Connections** can enable **Passive experience traces**; it is off by default. When enabled, it processes new conversations only and attempts to retain a small number of topic phrases / code identifiers with role and time information. It does not store full conversations or automatically create formal memories; traces are retained for 90 days. Filtering cannot catch every sensitive detail, and a phrase can still be sensitive. Review the setting before enabling it; you can inspect or clear traces from that connection later.

## Current limitations

- Memories and the service are local by default; this release has no cloud sync, mobile client, or multi-user service.
- PersonalMemory is not intended to retain full chat transcripts. Automatic capture and recall can both miss or get things wrong.
- Context sent to a cloud agent is subject to that provider's data-handling policies.
- This is an Alpha, not a stable release, and it should not be your only backup of important information.
