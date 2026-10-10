use std::{fs, io, path::Path};

use serde::{Deserialize, Serialize};

const SETTINGS_FILE: &str = "desktop-settings.json";

fn default_locale() -> String {
    "zh-CN".to_owned()
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(default)]
pub(crate) struct DesktopPreferences {
    pub(crate) show_window_on_autostart: bool,
    pub(crate) locale: String,
    pub(crate) auto_check_updates: bool,
    pub(crate) update_channel: String,
}

impl Default for DesktopPreferences {
    fn default() -> Self {
        Self {
            show_window_on_autostart: false,
            locale: default_locale(),
            auto_check_updates: true,
            update_channel: "stable".to_owned(),
        }
    }
}

pub(crate) fn load_or_default(data_dir: &Path) -> DesktopPreferences {
    load(data_dir).unwrap_or_default()
}

fn load(data_dir: &Path) -> io::Result<DesktopPreferences> {
    let path = data_dir.join(SETTINGS_FILE);
    if !path.is_file() {
        return Ok(DesktopPreferences::default());
    }
    let bytes = fs::read(path)?;
    serde_json::from_slice(&bytes)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))
}

fn save(data_dir: &Path, preferences: &DesktopPreferences) -> io::Result<()> {
    fs::create_dir_all(data_dir)?;
    let bytes = serde_json::to_vec_pretty(preferences)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    fs::write(data_dir.join(SETTINGS_FILE), bytes)
}

#[tauri::command]
pub(crate) fn get_desktop_preferences() -> Result<DesktopPreferences, String> {
    let data_dir = super::data_dir().map_err(|error| error.to_string())?;
    Ok(load_or_default(&data_dir))
}

#[tauri::command]
pub(crate) fn set_autostart_show_window(show_window: bool) -> Result<DesktopPreferences, String> {
    let data_dir = super::data_dir().map_err(|error| error.to_string())?;
    let mut preferences = load_or_default(&data_dir);
    preferences.show_window_on_autostart = show_window;
    save(&data_dir, &preferences).map_err(|error| error.to_string())?;
    Ok(preferences)
}

#[tauri::command]
pub(crate) fn set_desktop_locale(locale: String) -> Result<DesktopPreferences, String> {
    if locale != "zh-CN" && locale != "en-US" {
        return Err("unsupported locale".to_owned());
    }
    let data_dir = super::data_dir().map_err(|error| error.to_string())?;
    let mut preferences = load_or_default(&data_dir);
    preferences.locale = locale;
    save(&data_dir, &preferences).map_err(|error| error.to_string())?;
    Ok(preferences)
}

#[tauri::command]
pub(crate) fn set_update_preferences(
    auto_check_updates: bool,
    update_channel: String,
) -> Result<DesktopPreferences, String> {
    if update_channel != "stable" && update_channel != "alpha" {
        return Err("unsupported update channel".to_owned());
    }
    let data_dir = super::data_dir().map_err(|error| error.to_string())?;
    let mut preferences = load_or_default(&data_dir);
    preferences.auto_check_updates = auto_check_updates;
    preferences.update_channel = update_channel;
    save(&data_dir, &preferences).map_err(|error| error.to_string())?;
    Ok(preferences)
}

#[cfg(test)]
mod tests {
    use super::{DesktopPreferences, load_or_default, save};

    #[test]
    fn defaults_to_a_quiet_autostart() {
        let root = tempfile::tempdir().expect("create settings root");

        assert_eq!(load_or_default(root.path()), DesktopPreferences::default());
    }

    #[test]
    fn round_trips_the_startup_window_preference() {
        let root = tempfile::tempdir().expect("create settings root");
        let preferences = DesktopPreferences {
            show_window_on_autostart: true,
            locale: "en-US".to_owned(),
            ..DesktopPreferences::default()
        };

        save(root.path(), &preferences).expect("save preferences");

        assert_eq!(load_or_default(root.path()), preferences);
    }

    #[test]
    fn old_settings_default_to_simplified_chinese() {
        let root = tempfile::tempdir().expect("create settings root");
        std::fs::write(
            root.path().join("desktop-settings.json"),
            br#"{"show_window_on_autostart":true}"#,
        )
        .expect("write old settings");

        let preferences = load_or_default(root.path());

        assert!(preferences.show_window_on_autostart);
        assert_eq!(preferences.locale, "zh-CN");
        assert!(preferences.auto_check_updates);
        assert_eq!(preferences.update_channel, "stable");
    }
}
