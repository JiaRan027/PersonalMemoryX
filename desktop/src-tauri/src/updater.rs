use std::{
    fs,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::Mutex,
    time::Duration,
};

use reqwest::{Url, redirect::Policy};
use semver::Version;
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_updater::{Update, UpdaterExt};

use crate::ManagedService;

const STABLE_FEED: &str = "https://github.com/JiaRan027/PersonalMemory/releases/download/stable-channel/latest-stable.json";
const ALPHA_FEED: &str =
    "https://github.com/JiaRan027/PersonalMemory/releases/download/alpha-channel/latest-alpha.json";
const UPDATE_JOURNAL: &str = "update-state.json";
const PROGRESS_EVENT: &str = "personalmemory-update-progress";

#[derive(Default)]
pub(crate) struct UpdateCoordinator {
    pending: Mutex<Option<(Update, String)>>,
}

#[derive(Clone, Debug, Deserialize, Serialize, Default)]
struct UpdateJournal {
    highest_installed_version: Option<String>,
    pending_version: Option<String>,
    pending_channel: Option<String>,
    backup_path: Option<String>,
    last_result: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
pub(crate) struct UpdateSummary {
    current_version: String,
    version: String,
    notes: Option<String>,
    published_at: Option<String>,
    channel: String,
}

#[derive(Clone, Debug, Serialize)]
pub(crate) struct UpdateStatus {
    current_version: String,
    highest_installed_version: Option<String>,
    pending_version: Option<String>,
    backup_path: Option<String>,
    last_result: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
struct UpdateProgress {
    downloaded_bytes: u64,
    total_bytes: Option<u64>,
}

fn channel_endpoint(channel: &str) -> Result<Url, String> {
    let endpoint = match channel {
        "stable" => STABLE_FEED,
        "alpha" => ALPHA_FEED,
        _ => return Err("unsupported update channel".to_owned()),
    };
    Url::parse(endpoint).map_err(|error| error.to_string())
}

fn is_allowed_transport(url: &Url) -> bool {
    url.scheme() == "https"
        && matches!(
            url.host_str(),
            Some(
                "github.com"
                    | "release-assets.githubusercontent.com"
                    | "objects.githubusercontent.com"
            )
        )
}

fn updater_for_channel(
    app: &AppHandle,
    channel: &str,
) -> Result<tauri_plugin_updater::Updater, String> {
    let endpoint = channel_endpoint(channel)?;
    let app_for_exit = app.clone();
    app.updater_builder()
        .endpoints(vec![endpoint])
        .map_err(|error| error.to_string())?
        .version_comparator(|current, update| update.version > current)
        .timeout(Duration::from_secs(30))
        .configure_client(|client| {
            client.redirect(Policy::custom(|attempt| {
                if is_allowed_transport(attempt.url()) {
                    attempt.follow()
                } else {
                    attempt.stop()
                }
            }))
        })
        .on_before_exit(move || {
            if let Some(service) = app_for_exit.try_state::<ManagedService>() {
                let _ = service.stop_for_update();
            }
        })
        .build()
        .map_err(|error| error.to_string())
}

fn journal_path(data_dir: &Path) -> PathBuf {
    data_dir.join(UPDATE_JOURNAL)
}

fn load_journal(data_dir: &Path) -> Result<UpdateJournal, String> {
    let path = journal_path(data_dir);
    if !path.is_file() {
        return Ok(UpdateJournal::default());
    }
    let bytes = fs::read(path).map_err(|error| error.to_string())?;
    serde_json::from_slice(&bytes)
        .map_err(|error| format!("update recovery state is invalid: {error}"))
}

fn save_journal(data_dir: &Path, journal: &UpdateJournal) -> Result<(), String> {
    fs::create_dir_all(data_dir).map_err(|error| error.to_string())?;
    let bytes = serde_json::to_vec_pretty(journal).map_err(|error| error.to_string())?;
    fs::write(journal_path(data_dir), bytes).map_err(|error| error.to_string())
}

fn ensure_not_rolled_back(journal: &UpdateJournal, current: &Version) -> Result<(), String> {
    let Some(highest) = journal.highest_installed_version.as_deref() else {
        return Ok(());
    };
    let highest = Version::parse(highest).map_err(|error| error.to_string())?;
    if current < &highest {
        return Err(format!(
            "Installed version {current} is older than the last verified version {highest}; automatic updates are blocked. Reinstall a current signed release to recover."
        ));
    }
    Ok(())
}

pub(crate) fn reconcile_after_startup(
    data_dir: &Path,
    current_version: String,
) -> Result<(), String> {
    let current = Version::parse(&current_version).map_err(|error| error.to_string())?;
    let mut journal = load_journal(data_dir)?;

    if let Some(pending) = journal.pending_version.as_deref() {
        let pending_version = Version::parse(pending).map_err(|error| error.to_string())?;
        if current >= pending_version {
            journal.highest_installed_version = Some(current_version);
            journal.last_result = Some(format!("installed:{pending}"));
        } else {
            journal.last_result = Some(format!("not_completed:{pending}"));
        }
        journal.pending_version = None;
        journal.pending_channel = None;
    } else if let Some(highest) = journal.highest_installed_version.as_deref() {
        ensure_not_rolled_back(&journal, &current)?;
        if current > Version::parse(highest).map_err(|error| error.to_string())? {
            journal.highest_installed_version = Some(current_version);
        }
    } else {
        journal.highest_installed_version = Some(current_version);
    }

    save_journal(data_dir, &journal)
}

#[tauri::command]
pub(crate) async fn check_for_update(
    app: AppHandle,
    coordinator: State<'_, UpdateCoordinator>,
    channel: String,
) -> Result<Option<UpdateSummary>, String> {
    channel_endpoint(&channel)?;
    let current_version = app.package_info().version.to_string();
    let current = Version::parse(&current_version).map_err(|error| error.to_string())?;
    let data_dir = crate::data_dir().map_err(|error| error.to_string())?;
    let journal = load_journal(&data_dir)?;
    ensure_not_rolled_back(&journal, &current)?;

    let update = updater_for_channel(&app, &channel)?
        .check()
        .await
        .map_err(|error| format!("update check failed: {error}"))?;
    let Some(update) = update else {
        coordinator
            .pending
            .lock()
            .map_err(|_| "update lock is unavailable".to_owned())?
            .take();
        return Ok(None);
    };

    if !is_allowed_transport(&update.download_url) {
        return Err("update manifest points to an untrusted or non-HTTPS download host".to_owned());
    }

    let update_version = Version::parse(&update.version).map_err(|error| error.to_string())?;
    if update_version <= current {
        return Ok(None);
    }
    if channel == "stable" && !update_version.pre.is_empty() {
        return Err("stable update channel returned a prerelease; update rejected".to_owned());
    }
    if let Some(highest) = journal.highest_installed_version.as_deref() {
        if update_version <= Version::parse(highest).map_err(|error| error.to_string())? {
            return Err(
                "update version is not newer than the last verified installation".to_owned(),
            );
        }
    }

    let summary = UpdateSummary {
        current_version: update.current_version.clone(),
        version: update.version.clone(),
        notes: update.body.clone(),
        published_at: update.date.map(|date| date.to_string()),
        channel: channel.clone(),
    };
    *coordinator
        .pending
        .lock()
        .map_err(|_| "update lock is unavailable".to_owned())? = Some((update, channel));
    Ok(Some(summary))
}

fn run_database_backup(app: &AppHandle, data_dir: &Path) -> Result<Option<String>, String> {
    let resources = app
        .path()
        .resource_dir()
        .map_err(|error| error.to_string())?;
    let service_exe = resources
        .join("personalmemory-service")
        .join("personalmemory-service.exe");
    if !service_exe.is_file() {
        return Err(
            "the bundled database backup helper is missing; update was not installed".to_owned(),
        );
    }

    let local_app_data = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .ok_or_else(|| "LOCALAPPDATA is unavailable; update backup was not created".to_owned())?;
    let backup_dir = local_app_data
        .join("PersonalMemory Backups")
        .join("automatic-updates");
    let mut command = Command::new(service_exe);
    command
        .arg("backup")
        .env("PERSONALMEMORY_DATA_DIR", data_dir)
        .env("PERSONALMEMORY_BACKUP_DIR", &backup_dir)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let output = command
        .output()
        .map_err(|error| format!("could not run the database backup helper: {error}"))?;
    if !output.status.success() {
        return Err("database backup failed; update was not installed and the old service will be restarted".to_owned());
    }
    let report: serde_json::Value = serde_json::from_slice(&output.stdout)
        .map_err(|_| "database backup helper returned invalid verification data".to_owned())?;
    match report.get("status").and_then(serde_json::Value::as_str) {
        Some("skipped") => Ok(None),
        Some("created")
            if report
                .get("integrity_check")
                .and_then(serde_json::Value::as_str)
                == Some("ok")
                && report
                    .get("sha256")
                    .and_then(serde_json::Value::as_str)
                    .is_some_and(|hash| hash.len() == 64) =>
        {
            Ok(report
                .get("backup")
                .and_then(serde_json::Value::as_str)
                .map(str::to_owned))
        }
        _ => Err(
            "database backup did not pass its integrity check; update was not installed".to_owned(),
        ),
    }
}

fn restart_service_after_failure(service: &ManagedService, reason: &str) -> String {
    match service.restart_after_update_failure() {
        Ok(()) => reason.to_owned(),
        Err(restart_error) => {
            format!("{reason}; additionally, service recovery failed: {restart_error}")
        }
    }
}

#[tauri::command]
pub(crate) async fn install_update(
    app: AppHandle,
    coordinator: State<'_, UpdateCoordinator>,
) -> Result<(), String> {
    let (update, channel) = coordinator
        .pending
        .lock()
        .map_err(|_| "update lock is unavailable".to_owned())?
        .take()
        .ok_or_else(|| "check for a valid update before installing".to_owned())?;

    let service = app.state::<ManagedService>();
    let data_dir = crate::data_dir().map_err(|error| error.to_string())?;
    let mut downloaded_bytes = 0_u64;
    let app_for_progress = app.clone();
    let bytes = match update
        .download(
            |chunk, total| {
                downloaded_bytes = downloaded_bytes.saturating_add(chunk as u64);
                let _ = app_for_progress.emit(
                    PROGRESS_EVENT,
                    UpdateProgress {
                        downloaded_bytes,
                        total_bytes: total,
                    },
                );
            },
            || {},
        )
        .await
    {
        Ok(bytes) => bytes,
        Err(error) => {
            *coordinator
                .pending
                .lock()
                .map_err(|_| "update lock is unavailable".to_owned())? = Some((update, channel));
            return Err(format!(
                "download or signature verification failed: {error}"
            ));
        }
    };

    service.stop_for_update().map_err(|error| {
        restart_service_after_failure(
            &service,
            &format!("could not stop the local service safely: {error}"),
        )
    })?;

    let backup_path = match run_database_backup(&app, &data_dir) {
        Ok(path) => path,
        Err(error) => return Err(restart_service_after_failure(&service, &error)),
    };

    let mut journal = load_journal(&data_dir)?;
    let current_version = app.package_info().version.to_string();
    let current = Version::parse(&current_version).map_err(|error| error.to_string())?;
    let candidate = Version::parse(&update.version).map_err(|error| error.to_string())?;
    ensure_not_rolled_back(&journal, &current)?;
    if candidate <= current
        || journal
            .highest_installed_version
            .as_deref()
            .and_then(|version| Version::parse(version).ok())
            .is_some_and(|highest| candidate <= highest)
    {
        let message = "update version is not strictly newer than the installed version";
        return Err(restart_service_after_failure(&service, message));
    }
    journal.pending_version = Some(update.version.clone());
    journal.pending_channel = Some(channel);
    journal.backup_path = backup_path;
    journal.last_result = Some("installer_starting".to_owned());
    if let Err(error) = save_journal(&data_dir, &journal) {
        return Err(restart_service_after_failure(&service, &error));
    }

    match update.install(&bytes) {
        Ok(()) => Ok(()),
        Err(error) => {
            journal.pending_version = None;
            journal.pending_channel = None;
            journal.last_result = Some(format!("installer_launch_failed:{}", update.version));
            let _ = save_journal(&data_dir, &journal);
            Err(restart_service_after_failure(
                &service,
                &format!("installer could not be started: {error}"),
            ))
        }
    }
}

#[tauri::command]
pub(crate) fn get_update_status(app: AppHandle) -> Result<UpdateStatus, String> {
    let current_version = app.package_info().version.to_string();
    let data_dir = crate::data_dir().map_err(|error| error.to_string())?;
    let journal = load_journal(&data_dir)?;
    Ok(UpdateStatus {
        current_version,
        highest_installed_version: journal.highest_installed_version,
        pending_version: journal.pending_version,
        backup_path: journal.backup_path,
        last_result: journal.last_result,
    })
}

#[cfg(test)]
mod tests {
    use std::path::Path;

