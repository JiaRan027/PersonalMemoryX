!macro NSIS_HOOK_PREINSTALL
  DetailPrint "Closing active PersonalMemory processes before upgrading..."

  ; Desktop supervises the Service, while an Agent owns the MCP process. Stop
  ; every PersonalMemory binary that can lock installation files, but never
  ; close Claude or Codex themselves.
  nsExec::ExecToStack /TIMEOUT=5000 '"$SYSDIR\taskkill.exe" /F /T /IM personalmemory-desktop.exe'
  Pop $0
  Pop $1
  nsExec::ExecToStack /TIMEOUT=5000 '"$SYSDIR\taskkill.exe" /F /T /IM personalmemory-service.exe'
  Pop $0
  Pop $1
  ; Legacy MCP runtimes lived under the install directory and must be stopped
  ; before those files can be replaced. New MCP runtimes live in an immutable
  ; per-version user directory and use a different image name, so they survive
  ; upgrades and can reconnect after the Service restarts.
  nsExec::ExecToStack /TIMEOUT=5000 '"$SYSDIR\taskkill.exe" /F /T /IM personalmemory-mcp.exe'
  Pop $0
  Pop $1
  nsExec::ExecToStack /TIMEOUT=5000 '"$SYSDIR\taskkill.exe" /F /T /IM personalmemory-hook.exe'
  Pop $0
  Pop $1
  Sleep 500
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ; Remove the per-user login entry even if the app cannot run its normal
  ; disable command during uninstall.
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "PersonalMemory"
!macroend
