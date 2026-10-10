use std::{
    env, fs,
    io::{self, Read, Write},
    net::{IpAddr, Ipv4Addr, SocketAddr, TcpStream},
    path::{Component, Path, PathBuf},
    time::{Duration, UNIX_EPOCH},
};

use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

#[cfg(windows)]
use std::{
    ffi::{OsString, c_void},
    os::windows::ffi::{OsStrExt, OsStringExt},
};

#[cfg(windows)]
unsafe extern "system" {
    fn CreateFileW(
        file_name: *const u16,
        desired_access: u32,
        share_mode: u32,
        security_attributes: *const c_void,
        creation_disposition: u32,
        flags_and_attributes: u32,
        template_file: *mut c_void,
    ) -> *mut c_void;
    fn GetFinalPathNameByHandleW(
        file: *mut c_void,
        path: *mut u16,
        path_length: u32,
        flags: u32,
    ) -> u32;
    fn CloseHandle(object: *mut c_void) -> i32;
}

const DEFAULT_ENDPOINT: &str = "http://127.0.0.1:19191";
const DELIVERY_TIMEOUT: Duration = Duration::from_millis(500);
const MAX_HOOK_TEXT_BYTES: usize = 32 * 1024;
const DIAGNOSTICS_ENV: &str = "PERSONALMEMORY_HOOK_DIAGNOSTICS";

#[derive(Debug, Clone, Eq, PartialEq)]
struct Connection {
    endpoint: String,
    token: String,
}

#[derive(Debug, Clone, Eq, PartialEq)]
struct HttpEndpoint {
    address: SocketAddr,
    authority: String,
    base_path: String,
}

fn identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 180
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._:-".contains(&byte))
}

fn truncate_utf8(value: &str) -> &str {
    if value.len() <= MAX_HOOK_TEXT_BYTES {
        return value;
    }
    let mut boundary = MAX_HOOK_TEXT_BYTES;
    while !value.is_char_boundary(boundary) {
        boundary -= 1;
    }
    &value[..boundary]
}

fn default_data_dir() -> PathBuf {
    env::var_os("LOCALAPPDATA")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .map(|path| path.join("PersonalMemoryData"))
        .or_else(|| {
            env::var_os("USERPROFILE")
                .filter(|value| !value.is_empty())
                .map(PathBuf::from)
                .map(|path| path.join(".personalmemory"))
        })
        .unwrap_or_else(|| PathBuf::from(".personalmemory"))
}

fn resolve_connection() -> Connection {
    let mut endpoint = env::var("PERSONALMEMORY_ENDPOINT")
        .unwrap_or_default()
        .trim()
        .to_owned();
    let mut token = env::var("PERSONALMEMORY_TOKEN")
        .unwrap_or_default()
        .trim()
        .to_owned();
    if !endpoint.is_empty() && !token.is_empty() {
        return Connection { endpoint, token };
    }

    let data_dir = env::var_os("PERSONALMEMORY_DATA_DIR")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(default_data_dir);
    if let Ok(bytes) = fs::read(data_dir.join("runtime").join("connection.json"))
        && let Ok(payload) = serde_json::from_slice::<Value>(&bytes)
    {
        if endpoint.is_empty() {
            endpoint = payload
                .get("endpoint")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .trim()
                .to_owned();
        }
        if token.is_empty() {
            token = payload
                .get("token")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .trim()
                .to_owned();
        }
    }
    Connection {
        endpoint: if endpoint.is_empty() {
            DEFAULT_ENDPOINT.to_owned()
        } else {
            endpoint
        },
        token,
    }
}

fn absolute_path(raw: Option<&Value>, field: &str, jsonl: bool) -> Result<String, String> {
    let value = raw.and_then(Value::as_str).unwrap_or_default().trim();
    let path = Path::new(value);
    if value.is_empty()
        || !path.is_absolute()
        || path
            .components()
            .any(|component| matches!(component, Component::ParentDir))
    {
        return Err(format!("invalid {field}"));
    }
    if jsonl
        && path
            .extension()
            .and_then(|extension| extension.to_str())
            .is_none_or(|extension| !extension.eq_ignore_ascii_case("jsonl"))
    {
        return Err("invalid transcript_path".to_owned());
    }
    Ok(portable_windows_path(canonical_path(path)))
}

