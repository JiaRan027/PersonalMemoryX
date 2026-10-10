from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import ssl
import sqlite3
import tomllib
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from packaging.markers import Marker


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "THIRD_PARTY_NOTICES.txt"
UPSTREAM_LICENSE_CACHE = ROOT / "licenses" / "third-party-upstream"
LEGAL_NAME = re.compile(r"^(license|licence|copying|notice|copyright|patents)([-_. ].*)?$", re.I)
WINDOWS_TARGET = "x86_64-pc-windows-msvc"

UPSTREAM_LICENSE_URLS: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {
    ("alloc-stdlib", "0.2.4"): ((
        "https://raw.githubusercontent.com/dropbox/rust-alloc-no-stdlib/ae42d22078b98549e987d2f03d12df7b984fde47/LICENSE",
        "LICENSE-alloc-stdlib-0.2.4",
    ),),
    ("alloc-stdlib", "0.3.0"): ((
        "https://raw.githubusercontent.com/dropbox/rust-alloc-no-stdlib/0a81fd6928ea3b33c8cd484aa4575d50ffb98012/LICENSE",
        "LICENSE-alloc-stdlib-0.3.0",
    ),),
    ("selectors", "0.36.1"): ((
        "https://www.mozilla.org/media/MPL/2.0/index.815ca599c9df.txt",
        "LICENSE-MPL-2.0.txt",
    ),),
    ("selectors", "0.38.0"): ((
        "https://www.mozilla.org/media/MPL/2.0/index.815ca599c9df.txt",
        "LICENSE-MPL-2.0.txt",
    ),),
    ("unic-char-property", "0.9.0"): (
        ("https://raw.githubusercontent.com/open-i18n/rust-unic/5878605364af97a3358368a6eaef02104af2e016/LICENSE-MIT", "LICENSE-UNIC-MIT"),
        ("https://raw.githubusercontent.com/open-i18n/rust-unic/5878605364af97a3358368a6eaef02104af2e016/LICENSE-APACHE", "LICENSE-UNIC-APACHE"),
    ),
    ("unic-char-range", "0.9.0"): (
        ("https://raw.githubusercontent.com/open-i18n/rust-unic/5878605364af97a3358368a6eaef02104af2e016/LICENSE-MIT", "LICENSE-UNIC-MIT"),
        ("https://raw.githubusercontent.com/open-i18n/rust-unic/5878605364af97a3358368a6eaef02104af2e016/LICENSE-APACHE", "LICENSE-UNIC-APACHE"),
    ),
    ("unic-common", "0.9.0"): (
        ("https://raw.githubusercontent.com/open-i18n/rust-unic/5878605364af97a3358368a6eaef02104af2e016/LICENSE-MIT", "LICENSE-UNIC-MIT"),
        ("https://raw.githubusercontent.com/open-i18n/rust-unic/5878605364af97a3358368a6eaef02104af2e016/LICENSE-APACHE", "LICENSE-UNIC-APACHE"),
    ),
    ("unic-ucd-ident", "0.9.0"): (
        ("https://raw.githubusercontent.com/open-i18n/rust-unic/5878605364af97a3358368a6eaef02104af2e016/LICENSE-MIT", "LICENSE-UNIC-MIT"),
        ("https://raw.githubusercontent.com/open-i18n/rust-unic/5878605364af97a3358368a6eaef02104af2e016/LICENSE-APACHE", "LICENSE-UNIC-APACHE"),
    ),
    ("unic-ucd-version", "0.9.0"): (
        ("https://raw.githubusercontent.com/open-i18n/rust-unic/5878605364af97a3358368a6eaef02104af2e016/LICENSE-MIT", "LICENSE-UNIC-MIT"),
        ("https://raw.githubusercontent.com/open-i18n/rust-unic/5878605364af97a3358368a6eaef02104af2e016/LICENSE-APACHE", "LICENSE-UNIC-APACHE"),
    ),
    ("webview2-com", "0.38.2"): ((
        "https://raw.githubusercontent.com/wravery/webview2-rs/b74dc5e2b394044bea5191052868ce7a106c202c/LICENSE",
        "LICENSE-webview2-rs",
    ),),
    ("webview2-com-sys", "0.38.2"): ((
        "https://raw.githubusercontent.com/wravery/webview2-rs/b74dc5e2b394044bea5191052868ce7a106c202c/LICENSE",
        "LICENSE-webview2-rs",
    ),),
    ("webview2-com", "0.39.1"): ((
        "https://raw.githubusercontent.com/wravery/webview2-rs/edc2caf886175ccaebe86078c9cfe1ae2a187328/LICENSE",
        "LICENSE-webview2-rs-0.39.1",
    ),),
    ("webview2-com-sys", "0.39.1"): ((
        "https://raw.githubusercontent.com/wravery/webview2-rs/edc2caf886175ccaebe86078c9cfe1ae2a187328/LICENSE",
        "LICENSE-webview2-rs-0.39.1",
    ),),
    ("webview2-com-macros", "0.8.1"): ((
        "https://raw.githubusercontent.com/wravery/webview2-rs/b74dc5e2b394044bea5191052868ce7a106c202c/LICENSE",
        "LICENSE-webview2-rs",
    ),),
}


