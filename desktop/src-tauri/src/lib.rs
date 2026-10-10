mod agent_runtime;
mod agent_setup;
mod mcp_probe;
mod notifications;
mod preferences;
mod runtime;
mod source_actions;
mod supervisor;
mod tray;
mod updater;

use std::{
    fs, io,
    path::{Path, PathBuf},
    sync::Mutex,
    time::Duration,
};

#[cfg(debug_assertions)]
use std::ffi::OsString;

use runtime::RuntimeConnection;
use supervisor::{ServiceLaunch, ServiceSupervisor, generate_session_token, reserve_local_port};
use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};

pub(crate) struct ManagedService {
    pub(crate) supervisor: Mutex<ServiceSupervisor>,
    launch: ServiceLaunch,
}

impl ManagedService {
    pub(crate) fn stop_for_update(&self) -> Result<(), String> {
        let mut supervisor = self
            .supervisor
            .lock()
            .map_err(|_| "service supervisor lock is poisoned".to_owned())?;
        supervisor
            .stop(Duration::from_secs(5))
            .map(|_| ())
            .map_err(|error| error.to_string())
    }

    pub(crate) fn restart_after_update_failure(&self) -> Result<(), String> {
        let mut supervisor = self
            .supervisor
            .lock()
            .map_err(|_| "service supervisor lock is poisoned".to_owned())?;
        let mut restarted = ServiceSupervisor::spawn(self.launch.clone())
            .map_err(|error| format!("could not restart the local service: {error}"))?;
        restarted
            .wait_ready(Duration::from_secs(10))
            .map_err(|error| format!("the local service did not become healthy: {error}"))?;
        *supervisor = restarted;
        Ok(())
    }
}

#[tauri::command]
fn get_license_text() -> &'static str {
    include_str!("../../../LICENSE")
}

#[tauri::command]
fn get_third_party_notices_text(app: AppHandle) -> Result<String, String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| format!("Could not locate application resources: {error}"))?;
    let bundled = resource_dir.join("THIRD_PARTY_NOTICES.txt");
    #[cfg(debug_assertions)]
    let path = if bundled.is_file() {
        bundled
    } else {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../THIRD_PARTY_NOTICES.txt")
    };
    #[cfg(not(debug_assertions))]
    let path = bundled;

    fs::read_to_string(path).map_err(|error| format!("Could not read third-party notices: {error}"))
}

fn data_dir() -> io::Result<PathBuf> {
    let local_app_data = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "LOCALAPPDATA is unavailable"))?;
    prepare_data_dir(&local_app_data)
}

fn prepare_data_dir(local_app_data: &Path) -> io::Result<PathBuf> {
    let destination = local_app_data.join("PersonalMemoryData");
    if destination.exists() {
        return Ok(destination);
    }

    let legacy_data = local_app_data.join("PersonalMemory").join("data");
    if !legacy_data.join("memory.db").is_file() {
        return Ok(destination);
    }

    let staging = local_app_data.join(format!(
        "PersonalMemoryData.migrating-{}",
        std::process::id()
    ));
    let staging_data = staging.join("data");
    fs::create_dir_all(&staging_data)?;
    for entry in fs::read_dir(&legacy_data)? {
        let entry = entry?;
        if entry.file_type()?.is_file() {
            fs::copy(entry.path(), staging_data.join(entry.file_name()))?;
        }
    }
    fs::rename(staging, &destination)?;
    Ok(destination)
}

#[cfg(debug_assertions)]
fn service_launch(
    _app: &AppHandle,
    data_dir: &Path,
    port: u16,
    token: &str,
) -> io::Result<ServiceLaunch> {
    let python =
        std::env::var_os("PERSONALMEMORY_DEV_PYTHON").unwrap_or_else(|| OsString::from("python"));
    let service_source = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("service")
        .join("src")
        .canonicalize()?;
    Ok(ServiceLaunch::for_development(
        Path::new(&python),
        &service_source,
        data_dir,
        port,
        token,
    ))
}

#[cfg(any(not(debug_assertions), test))]
fn bundled_service_executable(resource_dir: &Path) -> PathBuf {
    resource_dir
        .join("personalmemory-service")
        .join("personalmemory-service.exe")
}

