use std::{
    fs, io,
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};

static STAGING_ID: AtomicU64 = AtomicU64::new(0);

/// Copy the bundled MCP runtime into an immutable, per-user version directory.
/// Agent processes can keep using this path while the Desktop installation is
/// upgraded or replaced.
#[cfg_attr(debug_assertions, allow(dead_code))]
pub fn ensure_mcp_runtime(
    bundled_dir: &Path,
    local_app_data: &Path,
    version: &str,
) -> io::Result<PathBuf> {
    if version.is_empty()
        || !version
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || ".-_".contains(character))
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid MCP runtime version",
        ));
    }

    let source_executable = bundled_dir.join("personalmemory-mcp-host.exe");
    if !source_executable.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "bundled MCP host executable is missing",
        ));
    }

    let parent = local_app_data
        .join("PersonalMemoryAgentRuntime")
        .join("mcp");
    let target = parent.join(version);
    let target_executable = target.join("personalmemory-mcp-host.exe");
    let target_internal = target.join("_internal");
    if target_executable.is_file() && target_internal.is_dir() {
        return Ok(target_executable);
    }
    if target.exists() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "existing MCP runtime is incomplete; refusing to overwrite it",
        ));
    }

    fs::create_dir_all(&parent)?;
    let staging = loop {
        let id = STAGING_ID.fetch_add(1, Ordering::Relaxed);
        let path = parent.join(format!(".{version}.staging-{}-{id}", std::process::id()));
        match fs::create_dir(&path) {
            Ok(()) => break path,
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        }
    };

    let copy_result = copy_directory_contents(bundled_dir, &staging);
    if let Err(error) = copy_result {
        let _ = fs::remove_dir_all(&staging);
        return Err(error);
    }
    if !staging.join("personalmemory-mcp-host.exe").is_file() || !staging.join("_internal").is_dir()
    {
        let _ = fs::remove_dir_all(&staging);
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "bundled MCP runtime is incomplete",
        ));
    }

    match fs::rename(&staging, &target) {
        Ok(()) => Ok(target_executable),
        Err(_error) if target_executable.is_file() && target_internal.is_dir() => {
            // Another Desktop process completed the same immutable version
            // while this process was copying it.
            let _ = fs::remove_dir_all(&staging);
            Ok(target_executable)
        }
        Err(error) => {
            let _ = fs::remove_dir_all(&staging);
            Err(error)
        }
    }
}

#[cfg_attr(debug_assertions, allow(dead_code))]
fn copy_directory_contents(source: &Path, destination: &Path) -> io::Result<()> {
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let file_type = entry.file_type()?;
        let target = destination.join(entry.file_name());
        if file_type.is_symlink() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "symbolic links are not allowed in the bundled MCP runtime",
            ));
        }
        if file_type.is_dir() {
            fs::create_dir(&target)?;
            copy_directory_contents(&entry.path(), &target)?;
        } else if file_type.is_file() {
            fs::copy(entry.path(), target)?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::ensure_mcp_runtime;
    use std::{fs, path::Path};

    fn bundled_runtime(root: &Path) -> std::path::PathBuf {
        let bundled = root.join("bundle").join("personalmemory-mcp");
        fs::create_dir_all(bundled.join("_internal")).expect("bundle directory");
        fs::write(
            bundled.join("personalmemory-mcp-host.exe"),
            b"mcp executable v1",
        )
        .expect("mcp executable");
        fs::write(bundled.join("_internal/runtime.dll"), b"runtime v1")
            .expect("runtime dependency");
        bundled
    }

    #[test]
    fn runtime_is_copied_to_an_immutable_versioned_user_directory() {
        let root = tempfile::tempdir().expect("temp root");
        let bundled = bundled_runtime(root.path());
        let local_app_data = root.path().join("local");

        let executable =
            ensure_mcp_runtime(&bundled, &local_app_data, "0.2.7").expect("stage MCP runtime");

        assert_eq!(
            executable,
            local_app_data.join("PersonalMemoryAgentRuntime/mcp/0.2.7/personalmemory-mcp-host.exe")
        );
        assert_eq!(fs::read(executable).unwrap(), b"mcp executable v1");
        assert_eq!(
            fs::read(
                local_app_data.join("PersonalMemoryAgentRuntime/mcp/0.2.7/_internal/runtime.dll")
            )
            .unwrap(),
            b"runtime v1"
        );
    }

    #[test]
    fn existing_runtime_is_not_overwritten_by_a_later_bundle() {
        let root = tempfile::tempdir().expect("temp root");
        let bundled = bundled_runtime(root.path());
        let local_app_data = root.path().join("local");
        let first =
            ensure_mcp_runtime(&bundled, &local_app_data, "0.2.7").expect("stage first runtime");

        fs::write(
            bundled.join("personalmemory-mcp-host.exe"),
            b"different executable",
        )
        .expect("replace test bundle");
        let second = ensure_mcp_runtime(&bundled, &local_app_data, "0.2.7")
            .expect("reuse immutable runtime");

        assert_eq!(first, second);
        assert_eq!(fs::read(second).unwrap(), b"mcp executable v1");
    }

    #[test]
    fn incomplete_runtime_is_rejected_instead_of_overwritten() {
        let root = tempfile::tempdir().expect("temp root");
        let bundled = bundled_runtime(root.path());
        let local_app_data = root.path().join("local");
        let incomplete = local_app_data.join("PersonalMemoryAgentRuntime/mcp/0.2.7");
        fs::create_dir_all(&incomplete).expect("incomplete runtime");
        fs::write(
            incomplete.join("personalmemory-mcp-host.exe"),
            b"partial runtime",
        )
        .expect("preserve incomplete executable");
        fs::write(incomplete.join("unrelated.txt"), b"preserve").expect("preserve existing file");

        let error = ensure_mcp_runtime(&bundled, &local_app_data, "0.2.7")
            .expect_err("reject incomplete runtime");

        assert_eq!(error.kind(), std::io::ErrorKind::InvalidData);
        assert_eq!(
            fs::read(incomplete.join("unrelated.txt")).unwrap(),
            b"preserve"
        );
    }

    #[test]
    fn version_cannot_escape_the_runtime_directory() {
        let root = tempfile::tempdir().expect("temp root");
        let bundled = bundled_runtime(root.path());
        let local_app_data = root.path().join("local");

        let error = ensure_mcp_runtime(&bundled, &local_app_data, "../outside")
            .expect_err("reject traversal version");

        assert_eq!(error.kind(), std::io::ErrorKind::InvalidInput);
    }
}