fn transcript_locator(
    raw: Option<&Value>,
    agent_family: &str,
    session_id: &str,
) -> Result<String, String> {
    if raw.is_none_or(Value::is_null) && agent_family == "codex" {
        return Ok(format!("codex://session/{session_id}"));
    }
    absolute_path(raw, "transcript_path", true)
}

#[cfg(windows)]
fn canonical_path(path: &Path) -> String {
    const FILE_SHARE_ALL: u32 = 0x0000_0001 | 0x0000_0002 | 0x0000_0004;
    const OPEN_EXISTING: u32 = 3;
    const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;
    const INVALID_HANDLE_VALUE: *mut c_void = -1_isize as *mut c_void;

    let wide = path
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
    // Opening with zero desired access is sufficient to query the stable final path.
    let handle = unsafe {
        CreateFileW(
            wide.as_ptr(),
            0,
            FILE_SHARE_ALL,
            std::ptr::null(),
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS,
            std::ptr::null_mut(),
        )
    };
    if handle != INVALID_HANDLE_VALUE {
        let mut buffer = vec![0_u16; 512];
        let mut length = unsafe {
            GetFinalPathNameByHandleW(handle, buffer.as_mut_ptr(), buffer.len() as u32, 0)
        };
        if length as usize >= buffer.len() {
            buffer.resize(length as usize + 1, 0);
            length = unsafe {
                GetFinalPathNameByHandleW(handle, buffer.as_mut_ptr(), buffer.len() as u32, 0)
            };
        }
        unsafe {
            CloseHandle(handle);
        }
        if length > 0 && (length as usize) < buffer.len() {
            return OsString::from_wide(&buffer[..length as usize])
                .to_string_lossy()
                .into_owned();
        }
    }
    fs::canonicalize(path)
        .unwrap_or_else(|_| path.to_path_buf())
        .to_string_lossy()
        .into_owned()
}

#[cfg(not(windows))]
fn canonical_path(path: &Path) -> String {
    fs::canonicalize(path)
        .unwrap_or_else(|_| path.to_path_buf())
        .to_string_lossy()
        .into_owned()
}

fn portable_windows_path(value: String) -> String {
    if let Some(unc) = value.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{unc}")
    } else {
        value.strip_prefix(r"\\?\").unwrap_or(&value).to_owned()
    }
}

fn prompt_id(
    payload: &Map<String, Value>,
    event_name: &str,
    session_id: &str,
    transcript_path: &str,
    selected_text: &str,
) -> Result<Option<String>, String> {
    if let Some(raw) = payload
        .get("prompt_id")
        .or_else(|| payload.get("promptId"))
        .or_else(|| payload.get("turn_id"))
    {
        let value = raw.as_str().unwrap_or_default();
        if !identifier(value) {
            return Err("invalid prompt_id".to_owned());
        }
        return Ok(Some(value.to_owned()));
    }
    if event_name != "UserPromptSubmit" {
        return Ok(None);
    }

    let transcript_state = fs::metadata(transcript_path)
        .ok()
        .and_then(|metadata| {
            let modified = metadata.modified().ok()?;
            let nanos = modified.duration_since(UNIX_EPOCH).ok()?.as_nanos();
            Some(format!("{}:{nanos}", metadata.len()))
        })
        .unwrap_or_else(|| "unavailable".to_owned());
    let identity = [
        session_id,
        transcript_path,
        &transcript_state,
        selected_text,
    ]
    .join("\0");
    let digest = format!("{:x}", Sha256::digest(identity.as_bytes()));
    Ok(Some(format!("prompt:auto:{}", &digest[..32])))
}

