import hashlib
import json
import zipfile

import pytest
from personalmemory.application.ingestion import IngestionService
from personalmemory.domain.memory.fingerprint import (
    legacy_semantic_fingerprint_value,
    semantic_fingerprint_value,
)
from personalmemory.infrastructure.database.models import (
    ConnectorInstanceRecord,
    MemoryObjectRecord,
    MemoryRelationRecord,
    MemorySpaceRecord,
    TombstoneRecord,
)
from personalmemory.infrastructure.database.session import Database
from personalmemory.settings import Settings
from sqlalchemy import func, select


def seeded_database(tmp_path) -> Database:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "SOURCE.md"
    source.write_text("## Insight\n- 经验比聊天记录更有价值。\n", encoding="utf-8")
    database = Database(Settings(data_dir=tmp_path / "instance", api_token="token"))
    database.initialize()
    with database.session_factory.begin() as session:
        session.add(MemorySpaceRecord(id="space_01", name="我的记忆"))
        session.flush()
        session.add(
            ConnectorInstanceRecord(
                id="source_01",
                memory_space_id="space_01",
                profile_id="generic_markdown_readonly",
                name="来源",
                source_path=str(source),
                namespace="general",
                can_read=True,
                can_write=False,
                read_enabled=True,
                write_enabled=False,
            )
        )
    IngestionService(database).ingest("source_01", source, source.read_text("utf-8"))
    return database


def test_protocol_export_inspect_and_restore_round_trip(tmp_path):
    from personalmemory.infrastructure.protocol.package import MemoryProtocol

    source_database = seeded_database(tmp_path / "source")
    package_path = tmp_path / "memory.pmemory"
    protocol = MemoryProtocol(source_database)
    with source_database.session_factory.begin() as session:
        memory_id = session.scalar(select(MemoryObjectRecord.id))
        session.add(
            MemoryRelationRecord(
                id="relation_01",
                memory_space_id="space_01",
                source_memory_id=memory_id,
                target_memory_id=memory_id,
                relation_type="supports",
                origin="user",
                validity_state="active",
                payload={"evidence": "round-trip"},
            )
        )

    exported = protocol.export_space("space_01", package_path)
    inspected = protocol.inspect(package_path)

    with zipfile.ZipFile(package_path) as archive:
        names = set(archive.namelist())

    assert exported.memory_count == 1
    assert inspected.valid is True
    assert inspected.memory_count == 1
    assert {
        "manifest.json",
        "checksums.sha256",
        "data/memory-space.json",
        "data/memories.jsonl",
        "data/revisions.jsonl",
        "data/tombstones.jsonl",
    } <= names

    restored_database = Database(
        Settings(data_dir=tmp_path / "restored", api_token="token")
    )
    restored_database.initialize()
    restored = MemoryProtocol(restored_database).restore(package_path)
    with restored_database.session_factory() as session:
        count = session.scalar(select(func.count()).select_from(MemoryObjectRecord))
        relation_count = session.scalar(
            select(func.count()).select_from(MemoryRelationRecord)
        )

    assert restored.memory_count == 1
    assert count == 1
    assert relation_count == 1


def test_protocol_import_accepts_pre_nfkc_package_and_upgrades_fingerprint(tmp_path):
    from personalmemory.infrastructure.protocol.package import MemoryProtocol

    source_database = seeded_database(tmp_path / "source")
    legacy_statement = "用户使用ＡＩ工具。"
    with source_database.session_factory.begin() as session:
        memory = session.scalar(select(MemoryObjectRecord))
        memory.statement = legacy_statement
        memory.semantic_fingerprint = legacy_semantic_fingerprint_value(
            memory.type, memory.namespace, legacy_statement
        )
    package_path = tmp_path / "legacy.pmemory"
    MemoryProtocol(source_database).export_space("space_01", package_path)

    target_database = Database(
        Settings(data_dir=tmp_path / "target", api_token="token")
    )
    target_database.initialize()
    with target_database.session_factory.begin() as session:
        session.add(MemorySpaceRecord(id="space_default", name="我的记忆"))

    protocol = MemoryProtocol(target_database)
    inspected = protocol.inspect(package_path)
    imported = protocol.import_into_space(
        package_path, memory_space_id="space_default"
    )
    with target_database.session_factory() as session:
        stored = session.scalar(select(MemoryObjectRecord))

    assert inspected.valid is True
    assert imported.imported == 1
    assert stored.statement == legacy_statement
    assert stored.semantic_fingerprint == semantic_fingerprint_value(
        stored.type, stored.namespace, legacy_statement
    )


def test_dual_export_refuses_to_overwrite_readable_sibling(tmp_path):
    from personalmemory.infrastructure.protocol.package import MemoryProtocol

    database = seeded_database(tmp_path / "source")
    package_path = tmp_path / "memory.pmemory"
    readable_path = tmp_path / "memory.md"
    readable_path.write_text("keep me", encoding="utf-8")

    with pytest.raises(FileExistsError):
        MemoryProtocol(database).export_space_with_readable(
            "space_01", package_path, readable_path
        )

    assert not package_path.exists()
    assert readable_path.read_text(encoding="utf-8") == "keep me"


