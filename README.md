# PersonalMemory

[简体中文](README.md) · [English](README_EN.md) · [功能与使用指南](docs/USAGE.md) · [下载 Windows Alpha](https://github.com/JiaRan027/PersonalMemoryX/releases/tag/alpha-channel)

> **记忆归用户，而不是某一个 AI 产品。**
>
> PersonalMemory 希望 AI 能从重要经历中留下可追溯的记忆，在相关时找到它们，并允许用户持续修正。

PersonalMemory 是一个**本地优先、由用户掌控的 AI 记忆系统**。它关注的不是保存整段聊天，而是从获准的来源中筛选值得留下的内容，保留记忆的来源与变化，并在相关情境中按需召回。

核心是记忆如何形成、关联、修订和被唤起；Claude Code、Codex 等 Agent 只是当前的输入与召回入口。跨 Agent 使用是用户拥有记忆后带来的能力，不是产品目标的全部。

## 它如何工作

```text
已授权的对话来源或用户导入
        ↓
自动筛选线索（不保存完整聊天，也不保证每条都命中）
        ↓
形成候选或记忆，并保留来源与修订信息
        ↓
用户可检查、纠正、审核或删除
        ↓
Agent 需要时通过 MCP 获取有限的相关上下文
```

**不需要逐条手动录入记忆。**在受支持的 Agent 中，Hook 会按当前规则选择性处理对话；普通交流可能不会形成候选，自动采集也可能漏掉重要内容。候选是否接纳由 PersonalMemory 的治理流程处理，用户可以进一步检查和修正。它不是对所有聊天、屏幕或声音的持续记录系统。

## 为什么不只是聊天记录或 Agent 插件

- **保留来历：**记忆可以关联来源证据和修订过程，便于检查“它为什么在这里”。
- **允许变化：**用户可以纠正或删除，不必把早期推断永久当成事实。
- **按需召回：**MCP 根据当前任务编译有限上下文，不会默认把整个记忆库塞给 Agent。
- **记忆由用户持有：**本地记忆不依赖某一家 Agent 的账号继续保存；接入只是可更换的使用入口。

## 当前 Alpha 能力

**v0.2.10 · Windows 10/11 x64 · Alpha**

| 能力 | 当前状态 |
| --- | --- |
| 本地记忆治理 | 在本机查看记忆及其来源，管理修订、纠正和删除 |
| 自动采集 | Claude Code 与 Codex 的 Hook 路径已实现；按规则选择性处理，不保存完整聊天记录 |
| Agent 召回 | 通过 MCP 按任务获取受预算限制的相关记忆；需要完成对应 Agent 接入 |
| Codex Hook 验证 | 配置入口已实现；是否收到真实 Hook 事件需在本机新建会话验证，配置存在不等于事件已送达 |
| `.pmemory` 导入/导出 | 实验性对象子集，不是完整数据库备份或完整迁移保证 |
| 云同步、移动端、多用户服务 | 当前版本不支持 |

这是早期 Alpha。它还不是能自动记住所有经历、像人一样完整回忆的成熟系统；记忆召回也不是百分之百保证。请勿把它当作重要资料的唯一备份。

## 安装与开始使用

1. 从 [GitHub Alpha Release](https://github.com/JiaRan027/PersonalMemoryX/releases/tag/alpha-channel) 下载安装包和对应的 `.sha256` 文件。
2. 在下载目录打开 PowerShell，校验安装包：

```powershell
$installer = ".\PersonalMemory_0.2.10_x64-setup.exe"
$expected = (Get-Content -Raw "$installer.sha256").Trim().Split()[0].ToLowerInvariant()
$actual = (Get-FileHash $installer -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "SHA-256 verification failed" }
"SHA-256 OK: $actual"
```

3. 安装并打开 PersonalMemory，在“接入”页面配置要使用的 Agent。
4. 按页面提示审核 MCP / Hook 配置，然后新建一个 Agent 会话验证连接状态。
5. 正常交流；之后可以在 PersonalMemory 中检查产生的候选和记忆，再在新会话里询问相关内容。

逐项操作说明见[功能与使用指南](docs/USAGE.md)。

**Windows 安全提示：**安装包没有 Authenticode 签名，Windows 可能显示“未知发布者”。SHA-256 仅用于检查文件是否与发布材料一致，不证明发布者身份。本版应用内更新包的 Tauri updater 签名用于验证更新包完整性，不是 Windows 代码签名或 SmartScreen 信誉；在线更新安装和失败恢复尚未完成端到端验收，建议通过 Release 页面手动更新。

## 数据与边界

- 记忆数据库和 PersonalMemory 服务默认保存在本机 `%LOCALAPPDATA%\PersonalMemoryData`，PM 不提供云端记忆存储或账号同步。
- **本地保存不等于所连接的 AI 也在本地运行。**使用云端 Agent 时，PersonalMemory 按需返回的相关上下文会进入该服务商的对话处理流程，并受其数据政策约束。
- 如果某个 Agent 服务或账号不可用，PersonalMemory 本地数据不会因此自动消失；但它不能保证该第三方服务继续可用，也不能保证无需配置即可切换到其他 Agent。
- `.pmemory` 当前只覆盖实验性对象子集，不能代替完整数据库备份。重要数据请另行备份。

## 参与与许可证

欢迎通过 [GitHub Issues](https://github.com/JiaRan027/PersonalMemoryX/issues) 提交可复现的问题和建议。构建步骤见[Windows 构建指南](docs/BUILDING.md)。请勿提交真实记忆数据库、私人对话、API Token、签名密钥或未经脱敏的日志。

PersonalMemory 使用 **AGPL-3.0-or-later**，版权归 **JiaRan027**。详见 [LICENSE](LICENSE) 和 [第三方许可清单](THIRD_PARTY_NOTICES.txt)。
