# PersonalMemory

[简体中文](README.md) · [English](README_EN.md) · [Feature and Usage Guide](docs/USAGE.md) · [Download the Windows Alpha](https://github.com/JiaRan027/PersonalMemoryX/releases/tag/alpha-channel)

> **Memory should belong to the user, not to a single AI product.**
>
> PersonalMemory aims to give AI traceable memories of meaningful experiences, recall them when relevant, and let users keep correcting them.

PersonalMemory is a **local-first, user-controlled memory system for AI**. It is not meant to archive entire chats. It selectively captures content from authorized sources, preserves where memories came from and how they change, and retrieves them when relevant.

The core is how memories form, connect, change, and come back to mind. Claude Code, Codex, and other agents are current input and recall paths. Cross-agent use follows from user-owned memory; it is not the whole product goal.

## How it works

```text
An authorized conversation source or user import
        ↓
Signals are selected automatically (not every message is captured)
        ↓
Candidates or memories retain provenance and revision information
        ↓
Users can inspect, correct, review, or delete them
        ↓
Agents request bounded, relevant context through MCP when needed
```

**You do not have to enter every memory manually.** Hooks selectively process conversations from supported agents according to the current rules. Ordinary conversation may produce no candidate, and automatic capture can miss useful information. PersonalMemory governs whether a candidate is admitted; users can inspect and correct the result. It does not continuously record every chat, screen, or sound.

## More than a chat archive or an agent plugin

- **Keep provenance:** memories can retain source evidence and revision history, so users can inspect why an item exists.
- **Allow change:** users can correct or delete memories instead of treating an early inference as permanent truth.
- **Recall on demand:** MCP compiles bounded context for the current task; it does not send the entire memory store by default.
- **Keep memory user-owned:** local memories do not depend on one agent account to remain stored. Agent integrations are replaceable ways to use them.

## Current Alpha

**v0.2.10 · Windows 10/11 x64 · Alpha**

| Capability | Current status |
| --- | --- |
| Local memory governance | Inspect memories and sources; manage revisions, corrections, and deletion on this device |
| Automatic capture | Hook paths for Claude Code and Codex are implemented; capture is selective and does not retain full chat transcripts |
| Agent recall | MCP retrieves task-relevant memories within a token budget; the corresponding agent connection must be configured |
| Codex Hook verification | Configuration is implemented; receipt of real Hook events must be verified with a new local session. A config entry alone does not prove delivery |
| `.pmemory` import/export | Experimental subset of objects; not a full database backup or complete migration guarantee |
| Cloud sync, mobile, multi-user service | Not supported in this release |

This is an early Alpha, not a mature system that automatically remembers every experience or recalls everything like a person. Memory recall is not guaranteed to be perfect. Do not use it as the only backup of important information.

## Install and get started

1. Download the installer and its matching `.sha256` file from the [GitHub Alpha Release](https://github.com/JiaRan027/PersonalMemoryX/releases/tag/alpha-channel).
2. Open PowerShell in the download directory and verify the installer:

```powershell
$installer = ".\PersonalMemory_0.2.10_x64-setup.exe"
$expected = (Get-Content -Raw "$installer.sha256").Trim().Split()[0].ToLowerInvariant()
$actual = (Get-FileHash $installer -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "SHA-256 verification failed" }
"SHA-256 OK: $actual"
```

3. Install and open PersonalMemory, then configure the agents you want to use from **Connections**.
4. Review the MCP / Hook configuration requested by the app, then start a new agent session to verify the connection.
5. Talk normally. Later, inspect resulting candidates and memories in PersonalMemory, then ask about relevant context in a new session.

See the [Feature and Usage Guide](docs/USAGE.md) for step-by-step instructions.

**Windows security note:** The installer is not Authenticode-signed, so Windows may show an “Unknown publisher” warning. SHA-256 only checks that the file matches the release material; it does not authenticate the publisher. Minisign verification for in-app update packages is not Windows code signing or SmartScreen reputation. End-to-end online update installation and recovery have not been validated for this release; manual updates from the Release page are recommended.

## Data and boundaries

- The memory database and PersonalMemory service are stored locally by default under `%LOCALAPPDATA%\PersonalMemoryData`; PM does not provide cloud memory storage or account sync.
- **Local storage does not mean the connected AI runs locally.** When using a cloud agent, relevant context returned by PersonalMemory becomes part of that provider's conversation processing and is subject to its data policies.
- If an agent service or account becomes unavailable, the local PersonalMemory data does not disappear automatically. However, PersonalMemory cannot keep that third-party service available or guarantee a no-setup switch to another agent.
- `.pmemory` currently covers only an experimental subset of objects and is not a full database backup. Back up important data separately.

## Contributing and license

Reproducible issues and suggestions are welcome on [GitHub Issues](https://github.com/JiaRan027/PersonalMemoryX/issues). Build steps are in the [Windows build guide](docs/BUILDING.md). Do not commit real memory databases, private conversations, API tokens, signing keys, or unsanitized logs.

PersonalMemory is licensed under **AGPL-3.0-or-later**, copyright **JiaRan027**. See [LICENSE](LICENSE) and [third-party license notices](THIRD_PARTY_NOTICES.txt).
