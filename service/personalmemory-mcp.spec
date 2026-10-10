"""PyInstaller spec：把 MCP stdio server 打包为独立控制台可执行文件。

与 service sidecar 分开构建，原因：
- service 是 windowed（console=False）后台进程，没有可用的 stdin/stdout，
  无法承载 MCP stdio 传输；
- MCP server 必须是 console=True 才能通过标准输入输出与 Agent 通信
  （Claude Code 等客户端以重定向管道 + CREATE_NO_WINDOW 启动，不会弹窗）。

产物：desktop/src-tauri/binaries/personalmemory-mcp/personalmemory-mcp-host.exe
"""

from pathlib import Path

project_root = Path(SPECPATH).parent
service_source = project_root / "service" / "src"
entrypoint = service_source / "personalmemory" / "agent_mcp.py"
icon = project_root / "desktop" / "src-tauri" / "icons" / "icon.ico"

analysis = Analysis(
    [str(entrypoint)],
    pathex=[str(service_source)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "httpx",
        "personalmemory.agent_hook",
        "personalmemory.domain.observation",
        "personalmemory.infrastructure.claude_hook",
        "personalmemory.settings",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "hypothesis", "ruff", "uvicorn", "fastapi", "alembic"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="personalmemory-mcp-host",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon),
    contents_directory="_internal",
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="personalmemory-mcp",
)