    use reqwest::Url;
    use semver::Version;
    use tempfile::tempdir;

    use super::{
        UpdateJournal, channel_endpoint, ensure_not_rolled_back, is_allowed_transport,
        load_journal, reconcile_after_startup, save_journal,
    };

    #[test]
    fn channels_resolve_to_fixed_github_https_endpoints() {
        assert_eq!(
            channel_endpoint("alpha").expect("alpha endpoint").as_str(),
            "https://github.com/JiaRan027/PersonalMemory/releases/download/alpha-channel/latest-alpha.json"
        );
        assert!(channel_endpoint("other").is_err());
    }

    #[test]
    fn rejects_untrusted_or_http_update_hosts() {
        assert!(is_allowed_transport(
            &Url::parse(
                "https://github.com/JiaRan027/PersonalMemory/releases/download/v1/update.zip"
            )
            .expect("test URL")
        ));
        assert!(is_allowed_transport(
            &Url::parse("https://release-assets.githubusercontent.com/file").expect("test URL")
        ));
        assert!(!is_allowed_transport(
            &Url::parse("http://github.com/file").expect("test URL")
        ));
        assert!(!is_allowed_transport(
            &Url::parse("https://example.invalid/file").expect("test URL")
        ));
    }

    #[test]
    fn rejects_version_rollback_against_high_water_mark() {
        let journal = UpdateJournal {
            highest_installed_version: Some("0.2.10".to_owned()),
            ..UpdateJournal::default()
        };
        assert!(ensure_not_rolled_back(&journal, &Version::parse("0.2.9").unwrap()).is_err());
        assert!(ensure_not_rolled_back(&journal, &Version::parse("0.2.10").unwrap()).is_ok());
    }