def test_protocol_inspect_rejects_oversized_archive_member(tmp_path):
    from personalmemory.infrastructure.protocol.package import (
        MemoryProtocol,
        ProtocolIntegrityError,
    )

    database = seeded_database(tmp_path / "source")
    package_path = tmp_path / "oversized.pmemory"
    with zipfile.ZipFile(package_path, "w") as archive:
        archive.writestr("oversized.bin", b"x" * (MemoryProtocol.max_member_bytes + 1))

    with pytest.raises(ProtocolIntegrityError, match="too large"):
        MemoryProtocol(database).inspect(package_path)


def test_export_does_not_include_another_space_tombstones(tmp_path):
    from personalmemory.infrastructure.protocol.package import MemoryProtocol

    database = seeded_database(tmp_path / "source")
    with database.session_factory.begin() as session:
        session.add(MemorySpaceRecord(id="space_02", name="Other"))
        session.add(
            TombstoneRecord(
                id="tombstone_other",
                memory_space_id="space_02",
                object_id="memory_other",
                object_kind="memory",
                semantic_fingerprint="a" * 64,
            )
        )
    package_path = tmp_path / "space-01.pmemory"

    MemoryProtocol(database).export_space("space_01", package_path)

    with zipfile.ZipFile(package_path) as archive:
        tombstones = archive.read("data/tombstones.jsonl").decode("utf-8")
    assert tombstones == ""


def test_inspect_rejects_memory_from_a_different_space(tmp_path):
    from personalmemory.infrastructure.protocol.package import (
        MemoryProtocol,
        ProtocolIntegrityError,
    )

    database = seeded_database(tmp_path / "source")
    package_path = tmp_path / "memory.pmemory"
    tampered_path = tmp_path / "wrong-space.pmemory"
    MemoryProtocol(database).export_space("space_01", package_path)
    with zipfile.ZipFile(package_path) as archive:
        payloads = {
            info.filename: archive.read(info.filename)
            for info in archive.infolist()
            if info.filename != "checksums.sha256"
        }
    memory = json.loads(payloads["data/memories.jsonl"].decode("utf-8"))
    memory["memory_space_id"] = "space_other"
    payloads["data/memories.jsonl"] = (
        json.dumps(memory, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    checksums = "".join(
        f"{hashlib.sha256(content).hexdigest()}  {name}\n"
        for name, content in sorted(payloads.items())
    ).encode("utf-8")
    with zipfile.ZipFile(tampered_path, "w") as archive:
        for name, content in payloads.items():
            archive.writestr(name, content)
        archive.writestr("checksums.sha256", checksums)

    with pytest.raises(ProtocolIntegrityError, match="different MemorySpace"):
        MemoryProtocol(database).inspect(tampered_path)


def test_protocol_inspect_rejects_tampered_checksum_without_writing(tmp_path):
    from personalmemory.infrastructure.protocol.package import (
        MemoryProtocol,
        ProtocolIntegrityError,
    )

    source_database = seeded_database(tmp_path / "source")
    package_path = tmp_path / "memory.pmemory"
    MemoryProtocol(source_database).export_space("space_01", package_path)
    with (
        pytest.warns(UserWarning, match="Duplicate name"),
        zipfile.ZipFile(package_path, "a") as archive,
    ):
        archive.writestr("data/memories.jsonl", "{}\n")

    empty_database = Database(Settings(data_dir=tmp_path / "empty", api_token="token"))
    empty_database.initialize()

    with pytest.raises(ProtocolIntegrityError):
        MemoryProtocol(empty_database).inspect(package_path)

    with empty_database.session_factory() as session:
        count = session.scalar(select(func.count()).select_from(MemoryObjectRecord))
    assert count == 0


def test_protocol_inspect_requires_checksum_for_every_payload_file(tmp_path):
    from personalmemory.infrastructure.protocol.package import (
        MemoryProtocol,
        ProtocolIntegrityError,
    )

    source_database = seeded_database(tmp_path / "source")
    package_path = tmp_path / "memory.pmemory"
    incomplete_path = tmp_path / "incomplete-checksums.pmemory"
    MemoryProtocol(source_database).export_space("space_01", package_path)

    with zipfile.ZipFile(package_path) as source_archive:
        payloads = {
            info.filename: source_archive.read(info.filename)
            for info in source_archive.infolist()
            if info.filename != "checksums.sha256"
        }
        checksums = source_archive.read("checksums.sha256").decode("utf-8")
    checksums = "\n".join(
        line for line in checksums.splitlines() if not line.endswith("data/memories.jsonl")
    )
    with zipfile.ZipFile(incomplete_path, "w") as target_archive:
        for name, content in payloads.items():
            target_archive.writestr(name, content)
        target_archive.writestr("checksums.sha256", f"{checksums}\n")

    with pytest.raises(ProtocolIntegrityError, match="missing required entries"):
        MemoryProtocol(source_database).inspect(incomplete_path)
