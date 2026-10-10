# PersonalMemory

[简体中文](README.md) · [English](README_EN.md) · [下载 Windows Alpha](https://github.com/JiaRan027/PersonalMemory/releases/tag/alpha-channel)

> **PersonalMemory exists to preserve continuity, not just memory.**
>
> 让 AI 长期协作形成的背景，不再困在某一个平台。

PersonalMemory 是一个由用户拥有、本地优先的 AI 连续性层。它希望让重要的事实、目标、决定、经验和协作方式，在用户切换 Agent、模型或应用后仍能接续，而不是要求用户一次次从头解释。

## 为什么做 PersonalMemory

和 AI 的长期协作会逐渐形成共同背景：我们讨论过什么、为什么作出某个决定、后来怎样修正，以及哪些事情仍然没有答案。但这些背景通常散落在不同的对话和平台中。新开一个会话、换一个 Agent，往往就像从未合作过一样。

我们想解决的不是“如何保存更多聊天”，而是：**怎样把真正有助于后续协作的连续性留在用户手中，并在需要时交给合适的 Agent。**

PersonalMemory 不是另一个 AI 模型，也不打算把整段聊天永久归档。它在本机管理可追溯、可修正的记忆，再通过已连接的接口向 Agent 提供有限且相关的背景。长期愿景包括个人与 AI 的持续协作，以及更多类型的 AI 应用；当前 Alpha 仍以 Windows 上的开发者工作流为主要验证场景。

### 我们希望用户最终感受到

- 换一个 Agent 时，不必重新讲完所有重要背景。
- Agent 得到的是与当前问题相关的上下文，而不是整份记忆库或完整聊天记录。
- 用户能检查一条记忆的依据、修正过时内容，或删除不想保留的信息。
- 没有证据的内容仍然可以是未知，不会因为 Agent 的猜测就自动成为事实。

这是产品目标，不是对完美采集或 100% 召回的承诺。更多背景见[产品说明](docs/PRODUCT_OVERVIEW.md)。

## 当前 Alpha 能做什么

**版本 0.2.10 · Windows 10/11 x64**

| 能力 | 当前状态 |
| --- | --- |
| 本地连续性内核 | 在本机管理记忆、来源、修订和删除状态 |
| Agent 上下文读取 | 通过 MCP 按任务提供有界上下文，不会每次发送全部记忆 |
| Claude Code | 接入与选择性采集路径已实现，并有真实会话验收 |
| Codex Desktop / CLI | MCP 与 Hook 接入路径已实现；真实 Hook 到达及自然的跨 Agent 召回仍需用户现场验证 |
| 数据迁移 | 提供实验性的 `.pmemory` 对象子集导入与导出；不是完整数据库备份 |
| 云同步、移动端、其他 Agent | 当前版本不支持或尚未完成 |

这是 Alpha，不是稳定版。自动采集可能漏掉有价值的信息，也可能需要用户纠正。**接入已配置不等于每个 Hook 都到达；Hook 到达也不等于每轮对话都应产生记忆。**请勿把本软件作为重要资料的唯一备份。

## 安装与快速开始

1. 从 [GitHub Alpha Release](https://github.com/JiaRan027/PersonalMemory/releases/tag/alpha-channel) 下载安装包和同名 `.sha256` 文件。
2. 在下载文件所在目录打开 PowerShell，校验安装包：

```powershell
$installer = ".\PersonalMemory_0.2.10_x64-setup.exe"
$expected = (Get-Content -Raw "$installer.sha256").Trim().Split()[0].ToLowerInvariant()
$actual = (Get-FileHash $installer -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "SHA-256 verification failed" }
"SHA-256 OK: $actual"
```

3. 运行安装包并打开 PersonalMemory，在“接入”页面连接需要使用的 Agent。
4. 按页面说明审核所需的 MCP / Hook 配置，然后新建 Agent 会话。
5. 像平时一样交流。之后在 PersonalMemory 查看产生的候选或记忆，再在新会话中自然询问相关背景。不要为了“制造记忆”反复发送测试提示。

**Windows 安装提示：**安装包没有 Authenticode 证书签名，Windows 可能显示“未知发布者”。Release 附带的 SHA-256 只能发现下载损坏或文件不匹配，不能证明发布者身份。应用内更新包使用 Minisign 校验更新文件，但这不是 Windows 发布者身份或 SmartScreen 信誉。本版在线更新安装和失败回滚尚未完成端到端验收，建议从 Release 页面手动下载并安装。

## 数据如何处理

- 记忆默认保存在本机 `%LOCALAPPDATA%\PersonalMemoryData`。本项目当前不提供云端记忆存储、账号同步或跨设备恢复。
- 本机 Service 通过 loopback 与桌面端和 Agent 接入通信；MCP 为当前任务编译有限上下文，不会把全部记忆库作为每次调用的默认内容。
- PersonalMemory 不以保存完整聊天记录为目标。记忆保留来源并可在应用中检查、修正、撤销、删除和导出。
- 将上下文交给云端 Agent 后，相关内容会受到该 Agent / 模型服务商自身的数据处理规则约束。若启用外部模型能力，也应检查相应服务的隐私条款。
- 本地数据仍需要用户自行备份；Alpha 没有云恢复能力。

## 设计原则

- **连续性属于用户。**平台可以变化，用户积累的背景不应被单一平台锁定。
- **提取可以替换，治理保持稳定。**Agent 可以提出记忆，PersonalMemory 负责来源、冲突、范围、修订和删除治理。
- **依据比来源品牌重要。**语义权威取决于是谁表达了什么、有什么证据，而不取决于内容来自哪家 Agent。
- **用户明确纠正优先。**新事实可以替代旧的或推断出来的描述，同时保留必要的修订关系。
- **不确定性应当保留。**缺少证据时，不把推测包装成确定事实。

## 从源码运行

当前构建目标为 Windows。开发环境、测试命令和安装包构建说明见[构建指南](docs/BUILDING.md)。欢迎通过 [GitHub Issues](https://github.com/JiaRan027/PersonalMemory/issues) 提交可复现的问题或改进建议；请勿上传真实记忆数据库、私人对话、API Token、签名密钥或未经脱敏的日志。

## 许可证

PersonalMemory 使用 **AGPL-3.0-or-later**，版权归 **JiaRan027**。请见 [LICENSE](LICENSE)。第三方依赖的许可材料见 [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt)。
