# PersonalMemory

[简体中文](README.md) · [English](README_EN.md) · [下载 Windows Alpha](https://github.com/JiaRan027/PersonalMemory/releases/tag/alpha-channel)

> **PersonalMemory exists to preserve continuity, not just memory.**
>
> 让重要的协作背景跟着用户走，而不是留在某一个 AI 平台里。

PersonalMemory 是一个**本地优先、由用户掌控的 AI 连续性层**。它帮助用户把重要背景、决定、经验和修正带到不同的 Agent 与模型中，让新的会话不必总从零开始。

它不是聊天记录仓库，也不承诺记住或召回每一件事。它要解决的是一个更具体的问题：**哪些背景值得延续，依据是什么，以及在需要时如何把合适的部分交给当前 Agent。**

## 为什么做它

长期使用 AI 会形成有价值的共同背景：一个决定背后的原因、已经试过但行不通的方案、用户后来作出的修正，以及仍然未知的事情。这些内容往往散落在会话和平台中；换一个工具，就可能再次解释。

例如，重要的不只是“选择本地部署”，还包括当时为什么这样决定、哪些方案已经试过、后来有没有修正。换一个 Agent 时，这些背景才有机会一起接续，而不是只留下一个孤立结论。

PersonalMemory 希望让这份连续性由用户管理。记忆保留来源并可检查、修正或删除；连接的 Agent 可以通过 MCP 获得与当前任务相关、受预算约束的上下文，而不是每次读取整份记忆库。

## 使用时会发生什么

```text
连接 Agent
    ↓
符合条件的信息可能成为记忆候选
    ↓
检查依据，修正或删除记忆
    ↓
在需要时向另一个 Agent 提供相关背景
```

不是每条消息都会成为记忆。接入配置成功也不代表每个 Hook 都已送达；上下文是否被正确召回，还取决于 Agent、问题和集成状态。产品目标与设计取舍见[产品说明](docs/PRODUCT_OVERVIEW.md)。

## 当前 Alpha

**v0.2.10 · Windows 10/11 x64 · Alpha**

| 能力 | 当前状态 |
| --- | --- |
| 本地记忆治理 | 在本机管理记忆、来源、修订和删除状态 |
| Agent 上下文 | 通过 MCP 按任务编译有限上下文，不默认发送全部记忆 |
| Claude Code | 集成与选择性采集路径已实现，并在真实会话中使用过 |
| Codex Desktop / CLI | MCP 与 Hook 路径已实现；Hook 到达和自然跨 Agent 召回仍需在用户环境验证 |
| `.pmemory` 迁移 | 实验性对象子集导入/导出；不是完整数据库备份 |
| 云同步、移动端、多用户服务 | 当前版本不支持 |

这是早期 Alpha，不是稳定版。自动采集可能漏掉重要内容，也可能产生需要修正的候选。请不要把 PersonalMemory 当作重要资料的唯一备份。长期愿景包括更多个人 AI 应用；当前版本首先验证 Windows 开发者工作流中的连续性体验。

## 安装与开始使用

1. 从 [GitHub Alpha Release](https://github.com/JiaRan027/PersonalMemory/releases/tag/alpha-channel) 下载安装包和对应的 `.sha256` 文件。
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

欢迎提交可复现的问题和改进建议：[GitHub Issues](https://github.com/JiaRan027/PersonalMemory/issues)。贡献者请先阅读[Windows 构建指南](docs/BUILDING.md)。请勿提交真实记忆数据库、私人对话、API Token、签名密钥或未经脱敏的日志。

## 许可证

PersonalMemory 使用 **AGPL-3.0-or-later**，版权归 **JiaRan027**。详见 [LICENSE](LICENSE) 和 [第三方许可清单](THIRD_PARTY_NOTICES.txt)。