fn shell_quote(value: &str) -> String {
    if value.contains([' ', '"']) {
        format!("\"{}\"", value.replace('"', "\\\""))
    } else {
        value.to_owned()
    }
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

#[cfg(any(debug_assertions, test))]
fn mcp_descriptor_for(python: &Path) -> (String, String) {
    let python_text = python.to_string_lossy().into_owned();
    let shell = shell_quote(&python_text);
    let command = format!("{shell} -m personalmemory.agent_mcp");
    let json = serde_json::json!({
        "mcpServers": {
            "personalmemory": {
                "command": python_text,
                "args": ["-m", "personalmemory.agent_mcp"],
            }
        }
    })
    .to_string();
    (command, json)
}

#[cfg(debug_assertions)]
fn mcp_descriptor(_app: &AppHandle) -> io::Result<(String, String)> {
    let python =
        std::env::var_os("PERSONALMEMORY_DEV_PYTHON").unwrap_or_else(|| OsString::from("python"));
    Ok(mcp_descriptor_for(Path::new(&python)))
}

#[cfg(not(debug_assertions))]
fn mcp_descriptor(app: &AppHandle) -> io::Result<(String, String)> {
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| io::Error::other(error.to_string()))?;
    let local_app_data = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "LOCALAPPDATA is unavailable"))?;
    let exe = agent_runtime::ensure_mcp_runtime(
        &resource_dir.join("personalmemory-mcp"),
        &local_app_data,
        env!("CARGO_PKG_VERSION"),
    )?;
    let exe_text = windows_command_path(&exe);
    let command = shell_quote(&exe_text);
    let json = serde_json::json!({
        "mcpServers": {
            "personalmemory": {
                "command": exe_text,
                "args": [],
            }
        }
    })
    .to_string();
    Ok((command, json))
}

