"""Render a validated .pmemory package as a human-readable UTF-8 Markdown file."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

MAX_PACKAGE_FILES = 64
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024


def _checksums(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in value.splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or len(digest) != 64 or not name:
            raise ValueError("invalid package checksum manifest")
        result[name] = digest
    return result


def _quote(value: object) -> list[str]:
    return [f"  > {line}" for line in str(value or "").splitlines() or [""]]


def render(package_path: Path, output_path: Path) -> tuple[Path, int]:
    with zipfile.ZipFile(package_path) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) > MAX_PACKAGE_FILES or len(names) != len(set(names)):
            raise ValueError("unsafe package entry count")
        if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("package exceeds uncompressed size limit")
        if any(info.file_size > MAX_MEMBER_BYTES for info in infos):
            raise ValueError("package member exceeds size limit")
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError("unsafe package path")
        required = {"manifest.json", "checksums.sha256", "data/memories.jsonl"}
        if not required <= set(names):
            raise ValueError("package is missing required files")
        expected = _checksums(archive.read("checksums.sha256").decode("utf-8"))
        if set(expected) != set(names) - {"checksums.sha256"}:
            raise ValueError("checksum manifest does not match package contents")
        for name, digest in expected.items():
            if name not in names or hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError(f"package checksum mismatch: {name}")
        manifest = json.loads(archive.read("manifest.json"))
        memories = [
            json.loads(line)
            for line in archive.read("data/memories.jsonl").decode("utf-8").splitlines()
            if line.strip()
        ]

    active = [item for item in memories if item.get("status") == "active"]
    created_at = datetime.fromisoformat(str(manifest["created_at"])).astimezone().isoformat()
    producer = manifest.get("producer") or {}
    version = producer.get("version") or f"protocol-{manifest.get('protocol_version', 'unknown')}"
    build_revision = producer.get("build_revision") or "legacy/unknown"
    lines = [
        "# PersonalMemory 可阅读导出",
        "",
        "> 这是从 `.pmemory` 实验迁移包生成的阅读视图；它不参与恢复。",
        "",
        f"- 生成产品/协议版本：{version}",
        f"- Build revision：{build_revision}",
        f"- 原包导出时间（本地时区）：{created_at}",
        f"- Memory Space：`{manifest.get('memory_space_id', 'unknown')}`",
        f"- 当前有效记忆：{len(active)}",
        "",
    ]
    for index, item in enumerate(active, start=1):
        lines.extend(
            [
                f"## {index}. {item.get('type', 'unknown')}",
                "",
                f"- ID：`{item.get('id', 'unknown')}`",
                f"- Namespace：`{item.get('namespace', 'unknown')}`",
                f"- Revision：`{item.get('revision', 'unknown')}`",
                "- 内容：",
                *_quote(item.get("statement")),
            ]
        )
        if item.get("reason"):
            lines.extend(["- 理由：", *_quote(item["reason"])])
        lines.append("")
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8-sig")
    return output_path, len(active)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    package_path = args.package.resolve()
    output_path = (args.output or package_path.with_suffix(".md")).resolve()
    if output_path.exists():
        raise FileExistsError(f"output already exists: {output_path}")
    rendered, count = render(package_path, output_path)
    print(json.dumps({"path": str(rendered), "memory_count": count}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