    #[test]
    fn startup_confirms_version_only_after_the_new_app_runs() {
        let root = tempdir().expect("temp data root");
        save_journal(
            root.path(),
            &UpdateJournal {
                highest_installed_version: Some("0.2.9".to_owned()),
                pending_version: Some("0.2.10".to_owned()),
                backup_path: Some("backup.db".to_owned()),
                ..UpdateJournal::default()
            },
        )
        .expect("save pending state");

        reconcile_after_startup(root.path(), "0.2.9".to_owned()).expect("reconcile failed install");
        let after_failure = load_journal(root.path()).expect("read failure state");
        assert_eq!(
            after_failure.last_result.as_deref(),
            Some("not_completed:0.2.10")
        );
        assert_eq!(
            after_failure.highest_installed_version.as_deref(),
            Some("0.2.9")
        );
        assert_eq!(after_failure.backup_path.as_deref(), Some("backup.db"));

        save_journal(
            root.path(),
            &UpdateJournal {
                highest_installed_version: Some("0.2.9".to_owned()),
                pending_version: Some("0.2.10".to_owned()),
                backup_path: Some("backup.db".to_owned()),
                ..UpdateJournal::default()
            },
        )
        .expect("save retry state");
        reconcile_after_startup(root.path(), "0.2.10".to_owned()).expect("reconcile success");
        let after_success = load_journal(root.path()).expect("read success state");
        assert_eq!(
            after_success.last_result.as_deref(),
            Some("installed:0.2.10")
        );
        assert_eq!(
            after_success.highest_installed_version.as_deref(),
            Some("0.2.10")
        );
    }

    #[test]
    fn update_journal_is_outside_the_installation_directory() {
        let root = tempdir().expect("data root");
        let installed = Path::new("C:/Users/test/AppData/Local/PersonalMemory");
        let data = root.path().join("PersonalMemoryData");
        assert_ne!(installed, data);
    }
}
