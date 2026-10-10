use std::{
    io::{Read, Write},
    process::{Child, Command, Stdio},
    thread,
    time::{Duration, Instant},
};

use serde_json::{Value, json};
#[cfg(not(debug_assertions))]
use tauri::Manager;

const MAX_STDOUT_BYTES: u64 = 64 * 1024;
const PROBE_TIMEOUT: Duration = Duration::from_secs(3);

/// Check that a fresh local PersonalMemory MCP process can reach its local service.
///
/// This does not inspect or modify Codex's existing MCP pipe or Hook trust.
#[tauri::command]
pub async fn verify_personalmemory_mcp(app: tauri::AppHandle) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || verify_personalmemory_mcp_blocking(app))
        .await
        .map_err(|_| "PersonalMemory MCP readiness check could not run".to_owned())?
}

fn verify_personalmemory_mcp_blocking(app: tauri::AppHandle) -> Result<(), String> {
    #[cfg(debug_assertions)]
    let _ = app;

    let data_dir =
        crate::data_dir().map_err(|_| "PersonalMemory data directory is unavailable".to_owned())?;

    #[cfg(debug_assertions)]
    let mut command = {
        let python =
            std::env::var_os("PERSONALMEMORY_DEV_PYTHON").unwrap_or_else(|| "python".into());
        let mut command = Command::new(python);
        command.args(["-m", "personalmemory.agent_mcp"]);
        command
    };

    #[cfg(not(debug_assertions))]
    let mut command = {
        let resource_dir = app
            .path()
            .resource_dir()
            .map_err(|_| "Bundled PersonalMemory MCP is unavailable".to_owned())?;
        let local_app_data = std::env::var_os("LOCALAPPDATA")
            .map(std::path::PathBuf::from)
            .ok_or_else(|| "PersonalMemory local runtime directory is unavailable".to_owned())?;
        let executable = crate::agent_runtime::ensure_mcp_runtime(
            &resource_dir.join("personalmemory-mcp"),
            &local_app_data,
            env!("CARGO_PKG_VERSION"),
        )
        .map_err(|_| "Bundled PersonalMemory MCP could not be prepared".to_owned())?;
        Command::new(executable)
    };

    command
        .env("PERSONALMEMORY_DATA_DIR", data_dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }

    let mut child = command
        .spawn()
        .map_err(|_| "PersonalMemory MCP could not be started".to_owned())?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| fail_child(&mut child, "PersonalMemory MCP could not be started"))?;

    // Drain output concurrently so a full pipe cannot prevent the child exiting. The extra byte
    // lets us distinguish output that fits the limit from truncated output.
    let output_reader = thread::spawn(move || {
        let mut output = Vec::new();
        let _ = stdout.take(MAX_STDOUT_BYTES + 1).read_to_end(&mut output);
        output
    });

    let input = match child.stdin.take() {
        Some(input) => input,
        None => {
            return Err(fail_child(
                &mut child,
                "PersonalMemory MCP could not be started",
            ));
        }
    };
    let mut input = input;
    let requests = [
        json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "personalmemory-readiness-check", "version": "1"}
            }
        }),
        json!({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json!({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "memory_context",
                "arguments": {
                    "query": "__personalmemory_connection_check__",
                    "token_budget": 128,
                    "limit": 1
                }
            }
        }),
    ];
    for request in requests {
        let mut line = serde_json::to_vec(&request)
            .map_err(|_| fail_child(&mut child, "PersonalMemory MCP check failed"))?;
        line.push(b'\n');
        if input.write_all(&line).is_err() {
            return Err(fail_child(&mut child, "PersonalMemory MCP check failed"));
        }
    }
    drop(input); // EOF signals the one-shot probe is complete.

    let deadline = Instant::now() + PROBE_TIMEOUT;
    loop {
        match child.try_wait() {
            Ok(Some(_status)) => break,
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(20)),
            Ok(None) => {
                return Err(fail_child(&mut child, "PersonalMemory MCP check timed out"));
            }
            Err(_) => return Err(fail_child(&mut child, "PersonalMemory MCP check failed")),
        }
    }

    let output = output_reader
        .join()
        .map_err(|_| "PersonalMemory MCP check failed".to_owned())?;
    if output.len() as u64 > MAX_STDOUT_BYTES {
        return Err("PersonalMemory MCP returned too much output".to_owned());
    }
    parse_tool_call_response(&output).map_err(str::to_owned)
}

fn fail_child(child: &mut Child, message: &'static str) -> String {
    let _ = child.kill();
    let _ = child.wait();
    message.to_owned()
}

fn parse_tool_call_response(output: &[u8]) -> Result<(), &'static str> {
    let output = std::str::from_utf8(output).map_err(|_| "PersonalMemory MCP check failed")?;
    for line in output.lines() {
        let Ok(response) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if response.get("id") != Some(&json!(2)) {
            continue;
        }
        let result = response
            .get("result")
            .ok_or("PersonalMemory MCP check failed")?;
        return match result.get("isError") {
            Some(Value::Bool(false)) => Ok(()),
            _ => Err("PersonalMemory MCP could not reach its local service"),
        };
    }
    Err("PersonalMemory MCP check failed")
}

#[cfg(test)]
mod tests {
    use super::parse_tool_call_response;

    #[test]
    fn parses_successful_tool_response() {
        let output = br#"{"jsonrpc":"2.0","id":2,"result":{"isError":false,"content":[]}}"#;
        assert_eq!(parse_tool_call_response(output), Ok(()));
    }

    #[test]
    fn rejects_tool_error_response() {
        let output = br#"{"jsonrpc":"2.0","id":2,"result":{"isError":true,"content":[]}}"#;
        assert_eq!(
            parse_tool_call_response(output),
            Err("PersonalMemory MCP could not reach its local service")
        );
    }

    #[test]
    fn rejects_malformed_or_missing_response() {
        assert_eq!(
            parse_tool_call_response(b"not json"),
            Err("PersonalMemory MCP check failed")
        );
        assert_eq!(
            parse_tool_call_response(br#"{"id":2,"result":{}}"#),
            Err("PersonalMemory MCP could not reach its local service")
        );
    }
}
