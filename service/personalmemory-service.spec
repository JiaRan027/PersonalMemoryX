import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


project_root = Path(SPECPATH).parent
service_source = project_root / "service" / "src"
entrypoint = service_source / "personalmemory" / "main.py"
icon = project_root / "desktop" / "src-tauri" / "icons" / "icon.ico"
hidden_imports = sorted(
    set(
        collect_submodules("uvicorn")
        + [
            "sqlalchemy.dialects.sqlite",
            "sqlalchemy.dialects.sqlite.pysqlite",
        ]
    )
)
diagnostic_console = os.environ.get("PERSONALMEMORY_PYINSTALLER_CONSOLE") == "1"

analysis = Analysis(
    [str(entrypoint)],
    pathex=[str(service_source)],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "hypothesis", "ruff"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="personalmemory-service",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=diagnostic_console,
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
    name="personalmemory-service",
)
