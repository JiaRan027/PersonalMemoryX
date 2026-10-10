use std::{
    fs,
    path::{Path, PathBuf},
};

use serde::Serialize;
use serde_json::{Map, Value, json};
use tauri::AppHandle;
#[cfg(not(debug_assertions))]
use tauri::Manager;
use toml_edit::{Array, DocumentMut, Item, Table, value};

const CONNECTOR_PREFIX: &str = "connector_";
const HOOK_SHELL_PREFIX: &str =
    "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command \"& '";
const CODEX_GUIDANCE_START: &str = "<!-- BEGIN PERSONALMEMORY MANAGED GUIDANCE -->";
const CODEX_GUIDANCE_END: &str = "<!-- END PERSONALMEMORY MANAGED GUIDANCE -->";
const CODEX_GUIDANCE_BLOCK: &str = r#"<!-- BEGIN PERSONALMEMORY MANAGED GUIDANCE -->
## PersonalMemory continuity

- Before answering questions about the user's identity, preferences, goals, recent projects, prior decisions, or past experience, call the PersonalMemory MCP server without requiring the user to ask for memory retrieval. Use `get_user_context` for profile questions and `memory_context` for task, project, history, or prior-reasoning questions.
- Treat returned memory as evidence, not infallible truth. Prefer the user's current explicit correction over older or inferred memory. If PersonalMemory has no relevant evidence, say you do not know rather than inferring personal facts from workspace files.
<!-- END PERSONALMEMORY MANAGED GUIDANCE -->"#;

#[derive(Clone, Debug)]
struct AgentCommands {
    mcp_command: String,
    mcp_args: Vec<String>,
    hook_command: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentSetupResult {
    settings_path: String,
    mcp_path: String,
    settings_backup: Option<String>,
    mcp_backup: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ClaudeGlobalIntegrationStatus {
    root_path: String,
    settings_path: String,
    mcp_path: String,
    settings_installed: bool,
    mcp_installed: bool,
    mcp_conflict: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CodexIntegrationStatus {
    root_path: String,
    config_path: String,
    guidance_path: String,
    hooks_path: String,
    mcp_installed: bool,
    guidance_installed: bool,
    hooks_installed: bool,
    installed: bool,
    conflict: bool,
}

fn json_object(path: &Path) -> Result<Map<String, Value>, String> {
    if !path.exists() {
        return Ok(Map::new());
    }
    let content = fs::read_to_string(path).map_err(|error| error.to_string())?;
    let value: Value = serde_json::from_str(&content)
        .map_err(|error| format!("{} contains invalid JSON: {error}", path.display()))?;
    value
        .as_object()
        .cloned()
        .ok_or_else(|| format!("{} must contain a JSON object", path.display()))
}

fn backup_path(path: &Path) -> PathBuf {
    PathBuf::from(format!("{}.personalmemory.bak", path.display()))
}

fn write_reviewable_json(
    path: &Path,
    root: &Map<String, Value>,
) -> Result<Option<PathBuf>, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let backup = if path.exists() {
        let backup = backup_path(path);
        fs::copy(path, &backup).map_err(|error| error.to_string())?;
        Some(backup)
    } else {
        None
    };
    let bytes = serde_json::to_vec_pretty(root).map_err(|error| error.to_string())?;
    let temporary = PathBuf::from(format!("{}.personalmemory.tmp", path.display()));
    fs::write(&temporary, [&bytes[..], b"\n"].concat()).map_err(|error| error.to_string())?;
    if path.exists() {
        fs::remove_file(path).map_err(|error| error.to_string())?;
    }
    fs::rename(&temporary, path).map_err(|error| error.to_string())?;
    Ok(backup)
}

fn write_reviewable_text(path: &Path, content: &str) -> Result<Option<PathBuf>, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let backup = if path.exists() {
        let backup = backup_path(path);
        fs::copy(path, &backup).map_err(|error| error.to_string())?;
        Some(backup)
    } else {
        None
    };
    let temporary = PathBuf::from(format!("{}.personalmemory.tmp", path.display()));
    fs::write(&temporary, content.as_bytes()).map_err(|error| error.to_string())?;
    if path.exists() {
        fs::remove_file(path).map_err(|error| error.to_string())?;
    }
    fs::rename(&temporary, path).map_err(|error| error.to_string())?;
    if fs::read_to_string(path).map_err(|error| error.to_string())? != content {
        return Err("managed configuration failed read-back verification".to_owned());
    }
    Ok(backup)
}

fn is_personalmemory_hook(value: &Value) -> bool {
    value
        .get("command")
        .and_then(Value::as_str)
        .is_some_and(|command| {
            command.contains("--connector-id")
                && (command.contains("personalmemory-hook")
                    || command.contains("personalmemory.agent_hook"))
        })
}

fn is_current_personalmemory_hook(value: &Value) -> bool {
    is_personalmemory_hook(value)
        && value
            .get("command")
            .and_then(Value::as_str)
            .is_some_and(|command| {
                let command = command.trim_start();
                command.starts_with(HOOK_SHELL_PREFIX)
                    && command.ends_with('"')
                    && !command.contains("PERSONALMEMORY_DATA_DIR=")
            })
}

fn is_personalmemory_mcp(value: &Value) -> bool {
    let command = value
        .get("command")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if command.contains("personalmemory-mcp") {
        return true;
    }
    value
        .get("args")
        .and_then(Value::as_array)
        .is_some_and(|args| {
            args.iter()
                .filter_map(Value::as_str)
                .any(|argument| argument == "personalmemory.agent_mcp")
        })
}

fn has_managed_hooks(root: &Map<String, Value>) -> bool {
    let Some(hooks) = root.get("hooks").and_then(Value::as_object) else {
        return false;
    };
    ["UserPromptSubmit", "Stop"].into_iter().all(|event_name| {
        hooks
            .get(event_name)
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(|group| group.get("hooks").and_then(Value::as_array))
            .flatten()
            .any(is_current_personalmemory_hook)
    })
}

fn has_codex_managed_hooks(root: &Map<String, Value>) -> bool {
    let Some(hooks) = root.get("hooks").and_then(Value::as_object) else {
        return false;
    };
    ["UserPromptSubmit", "Stop"].into_iter().all(|event_name| {
        hooks
            .get(event_name)
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(|group| group.get("hooks").and_then(Value::as_array))
            .flatten()
            .any(|value| {
                is_current_personalmemory_hook(value)
                    && value
                        .get("command")
                        .and_then(Value::as_str)
                        .is_some_and(|command| command.contains("--agent-family codex"))
            })
    })
}

fn remove_managed_hooks(root: &mut Map<String, Value>) -> Result<(), String> {
    let Some(hooks) = root.get_mut("hooks") else {
        return Ok(());
    };
    let hooks = hooks
        .as_object_mut()
        .ok_or_else(|| "Claude settings 'hooks' must be an object".to_owned())?;
    for event_name in ["UserPromptSubmit", "Stop"] {
        let Some(groups) = hooks.get_mut(event_name) else {
            continue;
        };
        let groups = groups
            .as_array_mut()
            .ok_or_else(|| format!("Claude settings hooks.{event_name} must be an array"))?;
        for group in groups.iter_mut() {
            if let Some(commands) = group.get_mut("hooks").and_then(Value::as_array_mut) {
                commands.retain(|command| !is_personalmemory_hook(command));
            }
        }
        groups.retain(|group| {
            group
                .get("hooks")
                .and_then(Value::as_array)
                .is_none_or(|commands| !commands.is_empty())
        });
    }
    Ok(())
}

fn install_managed_hooks(root: &mut Map<String, Value>, hook_command: &str) -> Result<(), String> {
    remove_managed_hooks(root)?;
    let hooks = root
        .entry("hooks")
        .or_insert_with(|| json!({}))
        .as_object_mut()
        .ok_or_else(|| "Claude settings 'hooks' must be an object".to_owned())?;
    for event_name in ["UserPromptSubmit", "Stop"] {
        let groups = hooks
            .entry(event_name)
            .or_insert_with(|| json!([]))
            .as_array_mut()
            .ok_or_else(|| format!("Claude settings hooks.{event_name} must be an array"))?;
        groups.push(json!({
            "hooks": [{
                "type": "command",
                "command": hook_command,
                "async": false,
                "timeout": 5
            }]
        }));
    }
    Ok(())
}

fn install_mcp(root: &mut Map<String, Value>, commands: &AgentCommands) -> Result<(), String> {
    let servers = root
        .entry("mcpServers")
        .or_insert_with(|| json!({}))
        .as_object_mut()
        .ok_or_else(|| ".mcp.json 'mcpServers' must be an object".to_owned())?;
    if let Some(existing) = servers.get("personalmemory")
        && !is_personalmemory_mcp(existing)
    {
        return Err(
            "An unmanaged MCP server named 'personalmemory' already exists; it was not overwritten"
                .to_owned(),
        );
    }
    servers.insert(
        "personalmemory".to_owned(),
        json!({
            "command": commands.mcp_command,
            "args": commands.mcp_args,
        }),
    );
    Ok(())
}

fn remove_mcp(root: &mut Map<String, Value>) -> Result<(), String> {
    let Some(servers) = root.get_mut("mcpServers") else {
        return Ok(());
    };
    let servers = servers
        .as_object_mut()
        .ok_or_else(|| ".mcp.json 'mcpServers' must be an object".to_owned())?;
    if servers
        .get("personalmemory")
        .is_some_and(is_personalmemory_mcp)
    {
        servers.remove("personalmemory");
    }
    Ok(())
}

#[cfg(any(not(debug_assertions), test))]
fn hook_command_path(executable: &str) -> Result<String, String> {
    // The native Hook discovers %LOCALAPPDATA% itself. Prefixing the command
    // with POSIX `VAR=value` syntax breaks when Claude runs Hooks in PowerShell.
    // Claude may host Hooks in PowerShell or Git Bash on Windows. The generated
    // command therefore invokes one explicit shell instead of relying on either
    // host shell's incompatible executable-path syntax.
    if executable.contains('"') || executable.contains(['\r', '\n']) {
        return Err("Agent executable path contains unsupported characters".to_owned());
    }
    Ok(format!(
        "& '{}'",
        executable.replace('\\', "/").replace('\'', "''")
    ))
}

fn hook_invocation(hook_command: &str, connector_id: &str) -> String {
    format!(
        "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command \"{hook_command} hook --connector-id {connector_id}\""
    )
}

fn codex_hook_invocation(hook_command: &str, connector_id: &str) -> String {
    format!(
        "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command \"{hook_command} hook --connector-id {connector_id} --agent-family codex\""
    )
}

#[cfg(any(not(debug_assertions), test))]
fn windows_command_path(path: &Path) -> String {
    let value = path.to_string_lossy();
    if let Some(unc) = value.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{unc}")
    } else {
        value.strip_prefix(r"\\?\").unwrap_or(&value).to_owned()
    }
}

fn validate_project_root(raw: &str) -> Result<PathBuf, String> {
    let root = fs::canonicalize(raw).map_err(|error| error.to_string())?;
    if !root.is_dir() {
        return Err("project root is not a directory".to_owned());
    }
    Ok(root)
}

fn user_home() -> Result<PathBuf, String> {
    let raw =
        std::env::var_os("USERPROFILE").ok_or_else(|| "USERPROFILE is unavailable".to_owned())?;
    let root = fs::canonicalize(raw).map_err(|error| error.to_string())?;
    if !root.is_dir() {
        return Err("user profile is not a directory".to_owned());
    }
    Ok(root)
}

fn validate_connector_id(connector_id: &str) -> Result<(), String> {
    if !connector_id.starts_with(CONNECTOR_PREFIX)
        || connector_id.len() > 180
        || !connector_id
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || "._:-".contains(character))
    {
        return Err("invalid Connector ID".to_owned());
    }
    Ok(())
}

