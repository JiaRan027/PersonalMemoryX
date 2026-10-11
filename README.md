# PersonalMemory

[简体中文](README.md) · [English](README_EN.md) · [功能与使用指南](docs/USAGE.md) · [下载 Windows Alpha](https://github.com/JiaRan027/PersonalMemoryX/releases/tag/alpha-channel)

> **PersonalMemory is a user-owned long-term memory system for AI.**
>
> 让 AI 从重要经历中积累记忆、建立联系，并在相关时自然想起。

PersonalMemory 是一个**本地优先、由用户掌控的 AI 长期记忆系统**。它的核心目标是让重要经历、知识、经验和变化能够被逐步记住、联系和修订，并在相关情境中被想起。用户拥有这些记忆；跨 Agent 携带记忆是这种所有权带来的能力之一，不是产品本身的定义。

它不是聊天记录仓库，也不承诺完美复制人的大脑或记住每个字。它关注的是：**如何从持续发生的交互和经历中形成可理解、可修正的记忆，并在需要时自然召回相关内容。**

## 为什么做它

人会从经历中积累知识、教训、关系、计划和对事情的理解；AI 对话也会产生这类值得长期保留的内容。但多数 AI 的记忆受限于单次会话或单一产品，难以让记忆随时间积累、相互关联并根据新经历修正。

重要的不只是“选择本地部署”，还包括当时的处境、为什么这样决定、哪些方案已经试过、后来发生了什么，以及这段经历是否改变了原来的判断。PersonalMemory 希望保存有意义的经历脉络，而不是只留下孤立结论。

这些记忆由用户掌控，保留来源并可检查、修正或删除。连接的 Agent 可以通过 MCP 获得与当前情境相关、受预算约束的记忆；Agent 接入只是记忆系统的使用入口和交付方式。

## 使用时会发生什么

```text
日常交互、资料与重要经历
    ↓
识别值得留下的线索，形成或修订记忆
    ↓
把记忆与相关经历、时间和背景联系起来
    ↓
在相关情境中召回，并由新经历或用户纠正继续更新
```

不是每条消息都会成为记忆。Alpha 通过 Windows 桌面应用以及 Claude Code、Codex 等 Agent 集成来检验采集、治理和召回；这些集成是记忆核心的当前验证入口，产品本身是这个记忆系统。接入配置成功也不代表每个 Hook 都已送达，召回效果仍取决于记忆质量、问题和集成状态。产品目标与设计取舍见[产品说明](docs/PRODUCT_OVERVIEW.md)。

## 当前 Alpha

**v0.2.10 · Windows 10/11 x64 · Alpha**

| 能力 | 当前状态 |
| --- | --- |
| 本地记忆治理 | 在本机管理记忆、来源、修订和删除状态 |
| Agent 上下文 | 通过 MCP 按任务编译有限上下文，不默认发送全部记忆 |
| Claude Code | 集成与选择性采集路径已实现，并在真实会话中使用过 |
| Codex Desktop / CLI | MCP 与 Hook 路径已实现；Hook 到达及自然召回仍需在用户环境验证 |
| 本机来源识别 | 只读扫描受支持的本机 Agent 文件，预览候选后再导入 |
| Continuity Capsule | 按任务和 Token 上限编译短期上下文，可预览、验证并复制或保存 |
| 活动与图谱 | 查看处理记录和记忆关系；只有符合条件的导入支持撤销 |
| `.pmemory` 迁移 | 实验性对象子集导入/导出；不是完整数据库备份 |
| 云同步、移动端、多用户服务 | 当前版本不支持 |

逐项功能说明和操作步骤见[功能与使用指南](docs/USAGE.md)。

这是早期 Alpha，不是稳定版。自动采集可能漏掉重要内容，也可能产生需要修正的候选；当前版本尚未实现对所有类型经历的成熟、类人记忆与召回。请不要把 PersonalMemory 当作重要资料的唯一备份。Windows 开发者工作流是首个便于观察和验证记忆核心的场景，不是产品愿景的边界。

## 安装与开始使用

1. 从 [GitHub Alpha Release](https://github.com/JiaRan027/PersonalMemoryX/releases/tag/alpha-channel) 下载安装包和对应的 `.sha256` 文件。
2. 在下载目录打开 PowerShell，校验文件完整性：

```powershell
$installer = ".\PersonalMemory_0.2.10_x64-setup.exe"
$expected = (Get-Content -Raw "$installer.sha256").Trim().Split()[0].ToLowerInvariant()
$actual = (Get-FileHash $installer -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "SHA-256 verification failed" }
"SHA-256 OK: $actual"
```

3. 安装并打开 PersonalMemory，在“接入”页面连接要使用的 Agent。
4. 按页面说明审核 MCP / Hook 配置，再新建一个 Agent 会话。
5. 正常交流；之后可在应用中检查新产生的候选或记忆，并在另一个会话里自然询问相关背景。

**Windows 安全提示：**安装包没有 Authenticode 签名，Windows 可能显示“未知发布者”。随包提供的 SHA-256 只用于检查文件是否与发布材料一致，不证明发布者身份。应用内更新包使用 Minisign 验证，但这不是 Windows 代码签名或 SmartScreen 信誉。本版在线更新安装和失败恢复尚未完成端到端验收，建议通过 Release 页面手动更新。

## 数据与边界

- 记忆默认保存在本机 `%LOCALAPPDATA%\PersonalMemoryData`；当前没有云端记忆存储、账号同步或跨设备恢复。
- PersonalMemory 不以保存完整聊天记录为目标。记忆有来源信息，并可在应用中检查、修正、撤销或删除。
- 只有相关上下文会按需交给 Agent。若使用云端 Agent，已发送的内容还受对应模型服务商的数据处理规则约束。
- 本机数据仍需用户自行备份；Alpha 不提供云恢复。

## 参与与构建

欢迎提交可复现的问题和改进建议：[GitHub Issues](https://github.com/JiaRan027/PersonalMemoryX/issues)。贡献者请先阅读[Windows 构建指南](docs/BUILDING.md)。请勿提交真实记忆数据库、私人对话、API Token、签名密钥或未经脱敏的日志。

## 许可证

PersonalMemory 使用 **AGPL-3.0-or-later**，版权归 **JiaRan027**。详见 [LICENSE](LICENSE) 和 [第三方许可清单](THIRD_PARTY_NOTICES.txt)。