fn translate_payload(
    payload: &Value,
    connector_id: &str,
    agent_family: &str,
) -> Result<Value, String> {
    if !identifier(connector_id) {
        return Err("invalid connector_id".to_owned());
    }
    if !matches!(agent_family, "claude_code" | "codex") {
        return Err("unsupported agent family".to_owned());
    }
    let payload = payload
        .as_object()
        .ok_or_else(|| "hook payload must be an object".to_owned())?;
    let event_name = payload
        .get("hook_event_name")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if !matches!(event_name, "UserPromptSubmit" | "Stop") {
        return Err("unsupported hook event".to_owned());
    }
    let session_id = payload
        .get("session_id")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if !identifier(session_id) {
        return Err("invalid session_id".to_owned());
    }
    let selected_text = match event_name {
        "UserPromptSubmit" => payload.get("prompt"),
        _ => payload.get("last_assistant_message"),
    }
    .and_then(Value::as_str)
    .map(str::trim)
    .filter(|value| !value.is_empty())
    .ok_or_else(|| "hook text is required".to_owned())?;
    let selected_text = truncate_utf8(selected_text);
    let transcript_path =
        transcript_locator(payload.get("transcript_path"), agent_family, session_id)?;
    let cwd = absolute_path(payload.get("cwd"), "cwd", false)?;
    let prompt_id = prompt_id(
        payload,
        event_name,
        session_id,
        &transcript_path,
        selected_text,
    )?;

    Ok(json!({
        "schema_version": "1",
        "connector_id": connector_id,
        "agent_family": agent_family,
        "fragment_kind": if event_name == "UserPromptSubmit" { "user_message" } else { "assistant_message" },
        "session_id": session_id,
        "prompt_id": prompt_id,
        "cwd": cwd,
        "transcript_path": transcript_path,
        "user_text": if event_name == "UserPromptSubmit" { Some(selected_text) } else { None },
        "assistant_text": if event_name == "Stop" { Some(selected_text) } else { None },
    }))
}

fn parse_http_endpoint(endpoint: &str) -> Result<HttpEndpoint, String> {
    let remainder = endpoint
        .strip_prefix("http://")
        .ok_or_else(|| "runtime endpoint must use http".to_owned())?;
    let (authority, base_path) = remainder
        .split_once('/')
        .map_or((remainder, String::new()), |(authority, path)| {
            (authority, format!("/{}", path.trim_end_matches('/')))
        });
    let (host, port) = authority
        .rsplit_once(':')
        .ok_or_else(|| "runtime endpoint must include a port".to_owned())?;
    if !matches!(host, "127.0.0.1" | "localhost") {
        return Err("runtime endpoint must be loopback".to_owned());
    }
    let port = port
        .parse::<u16>()
        .map_err(|_| "runtime endpoint has an invalid port".to_owned())?;
    Ok(HttpEndpoint {
        address: SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), port),
        authority: authority.to_owned(),
        base_path,
    })
}

fn decode_chunked(mut body: &[u8]) -> Result<Vec<u8>, String> {
    let mut decoded = Vec::new();
    loop {
        let line_end = body
            .windows(2)
            .position(|window| window == b"\r\n")
            .ok_or_else(|| "invalid chunked response".to_owned())?;
        let size_text =
            std::str::from_utf8(&body[..line_end]).map_err(|_| "invalid chunk size".to_owned())?;
        let size = usize::from_str_radix(size_text.split(';').next().unwrap_or_default(), 16)
            .map_err(|_| "invalid chunk size".to_owned())?;
        body = &body[line_end + 2..];
        if size == 0 {
            return Ok(decoded);
        }
        if body.len() < size + 2 || &body[size..size + 2] != b"\r\n" {
            return Err("truncated chunked response".to_owned());
        }
        decoded.extend_from_slice(&body[..size]);
        body = &body[size + 2..];
    }
}

fn post_json(endpoint: &str, token: &str, body: &[u8]) -> Result<Value, String> {
    let endpoint = parse_http_endpoint(endpoint)?;
    let mut stream = TcpStream::connect_timeout(&endpoint.address, DELIVERY_TIMEOUT)
        .map_err(|error| error.to_string())?;
    stream
        .set_read_timeout(Some(DELIVERY_TIMEOUT))
        .map_err(|error| error.to_string())?;
    stream
        .set_write_timeout(Some(DELIVERY_TIMEOUT))
        .map_err(|error| error.to_string())?;
    let path = format!(
        "{}/api/v1/observations/agent-turns",
        endpoint.base_path.trim_end_matches('/')
    );
    write!(
        stream,
        "POST {path} HTTP/1.1\r\nHost: {}\r\nAuthorization: Bearer {token}\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: {}\r\n\r\n",
        endpoint.authority,
        body.len(),
    )
    .map_err(|error| error.to_string())?;
    stream.write_all(body).map_err(|error| error.to_string())?;
    stream.flush().map_err(|error| error.to_string())?;

    let mut response = Vec::new();
    stream
        .read_to_end(&mut response)
        .map_err(|error| error.to_string())?;
    let header_end = response
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .ok_or_else(|| "invalid HTTP response".to_owned())?;
    let headers = std::str::from_utf8(&response[..header_end])
        .map_err(|_| "invalid HTTP headers".to_owned())?;
    let status = headers
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|value| value.parse::<u16>().ok())
        .ok_or_else(|| "invalid HTTP status".to_owned())?;
    if !matches!(status, 200..=202) {
        return Err(format!("unexpected HTTP status {status}"));
    }
    let raw_body = &response[header_end + 4..];
    let body = if headers
        .lines()
        .any(|line| line.eq_ignore_ascii_case("transfer-encoding: chunked"))
    {
        decode_chunked(raw_body)?
    } else {
        raw_body.to_vec()
    };
    serde_json::from_slice(&body).map_err(|error| error.to_string())
}