fn apply_install(
    project_root: &Path,
    connector_id: &str,
    commands: AgentCommands,
) -> Result<AgentSetupResult, String> {
    validate_connector_id(connector_id)?;
    let settings_path = project_root.join(".claude").join("settings.local.json");
    let mcp_path = project_root.join(".mcp.json");
    let mut settings = json_object(&settings_path)?;
    let mut mcp = json_object(&mcp_path)?;
    install_managed_hooks(
        &mut settings,
        &hook_invocation(&commands.hook_command, connector_id),
    )?;
    install_mcp(&mut mcp, &commands)?;
    let settings_backup = write_reviewable_json(&settings_path, &settings)?;
    let mcp_backup = write_reviewable_json(&mcp_path, &mcp)?;
    Ok(AgentSetupResult {
        settings_path: settings_path.display().to_string(),
        mcp_path: mcp_path.display().to_string(),
        settings_backup: settings_backup.map(|path| path.display().to_string()),
        mcp_backup: mcp_backup.map(|path| path.display().to_string()),
    })
}

fn apply_remove(project_root: &Path) -> Result<AgentSetupResult, String> {
    let settings_path = project_root.join(".claude").join("settings.local.json");
    let mcp_path = project_root.join(".mcp.json");
    let mut settings = json_object(&settings_path)?;
    let mut mcp = json_object(&mcp_path)?;
    remove_managed_hooks(&mut settings)?;
    remove_mcp(&mut mcp)?;
    let settings_backup = write_reviewable_json(&settings_path, &settings)?;
    let mcp_backup = write_reviewable_json(&mcp_path, &mcp)?;
    Ok(AgentSetupResult {
        settings_path: settings_path.display().to_string(),
        mcp_path: mcp_path.display().to_string(),
        settings_backup: settings_backup.map(|path| path.display().to_string()),
        mcp_backup: mcp_backup.map(|path| path.display().to_string()),
    })
}

fn global_paths(user_home: &Path) -> (PathBuf, PathBuf) {
    (
        user_home.join(".claude").join("settings.json"),
        user_home.join(".claude.json"),
    )
}

