# PersonalMemory: product overview

## The problem we want to solve

AI collaboration accumulates context over time. Some of it is easy to restate; some is not: why a decision was made, what the user later corrected, which approaches have already failed, what matters to the user, and what remains uncertain.

Today, that context is usually tied to a particular conversation, model, product, or account. A new session may start without it, and switching tools can mean rebuilding the working relationship from scratch. Native memory features can help within one product, but they do not give users a neutral continuity layer they can govern across products.

PersonalMemory exists to make that continuity portable and user-controlled.

## The intended user experience

The goal is not to make people operate a memory database or remember special retrieval commands. A connected agent should be able to receive a small, relevant amount of background when it helps with the current task. If the system does not have evidence, it should preserve that uncertainty rather than inventing a confident history.

Users should be able to see where information came from, distinguish an explicit statement from an inference, correct an outdated understanding, and remove information they no longer want. The system should carry forward useful experience without treating every sentence as permanent memory.

This is an intended experience, not a guarantee of perfect extraction or recall. Agent behavior, integration delivery, and the relevance of any retrieval all affect the result.

## What PersonalMemory is—and is not

PersonalMemory is a local-first continuity layer between people and the AI tools they choose. It is not:

- a complete transcript archive;
- a new foundation model or a replacement for an agent;
- a claim that one extraction method is always better than the memory built into an agent;
- a cloud synchronization service in the current release.

Agents may be good at understanding their own interactions. PersonalMemory focuses on the parts that should remain governed by the user: provenance, scope, conflicting or revised information, deletion, and delivery across integrations. Extraction can change as better sources and agent capabilities become available; the governance boundary should remain stable.

## Product principles

1. **Continuity belongs to the user.** The user, not a single AI provider, controls the durable continuity record.
2. **Evidence determines authority.** A direct user statement and an agent inference are not equivalent, even if they arrive through the same integration.
3. **All sources use one governance path.** Hook events, APIs, imported files, and agent proposals should not bypass provenance and admission rules.
4. **Corrections are part of continuity.** People change their minds and circumstances change. A current explicit correction should take precedence over a stale or inferred description.
5. **Relevant context is better than indiscriminate context.** Deliver only what is useful for the current task, within the user's permissions and the available budget.
6. **Unknown is a valid result.** A missing record must not be replaced with speculation presented as fact.

## Architecture in one view

```text
Sources
  → thin translators
  → evidence-bearing candidates
  → shared governance and admission
  → user-owned local continuity core
  → task-relevant context compiler
  → agent integrations, portable capsules, and exports
```

Platform-specific formats stay at the integration boundary. The core works with platform-neutral concepts such as memory, evidence, scope, revisions, and deletion. This lets integrations evolve without making a particular agent the architectural center.

## Current scope

The current public Alpha targets Windows 10/11 x64 and validates PersonalMemory first in developer workflows. Claude Code has a real-session integration path. Codex Desktop and CLI have MCP and Hook integration paths, but real Hook delivery and natural cross-agent recall still require user-side verification.

The Alpha is useful for testing local governance and agent context delivery. It does not yet provide cloud sync, mobile clients, multi-user hosting, broad platform coverage, or a guarantee that every valuable detail will be captured or recalled. Personal and relationship-oriented use cases are part of the longer-term product direction, not a claim about current release readiness.
