# PersonalMemory

[简体中文](README.md) · [English](README_EN.md) · [Feature and Usage Guide](docs/USAGE.md) · [Download the Windows Alpha](https://github.com/JiaRan027/PersonalMemoryX/releases/tag/alpha-channel)

> **PersonalMemory is a user-owned long-term memory system for AI.**
>
> Let AI accumulate memories from meaningful experiences, connect them, and recall them when relevant.

PersonalMemory is a **local-first, user-controlled long-term memory system for AI**. Its core purpose is to gradually retain, connect, and revise meaningful experiences, knowledge, and changes—and recall them in relevant situations. The user owns these memories; carrying them across agents is one capability that follows from that ownership, not the product's definition.

It is not a chat archive, nor does it promise to reproduce a human brain or remember every word. It focuses on **forming understandable, revisable memories from ongoing interactions and experiences, then naturally recalling relevant ones when they matter.**

## Why it exists

People accumulate knowledge, lessons, relationships, plans, and changing understandings through experience. AI interactions can produce similarly meaningful material. Yet most AI memory is bounded by a single session or product, making it difficult for memories to accumulate over time, connect to one another, and change in light of new experience.

What matters may be more than “choose local deployment”: it can include the situation, why that decision was made, which alternatives were tried, what happened next, and whether the experience changed the original view. PersonalMemory aims to preserve meaningful context around experiences, not just isolated conclusions.

The user controls these memories, which retain provenance and can be inspected, corrected, or deleted. Connected agents can receive context relevant to the current situation through MCP, within a budget; agent integrations are access and delivery paths for the memory system.

## What using it looks like

```text
Everyday interactions, materials, and meaningful experiences
      ↓
Identify useful signals and form or revise memories
      ↓
Connect memories to related experiences, time, and context
      ↓
Recall them in relevant situations; update from new experience or correction
```

Not every message becomes a memory. The Alpha uses the Windows desktop app and integrations such as Claude Code and Codex to test capture, governance, and recall; these integrations are current validation surfaces for the memory core, while the product itself is the memory system. A configured integration does not prove that every Hook was delivered, and recall still depends on memory quality, the question, and integration state. See the [product overview](docs/PRODUCT_OVERVIEW.md) for the product rationale and trade-offs.

## Current Alpha

**v0.2.10 · Windows 10/11 x64 · Alpha**

| Capability | Current status |
| --- | --- |
| Local memory governance | Manages memories, provenance, revisions, and deletion state on this device |
| Agent context | MCP compiles bounded task context; it does not default to sending the whole memory store |
| Claude Code | Integration and selective capture paths are implemented and have been used in real sessions |
| Codex Desktop / CLI | MCP and Hook paths are implemented; Hook delivery and natural recall still need validation in the user's environment |
| Local source recognition | Read-only scan of supported local agent files; preview candidates before importing |
| Continuity Capsule | Compiles short-lived task context within a token budget; preview, validate, copy, or save it |
| Activity and graph | Review processing history and memory relationships; undo is available only for eligible imports |
| `.pmemory` portability | Experimental import/export for a subset of objects; not a full database backup |
| Cloud sync, mobile, multi-user service | Not supported in this release |

See the [Feature and Usage Guide](docs/USAGE.md) for workflows and step-by-step instructions.

This is an early Alpha, not a stable release. Automatic capture can miss useful information or produce candidates that need correction; this release does not yet provide mature, human-like memory and recall across all kinds of experience. Do not use PersonalMemory as the only backup of important information. Windows developer workflows are the first observable setting for validating the memory core, not the boundary of the product vision.

## Install and get started

1. Download the installer and its matching `.sha256` file from the [GitHub Alpha Release](https://github.com/JiaRan027/PersonalMemoryX/releases/tag/alpha-channel).
2. Open PowerShell in the download directory and check file integrity:

```powershell
$installer = ".\PersonalMemory_0.2.10_x64-setup.exe"
$expected = (Get-Content -Raw "$installer.sha256").Trim().Split()[0].ToLowerInvariant()
$actual = (Get-FileHash $installer -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "SHA-256 verification failed" }
"SHA-256 OK: $actual"
```

3. Install and open PersonalMemory, then connect the agents you want to use from **Connections**.
4. Review the MCP / Hook configuration requested by the app and start a new agent session.
5. Talk normally. Later, inspect any resulting candidates or memories in the app and ask about relevant context naturally in another session.

**Windows security note:** The installer is not Authenticode-signed, so Windows may show an “Unknown publisher” warning. The bundled SHA-256 only checks that the file matches the release material; it does not authenticate the publisher. In-app update packages use Minisign verification, but that is not Windows code signing or SmartScreen reputation. End-to-end online update installation and recovery have not been validated for this release; manual updates from the Release page are recommended.

## Data and boundaries

- Memories are stored locally by default under `%LOCALAPPDATA%\PersonalMemoryData`; this release has no cloud memory storage, account sync, or cross-device recovery.
- PersonalMemory is not intended to retain complete chat transcripts. Memories include provenance and can be inspected, corrected, reverted, or deleted in the app.
- Only relevant context is delivered to an agent when needed. If you use a cloud agent, any context sent is also subject to that provider's data-handling policies.
- Local data still needs to be backed up by the user; this Alpha does not provide cloud recovery.

## Contributing and building

Reproducible issues and improvement suggestions are welcome on [GitHub Issues](https://github.com/JiaRan027/PersonalMemoryX/issues). Contributors should read the [Windows build guide](docs/BUILDING.md) first. Do not commit real memory databases, private conversations, API tokens, signing keys, or unsanitized logs.

## License

PersonalMemory is licensed under **AGPL-3.0-or-later**, copyright **JiaRan027**. See [LICENSE](LICENSE) and the [third-party license notices](THIRD_PARTY_NOTICES.txt).