#[cfg(not(debug_assertions))]
fn service_launch(
    app: &AppHandle,
    data_dir: &Path,
    port: u16,
    token: &str,
) -> io::Result<ServiceLaunch> {
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| io::Error::other(error.to_string()))?;
    Ok(ServiceLaunch::new(
        &bundled_service_executable(&resource_dir),
        data_dir,
        port,
        token,
    ))
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(
            tauri_plugin_autostart::Builder::new()
                .app_name("PersonalMemory")
                .args(["--autostart"])
                .build(),
        )
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_single_instance::init(
            |app, _arguments, _cwd| {
                tray::show_main(app);
            },
        ))
        .invoke_handler(tauri::generate_handler![
            get_license_text,
            get_third_party_notices_text,
            agent_setup::get_claude_global_integration,
            agent_setup::install_claude_global_integration,
            agent_setup::enable_claude_global_capture,
            agent_setup::enable_claude_global_memory_read,
            agent_setup::remove_claude_global_integration,
            agent_setup::get_codex_integration,
            mcp_probe::verify_personalmemory_mcp,
            agent_setup::install_codex_integration,
            agent_setup::enable_codex_active_recall,
            agent_setup::enable_codex_capture,
            agent_setup::remove_codex_integration,
            agent_setup::install_claude_project_integration,
            agent_setup::remove_claude_project_integration,
            source_actions::open_source_file,
            preferences::get_desktop_preferences,
            preferences::set_autostart_show_window,
            preferences::set_desktop_locale,
            preferences::set_update_preferences,
            updater::check_for_update,
            updater::install_update,
            updater::get_update_status
        ])
        .on_window_event(|window, event| {
            if window.label() == "main"
                && let WindowEvent::CloseRequested { api, .. } = event
            {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .setup(|app| {
            let launched_by_windows = std::env::args_os().any(|arg| arg == "--autostart");
            let show_after_autostart =
                preferences::load_or_default(&data_dir()?).show_window_on_autostart;
            let token = generate_session_token();
            let port = reserve_local_port()?;
            let launch = service_launch(app.handle(), &data_dir()?, port, &token)?;
            let (mcp_command, mcp_json) = mcp_descriptor(app.handle())?;
            if let Err(error) = agent_setup::migrate_legacy_mcp_configs(app.handle()) {
                eprintln!("PersonalMemory MCP config migration was skipped: {error}");
            }
            let connection =
                RuntimeConnection::new(launch.endpoint(), token, mcp_command, mcp_json);
            let mut supervisor = ServiceSupervisor::spawn(launch.clone())?;
            supervisor.wait_ready(Duration::from_secs(10))?;
            updater::reconcile_after_startup(&data_dir()?, app.package_info().version.to_string())?;
            app.manage(ManagedService {
                supervisor: Mutex::new(supervisor),
                launch,
            });
            app.manage(updater::UpdateCoordinator::default());

            let window =
                WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                    .title("PersonalMemory")
                    .inner_size(1180.0, 760.0)
                    .min_inner_size(920.0, 620.0)
                    .visible(false)
                    .initialization_script(connection.initialization_script())
                    .build()?;
            window.center()?;
            if !launched_by_windows || show_after_autostart {
                window.show()?;
            }
            tray::install(app)?;
            app.manage(notifications::NotificationMonitor::start(
                app.handle().clone(),
            ));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build PersonalMemory desktop application");

    app.run(|app_handle, event| {
        if let RunEvent::ExitRequested { .. } = event {
            if let Some(monitor) = app_handle.try_state::<notifications::NotificationMonitor>() {
                monitor.stop();
            }
            if let Some(service) = app_handle.try_state::<ManagedService>()
                && let Ok(mut supervisor) = service.supervisor.lock()
            {
                let _ = supervisor.stop(Duration::from_secs(5));
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use std::{fs, path::Path};

    use super::{
        bundled_service_executable, mcp_descriptor_for, prepare_data_dir, shell_quote,
        windows_command_path,
    };

    #[test]
    fn release_service_uses_bundled_resource_tree() {
        assert_eq!(
            bundled_service_executable(Path::new("C:/Program Files/PersonalMemory")),
            Path::new(
                "C:/Program Files/PersonalMemory/personalmemory-service/personalmemory-service.exe"
            )
        );
    }

    #[test]
    fn command_paths_remove_windows_verbatim_prefixes() {
        assert_eq!(
            windows_command_path(Path::new(r"\\?\C:\Program Files\PersonalMemory\mcp.exe")),
            r"C:\Program Files\PersonalMemory\mcp.exe"
        );
    }

    #[test]
    fn development_mcp_descriptor_points_at_python_module() {
        let (command, json) = mcp_descriptor_for(Path::new("C:/Python/python.exe"));

        assert_eq!(command, "C:/Python/python.exe -m personalmemory.agent_mcp");
        assert!(json.contains("\"command\":\"C:/Python/python.exe\""));
        assert!(json.contains("\"args\":[\"-m\",\"personalmemory.agent_mcp\"]"));
        assert!(!json.contains("token"));
    }

    #[test]
    fn shell_quote_quotes_only_when_needed() {
        assert_eq!(shell_quote("python"), "python");
        assert_eq!(
            shell_quote("C:/Program Files/App/personalmemory-mcp.exe"),
            "\"C:/Program Files/App/personalmemory-mcp.exe\""
        );
        assert_eq!(shell_quote("a\"b"), "\"a\\\"b\"");
    }

    #[test]
    fn data_directory_migrates_only_legacy_user_data() {
        let root = tempfile::tempdir().expect("create local app data root");
        let legacy = root.path().join("PersonalMemory");
        fs::create_dir_all(legacy.join("data")).expect("create legacy data directory");
        fs::write(legacy.join("data/memory.db"), b"legacy-database")
            .expect("write legacy database");
        fs::write(legacy.join("personalmemory-desktop.exe"), b"installed-app")
            .expect("write installed application fixture");

        let destination = prepare_data_dir(root.path()).expect("prepare data directory");

        assert_eq!(destination, root.path().join("PersonalMemoryData"));
        assert_eq!(
            fs::read(destination.join("data/memory.db")).expect("read migrated database"),
            b"legacy-database"
        );
        assert!(!destination.join("personalmemory-desktop.exe").exists());
    }

    #[test]
    fn data_directory_never_overwrites_existing_database() {
        let root = tempfile::tempdir().expect("create local app data root");
        let legacy_data = root.path().join("PersonalMemory/data");
        let current_data = root.path().join("PersonalMemoryData/data");
        fs::create_dir_all(&legacy_data).expect("create legacy data directory");
        fs::create_dir_all(&current_data).expect("create current data directory");
        fs::write(legacy_data.join("memory.db"), b"legacy-database")
            .expect("write legacy database");
        fs::write(current_data.join("memory.db"), b"current-database")
            .expect("write current database");

        prepare_data_dir(root.path()).expect("prepare data directory");

        assert_eq!(
            fs::read(current_data.join("memory.db")).expect("read current database"),
            b"current-database"
        );
    }
}
