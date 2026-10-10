# PersonalMemory

[简体中文](README.md) · [English](README_EN.md) · [Download the Windows Alpha](https://github.com/JiaRan027/PersonalMemory/releases/tag/alpha-channel)

> **PersonalMemory exists to preserve continuity, not just memory.**
>
> Keep the context built through long-term AI collaboration from being trapped in one platform.

PersonalMemory is a user-owned, local-first continuity layer for AI. It aims to carry important facts, goals, decisions, experience, and ways of working across agents, models, and apps—so people do not have to explain everything again whenever they switch tools.

## Why PersonalMemory exists

Long-term collaboration with AI builds shared context: what we discussed, why a decision was made, how it was later corrected, and what is still unknown. That context is usually scattered across conversations and platforms. Start a new session or switch agents, and it can feel as if the collaboration never happened.

We are not trying to save more chat. We are asking: **how can useful continuity remain under the user's control and reach the right agent when it matters?**

PersonalMemory is neither another AI model nor a permanent archive of every conversation. It manages traceable, correctable memories locally, then provides connected agents with bounded context relevant to a task. The long-term vision includes ongoing personal collaboration with AI and support for more kinds of AI applications. The current Alpha is still primarily a Windows developer-workflow validation release.

### The experience we want to enable

- Switching agents should not require retelling every important part of the background.
- An agent should receive context relevant to the current task—not the entire memory store or a full chat transcript.
- Users should be able to inspect the basis for a memory, correct outdated information, or delete what they do not want retained.
- Unknowns should remain unknown; an agent's guess should not silently become a fact.

These are product goals, not promises of perfect capture or 100% recall. See the [product overview](docs/PRODUCT_OVERVIEW.md) for more context.

## What the current Alpha can do

**Version 0.2.10 · Windows 10/11 x64**

| Capability | Current status |
| --- | --- |
| Local continuity core | Manages memories, provenance, revisions, and deletion state on this device |
| Agent context retrieval | MCP provides bounded task context; it does not send the entire memory store on every call |
| Claude Code | Integration and selective capture are implemented and have been exercised in real sessions |
| Codex Desktop / CLI | MCP and Hook integration paths are implemented; real Hook delivery and natural cross-agent recall still need user-side verification |
| Data portability | Experimental `.pmemory` import/export for a subset of objects; not a full database backup |
| Cloud sync, mobile, other agents | Not supported or not complete in this release |

This is an Alpha, not a stable release. Automatic capture may miss useful information and may need correction. **A configured integration does not prove that every Hook arrived; a delivered Hook does not mean every turn should create a memory.** Do not use this software as the only backup of important information.

## Install and get started

1. Download the installer and matching `.sha256` file from the [GitHub Alpha Release](https://github.com/JiaRan027/PersonalMemory/releases/tag/alpha-channel).
2. Open PowerShell in the download directory and verify the installer:

```powershell
$installer = ".\PersonalMemory_0.2.10_x64-setup.exe"
$expected = (Get-Content -Raw "$installer.sha256").Trim().Split()[0].ToLowerInvariant()
$actual = (Get-FileHash $installer -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "SHA-256 verification failed" }
"SHA-256 OK: $actual"
```

3. Run the installer and open PersonalMemory. Connect the agents you want to use from **Connections**.
4. Review the MCP / Hook configuration requested by the page, then start a new agent session.
5. Talk normally. Later, check PersonalMemory for any resulting candidates or memories, then ask about relevant context naturally in a new session. Do not repeatedly send test prompts just to manufacture memories.

**Windows installation note:** The installer is not Authenticode-signed, so Windows may show an “Unknown publisher” warning. The Release SHA-256 detects a damaged or mismatched download; it does not authenticate the publisher. In-app update packages use Minisign to verify update files, but this is not a Windows publisher identity or SmartScreen reputation. End-to-end online update installation and failure rollback have not been validated for this release; manual installation from the Release page is recommended.

## How data is handled

- Memories are stored locally by default under `%LOCALAPPDATA%\PersonalMemoryData`. This project currently provides no cloud memory storage, account sync, or cross-device recovery.
- The local Service communicates with the desktop app and agent integrations over loopback. MCP compiles bounded context for a task; it does not default to sending the whole memory store on every call.
- PersonalMemory is not intended to retain complete chat transcripts. Memories keep provenance and can be inspected, corrected, undone, deleted, and exported in the app.
- Once context is sent to a cloud agent, the agent/model provider's own data policies apply. Review the relevant privacy terms before enabling any external model features.
- Local data still needs to be backed up by the user; this Alpha does not provide cloud recovery.

## Design principles

- **Continuity belongs to the user.** Platforms can change; accumulated context should not be locked to one provider.
- **Extraction is replaceable; governance stays stable.** Agents may propose memories, while PersonalMemory governs provenance, conflicts, scope, revisions, and deletion.
- **Evidence matters more than the source brand.** Semantic authority depends on who said what and what evidence supports it—not which agent carried the data.
- **Explicit user corrections take priority.** New facts can supersede older or inferred descriptions while preserving the necessary revision relationship.
- **Preserve uncertainty.** When evidence is missing, do not present an inference as a fact.

## Build from source

The current build target is Windows. See the [build guide](docs/BUILDING.md) for development requirements, test commands, and installer instructions. Reproducible bug reports and improvement suggestions are welcome in [GitHub Issues](https://github.com/JiaRan027/PersonalMemory/issues). Do not upload real memory databases, private conversations, API tokens, signing keys, or unsanitized logs.

## License

PersonalMemory is licensed under **AGPL-3.0-or-later**, copyright **JiaRan027**. See [LICENSE](LICENSE). Third-party license materials are listed in [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).
