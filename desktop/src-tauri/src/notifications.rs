use std::{
    collections::BTreeSet,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::Duration,
};

use serde::Serialize;
use tauri::{AppHandle, Manager};
use tauri_plugin_notification::NotificationExt;

use crate::{ManagedService, supervisor::ConnectorStatus};

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ActionableIssue {
    pub fingerprint: String,
    pub title: String,
    pub body: String,
    pub route: String,
}

impl ActionableIssue {
    fn service_protected() -> Self {
        Self {
            fingerprint: "service:protected".to_owned(),
            title: "PersonalMemory 需要处理".to_owned(),
            body: "本机记忆服务进入保护状态，请打开应用查看。".to_owned(),
            route: "#/exceptions?issue=service-protected".to_owned(),
        }
    }

    fn connector_attention(id: &str, name: &str) -> Self {
        Self {
            fingerprint: format!("connector:{id}:attention"),
            title: "连接需要处理".to_owned(),
            body: format!("连接“{name}”需要检查路径或权限。"),
            route: format!("#/exceptions?connector_id={id}"),
        }
    }

    fn summary(count: usize) -> Self {
        Self {
            fingerprint: format!("summary:{count}"),
            title: "PersonalMemory 状态提醒".to_owned(),
            body: format!(
                "检测到 {count} 项后台问题，请在应用的“需要处理”页面集中查看。已保存的记忆不会因此删除。"
            ),
            route: "#/exceptions".to_owned(),
        }
    }
}

#[derive(Default)]
pub struct NotificationDedupe {
    active: BTreeSet<String>,
}

impl NotificationDedupe {
    #[cfg(test)]
    pub fn should_notify(&mut self, fingerprint: &str) -> bool {
        self.active.insert(fingerprint.to_owned())
    }

    #[cfg(test)]
    pub fn resolve(&mut self, fingerprint: &str) {
        self.active.remove(fingerprint);
    }

    pub fn reconcile(
        &mut self,
        issues: impl IntoIterator<Item = ActionableIssue>,
    ) -> Vec<ActionableIssue> {
        let previous = std::mem::take(&mut self.active);
        let mut current = BTreeSet::new();
        let mut new_issues = Vec::new();
        for issue in issues {
            current.insert(issue.fingerprint.clone());
            if !previous.contains(&issue.fingerprint) {
                new_issues.push(issue);
            }
        }
        self.active = current;
        new_issues
    }

    fn prime(&mut self, issues: impl IntoIterator<Item = ActionableIssue>) {
        self.active = issues.into_iter().map(|issue| issue.fingerprint).collect();
    }
}

pub fn actionable_issues(
    service_status: &str,
    connectors: &[ConnectorStatus],
) -> Vec<ActionableIssue> {
    let mut issues = Vec::new();
    if service_status == "protected" {
        issues.push(ActionableIssue::service_protected());
    }
    issues.extend(
        connectors
            .iter()
            .filter(|connector| connector.status == "attention")
            .map(|connector| ActionableIssue::connector_attention(&connector.id, &connector.name)),
    );
    issues
}

pub struct NotificationMonitor {
    stop_requested: Arc<AtomicBool>,
}

impl NotificationMonitor {
    pub fn start(app: AppHandle) -> Self {
        let stop_requested = Arc::new(AtomicBool::new(false));
        let worker_stop = Arc::clone(&stop_requested);
        thread::spawn(move || {
            let mut dedupe = NotificationDedupe::default();
            let mut initial_snapshot_seen = false;
            while !worker_stop.load(Ordering::Relaxed) {
                let snapshot = app
                    .state::<ManagedService>()
                    .inner()
                    .supervisor
                    .lock()
                    .ok()
                    .and_then(|service| {
                        let health = service.health_status().ok()?;
                        let connectors = service.connector_statuses().ok()?;
                        Some((health, connectors))
                    });

                if let Some((health, connectors)) = snapshot {
                    let issues = actionable_issues(&health, &connectors);
                    if !initial_snapshot_seen {
                        // Existing unresolved issues are already visible in the app. Do not
                        // turn them into a notification storm every time Desktop restarts.
                        dedupe.prime(issues);
                        initial_snapshot_seen = true;
                    } else {
                        let new_issues = dedupe.reconcile(issues);
                        if !new_issues.is_empty() {
                            let summary = ActionableIssue::summary(new_issues.len());
                            prepare_exception_route(&app, &summary.route);
                            let _ = app
                                .notification()
                                .builder()
                                .title(&summary.title)
                                .body(&summary.body)
                                .show();
                        }
                    }
                }

                for _ in 0..20 {
                    if worker_stop.load(Ordering::Relaxed) {
                        return;
                    }
                    thread::sleep(Duration::from_millis(250));
                }
            }
        });
        Self { stop_requested }
    }

