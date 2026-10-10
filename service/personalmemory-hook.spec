"""PyInstaller spec for the stdlib-only synchronous agent Hook."""

from pathlib import Path

project_root = Path(SPECPATH).parent
service_source = project_root / "service" / "src"
entrypoint = service_source / "personalmemory" / "agent_hook_light.py"
icon = project_root / "desktop" / "src-tauri" / "icons" / "icon.ico"

analysis = Analysis(
    [str(entrypoint)],
    pathex=[str(service_source)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "httpx",
        "pydantic",
        "pydantic_settings",
        "sqlalchemy",
        "fastapi",
        "uvicorn",
        "numpy",
        "PIL",
        "rich",
        "pytest",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="personalmemory-hook",
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
    name="personalmemory-hook",
)