@dataclass
class Component:
    ecosystem: str
    name: str
    version: str
    declared_license: str
    source: str
    texts: list[tuple[str, str]] = field(default_factory=list)


def is_legal_filename(name: str) -> bool:
    return bool(LEGAL_NAME.match(name))


def license_doc_sort_key(item: tuple[str, str]) -> tuple[str, str]:
    return item[0].casefold(), hashlib.sha256(item[1].encode("utf-8")).hexdigest()


def read_legal_files(paths: Iterable[Path]) -> list[tuple[str, str]]:
    found: dict[tuple[str, str], tuple[str, str]] = {}
    for path in paths:
        if not path.is_file() or not is_legal_filename(path.name):
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        if not text.strip():
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        found[(path.name, digest)] = (path.name, text.rstrip() + "\n")
    return sorted(found.values(), key=license_doc_sort_key)


def legal_files_near(root: Path) -> list[tuple[str, str]]:
    if not root.is_dir():
        return []
    candidates = list(root.iterdir())
    licenses_dir = root / "licenses"
    if licenses_dir.is_dir():
        candidates.extend(licenses_dir.rglob("*"))
    # Some packages keep a license in a dedicated top-level subdirectory.
    for child in root.iterdir():
        if child.is_dir() and child.name.casefold() in {"license", "licenses", "legal"}:
            candidates.extend(child.rglob("*"))
    return read_legal_files(candidates)


def fetch_upstream_license(url: str, filename: str) -> list[tuple[str, str]]:
    cached = UPSTREAM_LICENSE_CACHE / filename
    if not cached.is_file():
        cached.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "PersonalMemory third-party notice generator"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
        if len(data) < 200:
            raise RuntimeError(f"upstream license document is unexpectedly short: {url}")
        cached.write_bytes(data)
    return read_legal_files([cached])


def upstream_license_docs(name: str, version: str) -> list[tuple[str, str]]:
    docs: list[tuple[str, str]] = []
    for url, filename in UPSTREAM_LICENSE_URLS.get((name, version), ()):
        docs.extend(fetch_upstream_license(url, filename))
    return docs


