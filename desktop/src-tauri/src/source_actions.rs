use std::path::PathBuf;
use std::process::Command;

#[tauri::command]
pub fn open_source_file(path: String) -> Result<(), String> {
    let candidate = path.trim();
    let source = PathBuf::from(candidate);
    if !source.is_absolute() {
        return Err("来源不是本地绝对路径".to_owned());
    }
    if !source.is_file() {
        return Err("来源文件不存在，可能已经被移动或删除".to_owned());
    }

    #[cfg(target_os = "windows")]
    {
        Command::new("explorer.exe")
            .arg("/select,")
            .arg(&source)
            .spawn()
            .map_err(|error| format!("无法打开来源文件：{error}"))?;
    }
    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .arg("-R")
            .arg(&source)
            .spawn()
            .map_err(|error| format!("无法打开来源文件：{error}"))?;
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        Command::new("xdg-open")
            .arg(&source)
            .spawn()
            .map_err(|error| format!("无法打开来源文件：{error}"))?;
    }
    Ok(())
}