fn codex_config_path(user_home: &Path) -> PathBuf {
    user_home.join(".codex").join("config.toml")
}

fn codex_hooks_path(user_home: &Path) -> PathBuf {
    user_home.join(".codex").join("hooks.json")
}

fn codex_guidance_path(user_home: &Path) -> PathBuf {
    let codex_home = user_home.join(".codex");
    let override_path = codex_home.join("AGENTS.override.md");
    if fs::read_to_string(&override_path).is_ok_and(|content| !content.trim().is_empty()) {
        override_path
    } else {
        codex_home.join("AGENTS.md")
    }
}

fn has_current_codex_guidance(content: &str) -> bool {
    content.contains(CODEX_GUIDANCE_BLOCK)
}

fn without_codex_guidance(content: &str) -> Result<(String, bool), String> {
    let Some(start) = content.find(CODEX_GUIDANCE_START) else {
        if content.contains(CODEX_GUIDANCE_END) {
            return Err(
                "Codex guidance contains an unmatched PersonalMemory end marker".to_owned(),
            );
        }
        return Ok((content.to_owned(), false));
    };
    let after_start = &content[start + CODEX_GUIDANCE_START.len()..];
    let Some(relative_end) = after_start.find(CODEX_GUIDANCE_END) else {
        return Err("Codex guidance contains an unmatched PersonalMemory start marker".to_owned());
    };
    let end = start + CODEX_GUIDANCE_START.len() + relative_end + CODEX_GUIDANCE_END.len();
    if content[end..].contains(CODEX_GUIDANCE_START)
        || content[..start].contains(CODEX_GUIDANCE_END)
    {
        return Err(
            "Codex guidance contains multiple or overlapping PersonalMemory blocks".to_owned(),
        );
    }
    let before = &content[..start];
    let after = &content[end..];
    let remaining = match (before.is_empty(), after.is_empty()) {
        (true, true) => String::new(),
        (true, false) => after
            .strip_prefix("\r\n\r\n")
            .or_else(|| after.strip_prefix("\n\n"))
            .or_else(|| after.strip_prefix("\r\n"))
            .or_else(|| after.strip_prefix('\n'))
            .unwrap_or(after)
            .to_owned(),
        _ => format!("{before}{after}"),
    };
    Ok((remaining, true))
}

fn install_codex_guidance(user_home: &Path) -> Result<Option<PathBuf>, String> {
    let path = codex_guidance_path(user_home);
    let existing = if path.exists() {
        fs::read_to_string(&path).map_err(|error| error.to_string())?
    } else {
        String::new()
    };
    let (remaining, _) = without_codex_guidance(&existing)?;
    let content = if remaining.trim().is_empty() {
        format!("{CODEX_GUIDANCE_BLOCK}\n")
    } else {
        format!("{CODEX_GUIDANCE_BLOCK}\n\n{remaining}")
    };
    write_reviewable_text(&path, &content)
}

fn remove_codex_guidance(user_home: &Path) -> Result<(), String> {
    for name in ["AGENTS.md", "AGENTS.override.md"] {
        let path = user_home.join(".codex").join(name);
        if !path.exists() {
            continue;
        }
        let existing = fs::read_to_string(&path).map_err(|error| error.to_string())?;
        let (remaining, removed) = without_codex_guidance(&existing)?;
        if removed {
            write_reviewable_text(&path, &remaining)?;
        }
    }
    Ok(())
}

fn read_toml_document(path: &Path) -> Result<DocumentMut, String> {
    if !path.exists() {
        return Ok(DocumentMut::new());
    }
    fs::read_to_string(path)
        .map_err(|error| error.to_string())?
        .parse::<DocumentMut>()
        .map_err(|error| format!("{} contains invalid TOML: {error}", path.display()))
}

fn codex_server(document: &DocumentMut) -> Option<&Table> {
    document
        .as_table()
        .get("mcp_servers")
        .and_then(Item::as_table)
        .and_then(|servers| servers.get("personalmemory"))
        .and_then(Item::as_table)
}

fn is_personalmemory_codex_server(server: &Table) -> bool {
    let command = server
        .get("command")
        .and_then(Item::as_value)
        .and_then(|value| value.as_str())
        .unwrap_or_default();
    if command.contains("personalmemory-mcp") {
        return true;
    }
    server
        .get("args")
        .and_then(Item::as_value)
        .and_then(|value| value.as_array())
        .is_some_and(|args| {
            args.iter()
                .filter_map(|value| value.as_str())
                .any(|argument| argument == "personalmemory.agent_mcp")
        })
}

#[cfg_attr(debug_assertions, allow(dead_code))]
fn migrate_codex_mcp_command(user_home: &Path, commands: &AgentCommands) -> Result<bool, String> {
    let path = codex_config_path(user_home);
    let mut document = read_toml_document(&path)?;
    let Some(server) = codex_server(&document) else {
        return Ok(false);
    };
    if !is_personalmemory_codex_server(server) {
        return Ok(false);
    }
    let current_command = server
        .get("command")
        .and_then(Item::as_value)
        .and_then(|value| value.as_str())
        .unwrap_or_default();
    if current_command == commands.mcp_command {
        return Ok(false);
    }

    let server = document["mcp_servers"]["personalmemory"]
        .as_table_mut()
        .ok_or_else(|| "Codex PersonalMemory MCP entry must be a table".to_owned())?;
    server["command"] = value(commands.mcp_command.clone());
    let mut args = Array::new();
    for argument in &commands.mcp_args {
        args.push(argument.as_str());
    }
    server["args"] = value(args);
    write_reviewable_text(&path, &document.to_string())?;
    Ok(true)
}

#[cfg_attr(debug_assertions, allow(dead_code))]
fn migrate_claude_global_mcp_command(
    user_home: &Path,
    commands: &AgentCommands,
) -> Result<bool, String> {
    let (_, path) = global_paths(user_home);
    let mut document = json_object(&path)?;
    let Some(server) = document
        .get("mcpServers")
        .and_then(Value::as_object)
        .and_then(|servers| servers.get("personalmemory"))
    else {
        return Ok(false);
    };
    if !is_personalmemory_mcp(server) {
        return Ok(false);
    }
    if server.get("command").and_then(Value::as_str) == Some(commands.mcp_command.as_str()) {
        return Ok(false);
    }

    let server = document
        .get_mut("mcpServers")
        .and_then(Value::as_object_mut)
        .and_then(|servers| servers.get_mut("personalmemory"))
        .and_then(Value::as_object_mut)
        .ok_or_else(|| "Claude PersonalMemory MCP entry must be an object".to_owned())?;
    server.insert(
        "command".to_owned(),
        Value::String(commands.mcp_command.clone()),
    );
    server.insert(
        "args".to_owned(),
        Value::Array(
            commands
                .mcp_args
                .iter()
                .cloned()
                .map(Value::String)
                .collect(),
        ),
    );
    write_reviewable_json(&path, &document)?;
    Ok(true)
}