def cargo_components(manifest: Path) -> list[Component]:
    tree = subprocess.run(
        [
            "cargo",
            "tree",
            "--target",
            WINDOWS_TARGET,
            "--prefix",
            "none",
            "--format",
            "{p}",
            "--edges",
            "normal",
            "--manifest-path",
            str(manifest),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    package_keys: set[tuple[str, str]] = set()
    for line in tree.stdout.splitlines():
        normalized = line.removesuffix(" (*)").strip()
        match = re.match(r"^([^ ]+) v([^ ]+)(?: \(.+\))?$", normalized)
        if not match:
            raise RuntimeError(f"unexpected cargo tree package entry: {line!r}")
        package_keys.add((match.group(1), match.group(2)))

    result = subprocess.run(
        [
            "cargo",
            "metadata",
            "--format-version",
            "1",
            "--locked",
            "--filter-platform",
            "x86_64-pc-windows-msvc",
            "--manifest-path",
            str(manifest),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    metadata = json.loads(result.stdout)
    packages: dict[tuple[str, str], list[dict[str, object]]] = {}
    for package in metadata["packages"]:
        packages.setdefault((package["name"], package["version"]), []).append(package)
    crate_name = (
        "personalmemory-desktop"
        if manifest.parent.name == "src-tauri"
        else "personalmemory-hook"
    )

    components: list[Component] = []
    for name, version in sorted(package_keys):
        candidates = packages.get((name, version), [])
        if not candidates:
            raise RuntimeError(f"cargo tree package missing from cargo metadata: {name} {version}")
        package = candidates[0]
        if package["name"] == crate_name:
            continue
        manifest_path = Path(package["manifest_path"])
        component_root = manifest_path.parent
        docs = legal_files_near(component_root)
        explicit_file = package.get("license_file")
        if explicit_file:
            candidate = Path(explicit_file)
            if not candidate.is_absolute():
                candidate = component_root / candidate
            docs = read_legal_files([*component_root.iterdir(), candidate])
        source = package.get("source") or "path dependency"
        if source.startswith("registry+"):
            source = f"https://crates.io/crates/{package['name']}/{package['version']}"
        components.append(
            Component(
                ecosystem="Rust",
                name=package["name"],
                version=package["version"],
                declared_license=package.get("license") or "Not stated in Cargo metadata",
                source=source,
                texts=docs or upstream_license_docs(package["name"], package["version"]),
            )
        )
    return components


def python_components() -> list[Component]:
    result = subprocess.run(
        [
            "uv",
            "export",
            "--no-dev",
            "--no-emit-project",
            "--format",
            "requirements.txt",
            "--locked",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    pinned: dict[str, str] = {}
    for line in result.stdout.splitlines():
        line = line.removesuffix(" \\").rstrip()
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^ ;\\]+)(?:\s*;\s*(.*))?$", line)
        if match:
            if match.group(3) and not Marker(match.group(3)).evaluate():
                continue
            pinned[match.group(1).casefold().replace("_", "-")] = match.group(2)

    components: list[Component] = []
    for normalized_name, version in sorted(pinned.items()):
        distribution = importlib.metadata.distribution(normalized_name)
        actual_name = distribution.metadata.get("Name") or normalized_name
        if distribution.version != version:
            raise RuntimeError(
                f"Python runtime is not synced with uv.lock: {actual_name} "
                f"{distribution.version} != {version}"
            )
        docs: list[tuple[str, str]] = []
        dist_files = distribution.files or []
        for relative in dist_files:
            if is_legal_filename(Path(str(relative)).name):
                candidate = Path(distribution.locate_file(relative))
                docs.extend(read_legal_files([candidate]))
        docs.extend(legal_files_near(Path(distribution._path)))
        if not docs:
            docs.extend(legal_files_near(Path(distribution.locate_file("")) / actual_name))
        license_expression = (
            distribution.metadata.get("License-Expression")
            or distribution.metadata.get("License")
            or "See included license file(s); package metadata does not state an SPDX license"
        )
        components.append(
            Component(
                ecosystem="Python",
                name=actual_name,
                version=version,
                declared_license=license_expression,
                source=f"https://pypi.org/project/{actual_name}/{version}/",
                texts=sorted(set(docs), key=license_doc_sort_key),
            )
        )
    return components


def node_components() -> list[Component]:
    lock_path = ROOT / "desktop" / "package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    components: list[Component] = []
    for package_path, entry in sorted(lock.get("packages", {}).items()):
        if not package_path.startswith("node_modules/") or entry.get("dev") is True:
            continue
        root = ROOT / "desktop" / package_path
        package_json = root / "package.json"
        if not package_json.is_file():
            raise RuntimeError(f"Locked npm package is missing from node_modules: {package_path}")
        metadata = json.loads(package_json.read_text(encoding="utf-8"))
        name = metadata.get("name") or package_path.removeprefix("node_modules/")
        version = entry.get("version") or metadata.get("version") or "unknown"
        if metadata.get("version") != version:
            raise RuntimeError(
                f"node_modules is not synced with package-lock.json: {name} "
                f"{metadata.get('version')} != {version}"
            )
        components.append(
            Component(
                ecosystem="JavaScript",
                name=name,
                version=version,
                declared_license=metadata.get("license") or "Not stated in package metadata",
                source=entry.get("resolved") or f"npm package {name}@{version}",
                texts=legal_files_near(root),
            )
        )
    return components


def pyinstaller_bootloader() -> Component:
    distribution = importlib.metadata.distribution("pyinstaller")
    docs: list[tuple[str, str]] = []
    for relative in distribution.files or []:
        if is_legal_filename(Path(str(relative)).name):
            docs.extend(read_legal_files([Path(distribution.locate_file(relative))]))
    docs.extend(legal_files_near(Path(distribution._path)))
    package_root = Path(distribution.locate_file("PyInstaller"))
    docs.extend(legal_files_near(package_root))
    if not docs:
        raise RuntimeError("PyInstaller bootloader license text was not found in the build environment")
    return Component(
        ecosystem="Python build output",
        name="PyInstaller bootloader embedded in service and MCP sidecars",
        version=distribution.version,
        declared_license=(
            distribution.metadata.get("License-Expression")
            or distribution.metadata.get("License")
            or "GPL-2.0-or-later with the PyInstaller bootloader exception"
        ),
        source="https://pyinstaller.org/",
        texts=sorted(set(docs), key=license_doc_sort_key),
    )


def python_payload_components() -> list[Component]:
    components: list[Component] = []
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError(f"CPython license file not found: {python_license}")
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    python_docs = read_legal_files([python_license])
    openssl_version_match = re.search(r"OpenSSL ([0-9]+\.[0-9]+\.[0-9]+)", ssl.OPENSSL_VERSION)
    if not openssl_version_match:
        raise RuntimeError(f"Could not determine the bundled OpenSSL version: {ssl.OPENSSL_VERSION}")
    openssl_version = openssl_version_match.group(1)
    if not openssl_version.startswith("3."):
        raise RuntimeError(
            f"OpenSSL {openssl_version} uses a different license family and needs an explicit audit"
        )
    openssl_docs = fetch_upstream_license(
        f"https://raw.githubusercontent.com/openssl/openssl/openssl-{openssl_version}/LICENSE.txt",
        f"LICENSE-OpenSSL-{openssl_version}.txt",
    )
    components.append(
        Component(
            ecosystem="Python native runtime",
            name="CPython standard library and interpreter DLLs",
            version=python_version,
            declared_license="Python Software Foundation License with bundled third-party notices",
            source=f"https://www.python.org/downloads/release/python-{python_version.replace('.', '')}/",
            texts=python_docs,
        )
    )
    components.append(
        Component(
            ecosystem="Python native runtime",
            name="OpenSSL (libcrypto-3.dll, libssl-3.dll)",
            version=ssl.OPENSSL_VERSION,
            declared_license="Apache-2.0 (OpenSSL 3.0 and later)",
            source="https://openssl-library.org/source/license/index.html",
            texts=openssl_docs,
        )
    )
    sqlite_notice = (
        "SQLite is dedicated to the public domain by its authors. The official SQLite copyright page "
        "states that the deliverable code and documentation may be copied, modified, published, used, "
        "compiled, sold, or distributed without limitation.\n\n"
        "Official notice: https://www.sqlite.org/copyright.html\n"
    )
    components.append(
        Component(
            ecosystem="Python native runtime",
            name="SQLite (sqlite3.dll)",
            version=sqlite3.sqlite_version,
            declared_license="Public domain",
            source="https://www.sqlite.org/copyright.html",
            texts=[("SQLITE-PUBLIC-DOMAIN-NOTICE", sqlite_notice)],
        )
    )
    components.append(
        Component(
            ecosystem="Python native runtime",
            name="libffi (libffi-8.dll)",
            version=f"ABI 8 (bundled with CPython {python_version})",
            declared_license="MIT-style license; notice included in the CPython license file",
            source="https://sourceware.org/libffi/",
            texts=python_docs,
        )
    )
    components.append(
        Component(
            ecosystem="Windows runtime",
            name="Microsoft Visual C++ v14 runtime DLLs copied from Visual Studio 2026 REDIST",
            version="14.51.36247.0 (Visual Studio REDIST folder 14.51.36231)",
            declared_license="Microsoft Distributable Code; subject to the applicable Visual Studio license terms",
            source="https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files",
            texts=[("MICROSOFT-VISUAL-STUDIO-REDIST", (
                "Microsoft Visual C++ runtime binaries are copied unmodified from the x64 "
                "Microsoft.VC145.CRT directory shipped with Visual Studio 2026. Microsoft lists "
                "the VC\\Redist\\MSVC tree as distributable code for validly licensed Visual "
                "Studio users, subject to the license terms. Eligibility depends on the distributor's "
                "license and applicable use restrictions.\n\n"
                "Official redistribution list: https://learn.microsoft.com/en-us/visualstudio/releases/2026/redistribution\n"
                "Official deployment guidance: https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files\n"
            ))],
        )
    )

    # PyInstaller's one-folder analyses list the actual data files copied into
    # the service and MCP sidecars. Use those entries to include setuptools'
    # vendored package notices without treating unrelated build tools as runtime.
    vendored_dist_info: set[Path] = set()
    setuptools_used = False
    for toc_path in (ROOT / "build" / "pyinstaller").glob("*/Analysis-00.toc"):
        toc = ast.literal_eval(toc_path.read_text(encoding="utf-8"))
        for entry in toc[14]:
            if isinstance(entry, (tuple, list)) and entry and "_distutils_hack" in str(entry[0]):
                setuptools_used = True
        for entry in toc[18]:
            if not isinstance(entry, (tuple, list)) or len(entry) < 2:
                continue
            source_path = Path(entry[1])
            parts = [part.casefold() for part in source_path.parts]
            if "_vendor" not in parts:
                continue
            dist_index = next(
                (index for index, part in enumerate(parts) if part.endswith(".dist-info")),
                None,
            )
            if dist_index is not None:
                vendored_dist_info.add(Path(*source_path.parts[: dist_index + 1]))

    for dist_info in sorted(vendored_dist_info, key=lambda path: str(path).casefold()):
        distribution = importlib.metadata.PathDistribution(dist_info)
        name = distribution.metadata.get("Name") or dist_info.name.removesuffix(".dist-info")
        components.append(
            Component(
                ecosystem="Python bundled vendor",
                name=f"setuptools vendored {name}",
                version=distribution.version,
                declared_license=(
                    distribution.metadata.get("License-Expression")
                    or distribution.metadata.get("License")
                    or "See included license files"
                ),
                source=distribution.metadata.get("Home-page") or "Bundled within setuptools",
                texts=legal_files_near(dist_info),
            )
        )

    if setuptools_used:
        setuptools = importlib.metadata.distribution("setuptools")
        components.append(
            Component(
                ecosystem="Python bundled runtime",
                name="setuptools compatibility shim (_distutils_hack)",
                version=setuptools.version,
                declared_license=(
                    setuptools.metadata.get("License-Expression")
                    or setuptools.metadata.get("License")
                    or "See included license file(s)"
                ),
                source="https://pypi.org/project/setuptools/",
                texts=legal_files_near(Path(setuptools._path))
                or legal_files_near(Path(setuptools.locate_file("setuptools"))),
            )
        )
    return components


def nsis_components() -> list[Component]:
    nsis_root = Path(os.environ.get("LOCALAPPDATA", "")) / "tauri" / "NSIS"
    copying = nsis_root / "COPYING"
    makensis = nsis_root / "makensis.exe"
    if not copying.is_file() or not makensis.is_file():
        raise RuntimeError(f"NSIS compiler or COPYING file not found under {nsis_root}")
    version_result = subprocess.run(
        [str(makensis), "/VERSION"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    nsis_version = version_result.stdout.strip().removeprefix("v")
    if not nsis_version:
        raise RuntimeError("Could not determine the NSIS version from makensis")
    return [
        Component(
            ecosystem="Installer runtime",
            name=f"NSIS {nsis_version} framework and nsExec plug-in used by installer hooks",
            version=nsis_version,
            declared_license="zlib/libpng, bzip2, and Common Public License 1.0 (per NSIS COPYING)",
            source="https://nsis.sourceforge.io/License",
            texts=read_legal_files([copying]),
        )
    ]


def render(components: list[Component]) -> str:
    unique_texts: dict[str, dict[str, object]] = {}
    component_text_ids: dict[int, list[str]] = {}
    for index, component in enumerate(components):
        ids: list[str] = []
        for filename, text in component.texts:
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if digest not in unique_texts:
                unique_texts[digest] = {"id": f"LT-{len(unique_texts) + 1:04d}", "text": text}
            notice_id = str(unique_texts[digest]["id"])
            ids.append(f"{notice_id} ({filename})")
            owners = unique_texts[digest].setdefault("owners", [])
            owner = f"{component.ecosystem}: {component.name} {component.version}"
            if owner not in owners:
                owners.append(owner)
        component_text_ids[index] = ids

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project_version = project["project"]["version"]
    package_init = (ROOT / "service" / "src" / "personalmemory" / "__init__.py").read_text(encoding="utf-8")
    revision_match = re.search(r'^__build_revision__\s*=\s*["\']([^"\']+)["\']', package_init, re.M)
    build_revision = revision_match.group(1) if revision_match else "unknown"

    lines = [
        "PERSONALMEMORY — THIRD-PARTY NOTICES",
        f"Generated from the locked dependency files and build environment for PersonalMemory {project_version} / {build_revision}.",
        "",
        "Copyright (C) 2026 JiaRan027 applies to original PersonalMemory code and documentation only.",
        "The third-party components listed below retain their respective copyrights and licenses.",
        "This file is informational and does not replace any license text or source-code obligations.",
        "",
        "Scope: target-specific normal Rust Cargo graphs, Python runtime dependencies exported from uv.lock,",
        "non-development JavaScript packages, the PyInstaller bootloader, PyInstaller TOC-detected Python",
        "payload/vendor data, bundled CPython/OpenSSL/SQLite/libffi runtime files, Visual Studio VC runtime files, and NSIS installer materials.",
        "The Python and JavaScript lockfile inventories are conservative: a locked package may be tree-shaken",
        "or excluded from a specific binary. System-provided Windows components and WebView2 runtime are not bundled.",
        "The x64 VC runtime DLLs are copied byte-for-byte from Visual Studio 2026 REDIST. Windows 10/11 UCRT is",
        "provided by the operating system and is not bundled. Redistribution still requires a validly licensed",
        "Visual Studio copy and compliance with its license terms; this inventory is not legal advice or a legal opinion.",
        "",
        "PACKAGE INVENTORY",
    ]
    grouped: dict[str, list[tuple[int, Component]]] = {}
    for index, component in enumerate(components):
        grouped.setdefault(component.ecosystem, []).append((index, component))
    for ecosystem in (
        "Rust",
        "Python",
        "Python bundled runtime",
        "Python bundled vendor",
        "Python native runtime",
        "Python build output",
        "JavaScript",
        "Windows runtime",
        "Installer runtime",
    ):
        group = grouped.get(ecosystem, [])
        if not group:
            continue
        lines.extend(["", f"[{ecosystem}]", "Name | Version | Declared license | Included text references | Source"])
        lines.append("--- | --- | --- | --- | ---")
        for index, component in group:
            refs = "; ".join(component_text_ids[index]) or "No license file found in local package source"
            lines.append(
                " | ".join(
                    value.replace("|", "\\|").replace("\n", " ")
                    for value in (
                        component.name,
                        component.version,
                        component.declared_license,
                        refs,
                        component.source,
                    )
                )
            )

    lines.extend(["", "LICENSE AND NOTICE TEXTS"])
    for digest, item in unique_texts.items():
        lines.extend(
            [
                "",
                f"[{item['id']}]",
                "Attribution: " + "; ".join(item.get("owners", [])),
                "",
                str(item["text"]).rstrip(),
            ]
        )
    missing = [component for component in components if not component.texts]
    if missing:
        lines.extend(["", "LICENSE-TEXT FOLLOW-UP REQUIRED"])
        lines.extend(
            f"- {component.ecosystem}: {component.name} {component.version} "
            f"({component.declared_license}) — no license document was found in the local package source."
            for component in missing
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    os.chdir(ROOT)
    components = [
        *cargo_components(ROOT / "desktop" / "src-tauri" / "Cargo.toml"),
        *cargo_components(ROOT / "desktop" / "native" / "personalmemory-hook" / "Cargo.toml"),
        *python_components(),
        pyinstaller_bootloader(),
        *python_payload_components(),
        *nsis_components(),
        *node_components(),
    ]
    OUTPUT.write_text(render(components), encoding="utf-8", newline="\n")
    missing = [component for component in components if not component.texts]
    print(
        f"Wrote {OUTPUT}: {len(components)} components, "
        f"{sum(len(component.texts) for component in components)} license/notice documents, "
        f"{len(missing)} components without a local license document."
    )
    for component in missing:
        print(f"MISSING: {component.ecosystem}: {component.name} {component.version}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
