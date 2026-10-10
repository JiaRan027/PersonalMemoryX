use std::{
    collections::BTreeMap,
    ffi::OsString,
    io::{self, Read, Write},
    net::{SocketAddr, TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    thread,
    time::{Duration, Instant},
};

use base64::{Engine as _, engine::general_purpose::URL_SAFE_NO_PAD};
use rand::RngCore;
use serde::Deserialize;
use thiserror::Error;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

pub fn generate_session_token() -> String {
    let mut bytes = [0_u8; 48];
    rand::rng().fill_bytes(&mut bytes);
    URL_SAFE_NO_PAD.encode(bytes)
}

pub fn reserve_local_port() -> io::Result<u16> {
    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    Ok(listener.local_addr()?.port())
}

#[derive(Clone, Debug)]
pub struct ServiceLaunch {
    pub executable: PathBuf,
    pub args: Vec<OsString>,
    pub environment: BTreeMap<String, String>,
    port: u16,
}

impl ServiceLaunch {
    pub fn new(executable: &Path, data_dir: &Path, port: u16, token: &str) -> Self {
        let environment = BTreeMap::from([
            (
                "PERSONALMEMORY_DATA_DIR".to_owned(),
                data_dir.to_string_lossy().into_owned(),
            ),
            ("PERSONALMEMORY_HOST".to_owned(), "127.0.0.1".to_owned()),
            ("PERSONALMEMORY_PORT".to_owned(), port.to_string()),
            ("PERSONALMEMORY_API_TOKEN".to_owned(), token.to_owned()),
            (
                "PERSONALMEMORY_PARENT_PID".to_owned(),
                std::process::id().to_string(),
            ),
        ]);

        Self {
            executable: executable.to_path_buf(),
            args: Vec::new(),
            environment,
            port,
        }
    }

    pub fn endpoint(&self) -> String {
        format!("http://127.0.0.1:{}", self.port)
    }

    #[cfg(any(debug_assertions, test))]
    pub fn for_development(
        python: &Path,
        service_source: &Path,
        data_dir: &Path,
        port: u16,
        token: &str,
    ) -> Self {
        let mut launch = Self::new(python, data_dir, port, token);
        launch.args = vec![OsString::from("-m"), OsString::from("personalmemory.main")];
        launch.environment.insert(
            "PYTHONPATH".to_owned(),
            service_source.to_string_lossy().into_owned(),
        );
        launch
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ServiceState {
    Starting,
    Ready,
    Stopping,
    Stopped,
    Failed,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum StopOutcome {
    Graceful,
    Forced,
    AlreadyStopped,
}

#[derive(Debug, Error)]
pub enum SupervisorError {
    #[error("service launch is missing a session token")]
    MissingSessionToken,
    #[error("failed to start memory service: {0}")]
    Spawn(#[source] io::Error),
    #[error("failed to inspect memory service process: {0}")]
    Process(#[source] io::Error),
    #[error("memory service exited before it became ready with code {0:?}")]
    ChildExited(Option<i32>),
    #[error("memory service did not become ready before the timeout")]
    ReadyTimeout,
    #[error("local API request failed: {0}")]
    Request(#[source] io::Error),
    #[error("local API returned an invalid HTTP response")]
    InvalidHttpResponse,
    #[error("local API returned status {0}")]
    ApiStatus(u16),
    #[error("local API returned invalid JSON: {0}")]
    InvalidJson(#[source] serde_json::Error),
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
pub struct RuntimeControlState {
    pub sync_paused: bool,
    pub privacy_mode: bool,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
pub struct SyncNowResult {
    pub ingested: u64,
    pub projected: u64,
    pub failed: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct ConnectorStatus {
    pub id: String,
    pub name: String,
    pub status: String,
}

#[derive(Deserialize)]
struct HealthStatusResponse {
    status: String,
}

#[derive(Deserialize)]
struct ConnectorStatusResponse {
    items: Vec<ConnectorStatus>,
}

impl ConnectorStatus {
    #[cfg(test)]
    pub fn new(id: &str, name: &str, status: &str) -> Self {
        Self {
            id: id.to_owned(),
            name: name.to_owned(),
            status: status.to_owned(),
        }
    }
}

pub struct ServiceSupervisor {
    child: Child,
    port: u16,
    token: String,
    state: ServiceState,
}

impl ServiceSupervisor {
    pub fn spawn(launch: ServiceLaunch) -> Result<Self, SupervisorError> {
        let token = launch
            .environment
            .get("PERSONALMEMORY_API_TOKEN")
            .cloned()
            .ok_or(SupervisorError::MissingSessionToken)?;
        let mut command = Command::new(&launch.executable);
        command
            .args(&launch.args)
            .envs(&launch.environment)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);
        let child = command.spawn().map_err(SupervisorError::Spawn)?;

        Ok(Self {
            child,
            port: launch.port,
            token,
            state: ServiceState::Starting,
        })
    }

    #[cfg(test)]
    pub fn state(&self) -> ServiceState {
        self.state
    }

    pub fn wait_ready(&mut self, timeout: Duration) -> Result<(), SupervisorError> {
        let deadline = Instant::now() + timeout;
        while Instant::now() < deadline {
            if let Some(status) = self.child.try_wait().map_err(SupervisorError::Process)? {
                self.state = ServiceState::Failed;
                return Err(SupervisorError::ChildExited(status.code()));
            }
            if self.probe_health().unwrap_or(false) {
                self.state = ServiceState::Ready;
                return Ok(());
            }
            thread::sleep(Duration::from_millis(50));
        }
        self.state = ServiceState::Failed;
        Err(SupervisorError::ReadyTimeout)
    }

    pub fn stop(&mut self, timeout: Duration) -> Result<StopOutcome, SupervisorError> {
        if self
            .child
            .try_wait()
            .map_err(SupervisorError::Process)?
            .is_some()
        {
            self.state = ServiceState::Stopped;
            return Ok(StopOutcome::AlreadyStopped);
        }

        self.state = ServiceState::Stopping;
        let _ = self.request("POST", "/api/v1/system/shutdown");
        let deadline = Instant::now() + timeout;
        while Instant::now() < deadline {
            if self
                .child
                .try_wait()
                .map_err(SupervisorError::Process)?
                .is_some()
            {
                self.state = ServiceState::Stopped;
                return Ok(StopOutcome::Graceful);
            }
            thread::sleep(Duration::from_millis(50));
        }

        self.child.kill().map_err(SupervisorError::Process)?;
        self.child.wait().map_err(SupervisorError::Process)?;
        self.state = ServiceState::Stopped;
        Ok(StopOutcome::Forced)
    }

    pub fn runtime_control(&self) -> Result<RuntimeControlState, SupervisorError> {
        self.request_json("GET", "/api/v1/system/runtime", None)
    }

    pub fn update_runtime_control(
        &self,
        sync_paused: Option<bool>,
        privacy_mode: Option<bool>,
    ) -> Result<RuntimeControlState, SupervisorError> {
        let mut payload = serde_json::Map::new();
        if let Some(value) = sync_paused {
            payload.insert("sync_paused".to_owned(), value.into());
        }
        if let Some(value) = privacy_mode {
            payload.insert("privacy_mode".to_owned(), value.into());
        }
        let body = serde_json::Value::Object(payload).to_string();
        self.request_json("PATCH", "/api/v1/system/runtime", Some(&body))
    }

    pub fn sync_now(&self) -> Result<SyncNowResult, SupervisorError> {
        self.request_json("POST", "/api/v1/system/sync", None)
    }

    pub fn health_status(&self) -> Result<String, SupervisorError> {
        self.request_json::<HealthStatusResponse>("GET", "/api/v1/health", None)
            .map(|response| response.status)
    }

    pub fn connector_statuses(&self) -> Result<Vec<ConnectorStatus>, SupervisorError> {
        self.request_json::<ConnectorStatusResponse>("GET", "/api/v1/connectors", None)
            .map(|response| response.items)
    }

    fn probe_health(&self) -> io::Result<bool> {
        let response = self.request("GET", "/api/v1/health")?;
        Ok(response.starts_with("HTTP/1.0 200") || response.starts_with("HTTP/1.1 200"))
            .map(|ready| ready && response.contains("\"status\":\"ready\""))
    }

    fn request(&self, method: &str, path: &str) -> io::Result<String> {
        self.request_with_body(method, path, None)
    }

    fn request_with_body(
        &self,
        method: &str,
        path: &str,
        body: Option<&str>,
    ) -> io::Result<String> {
        let address = SocketAddr::from(([127, 0, 0, 1], self.port));
        let mut stream = TcpStream::connect_timeout(&address, Duration::from_millis(200))?;
        stream.set_read_timeout(Some(Duration::from_millis(300)))?;
        stream.set_write_timeout(Some(Duration::from_millis(300)))?;
        let body = body.unwrap_or("");
        write!(
            stream,
            "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {}\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: {}\r\n\r\n{}",
            self.port,
            self.token,
            body.len(),
            body,
        )?;
        stream.flush()?;
        let mut response = String::new();
        stream.read_to_string(&mut response)?;
        Ok(response)
    }

    fn request_json<T: for<'de> Deserialize<'de>>(
        &self,
        method: &str,
        path: &str,
        body: Option<&str>,
    ) -> Result<T, SupervisorError> {
        let response = self
            .request_with_body(method, path, body)
            .map_err(SupervisorError::Request)?;
        let (headers, response_body) = response
            .split_once("\r\n\r\n")
            .ok_or(SupervisorError::InvalidHttpResponse)?;
        let status = headers
            .lines()
            .next()
            .and_then(|line| line.split_whitespace().nth(1))
            .and_then(|value| value.parse::<u16>().ok())
            .ok_or(SupervisorError::InvalidHttpResponse)?;
        if !(200..300).contains(&status) {
            return Err(SupervisorError::ApiStatus(status));
        }
        serde_json::from_str(response_body).map_err(SupervisorError::InvalidJson)
    }
}

impl Drop for ServiceSupervisor {
    fn drop(&mut self) {
        if matches!(self.child.try_wait(), Ok(None)) {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}

#[cfg(test)]
mod tests {
    use std::{ffi::OsString, path::Path, time::Duration};

    use super::{
        ServiceLaunch, ServiceState, ServiceSupervisor, StopOutcome, SupervisorError,
        generate_session_token, reserve_local_port,
    };

    fn fake_service(mode: &str) -> (ServiceLaunch, tempfile::TempDir) {
        let data_dir = tempfile::tempdir().expect("create temporary data directory");
        let script = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests")
            .join("fixtures")
            .join("fake_service.py");
        let python =
            std::env::var("PERSONALMEMORY_TEST_PYTHON").unwrap_or_else(|_| "python".to_owned());
        let mut launch = ServiceLaunch::new(
            Path::new(&python),
            data_dir.path(),
            reserve_local_port().expect("reserve fake service port"),
            "test-session-token",
        );
        launch.args = vec![script.into_os_string(), OsString::from(mode)];
        (launch, data_dir)
    }

    #[test]
    fn session_tokens_are_random_and_not_short() {
        let first = generate_session_token();
        let second = generate_session_token();

        assert_ne!(first, second);
        assert!(first.len() >= 64);
    }

    #[test]
    fn reserved_service_port_is_ephemeral_and_local() {
        let port = reserve_local_port().expect("reserve local port");

        assert_ne!(port, 0);
    }

    #[test]
    fn service_secret_is_passed_by_environment_not_command_line() {
        let launch = ServiceLaunch::new(
            Path::new("personalmemory-service.exe"),
            Path::new("C:/Users/Test/AppData/Local/PersonalMemory"),
            19421,
            "session-secret",
        );

        let args = launch
            .args
            .iter()
            .map(|value| value.to_string_lossy())
            .collect::<Vec<_>>()
            .join(" ");
        assert!(!args.contains("session-secret"));
        assert_eq!(launch.environment["PERSONALMEMORY_HOST"], "127.0.0.1");
        assert_eq!(launch.environment["PERSONALMEMORY_PORT"], "19421");
        assert_eq!(
            launch.environment["PERSONALMEMORY_API_TOKEN"],
            "session-secret"
        );
        assert_eq!(
            launch.environment["PERSONALMEMORY_PARENT_PID"],
            std::process::id().to_string()
        );
    }

    #[test]
    fn development_launch_uses_python_module_with_explicit_source_path() {
        let launch = ServiceLaunch::for_development(
            Path::new("C:/Python/python.exe"),
            Path::new("C:/repo/service/src"),
            Path::new("C:/pm-data"),
            19421,
            "session-secret",
        );

        assert_eq!(launch.executable, Path::new("C:/Python/python.exe"));
        assert_eq!(
            launch.args,
            vec![OsString::from("-m"), OsString::from("personalmemory.main")]
        );
        assert_eq!(launch.environment["PYTHONPATH"], "C:/repo/service/src");
    }

    #[test]
    fn launch_endpoint_is_loopback_only() {
        let launch = ServiceLaunch::new(
            Path::new("personalmemory-service.exe"),
            Path::new("C:/pm-data"),
            19421,
            "secret",
        );

        assert_eq!(launch.endpoint(), "http://127.0.0.1:19421");
        assert!(
            launch
                .args
                .iter()
                .all(|argument| !argument.to_string_lossy().contains("19421"))
        );
    }

    #[test]
    fn supervisor_waits_for_ready_and_stops_gracefully() {
        let (launch, data_dir) = fake_service("ready");
        let mut supervisor = ServiceSupervisor::spawn(launch).expect("spawn fake service");

        supervisor
            .wait_ready(Duration::from_secs(3))
            .expect("fake service ready");
        assert_eq!(supervisor.state(), ServiceState::Ready);

        let outcome = supervisor
            .stop(Duration::from_secs(3))
            .expect("stop fake service");
        assert_eq!(outcome, StopOutcome::Graceful);
        assert_eq!(supervisor.state(), ServiceState::Stopped);
        assert!(data_dir.path().join("shutdown.received").is_file());
    }

    #[test]
    fn ready_probe_rejects_unauthorized_response() {
        let (launch, _data_dir) = fake_service("unauthorized");
        let mut supervisor = ServiceSupervisor::spawn(launch).expect("spawn fake service");

        let error = supervisor
            .wait_ready(Duration::from_millis(400))
            .expect_err("unauthorized service must not be ready");
        assert!(matches!(error, SupervisorError::ReadyTimeout));

        supervisor
            .stop(Duration::from_secs(3))
            .expect("stop unauthorized fake service");
    }

    #[test]
    fn supervisor_times_out_when_child_never_becomes_ready() {
        let (launch, _data_dir) = fake_service("never-ready");
        let mut supervisor = ServiceSupervisor::spawn(launch).expect("spawn fake service");

        let error = supervisor
            .wait_ready(Duration::from_millis(400))
            .expect_err("unready service must time out");
        assert!(matches!(error, SupervisorError::ReadyTimeout));

        supervisor
            .stop(Duration::from_secs(3))
            .expect("stop unready fake service");
    }

    #[test]
    fn supervisor_controls_runtime_through_authenticated_api() {
        let (launch, _data_dir) = fake_service("ready");
        let mut supervisor = ServiceSupervisor::spawn(launch).expect("spawn fake service");
        supervisor
            .wait_ready(Duration::from_secs(3))
            .expect("fake service ready");

        let initial = supervisor.runtime_control().expect("read runtime control");
        assert!(!initial.sync_paused);
        assert!(!initial.privacy_mode);

        let updated = supervisor
            .update_runtime_control(Some(true), Some(true))
            .expect("update runtime control");
        assert!(updated.sync_paused);
        assert!(updated.privacy_mode);

        let synced = supervisor.sync_now().expect("request immediate sync");
        assert_eq!(synced.ingested, 1);
        assert_eq!(synced.projected, 2);
        assert_eq!(synced.failed, 0);

        supervisor
            .stop(Duration::from_secs(3))
            .expect("stop fake service");
    }

    #[test]
    fn supervisor_reads_only_actionable_status_summaries() {
        let (launch, _data_dir) = fake_service("ready");
        let mut supervisor = ServiceSupervisor::spawn(launch).expect("spawn fake service");
        supervisor
            .wait_ready(Duration::from_secs(3))
            .expect("fake service ready");

        assert_eq!(supervisor.health_status().expect("read health"), "ready");
        let connectors = supervisor.connector_statuses().expect("read connectors");
        assert_eq!(connectors.len(), 1);
        assert_eq!(connectors[0].id, "connector-a");
        assert_eq!(connectors[0].name, "Claude Memory");
        assert_eq!(connectors[0].status, "attention");

        supervisor
            .stop(Duration::from_secs(3))
            .expect("stop fake service");
    }
}
