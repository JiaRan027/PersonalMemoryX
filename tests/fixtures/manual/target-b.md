# PersonalMemory Windows 验收目标

这段正文由用户维护，PersonalMemory 不得覆盖或删除。

用户正文保护标记：WINDOWS-ACCEPTANCE-OUTSIDE-MANAGED-BLOCK。
<!-- personalmemory:start -->
<!-- projection_id: projection_example_windows_acceptance -->
# PersonalMemory

## Fact

- 示例用户主要在 Windows 桌面环境中测试本地应用。
  - 原因：用于验证投影格式，不代表真实用户资料。

## Goal

- 验证本地记忆服务能够安全地向 Agent 提供受限上下文。
  - 原因：这是本地验收夹具中的合成目标。

## Decision

- 示例应用采用本地优先存储，Agent 只读取授权的记忆。
  - 原因：验证 managed block 内容可被更新，同时保护块外用户正文。

## Insight

- 投影中的可读上下文应保留来源与边界，不应混入无关记录。
  - 原因：这是合成验收样例。

<!-- personalmemory:end -->
这段结尾正文也必须永久保留。
