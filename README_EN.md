# PersonalMemory

[简体中文](README.md) · [English](README_EN.md) · [Download the Windows Alpha](https://github.com/JiaRan027/PersonalMemory/releases/tag/alpha-channel)

> **PersonalMemory exists to preserve continuity, not just memory.**
>
> Keep important collaboration context with the user—not trapped in one AI platform.

PersonalMemory is a **local-first continuity layer for AI, controlled by the user**. It helps carry important background, decisions, experience, and corrections across agents and models, so a new session does not always have to start from zero.

It is not a chat archive, and it does not promise to capture or recall everything. It focuses on a more specific problem: **what context is worth carrying forward, what evidence supports it, and how to deliver the relevant parts to the agent at hand.**

## Why it exists

Long-term AI collaboration builds useful shared context: why a decision was made, which approaches have already failed, what the user later corrected, and what is still unknown. That context is often scattered across sessions and platforms; switching tools can mean explaining it all again.

For example, what matters may be more than “choose local deployment”: it can include why that decision was made, which alternatives were already tried, and whether it was later revised. When switching agents, that background has a chance to carry forward instead of leaving behind an isolated conclusion.

PersonalMemory aims to put that continuity under the user's control. Memories retain provenance and can be inspected, corrected, or deleted. Connected agents can receive task-relevant, budgeted context through MCP instead of reading the entire memory store on every call.

## What using it looks like

```text
Connect an agent
      ↓
Eligible information may become a memory candidate
      ↓
Inspect the source, correct, or delete a memory
      ↓
Make relevant background available to another agent when needed
```

Not every message becomes a memory. A configured integration does not prove that every Hook was delivered; successful recall still depends on the agent, the question, and integration state. See the [product overview](docs/PRODUCT_OVERVIEW.md) for the product rationale and trade-offs.

## Current Alpha

**v0.2.10 · Windows 10/11 x64 · Alpha**

| Capability | Current status |
| --- | --- |
| Local memory governance | Manages memories, provenance, revisions, and deletion state on this device |
| Agent context | MCP compiles bounded task context; it does not default to sending the whole memory store |
| Claude Code | Integration and selective capture paths are implemented and have been used in real sessions |
| Codex Desktop / CLI | MCP and Hook paths are implemented; Hook delivery and natural cross-agent recall still need validation in the user's environment |
| `.pmemory` portability | Experimental import/export for a subset of objects; not a full database backup |
| Cloud sync, mobile, multi-user service | Not supported in this release |

This is an early Alpha, not a stable release. Automatic capture can miss useful information or produce candidates that need correction. Do not use PersonalMemory as the only backup of important information. The longer-term vision includes more personal AI applications; this release first validates continuity in Windows developer workflows.

## Install and get started

1. Download the installer and its matching `.sha256` file from the [GitHub Alpha Release](https://github.com/JiaRan027/PersonalMemory/releases/tag/alpha-channel).
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

Reproducible issues and improvement suggestions are welcome on [GitHub Issues](https://github.com/JiaRan027/PersonalMemory/issues). Contributors should read the [Windows build guide](docs/BUILDING.md) first. Do not commit real memory databases, private conversations, API tokens, signing keys, or unsanitized logs.

## License

PersonalMemory is licensed under **AGPL-3.0-or-later**, copyright **JiaRan027**. See [LICENSE](LICENSE) and the [third-party license notices](THIRD_PARTY_NOTICES.txt).
