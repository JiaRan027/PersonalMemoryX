from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _write_windows_stub(path: Path, body: str) -> None:
    path.write_text(body.replace("\n", "\r\n"), encoding="utf-8")


def test_verify_script_fails_when_desktop_command_fails(tmp_path):
    project_root = tmp_path
    scripts_dir = project_root / "scripts"
    desktop_dir = project_root / "desktop"
    service_tests_dir = project_root / "service" / "tests"
    fake_bin = project_root / "fake-bin"

    scripts_dir.mkdir()
    desktop_dir.mkdir()
    service_tests_dir.mkdir(parents=True)
    fake_bin.mkdir()

    source_script = (
        Path(__file__).resolve().parents[2] / "scripts" / "verify.ps1"
    )
    verify_script = scripts_dir / "verify.ps1"
    verify_script.write_text(source_script.read_text(encoding="utf-8"), encoding="utf-8")

    (desktop_dir / "package.json").write_text("{}", encoding="utf-8")

    _write_windows_stub(
        fake_bin / "python.cmd",
        """@echo off
exit /b 0
""",
    )
    _write_windows_stub(
        fake_bin / "npm.cmd",
        """@echo off
if "%2"=="typecheck" exit /b 1
exit /b 0
""",
    )

    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin};{environment['PATH']}"

    result = subprocess.run(
        [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(verify_script),
        ],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0, result.stdout + result.stderr