#[cfg(not(debug_assertions))]
pub fn migrate_legacy_mcp_configs(app: &AppHandle) -> Result<(), String> {
    let home = user_home()?;
    let commands = agent_commands(app)?;
    // Only migrate entries that already identify themselves as PersonalMemory.
    // Unknown servers and all unrelated Agent settings remain untouched.
    migrate_codex_mcp_command(&home, &commands)?;
    migrate_claude_global_mcp_command(&home, &commands)?;
    Ok(())
}

#[cfg(debug_assertions)]
pub fn migrate_legacy_mcp_configs(_app: &AppHandle) -> Result<(), String> {
    Ok(())
}

fn codex_status(user_home: &Path) -> Result<CodexIntegrationStatus, String> {
    let path = codex_config_path(user_home);
    let guidance_path = codex_guidance_path(user_home);
    let hooks_path = codex_hooks_path(user_home);
    let document = read_toml_document(&path)?;
    let hooks = json_object(&hooks_path)?;
    let server = codex_server(&document);
    let mcp_installed = server.is_some_and(is_personalmemory_codex_server);
    let guidance_installed = fs::read_to_string(&guidance_path)
        .is_ok_and(|content| has_current_codex_guidance(&content));
    let hooks_installed = has_codex_managed_hooks(&hooks);
    Ok(CodexIntegrationStatus {
        root_path: user_home.display().to_string(),
        config_path: path.display().to_string(),
        guidance_path: guidance_path.display().to_string(),
        hooks_path: hooks_path.display().to_string(),
        mcp_installed,
        guidance_installed,
        hooks_installed,
        installed: mcp_installed && guidance_installed && hooks_installed,
        conflict: server.is_some_and(|value| !is_personalmemory_codex_server(value)),
    })
}

fn apply_codex_capture(
    user_home: &Path,
    connector_id: &str,
    commands: &AgentCommands,
) -> Result<Option<PathBuf>, String> {
    validate_connector_id(connector_id)?;
    let path = codex_hooks_path(user_home);
    let mut hooks = json_object(&path)?;
    install_managed_hooks(
        &mut hooks,
        &codex_hook_invocation(&commands.hook_command, connector_id),
    )?;
    write_reviewable_json(&path, &hooks)
}

fn remove_codex_capture(user_home: &Path) -> Result<Option<PathBuf>, String> {
    let path = codex_hooks_path(user_home);
    if !path.exists() {
        return Ok(None);
    }
    let mut hooks = json_object(&path)?;
    remove_managed_hooks(&mut hooks)?;
    write_reviewable_json(&path, &hooks)
}

fn apply_codex_install(
    user_home: &Path,
    commands: &AgentCommands,
) -> Result<Option<PathBuf>, String> {
    let path = codex_config_path(user_home);
    let mut document = read_toml_document(&path)?;
    if codex_server(&document).is_some_and(|server| !is_personalmemory_codex_server(server)) {
        return Err(
            "An unmanaged Codex MCP server named 'personalmemory' already exists; it was not overwritten"
                .to_owned(),
        );
    }
    if !document.as_table().contains_key("mcp_servers") {
        document["mcp_servers"] = Item::Table(Table::new());
    }
    let servers = document["mcp_servers"]
        .as_table_mut()
        .ok_or_else(|| "Codex config 'mcp_servers' must be a table".to_owned())?;
    let mut server = Table::new();
    server["command"] = value(commands.mcp_command.clone());
    let mut args = Array::new();
    for argument in &commands.mcp_args {
        args.push(argument.as_str());
    }
    server["args"] = value(args);
    servers["personalmemory"] = Item::Table(server);
    write_reviewable_text(&path, &document.to_string())
}

fn apply_codex_remove(user_home: &Path) -> Result<Option<PathBuf>, String> {
    let path = codex_config_path(user_home);
    let mut document = read_toml_document(&path)?;
    if codex_server(&document).is_some_and(is_personalmemory_codex_server)
        && let Some(servers) = document["mcp_servers"].as_table_mut()
    {
        servers.remove("personalmemory");
        if servers.is_empty() {
            document.as_table_mut().remove("mcp_servers");
        }
        return write_reviewable_text(&path, &document.to_string());
    }
    Ok(None)
}

fn global_status(user_home: &Path) -> Result<ClaudeGlobalIntegrationStatus, String> {
    let (settings_path, mcp_path) = global_paths(user_home);
    let settings = json_object(&settings_path)?;
    let mcp = json_object(&mcp_path)?;
    let server = mcp
        .get("mcpServers")
        .and_then(Value::as_object)
        .and_then(|servers| servers.get("personalmemory"));
    Ok(ClaudeGlobalIntegrationStatus {
        root_path: user_home.display().to_string(),
        settings_path: settings_path.display().to_string(),
        mcp_path: mcp_path.display().to_string(),
        settings_installed: has_managed_hooks(&settings),
        mcp_installed: server.is_some_and(is_personalmemory_mcp),
        mcp_conflict: server.is_some_and(|value| !is_personalmemory_mcp(value)),
    })
}

fn apply_global_capture(
    user_home: &Path,
    connector_id: &str,
    commands: &AgentCommands,
) -> Result<Option<PathBuf>, String> {
    validate_connector_id(connector_id)?;
    let (settings_path, _) = global_paths(user_home);
    let mut settings = json_object(&settings_path)?;
    install_managed_hooks(
        &mut settings,
        &hook_invocation(&commands.hook_command, connector_id),
    )?;
    write_reviewable_json(&settings_path, &settings)
}

fn apply_global_memory_read(
    user_home: &Path,
    commands: &AgentCommands,
) -> Result<Option<PathBuf>, String> {
    let (_, mcp_path) = global_paths(user_home);
    let mut mcp = json_object(&mcp_path)?;
    install_mcp(&mut mcp, commands)?;
    write_reviewable_json(&mcp_path, &mcp)
}

fn apply_global_install(
    user_home: &Path,
    connector_id: &str,
    commands: AgentCommands,
) -> Result<AgentSetupResult, String> {
    validate_connector_id(connector_id)?;
    let (settings_path, mcp_path) = global_paths(user_home);
    let mut settings = json_object(&settings_path)?;
    let mut mcp = json_object(&mcp_path)?;
    install_managed_hooks(
        &mut settings,
        &hook_invocation(&commands.hook_command, connector_id),
    )?;
    install_mcp(&mut mcp, &commands)?;
    let settings_backup = write_reviewable_json(&settings_path, &settings)?;
    let mcp_backup = write_reviewable_json(&mcp_path, &mcp)?;
    Ok(AgentSetupResult {
        settings_path: settings_path.display().to_string(),
        mcp_path: mcp_path.display().to_string(),
        settings_backup: settings_backup.map(|path| path.display().to_string()),
        mcp_backup: mcp_backup.map(|path| path.display().to_string()),
    })
}

