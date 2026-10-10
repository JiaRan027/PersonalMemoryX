use std::thread;

use tauri::{
    App, AppHandle, Manager,
    menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem},
    tray::TrayIconBuilder,
};

use crate::ManagedService;
use crate::supervisor::RuntimeControlState;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TrayAction {
    Open,
    SyncNow,
    TogglePause,
    TogglePrivacy,
    Settings,
    Quit,
}

impl TryFrom<&str> for TrayAction {
    type Error = ();

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        match value {
            "open" => Ok(Self::Open),
            "sync_now" => Ok(Self::SyncNow),
            "toggle_pause" => Ok(Self::TogglePause),
            "toggle_privacy" => Ok(Self::TogglePrivacy),
            "settings" => Ok(Self::Settings),
            "quit" => Ok(Self::Quit),
            _ => Err(()),
        }
    }
}

fn runtime_status_text(control: RuntimeControlState) -> &'static str {
    if control.privacy_mode {
        "privacy"
    } else if control.sync_paused {
        "paused"
    } else {
        "normal"
    }
}

pub fn show_main(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn show_settings(app: &AppHandle) {
    show_main(app);
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.eval("window.location.hash = '#/data-security'");
    }
}

pub fn install(app: &App) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "打开 PersonalMemory", true, None::<&str>)?;
    let status = MenuItem::with_id(app, "sync_status", "同步状态：正常", false, None::<&str>)?;
    let sync_now = MenuItem::with_id(app, "sync_now", "立即同步", true, None::<&str>)?;
    let pause = CheckMenuItem::with_id(
        app,
        "toggle_pause",
        "暂停后台同步",
        true,
        false,
        None::<&str>,
    )?;
    let privacy =
        CheckMenuItem::with_id(app, "toggle_privacy", "隐私模式", true, false, None::<&str>)?;
    let settings = MenuItem::with_id(app, "settings", "设置", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "退出 PersonalMemory", true, None::<&str>)?;
    let separator_a = PredefinedMenuItem::separator(app)?;
    let separator_b = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(
        app,
        &[
            &open,
            &status,
            &separator_a,
            &sync_now,
            &pause,
            &privacy,
            &separator_b,
            &settings,
            &quit,
        ],
    )?;

    if let Ok(service) = app.state::<ManagedService>().supervisor.lock()
        && let Ok(control) = service.runtime_control()
    {
        pause.set_checked(control.sync_paused)?;
        privacy.set_checked(control.privacy_mode)?;
        status.set_text(match runtime_status_text(control) {
            "privacy" => "同步状态：隐私模式",
            "paused" => "同步状态：已暂停",
            _ => "同步状态：正常",
        })?;
    }

    let status_for_events = status.clone();
    let pause_for_events = pause.clone();
    let privacy_for_events = privacy.clone();
    let mut tray = TrayIconBuilder::with_id("main-tray")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .tooltip("PersonalMemory - 本机运行中")
        .on_menu_event(move |app, event| {
            let Ok(action) = TrayAction::try_from(event.id().as_ref()) else {
                return;
            };
            match action {
                TrayAction::Open => show_main(app),
                TrayAction::Settings => show_settings(app),
                TrayAction::Quit => app.exit(0),
                TrayAction::SyncNow => {
                    let app = app.clone();
                    let status = status_for_events.clone();
                    let _ = status.set_text("同步状态：同步中");
                    thread::spawn(move || {
                        let succeeded = app
                            .state::<ManagedService>()
                            .inner()
                            .supervisor
                            .lock()
                            .map(|service| service.sync_now().is_ok())
                            .unwrap_or(false);
                        let _ = status.set_text(if succeeded {
                            "同步状态：正常"
                        } else {
                            "同步状态：需要处理"
                        });
                    });
                }
                TrayAction::TogglePause => {
                    let checked = pause_for_events.is_checked().unwrap_or(false);
                    let succeeded = app
                        .state::<ManagedService>()
                        .inner()
                        .supervisor
                        .lock()
                        .map(|service| service.update_runtime_control(Some(checked), None).is_ok())
                        .unwrap_or(false);
                    if succeeded {
                        let _ = status_for_events.set_text(if checked {
                            "同步状态：已暂停"
                        } else {
                            "同步状态：正常"
                        });
                    } else {
                        let _ = pause_for_events.set_checked(!checked);
                        let _ = status_for_events.set_text("同步状态：需要处理");
                    }
                }
                TrayAction::TogglePrivacy => {
                    let checked = privacy_for_events.is_checked().unwrap_or(false);
                    let succeeded = app
                        .state::<ManagedService>()
                        .inner()
                        .supervisor
                        .lock()
                        .map(|service| service.update_runtime_control(None, Some(checked)).is_ok())
                        .unwrap_or(false);
                    if succeeded {
                        let _ = status_for_events.set_text(if checked {
                            "同步状态：隐私模式"
                        } else {
                            "同步状态：正常"
                        });
                    } else {
                        let _ = privacy_for_events.set_checked(!checked);
                        let _ = status_for_events.set_text("同步状态：需要处理");
                    }
                }
            }
        });

    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.build(app)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{TrayAction, runtime_status_text};
    use crate::supervisor::RuntimeControlState;

    #[test]
    fn tray_ids_map_to_stable_actions() {
        assert_eq!(TrayAction::try_from("open"), Ok(TrayAction::Open));
        assert_eq!(TrayAction::try_from("sync_now"), Ok(TrayAction::SyncNow));
        assert_eq!(
            TrayAction::try_from("toggle_pause"),
            Ok(TrayAction::TogglePause)
        );
        assert_eq!(
            TrayAction::try_from("toggle_privacy"),
            Ok(TrayAction::TogglePrivacy)
        );
        assert_eq!(TrayAction::try_from("settings"), Ok(TrayAction::Settings));
        assert_eq!(TrayAction::try_from("quit"), Ok(TrayAction::Quit));
        assert_eq!(TrayAction::try_from("unknown"), Err(()));
    }

    #[test]
    fn runtime_status_prioritizes_privacy_then_pause() {
        assert_eq!(
            runtime_status_text(RuntimeControlState {
                sync_paused: false,
                privacy_mode: false,
            }),
            "normal"
        );
        assert_eq!(
            runtime_status_text(RuntimeControlState {
                sync_paused: true,
                privacy_mode: false,
            }),
            "paused"
        );
        assert_eq!(
            runtime_status_text(RuntimeControlState {
                sync_paused: true,
                privacy_mode: true,
            }),
            "privacy"
        );
    }
}