    pub fn stop(&self) {
        self.stop_requested.store(true, Ordering::Relaxed);
    }
}

fn prepare_exception_route(app: &AppHandle, route: &str) {
    let Some(window) = app.get_webview_window("main") else {
        return;
    };
    if window.is_visible().unwrap_or(true) {
        return;
    }
    let route = serde_json::to_string(route).expect("notification route is serializable");
    let _ = window.eval(format!("window.location.hash = {route};"));
}

#[cfg(test)]
mod tests {
    use super::{ActionableIssue, NotificationDedupe, actionable_issues};
    use crate::supervisor::ConnectorStatus;

    #[test]
    fn unchanged_attention_is_not_notified_twice() {
        let mut dedupe = NotificationDedupe::default();
        assert!(dedupe.should_notify("connector-a:path_missing:attention"));
        assert!(!dedupe.should_notify("connector-a:path_missing:attention"));
        dedupe.resolve("connector-a:path_missing:attention");
        assert!(dedupe.should_notify("connector-a:path_missing:attention"));
    }

    #[test]
    fn resolved_issue_is_notified_when_it_reappears() {
        let issue = ActionableIssue::connector_attention("connector-a", "Claude Memory");
        let mut dedupe = NotificationDedupe::default();

        assert_eq!(dedupe.reconcile([issue.clone()]), vec![issue.clone()]);
        assert!(dedupe.reconcile([issue.clone()]).is_empty());
        assert!(dedupe.reconcile([]).is_empty());
        assert_eq!(dedupe.reconcile([issue.clone()]), vec![issue]);
    }

    #[test]
    fn startup_baseline_suppresses_existing_issues_but_new_issues_are_reported_once() {
        let existing = ActionableIssue::connector_attention("connector-a", "Source A");
        let new_issue = ActionableIssue::connector_attention("connector-b", "Source B");
        let mut dedupe = NotificationDedupe::default();

        dedupe.prime([existing.clone()]);
        assert!(dedupe.reconcile([existing.clone()]).is_empty());
        assert_eq!(
            dedupe.reconcile([existing.clone(), new_issue.clone()]),
            vec![new_issue]
        );
        assert!(dedupe.reconcile([existing]).is_empty());
    }

    #[test]
    fn notification_summary_does_not_repeat_connector_names_or_paths() {
        let summary = ActionableIssue::summary(16);

        assert_eq!(summary.title, "PersonalMemory 状态提醒");
        assert!(summary.body.contains("16 项后台问题"));
        assert!(summary.body.contains("已保存的记忆不会因此删除"));
        assert_eq!(summary.route, "#/exceptions");
        assert!(!summary.body.contains("C:\\\\Users"));
    }

    #[test]
    fn only_protected_service_and_attention_connectors_are_actionable() {
        let issues = actionable_issues(
            "protected",
            &[
                ConnectorStatus::new("connector-a", "Claude Memory", "attention"),
                ConnectorStatus::new("connector-b", "Local Notes", "healthy"),
                ConnectorStatus::new("connector-c", "Paused Notes", "paused"),
            ],
        );

        assert_eq!(issues.len(), 2);
        assert!(
            issues
                .iter()
                .any(|issue| issue.fingerprint == "service:protected")
        );
        assert!(
            issues
                .iter()
                .any(|issue| issue.fingerprint == "connector:connector-a:attention")
        );
    }

    #[test]
    fn notification_payload_contains_no_memory_content_token_or_path() {
        let issue = ActionableIssue::connector_attention("connector-a", "Claude Memory");
        let serialized = serde_json::to_string(&issue).expect("serialize notification payload");

        assert!(serialized.contains("Claude Memory"));
        assert!(!serialized.contains("session-secret"));
        assert!(!serialized.contains("C:\\\\Users"));
        assert!(!serialized.contains("private memory statement"));
        assert_eq!(issue.route, "#/exceptions?connector_id=connector-a");
    }
}