fn apply_global_remove(user_home: &Path) -> Result<AgentSetupResult, String> {
    let (settings_path, mcp_path) = global_paths(user_home);
    let mut settings = json_object(&settings_path)?;
    let mut mcp = json_object(&mcp_path)?;
    remove_managed_hooks(&mut settings)?;
    remove_mcp(&mut mcp)?;
    let settings_backup = write_reviewable_json(&settings_path, &settings)?;
    let mcp_backup = write_reviewable_json(&mcp_path, &mcp)?;
    Ok(AgentSetupResult {
        settings_path: settings_path.display().to_string(),
        mcp_path: mcp_path.display().to_string(),
        settings_backup: settings_backup.map(|path| path.display().to_string()),
        mcp_backup: mcp_backup.map(|path| path.display().to_string()),
    })
}

#[cfg(debug_assertions)]
fn agent_commands(_app: &AppHandle) -> Result<AgentCommands, String> {
    let python = std::env::var("PERSONALMEMORY_DEV_PYTHON").unwrap_or_else(|_| "python".to_owned());
    Ok(AgentCommands {
        mcp_command: python.clone(),
        mcp_args: vec!["-m".to_owned(), "personalmemory.agent_mcp".to_owned()],
        hook_command: format!(
            "& '{}' -m personalmemory.agent_hook",
            python.replace('\'', "''")
        ),
    })
}

#[cfg(not(debug_assertions))]
fn agent_commands(app: &AppHandle) -> Result<AgentCommands, String> {
    let resources = app
        .path()
        .resource_dir()
        .map_err(|error| error.to_string())?;
    let local_app_data = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .ok_or_else(|| "LOCALAPPDATA is unavailable".to_owned())?;
    let mcp = crate::agent_runtime::ensure_mcp_runtime(
        &resources.join("personalmemory-mcp"),
        &local_app_data,
        env!("CARGO_PKG_VERSION"),
    )
    .map_err(|error| format!("could not prepare the MCP runtime: {error}"))?;
    let hook = resources
        .join("personalmemory-hook")
        .join("personalmemory-hook.exe");
    if !hook.is_file() {
        return Err("bundled Agent integration executables are missing".to_owned());
    }
    Ok(AgentCommands {
        mcp_command: windows_command_path(&mcp),
        mcp_args: Vec::new(),
        hook_command: hook_command_path(&windows_command_path(&hook))?,
    })
}

#[tauri::command]
pub fn install_claude_project_integration(
    app: AppHandle,
    project_root: String,
    connector_id: String,
) -> Result<AgentSetupResult, String> {
    let root = validate_project_root(&project_root)?;
    apply_install(&root, &connector_id, agent_commands(&app)?)
}

#[tauri::command]
pub fn remove_claude_project_integration(project_root: String) -> Result<AgentSetupResult, String> {
    apply_remove(&validate_project_root(&project_root)?)
}

#[tauri::command]
pub fn get_claude_global_integration() -> Result<ClaudeGlobalIntegrationStatus, String> {
    global_status(&user_home()?)
}

#[tauri::command]
pub fn install_claude_global_integration(
    app: AppHandle,
    connector_id: String,
) -> Result<AgentSetupResult, String> {
    apply_global_install(&user_home()?, &connector_id, agent_commands(&app)?)
}

#[tauri::command]
pub fn enable_claude_global_capture(
    app: AppHandle,
    connector_id: String,
) -> Result<ClaudeGlobalIntegrationStatus, String> {
    let home = user_home()?;
    apply_global_capture(&home, &connector_id, &agent_commands(&app)?)?;
    global_status(&home)
}

#[tauri::command]
pub fn enable_claude_global_memory_read(
    app: AppHandle,
) -> Result<ClaudeGlobalIntegrationStatus, String> {
    let home = user_home()?;
    apply_global_memory_read(&home, &agent_commands(&app)?)?;
    global_status(&home)
}

#[tauri::command]
pub fn remove_claude_global_integration() -> Result<AgentSetupResult, String> {
    apply_global_remove(&user_home()?)
}

#[tauri::command]
pub fn get_codex_integration() -> Result<CodexIntegrationStatus, String> {
    codex_status(&user_home()?)
}

#[tauri::command]
pub fn install_codex_integration(
    app: AppHandle,
    connector_id: String,
) -> Result<CodexIntegrationStatus, String> {
    let home = user_home()?;
    let commands = agent_commands(&app)?;
    // Validate all user-owned config layers before mutating any of them.
    read_toml_document(&codex_config_path(&home))?;
    json_object(&codex_hooks_path(&home))?;
    if let Ok(content) = fs::read_to_string(codex_guidance_path(&home)) {
        without_codex_guidance(&content)?;
    }
    apply_codex_install(&home, &commands)?;
    install_codex_guidance(&home)?;
    apply_codex_capture(&home, &connector_id, &commands)?;
    codex_status(&home)
}

#[tauri::command]
pub fn enable_codex_active_recall() -> Result<CodexIntegrationStatus, String> {
    let home = user_home()?;
    install_codex_guidance(&home)?;
    codex_status(&home)
}

#[tauri::command]
pub fn enable_codex_capture(
    app: AppHandle,
    connector_id: String,
) -> Result<CodexIntegrationStatus, String> {
    let home = user_home()?;
    apply_codex_capture(&home, &connector_id, &agent_commands(&app)?)?;
    codex_status(&home)
}

#[tauri::command]
pub fn remove_codex_integration() -> Result<CodexIntegrationStatus, String> {
    let home = user_home()?;
    apply_codex_remove(&home)?;
    remove_codex_guidance(&home)?;
    remove_codex_capture(&home)?;
    codex_status(&home)
}

#[cfg(test)]
mod tests {
    use super::{
        AgentCommands, CODEX_GUIDANCE_START, apply_codex_capture, apply_codex_install,
        apply_codex_remove, apply_global_capture, apply_global_install, apply_global_memory_read,
        apply_global_remove, apply_install, apply_remove, codex_status, global_status,
        hook_command_path, install_codex_guidance, migrate_claude_global_mcp_command,
        migrate_codex_mcp_command, remove_codex_capture, remove_codex_guidance,
        windows_command_path,
    };
    use serde_json::Value;
    use std::{fs, path::Path};

    #[test]
    fn command_paths_remove_windows_verbatim_prefixes() {
        assert_eq!(
            windows_command_path(Path::new(r"\\?\C:\Program Files\PersonalMemory\hook.exe")),
            r"C:\Program Files\PersonalMemory\hook.exe"
        );
        assert_eq!(
            windows_command_path(Path::new(r"\\?\UNC\server\share\hook.exe")),
            r"\\server\share\hook.exe"
        );
    }

    #[test]
    fn hook_command_uses_a_powershell_literal_and_discovers_localappdata_itself() {
        assert_eq!(
            hook_command_path(r"C:\Program Files\PersonalMemory\hook.exe").unwrap(),
            "& 'C:/Program Files/PersonalMemory/hook.exe'",
        );
        assert_eq!(
            hook_command_path(r"C:\Users\O'Brien\hook.exe").unwrap(),
            "& 'C:/Users/O''Brien/hook.exe'",
        );
    }