fn deliver(
    connector_id: &str,
    agent_family: &str,
    payload: &Value,
) -> Result<Option<String>, String> {
    let event = translate_payload(payload, connector_id, agent_family)?;
    let connection = resolve_connection();
    if connection.token.is_empty() {
        return Ok(None);
    }
    let body = serde_json::to_vec(&event).map_err(|error| error.to_string())?;
    let result = post_json(&connection.endpoint, &connection.token, &body)?;
    if event.get("fragment_kind").and_then(Value::as_str) != Some("user_message") {
        return Ok(None);
    }
    Ok(result
        .get("preflight")
        .and_then(|value| value.get("context"))
        .and_then(Value::as_str)
        .filter(|context| !context.is_empty())
        .map(str::to_owned))
}

fn parse_options() -> Option<(String, String)> {
    let mut arguments = env::args().skip(1);
    if arguments.next().as_deref() != Some("hook") {
        return None;
    }
    let mut connector_id = None;
    let mut agent_family = "claude_code".to_owned();
    while let Some(argument) = arguments.next() {
        if argument == "--connector-id" {
            connector_id = arguments.next().filter(|value| identifier(value));
        } else if argument == "--agent-family" {
            agent_family = arguments.next()?;
        }
    }
    if !matches!(agent_family.as_str(), "claude_code" | "codex") {
        return None;
    }
    Some((connector_id?, agent_family))
}

fn run() -> Result<(), String> {
    let (connector_id, agent_family) =
        parse_options().ok_or_else(|| "invalid arguments".to_owned())?;
    let mut input = Vec::new();
    io::stdin()
        .read_to_end(&mut input)
        .map_err(|error| error.to_string())?;
    let payload = serde_json::from_slice::<Value>(&input).map_err(|error| error.to_string())?;
    if let Some(context) = deliver(&connector_id, &agent_family, &payload)? {
        let output = json!({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        });
        serde_json::to_writer(io::stdout().lock(), &output).map_err(|error| error.to_string())?;
    }
    Ok(())
}

fn main() {
    if let Err(error) = run()
        && env::var(DIAGNOSTICS_ENV).as_deref() == Ok("1")
    {
        eprintln!("PersonalMemory Hook: {error}");
    }
}

#[cfg(test)]
mod tests {
    use super::{
        MAX_HOOK_TEXT_BYTES, decode_chunked, identifier, parse_http_endpoint,
        portable_windows_path, prompt_id, translate_payload, truncate_utf8,
    };
    use serde_json::{Map, Value, json};
    use std::{fs, net::Ipv4Addr};

    fn payload(root: &std::path::Path) -> Value {
        let transcript = root.join("session.jsonl");
        fs::write(&transcript, "").expect("transcript");
        json!({
            "session_id": "session-01",
            "prompt_id": "prompt-01",
            "transcript_path": transcript,
            "cwd": root,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "I prefer local-first storage."
        })
    }

    #[test]
    fn identifiers_match_the_boundary_contract() {
        assert!(identifier("connector_abc:01"));
        assert!(!identifier(""));
        assert!(!identifier("has spaces"));
        assert!(!identifier(&"a".repeat(181)));
    }

    #[test]
    fn utf8_truncation_preserves_character_boundaries() {
        let input = format!("{}中", "a".repeat(MAX_HOOK_TEXT_BYTES - 1));
        let output = truncate_utf8(&input);
        assert_eq!(output.len(), MAX_HOOK_TEXT_BYTES - 1);
        assert!(!output.ends_with('中'));
    }

