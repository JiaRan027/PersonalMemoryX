# Agent Memory

## 用户事实

- 用户正在构建 PersonalMemory。

## Goal

- 发布一个可运行的 Windows 首版。

## Insight

- 不能完全依赖 Agent 主动判断长期价值。
  原因：模型无法可靠预测未来价值。

## Decision

- 首版采用结构化查询优先。
  原因：大量个人记忆属于明确事实。

## 随手记录

- 这个未知章节应以较低置信度保留。

## Fact

- api_token: SuperSecretValue123

<!-- personalmemory:start -->
## Fact
- 这条 PersonalMemory 投影不应重新摄入。
<!-- personalmemory:end -->