    fn commands() -> AgentCommands {
        AgentCommands {
            mcp_command: "C:/Program Files/PersonalMemory/personalmemory-mcp.exe".to_owned(),
            mcp_args: Vec::new(),
            hook_command: "& 'C:/Program Files/PersonalMemory/personalmemory-hook.exe'".to_owned(),
        }
    }

    fn versioned_runtime_commands() -> AgentCommands {
        AgentCommands {
            mcp_command: r"C:/Users/demo/AppData/Local/PersonalMemoryAgentRuntime/mcp/0.2.8/personalmemory-mcp-host.exe"
                .to_owned(),
            mcp_args: Vec::new(),
            hook_command: "& 'C:/Program Files/PersonalMemory/personalmemory-hook.exe'"
                .to_owned(),
        }
    }

    #[test]
    fn codex_mcp_runtime_migration_only_changes_personalmemory_entry() {
        let home = tempfile::tempdir().expect("user home");
        let codex = home.path().join(".codex");
        fs::create_dir_all(&codex).expect("Codex directory");
        let config = codex.join("config.toml");
        fs::write(
            &config,
            "model = \"test-model\"\n\n[mcp_servers.personalmemory]\ncommand = \"C:/Users/demo/AppData/Local/PersonalMemory/personalmemory-mcp/personalmemory-mcp.exe\"\nargs = []\nstartup_timeout_sec = 30\n\n[mcp_servers.other]\ncommand = \"other.exe\"\n",
        )
        .expect("Codex config");

        assert!(migrate_codex_mcp_command(home.path(), &versioned_runtime_commands()).unwrap());

        let migrated = fs::read_to_string(config).expect("migrated config");
        assert!(migrated.contains("personalmemory-mcp-host.exe"));
        assert!(migrated.contains("startup_timeout_sec = 30"));
        assert!(migrated.contains("[mcp_servers.other]"));
        assert!(migrated.contains("command = \"other.exe\""));
        assert!(
            home.path()
                .join(".codex/config.toml.personalmemory.bak")
                .is_file()
        );
    }

    #[test]
    fn claude_global_mcp_runtime_migration_preserves_other_servers_and_fields() {
        let home = tempfile::tempdir().expect("user home");
        let config = home.path().join(".claude.json");
        fs::write(
            &config,
            r#"{"mcpServers":{"personalmemory":{"command":"C:/Users/demo/PersonalMemory/personalmemory-mcp.exe","args":[],"env":{"keep":"me"}},"other":{"command":"other.exe"}}}"#,
        )
        .expect("Claude config");

        assert!(
            migrate_claude_global_mcp_command(home.path(), &versioned_runtime_commands()).unwrap()
        );

        let migrated: Value =
            serde_json::from_slice(&fs::read(config).expect("migrated Claude config"))
                .expect("valid JSON");
        assert!(
            migrated["mcpServers"]["personalmemory"]["command"]
                .as_str()
                .unwrap()
                .ends_with("personalmemory-mcp-host.exe")
        );
        assert_eq!(
            migrated["mcpServers"]["personalmemory"]["env"]["keep"],
            "me"
        );
        assert_eq!(migrated["mcpServers"]["other"]["command"], "other.exe");
        assert!(
            home.path()
                .join(".claude.json.personalmemory.bak")
                .is_file()
        );
    }

