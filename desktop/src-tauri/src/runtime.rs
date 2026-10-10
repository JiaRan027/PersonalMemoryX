use serde::Serialize;

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeConnection {
    endpoint: String,
    token: String,
    mcp_command: String,
    mcp_json: String,
}

impl RuntimeConnection {
    pub fn new(
        endpoint: impl Into<String>,
        token: impl Into<String>,
        mcp_command: impl Into<String>,
        mcp_json: impl Into<String>,
    ) -> Self {
        Self {
            endpoint: endpoint.into(),
            token: token.into(),
            mcp_command: mcp_command.into(),
            mcp_json: mcp_json.into(),
        }
    }

    pub fn initialization_script(&self) -> String {
        let value = serde_json::to_string(self).expect("runtime connection is serializable");
        format!(
            "Object.defineProperty(window, '__PERSONALMEMORY__', {{ value: Object.freeze({value}), writable: false, configurable: false }});"
        )
    }
}

#[cfg(test)]
mod tests {
    use super::RuntimeConnection;

    fn connection() -> RuntimeConnection {
        RuntimeConnection::new(
            "http://127.0.0.1:19421",
            "session-secret",
            "python -m personalmemory.agent_mcp",
            r#"{"mcpServers":{"personalmemory":{"command":"python","args":["-m","personalmemory.agent_mcp"]}}}"#,
        )
    }

    #[test]
    fn initialization_script_uses_a_read_only_window_value() {
        let script = connection().initialization_script();

        assert!(script.contains("window"));
        assert!(script.contains("__PERSONALMEMORY__"));
        assert!(script.contains("Object.freeze"));
        assert!(script.contains("writable: false"));
        assert!(script.contains("configurable: false"));
        assert!(!script.contains("?token="));
        assert!(!script.contains("localStorage"));
    }

    #[test]
    fn initialization_script_serializes_values_instead_of_interpolating_them() {
        let script = RuntimeConnection::new(
            "http://127.0.0.1:19421",
            "quote-' newline-\n-session-secret",
            "python -m personalmemory.agent_mcp",
            r#"{"mcpServers":{}}"#,
        )
        .initialization_script();

        assert!(script.contains("newline-\\n-session-secret"));
        assert!(!script.contains("newline-\n-session-secret"));
    }

    #[test]
    fn initialization_script_exposes_mcp_copy_configuration() {
        let script = connection().initialization_script();

        assert!(script.contains("mcpCommand"));
        assert!(script.contains("python -m personalmemory.agent_mcp"));
        assert!(script.contains("mcpJson"));
        assert!(script.contains("personalmemory.agent_mcp"));
    }
}