    #[test]
    fn translates_user_and_stop_fragments() {
        let root = tempfile::tempdir().expect("temporary project");
        let user = translate_payload(&payload(root.path()), "connector-01", "claude_code")
            .expect("user event");
        assert_eq!(user["fragment_kind"], "user_message");
        assert_eq!(user["user_text"], "I prefer local-first storage.");
        assert!(user["assistant_text"].is_null());
        assert!(!user["cwd"].as_str().unwrap().starts_with(r"\\?\"));

        let mut stop = payload(root.path());
        stop["hook_event_name"] = "Stop".into();
        stop["last_assistant_message"] = "Done.".into();
        let stop = translate_payload(&stop, "connector-01", "claude_code").expect("stop event");
        assert_eq!(stop["fragment_kind"], "assistant_message");
        assert_eq!(stop["assistant_text"], "Done.");
        assert!(stop["user_text"].is_null());
    }

    #[test]
    fn removes_windows_verbatim_prefixes() {
        assert_eq!(
            portable_windows_path(r"\\?\C:\Users\Test\project".to_owned()),
            r"C:\Users\Test\project"
        );
        assert_eq!(
            portable_windows_path(r"\\?\UNC\server\share\project".to_owned()),
            r"\\server\share\project"
        );
    }

    #[cfg(windows)]
    #[test]
    fn canonical_path_uses_filesystem_casing() {
        let root = tempfile::tempdir().expect("temporary root");
        let mixed = root.path().join("MixedCase");
        fs::create_dir(&mixed).expect("mixed-case directory");
        let lower = root.path().join("mixedcase");
        assert!(super::canonical_path(&lower).ends_with("MixedCase"));
    }

    #[test]
    fn synthesizes_a_stable_prompt_id() {
        let root = tempfile::tempdir().expect("temporary project");
        let mut payload = payload(root.path());
        payload.as_object_mut().expect("object").remove("prompt_id");
        let first = translate_payload(&payload, "connector-01", "claude_code").expect("first");
        let second = translate_payload(&payload, "connector-01", "claude_code").expect("second");
        assert!(
            first["prompt_id"]
                .as_str()
                .unwrap()
                .starts_with("prompt:auto:")
        );
        assert_eq!(first["prompt_id"], second["prompt_id"]);
    }

    #[test]
    fn stop_without_prompt_id_does_not_invent_one() {
        let payload = Map::<String, Value>::new();
        assert_eq!(
            prompt_id(&payload, "Stop", "session", "C:/session.jsonl", "Done").unwrap(),
            None
        );
    }

    #[test]
    fn codex_turn_id_correlates_both_hook_fragments() {
        let root = tempfile::tempdir().expect("temporary project");
        let mut user_payload = payload(root.path());
        user_payload
            .as_object_mut()
            .expect("object")
            .remove("prompt_id");
        user_payload["turn_id"] = "turn-01".into();
        let user = translate_payload(&user_payload, "connector-01", "codex").expect("user");

        let mut stop_payload = user_payload;
        stop_payload["hook_event_name"] = "Stop".into();
        stop_payload["last_assistant_message"] = "Done.".into();
        let stop = translate_payload(&stop_payload, "connector-01", "codex").expect("stop");

        assert_eq!(user["agent_family"], "codex");
        assert_eq!(user["prompt_id"], "turn-01");
        assert_eq!(stop["prompt_id"], "turn-01");
    }

    #[test]
    fn codex_accepts_null_transcript_path_with_a_truthful_locator() {
        let root = tempfile::tempdir().expect("temporary project");
        let mut event = payload(root.path());
        event.as_object_mut().expect("object").remove("prompt_id");
        event["turn_id"] = "turn-null-transcript".into();
        event["transcript_path"] = Value::Null;

        let translated = translate_payload(&event, "connector-01", "codex").expect("event");

        assert_eq!(translated["transcript_path"], "codex://session/session-01");
        assert_eq!(translated["prompt_id"], "turn-null-transcript");
    }

    #[test]
    fn endpoint_is_restricted_to_authenticated_loopback_service() {
        let endpoint = parse_http_endpoint("http://127.0.0.1:19191").expect("endpoint");
        assert_eq!(endpoint.address.ip(), Ipv4Addr::LOCALHOST);
        assert!(parse_http_endpoint("https://127.0.0.1:19191").is_err());
        assert!(parse_http_endpoint("http://example.com:19191").is_err());
    }

    #[test]
    fn decodes_chunked_json_body() {
        assert_eq!(
            decode_chunked(b"3\r\n{\"a\r\n4\r\n\":1}\r\n0\r\n\r\n").unwrap(),
            b"{\"a\":1}"
        );
    }
}