    #[test]
    fn install_preserves_other_hooks_and_mcp_servers_and_is_idempotent() {
        let root = tempfile::tempdir().expect("project root");
        fs::create_dir(root.path().join(".claude")).expect("claude directory");
        fs::write(
            root.path().join(".claude/settings.local.json"),
            r#"{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"other"}]}]},"theme":"dark"}"#,
        )
        .expect("settings");
        fs::write(
            root.path().join(".mcp.json"),
            r#"{"mcpServers":{"other":{"command":"other"}}}"#,
        )
        .expect("mcp");

        apply_install(root.path(), "connector_abc", commands()).expect("first install");
        apply_install(root.path(), "connector_abc", commands()).expect("second install");

        let settings: Value = serde_json::from_slice(
            &fs::read(root.path().join(".claude/settings.local.json")).expect("read settings"),
        )
        .expect("parse settings");
        assert_eq!(settings["theme"], "dark");
        assert_eq!(
            settings["hooks"]["UserPromptSubmit"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(settings["hooks"]["Stop"].as_array().unwrap().len(), 2);
        assert_eq!(
            settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"],
            "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command \"& 'C:/Program Files/PersonalMemory/personalmemory-hook.exe' hook --connector-id connector_abc\""
        );
        let mcp: Value =
            serde_json::from_slice(&fs::read(root.path().join(".mcp.json")).expect("read mcp"))
                .expect("parse mcp");
        assert!(mcp["mcpServers"]["other"].is_object());
        assert!(mcp["mcpServers"]["personalmemory"].is_object());
        assert!(root.path().join(".mcp.json.personalmemory.bak").is_file());
    }

    #[test]
    fn remove_deletes_only_personalmemory_entries() {
        let root = tempfile::tempdir().expect("project root");
        apply_install(root.path(), "connector_abc", commands()).expect("install");
        apply_remove(root.path()).expect("remove");

        let settings: Value = serde_json::from_slice(
            &fs::read(root.path().join(".claude/settings.local.json")).expect("read settings"),
        )
        .expect("parse settings");
        assert!(
            settings["hooks"]["UserPromptSubmit"]
                .as_array()
                .unwrap()
                .is_empty()
        );
        let mcp: Value =
            serde_json::from_slice(&fs::read(root.path().join(".mcp.json")).expect("read mcp"))
                .expect("parse mcp");
        assert!(mcp["mcpServers"]["personalmemory"].is_null());
    }

    #[test]
    fn invalid_existing_json_is_not_overwritten() {
        let root = tempfile::tempdir().expect("project root");
        fs::create_dir(root.path().join(".claude")).expect("claude directory");
        let path = root.path().join(".claude/settings.local.json");
        fs::write(&path, "{broken").expect("settings");
        assert!(apply_install(root.path(), "connector_abc", commands()).is_err());
        assert_eq!(
            fs::read_to_string(path).expect("unchanged settings"),
            "{broken"
        );
    }

    #[test]
    fn global_install_is_idempotent_and_preserves_user_settings() {
        let home = tempfile::tempdir().expect("user home");
        fs::create_dir(home.path().join(".claude")).expect("claude directory");
        fs::write(
            home.path().join(".claude/settings.json"),
            r#"{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"other"}]}]},"theme":"dark"}"#,
        )
        .expect("settings");
        fs::write(
            home.path().join(".claude.json"),
            r#"{"mcpServers":{"other":{"command":"other"}},"numStartups":12}"#,
        )
        .expect("mcp config");

        apply_global_install(home.path(), "connector_global", commands()).expect("install");
        apply_global_install(home.path(), "connector_global", commands()).expect("reinstall");

        let status = global_status(home.path()).expect("status");
        assert!(status.settings_installed);
        assert!(status.mcp_installed);
        let settings: Value = serde_json::from_slice(
            &fs::read(home.path().join(".claude/settings.json")).expect("read settings"),
        )
        .expect("parse settings");
        assert_eq!(settings["theme"], "dark");
        assert_eq!(settings["hooks"]["Stop"].as_array().unwrap().len(), 2);
        let config: Value = serde_json::from_slice(
            &fs::read(home.path().join(".claude.json")).expect("read config"),
        )
        .expect("parse config");
        assert_eq!(config["numStartups"], 12);
        assert!(config["mcpServers"]["other"].is_object());
        assert!(config["mcpServers"]["personalmemory"].is_object());
    }

    #[test]
    fn global_partial_actions_install_only_the_missing_capability() {
        let home = tempfile::tempdir().expect("user home");

        apply_global_memory_read(home.path(), &commands()).expect("memory read");
        let read_only = global_status(home.path()).expect("read-only status");
        assert!(!read_only.settings_installed);
        assert!(read_only.mcp_installed);
        assert!(!read_only.mcp_conflict);

        apply_global_capture(home.path(), "connector_global", &commands()).expect("capture");
        let complete = global_status(home.path()).expect("complete status");
        assert!(complete.settings_installed);
        assert!(complete.mcp_installed);
        assert!(!complete.mcp_conflict);
    }

    #[test]
    fn old_posix_style_global_hook_is_reported_as_needing_repair() {
        let home = tempfile::tempdir().expect("home");
        fs::create_dir(home.path().join(".claude")).expect("claude directory");
        fs::write(
            home.path().join(".claude/settings.json"),
            r#"{"hooks":{"UserPromptSubmit":[{"hooks":[{"type":"command","command":"PERSONALMEMORY_DATA_DIR=C:/Users/demo/Data personalmemory-hook.exe hook --connector-id connector_old"}]}]}}"#,
        )
        .expect("settings");
        fs::write(
            home.path().join(".claude.json"),
            r#"{"mcpServers":{"personalmemory":{"command":"personalmemory-mcp.exe"}}}"#,
        )
        .expect("mcp");

        let status = global_status(home.path()).expect("status");
        assert!(!status.settings_installed);
        assert!(status.mcp_installed);
        assert!(!status.mcp_conflict);
    }

    #[test]
    fn quoted_executable_without_powershell_call_operator_needs_repair() {
        let home = tempfile::tempdir().expect("home");
        fs::create_dir(home.path().join(".claude")).expect("claude directory");
        fs::write(
            home.path().join(".claude/settings.json"),
            r#"{"hooks":{"UserPromptSubmit":[{"hooks":[{"type":"command","command":"\"C:/Program Files/PersonalMemory/personalmemory-hook.exe\" hook --connector-id connector_old"}]}],"Stop":[{"hooks":[{"type":"command","command":"\"C:/Program Files/PersonalMemory/personalmemory-hook.exe\" hook --connector-id connector_old"}]}]}}"#,
        )
        .expect("settings");
        fs::write(
            home.path().join(".claude.json"),
            r#"{"mcpServers":{"personalmemory":{"command":"personalmemory-mcp.exe"}}}"#,
        )
        .expect("mcp");

        let status = global_status(home.path()).expect("status");
        assert!(!status.settings_installed);
        assert!(status.mcp_installed);
    }

    #[test]
    fn powershell_only_call_operator_hook_needs_cross_shell_repair() {
        let home = tempfile::tempdir().expect("home");
        fs::create_dir(home.path().join(".claude")).expect("claude directory");
        fs::write(
            home.path().join(".claude/settings.json"),
            r#"{"hooks":{"UserPromptSubmit":[{"hooks":[{"type":"command","command":"& \"C:/Program Files/PersonalMemory/personalmemory-hook.exe\" hook --connector-id connector_old"}]}],"Stop":[{"hooks":[{"type":"command","command":"& \"C:/Program Files/PersonalMemory/personalmemory-hook.exe\" hook --connector-id connector_old"}]}]}}"#,
        )
        .expect("settings");
        fs::write(
            home.path().join(".claude.json"),
            r#"{"mcpServers":{"personalmemory":{"command":"personalmemory-mcp.exe"}}}"#,
        )
        .expect("mcp");

        let status = global_status(home.path()).expect("status");
        assert!(!status.settings_installed);
        assert!(status.mcp_installed);
    }

    #[test]
    fn codex_install_preserves_toml_and_removes_only_managed_server() {
        let home = tempfile::tempdir().expect("home");
        fs::create_dir(home.path().join(".codex")).expect("codex directory");
        fs::write(
            home.path().join(".codex/config.toml"),
            "model = \"gpt-test\"\n\n[mcp_servers.other]\ncommand = \"other.exe\"\n",
        )
        .expect("config");
        fs::write(
            home.path().join(".codex/hooks.json"),
            r#"{"hooks":{"UserPromptSubmit":[{"hooks":[{"type":"command","command":"other-hook.exe prompt"}]}],"Stop":[{"hooks":[{"type":"command","command":"other-hook.exe stop"}]}]}}"#,
        )
        .expect("hooks");

        apply_codex_install(home.path(), &commands()).expect("install");
        install_codex_guidance(home.path()).expect("guidance install");
        apply_codex_capture(home.path(), "connector_codex", &commands()).expect("hooks install");
        apply_codex_install(home.path(), &commands()).expect("reinstall");
        install_codex_guidance(home.path()).expect("guidance reinstall");
        apply_codex_capture(home.path(), "connector_codex", &commands()).expect("hooks reinstall");
        let status = codex_status(home.path()).expect("status");
        assert!(status.installed);
        assert!(status.mcp_installed);
        assert!(status.guidance_installed);
        assert!(status.hooks_installed);
        assert!(!status.conflict);
        let installed = fs::read_to_string(home.path().join(".codex/config.toml")).unwrap();
        assert!(installed.contains("model = \"gpt-test\""));
        assert!(installed.contains("[mcp_servers.other]"));
        assert!(installed.contains("[mcp_servers.personalmemory]"));
        let guidance = fs::read_to_string(home.path().join(".codex/AGENTS.md")).unwrap();
        assert_eq!(guidance.matches(CODEX_GUIDANCE_START).count(), 1);
        assert!(guidance.contains("without requiring the user to ask"));
        let hooks: Value = serde_json::from_slice(
            &fs::read(home.path().join(".codex/hooks.json")).expect("read hooks"),
        )
        .expect("parse hooks");
        assert_eq!(
            hooks["hooks"]["UserPromptSubmit"].as_array().unwrap().len(),
            2
        );
        assert_eq!(
            hooks["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"],
            "other-hook.exe prompt"
        );
        assert!(
            hooks["hooks"]["Stop"]
                .as_array()
                .unwrap()
                .iter()
                .filter_map(|group| group["hooks"].as_array())
                .flatten()
                .filter_map(|hook| hook["command"].as_str())
                .any(|command| command.contains("--agent-family codex"))
        );
        assert!(
            home.path()
                .join(".codex/config.toml.personalmemory.bak")
                .is_file()
        );

        apply_codex_remove(home.path()).expect("remove");
        remove_codex_guidance(home.path()).expect("remove guidance");
        remove_codex_capture(home.path()).expect("remove hooks");
        let removed = fs::read_to_string(home.path().join(".codex/config.toml")).unwrap();
        assert!(removed.contains("[mcp_servers.other]"));
        assert!(!removed.contains("[mcp_servers.personalmemory]"));
        assert!(
            !fs::read_to_string(home.path().join(".codex/AGENTS.md"))
                .unwrap()
                .contains(CODEX_GUIDANCE_START)
        );
        let hooks_after: Value = serde_json::from_slice(
            &fs::read(home.path().join(".codex/hooks.json")).expect("read hooks"),
        )
        .expect("parse hooks");
        assert_eq!(
            hooks_after["hooks"]["UserPromptSubmit"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            hooks_after["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"],
            "other-hook.exe prompt"
        );
    }

    #[test]
    fn invalid_codex_hooks_json_is_not_overwritten() {
        let home = tempfile::tempdir().expect("home");
        fs::create_dir(home.path().join(".codex")).expect("codex directory");
        let path = home.path().join(".codex/hooks.json");
        fs::write(&path, "{broken").expect("hooks");

        assert!(apply_codex_capture(home.path(), "connector_codex", &commands()).is_err());
        assert_eq!(fs::read_to_string(path).unwrap(), "{broken");
    }

    #[test]
    fn codex_hook_without_explicit_agent_family_needs_repair() {
        let home = tempfile::tempdir().expect("home");
        fs::create_dir(home.path().join(".codex")).expect("codex directory");
        fs::write(
            home.path().join(".codex/hooks.json"),
            r#"{"hooks":{"UserPromptSubmit":[{"hooks":[{"type":"command","command":"powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command \"& 'C:/Program Files/PersonalMemory/personalmemory-hook.exe' hook --connector-id connector_old\""}]}],"Stop":[{"hooks":[{"type":"command","command":"powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command \"& 'C:/Program Files/PersonalMemory/personalmemory-hook.exe' hook --connector-id connector_old\""}]}]}}"#,
        )
        .expect("hooks");

        let status = codex_status(home.path()).expect("status");
        assert!(!status.hooks_installed);
    }

    #[test]
    fn codex_guidance_uses_active_override_and_preserves_user_instructions() {
        let home = tempfile::tempdir().expect("home");
        fs::create_dir(home.path().join(".codex")).expect("codex directory");
        fs::write(
            home.path().join(".codex/AGENTS.md"),
            "# Base guidance\n\n- Keep this base rule.\n",
        )
        .expect("base guidance");
        fs::write(
            home.path().join(".codex/AGENTS.override.md"),
            "# Temporary override\n\n- Keep this override rule.\n",
        )
        .expect("override guidance");

        install_codex_guidance(home.path()).expect("install guidance");
        let status_before_mcp = codex_status(home.path()).expect("status");
        assert!(!status_before_mcp.installed);
        assert!(!status_before_mcp.mcp_installed);
        assert!(status_before_mcp.guidance_installed);
        assert!(
            status_before_mcp
                .guidance_path
                .ends_with("AGENTS.override.md")
        );

        let base = fs::read_to_string(home.path().join(".codex/AGENTS.md")).unwrap();
        let override_text =
            fs::read_to_string(home.path().join(".codex/AGENTS.override.md")).unwrap();
        assert_eq!(base, "# Base guidance\n\n- Keep this base rule.\n");
        assert!(override_text.contains("# Temporary override"));
        assert!(override_text.contains(CODEX_GUIDANCE_START));

        remove_codex_guidance(home.path()).expect("remove guidance");
        let override_after =
            fs::read_to_string(home.path().join(".codex/AGENTS.override.md")).unwrap();
        assert_eq!(
            override_after,
            "# Temporary override\n\n- Keep this override rule.\n"
        );
        assert_eq!(
            fs::read_to_string(home.path().join(".codex/AGENTS.md")).unwrap(),
            "# Base guidance\n\n- Keep this base rule.\n"
        );
    }

    #[test]
    fn malformed_codex_guidance_is_not_rewritten() {
        let home = tempfile::tempdir().expect("home");
        fs::create_dir(home.path().join(".codex")).expect("codex directory");
        let path = home.path().join(".codex/AGENTS.md");
        let malformed = format!("# User rules\n\n{CODEX_GUIDANCE_START}\nunfinished\n");
        fs::write(&path, &malformed).expect("guidance");

        assert!(install_codex_guidance(home.path()).is_err());
        assert_eq!(fs::read_to_string(path).unwrap(), malformed);
    }

    #[test]
    fn codex_install_refuses_unmanaged_name_collision() {
        let home = tempfile::tempdir().expect("home");
        fs::create_dir(home.path().join(".codex")).expect("codex directory");
        fs::write(
            home.path().join(".codex/config.toml"),
            "[mcp_servers.personalmemory]\ncommand = \"other.exe\"\n",
        )
        .expect("config");

        let error = apply_codex_install(home.path(), &commands()).unwrap_err();
        assert!(error.contains("not overwritten"));
        assert!(codex_status(home.path()).unwrap().conflict);
    }

    #[test]
    fn global_remove_keeps_unmanaged_config_and_memory_data() {
        let home = tempfile::tempdir().expect("user home");
        apply_global_install(home.path(), "connector_global", commands()).expect("install");
        apply_global_remove(home.path()).expect("remove");

        let status = global_status(home.path()).expect("status");
        assert!(!status.settings_installed);
        assert!(!status.mcp_installed);
        assert!(
            home.path()
                .join(".claude/settings.json.personalmemory.bak")
                .is_file()
        );
        assert!(
            home.path()
                .join(".claude.json.personalmemory.bak")
                .is_file()
        );
    }

    #[test]
    fn unmanaged_personalmemory_mcp_is_never_overwritten_or_removed() {
        let home = tempfile::tempdir().expect("user home");
        fs::create_dir(home.path().join(".claude")).expect("claude directory");
        fs::write(home.path().join(".claude/settings.json"), "{}\n").expect("settings");
        fs::write(
            home.path().join(".claude.json"),
            r#"{"mcpServers":{"personalmemory":{"command":"custom-memory-server"}}}"#,
        )
        .expect("config");

        let error = apply_global_install(home.path(), "connector_global", commands())
            .expect_err("unmanaged server must block install");
        assert!(error.contains("not overwritten"));
        assert!(global_status(home.path()).unwrap().mcp_conflict);
        apply_global_remove(home.path()).expect("remove skips unmanaged server");
        let config: Value = serde_json::from_slice(
            &fs::read(home.path().join(".claude.json")).expect("read config"),
        )
        .expect("parse config");
        assert_eq!(
            config["mcpServers"]["personalmemory"]["command"],
            "custom-memory-server"
        );
    }
}
