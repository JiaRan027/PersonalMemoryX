from __future__ import annotations

import hashlib
import json
import os
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import func, or_, select

from personalmemory import __build_revision__, __version__
from personalmemory.application.ingestion import new_id
from personalmemory.domain.memory.fingerprint import (
    exact_content_fingerprint,
    legacy_semantic_fingerprint_value,
    semantic_fingerprint_value,
)
from personalmemory.domain.memory.types import EpistemicState, MemoryType, Sensitivity
from personalmemory.domain.policy.admission import contains_secret
from personalmemory.infrastructure.database.models import (
    MemoryObjectRecord,
    MemoryRelationRecord,
    MemoryRevisionRecord,
    MemorySpaceRecord,
    TombstoneRecord,
)
from personalmemory.infrastructure.database.session import Database


class ProtocolIntegrityError(RuntimeError):
    pass


class ProtocolRestoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProtocolReport:
    valid: bool
    memory_count: int
    memory_space_id: str


@dataclass(frozen=True)
class ProtocolImportReport:
    imported: int
    skipped_tombstoned: int
    skipped_existing: int


@dataclass(frozen=True)
class _ExportSnapshot:
    space: MemorySpaceRecord
    memories: tuple[MemoryObjectRecord, ...]
    revisions: tuple[MemoryRevisionRecord, ...]
    relations: tuple[MemoryRelationRecord, ...]
    tombstones: tuple[TombstoneRecord, ...]
    created_at: datetime


def supported_semantic_fingerprints(
    memory_type: MemoryType | str,
    namespace: str,
    statement: str,
) -> tuple[str, ...]:
    """Return current and pre-NFKC fingerprints accepted from signed packages."""
    current = semantic_fingerprint_value(memory_type, namespace, statement)
    legacy = legacy_semantic_fingerprint_value(memory_type, namespace, statement)
    return (current,) if current == legacy else (current, legacy)


def json_bytes(value: object, *, pretty: bool = False) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def jsonl_bytes(values: list[dict[str, object]]) -> bytes:
    return b"".join(json_bytes(value) for value in values)


class MemoryProtocol:
    protocol_version = "0.1.0"
    max_members = 64
    max_member_bytes = 16 * 1024 * 1024
    max_package_bytes = 32 * 1024 * 1024

    def __init__(self, database: Database) -> None:
        self.database = database

    def export_space(self, memory_space_id: str, package_path: Path) -> ProtocolReport:
        snapshot = self._export_snapshot(memory_space_id)
        self._write_package(snapshot, package_path)
        return ProtocolReport(True, len(snapshot.memories), memory_space_id)

    def export_space_with_readable(
        self,
        memory_space_id: str,
        package_path: Path,
        readable_path: Path,
    ) -> ProtocolReport:
        """Write both user exports from one snapshot without replacing existing files."""
        if package_path.resolve() == readable_path.resolve():
            raise ValueError("Package and readable paths must be different")
        snapshot = self._export_snapshot(memory_space_id)
        package_path.parent.mkdir(parents=True, exist_ok=True)
        readable_path.parent.mkdir(parents=True, exist_ok=True)
        package_temp = package_path.with_name(f".{package_path.name}.{new_id('tmp')}")
        readable_temp = readable_path.with_name(f".{readable_path.name}.{new_id('tmp')}")
        reserved: list[Path] = []
        try:
            self._write_package(snapshot, package_temp)
            readable_temp.write_bytes(
                self._readable_markdown(
                    snapshot.space,
                    [memory for memory in snapshot.memories if memory.status == "active"],
                    generated_at=snapshot.created_at,
                    with_bom=True,
                )
            )
            for target in (package_path, readable_path):
                target.open("xb").close()
                reserved.append(target)
            os.replace(package_temp, package_path)
            os.replace(readable_temp, readable_path)
        except Exception:
            for target in reserved:
                target.unlink(missing_ok=True)
            raise
        finally:
            package_temp.unlink(missing_ok=True)
            readable_temp.unlink(missing_ok=True)
        return ProtocolReport(True, len(snapshot.memories), memory_space_id)

    def _export_snapshot(self, memory_space_id: str) -> _ExportSnapshot:
        with self.database.session_factory() as session:
            space = session.get(MemorySpaceRecord, memory_space_id)
            if space is None:
                raise KeyError(memory_space_id)
            memories = session.scalars(
                select(MemoryObjectRecord)
                .where(MemoryObjectRecord.memory_space_id == memory_space_id)
                .order_by(MemoryObjectRecord.id)
            ).all()
            memory_ids = [memory.id for memory in memories]
            revisions = (
                session.scalars(
                    select(MemoryRevisionRecord)
                    .where(MemoryRevisionRecord.memory_id.in_(memory_ids))
                    .order_by(MemoryRevisionRecord.memory_id, MemoryRevisionRecord.revision)
                ).all()
                if memory_ids
                else []
            )
            relations = session.scalars(
                select(MemoryRelationRecord)
                .where(MemoryRelationRecord.memory_space_id == memory_space_id)
                .order_by(MemoryRelationRecord.id)
            ).all()
            tombstones = session.scalars(
                select(TombstoneRecord)
                .where(TombstoneRecord.memory_space_id == memory_space_id)
                .order_by(TombstoneRecord.id)
            ).all()
        return _ExportSnapshot(
            space=space,
            memories=tuple(memories),
            revisions=tuple(revisions),
            relations=tuple(relations),
            tombstones=tuple(tombstones),
            created_at=datetime.now(UTC),
        )

    def _write_package(self, snapshot: _ExportSnapshot, package_path: Path) -> None:
        space = snapshot.space
        memories = snapshot.memories
        revisions = snapshot.revisions
        relations = snapshot.relations
        tombstones = snapshot.tombstones
        readable = self._readable_markdown(
            space,
            [memory for memory in memories if memory.status == "active"],
            generated_at=snapshot.created_at,
        )
        manifest = {
            "protocol": "personal-memory",
            "protocol_version": self.protocol_version,
            "export_id": new_id("export"),
            "created_at": snapshot.created_at.isoformat(),
            "memory_space_id": space.id,
            "producer": {
                "name": "PersonalMemory",
                "version": __version__,
                "build_revision": __build_revision__,
            },
            "contents": {
                "memories": len(memories),
                "revisions": len(revisions),
                "relations": len(relations),
                "evidence": 0,
                "tombstones": len(tombstones),
                "readable_view": "views/memories.md",
            },
            "implementation": {
                "round_trip": ["memory_space", "memory", "revision", "relation", "tombstone"],
                "not_implemented": ["schema", "profile", "subject", "evidence", "signature"],
            },
            "integrity": {"algorithm": "sha256", "checksums_file": "checksums.sha256"},
        }
        files = {
            "README.md": self._package_readme(),
            "manifest.json": json_bytes(manifest, pretty=True),
            "data/memory-space.json": json_bytes(
                {"id": space.id, "name": space.name, "created_at": space.created_at.isoformat()}
            ),
            "data/memories.jsonl": jsonl_bytes(
                [self._memory_dict(memory) for memory in memories]
            ),
            "data/revisions.jsonl": jsonl_bytes(
                [self._revision_dict(revision) for revision in revisions]
            ),
            "data/relations.jsonl": jsonl_bytes(
                [self._relation_dict(relation) for relation in relations]
            ),
            "data/evidence.jsonl": b"",
            "data/tombstones.jsonl": jsonl_bytes(
                [self._tombstone_dict(tombstone) for tombstone in tombstones]
            ),
            "views/memories.md": readable,
        }
        checksums = "".join(
            f"{hashlib.sha256(content).hexdigest()}  {name}\n"
            for name, content in sorted(files.items())
        ).encode("utf-8")
        package_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
            archive.writestr("checksums.sha256", checksums)

    def export_readable_space(self, memory_space_id: str, markdown_path: Path) -> Path:
        """Write a rebuildable human view of the package's implemented object subset."""
        with self.database.session_factory() as session:
            space = session.get(MemorySpaceRecord, memory_space_id)
            if space is None:
                raise KeyError(memory_space_id)
            memories = session.scalars(
                select(MemoryObjectRecord)
                .where(
                    MemoryObjectRecord.memory_space_id == memory_space_id,
                    MemoryObjectRecord.status == "active",
                )
                .order_by(MemoryObjectRecord.type, MemoryObjectRecord.updated_at.desc())
            ).all()
        markdown_path.write_bytes(self._readable_markdown(space, memories, with_bom=True))
        return markdown_path

    def inspect(self, package_path: Path) -> ProtocolReport:
        with zipfile.ZipFile(package_path) as archive:
            infos = archive.infolist()
            self._validate_archive_limits(infos)
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ProtocolIntegrityError("Package contains duplicate paths")
            self._validate_paths(names)
            required = {
                "manifest.json",
                "checksums.sha256",
                "data/memory-space.json",
                "data/memories.jsonl",
                "data/revisions.jsonl",
                "data/tombstones.jsonl",
            }
            if not required <= set(names):
                raise ProtocolIntegrityError("Package is missing required files")
            expected_checksums = self._parse_checksums(
                archive.read("checksums.sha256").decode("utf-8")
            )
            payload_names = set(names) - {"checksums.sha256"}
            if set(expected_checksums) != payload_names:
                raise ProtocolIntegrityError(
                    "Checksum manifest has missing required entries or unknown entries"
                )
            for name, expected in expected_checksums.items():
                actual = hashlib.sha256(archive.read(name)).hexdigest()
                if actual != expected:
                    raise ProtocolIntegrityError(f"Checksum mismatch: {name}")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("protocol") != "personal-memory":
                raise ProtocolIntegrityError("Unsupported protocol identifier")
            if manifest.get("protocol_version") != self.protocol_version:
                raise ProtocolIntegrityError("Unsupported protocol version")
            space_data = json.loads(archive.read("data/memory-space.json"))
            memory_space_id = manifest.get("memory_space_id")
            if not isinstance(space_data, dict) or space_data.get("id") != memory_space_id:
                raise ProtocolIntegrityError("MemorySpace identity mismatch")
            memories = self._read_jsonl(archive.read("data/memories.jsonl"))
            self._validate_memory_records(memories, str(memory_space_id))
            revisions = self._read_jsonl(archive.read("data/revisions.jsonl"))
            relations = (
                self._read_jsonl(archive.read("data/relations.jsonl"))
                if "data/relations.jsonl" in names
                else []
            )
            tombstones = self._read_jsonl(archive.read("data/tombstones.jsonl"))
            self._validate_references(
                memories, revisions, relations, tombstones, str(memory_space_id)
            )
            contents = manifest.get("contents")
            if isinstance(contents, dict):
                declared = {
                    "memories": len(memories),
                    "revisions": len(revisions),
                    "relations": len(relations),
                }
                for key, count in declared.items():
                    if key in contents and contents[key] != count:
                        raise ProtocolIntegrityError(f"Manifest count mismatch: {key}")
            return ProtocolReport(
                valid=True,
                memory_count=len(memories),
                memory_space_id=manifest["memory_space_id"],
            )

    def restore(self, package_path: Path) -> ProtocolReport:
        report = self.inspect(package_path)
        with self.database.session_factory() as session:
            existing_spaces = session.scalar(select(func.count()).select_from(MemorySpaceRecord))
            existing_memories = session.scalar(
                select(func.count()).select_from(MemoryObjectRecord)
            )
        if existing_spaces or existing_memories:
            raise ProtocolRestoreError("Restore target must be empty")

        with zipfile.ZipFile(package_path) as archive:
            space_data = json.loads(archive.read("data/memory-space.json"))
            memory_data = self._read_jsonl(archive.read("data/memories.jsonl"))
            revision_data = self._read_jsonl(archive.read("data/revisions.jsonl"))
            relation_data = (
                self._read_jsonl(archive.read("data/relations.jsonl"))
                if "data/relations.jsonl" in archive.namelist()
                else []
            )
            tombstone_data = self._read_jsonl(archive.read("data/tombstones.jsonl"))

        with self.database.session_factory.begin() as session:
            session.add(
                MemorySpaceRecord(
                    id=space_data["id"],
                    name=space_data["name"],
                    created_at=datetime.fromisoformat(space_data["created_at"]),
                )
            )
            session.flush()
            for data in memory_data:
                session.add(
                    MemoryObjectRecord(
                        id=data["id"],
                        memory_space_id=data["memory_space_id"],
                        type=data["type"],
                        statement=data["statement"],
                        reason=data["reason"],
                        namespace=data["namespace"],
                        sensitivity=data["sensitivity"],
                        epistemic_state=data["epistemic_state"],
                        status=data["status"],
                        semantic_fingerprint=data["semantic_fingerprint"],
                        exact_content_fingerprint=exact_content_fingerprint(
                            data["type"], data["statement"]
                        ),
                        semantic_key=data["semantic_key"],
                        payload=data["payload"],
                        revision=data["revision"],
                        created_at=datetime.fromisoformat(data["created_at"]),
                        updated_at=datetime.fromisoformat(data["updated_at"]),
                    )
                )
            session.flush()
            for data in revision_data:
                session.add(
                    MemoryRevisionRecord(
                        id=data["id"],
                        memory_id=data["memory_id"],
                        revision=data["revision"],
                        snapshot=data["snapshot"],
                        actor_kind=data["actor_kind"],
                        created_at=datetime.fromisoformat(data["created_at"]),
                    )
                )
            for data in relation_data:
                session.add(
                    MemoryRelationRecord(
                        id=data["id"],
                        memory_space_id=data["memory_space_id"],
                        source_memory_id=data["source_memory_id"],
                        target_memory_id=data["target_memory_id"],
                        relation_type=data["relation_type"],
                        origin=data["origin"],
                        validity_state=data["validity_state"],
                        payload=data["payload"],
                        created_at=datetime.fromisoformat(data["created_at"]),
                    )
                )
            for data in tombstone_data:
                session.add(
                    TombstoneRecord(
                        id=data["id"],
                        memory_space_id=space_data["id"],
                        object_id=data["object_id"],
                        object_kind=data["object_kind"],
                        semantic_fingerprint=data["semantic_fingerprint"],
                        deleted_at=datetime.fromisoformat(data["deleted_at"]),
                    )
                )
        return report

    def import_into_space(
        self,
        package_path: Path,
        *,
        memory_space_id: str,
    ) -> ProtocolImportReport:
        self.inspect(package_path)
        with zipfile.ZipFile(package_path) as archive:
            memory_data = self._read_jsonl(archive.read("data/memories.jsonl"))
            revision_data = self._read_jsonl(archive.read("data/revisions.jsonl"))
            relation_data = (
                self._read_jsonl(archive.read("data/relations.jsonl"))
                if "data/relations.jsonl" in archive.namelist()
                else []
            )
            tombstone_data = self._read_jsonl(archive.read("data/tombstones.jsonl"))
        if any(
            contains_secret(
                "\n".join(
                    str(value)
                    for value in (memory.get("statement"), memory.get("reason"))
                    if value
                )
            )
            for memory in memory_data
        ):
            raise ProtocolIntegrityError("Package contains secret-like memory content")

        imported = 0
        skipped_tombstoned = 0
        skipped_existing = 0
        imported_ids: dict[str, str] = {}
        revisions_by_memory: dict[str, list[dict[str, object]]] = {}
        for revision in revision_data:
            revisions_by_memory.setdefault(str(revision["memory_id"]), []).append(revision)

        with self.database.session_factory.begin() as session:
            if session.get(MemorySpaceRecord, memory_space_id) is None:
                raise KeyError(memory_space_id)
            local_tombstones = set(
                session.scalars(
                    select(TombstoneRecord.semantic_fingerprint).where(
                        or_(
                            TombstoneRecord.memory_space_id == memory_space_id,
                            TombstoneRecord.memory_space_id.is_(None),
                        ),
                        TombstoneRecord.semantic_fingerprint.is_not(None)
                    )
                ).all()
            )
            package_tombstones = {
                str(item["semantic_fingerprint"])
                for item in tombstone_data
                if item.get("semantic_fingerprint")
            }
            blocked_fingerprints = local_tombstones | package_tombstones

            for data in memory_data:
                fingerprint_aliases = supported_semantic_fingerprints(
                    str(data["type"]), str(data["namespace"]), str(data["statement"])
                )
                fingerprint = fingerprint_aliases[0]
                if blocked_fingerprints.intersection(fingerprint_aliases):
                    skipped_tombstoned += 1
                    continue
                existing = session.scalar(
                    select(MemoryObjectRecord.id).where(
                        MemoryObjectRecord.memory_space_id == memory_space_id,
                        MemoryObjectRecord.semantic_fingerprint.in_(fingerprint_aliases),
                    )
                )
                if existing is not None:
                    imported_ids[str(data["id"])] = existing
                    skipped_existing += 1
                    continue

                source_id = str(data["id"])
                memory_id = (
                    source_id
                    if session.get(MemoryObjectRecord, source_id) is None
                    else new_id("memory")
                )
                imported_ids[source_id] = memory_id
                session.add(
                    MemoryObjectRecord(
                        id=memory_id,
                        memory_space_id=memory_space_id,
                        type=data["type"],
                        statement=data["statement"],
                        reason=data["reason"],
                        namespace=data["namespace"],
                        sensitivity=data["sensitivity"],
                        epistemic_state=data["epistemic_state"],
                        status=data["status"],
                        semantic_fingerprint=fingerprint,
                        exact_content_fingerprint=exact_content_fingerprint(
                            data["type"], data["statement"]
                        ),
                        semantic_key=data["semantic_key"],
                        payload=data["payload"],
                        revision=data["revision"],
                        created_at=datetime.fromisoformat(str(data["created_at"])),
                        updated_at=datetime.fromisoformat(str(data["updated_at"])),
                    )
                )
                session.flush()
                for revision in revisions_by_memory.get(source_id, []):
                    snapshot = dict(revision["snapshot"])
                    snapshot["id"] = memory_id
                    session.add(
                        MemoryRevisionRecord(
                            id=new_id("revision"),
                            memory_id=memory_id,
                            revision=revision["revision"],
                            snapshot=snapshot,
                            actor_kind=revision["actor_kind"],
                            created_at=datetime.fromisoformat(str(revision["created_at"])),
                        )
                    )
                imported += 1

            for data in relation_data:
                source_id = imported_ids.get(str(data["source_memory_id"]))
                target_id = imported_ids.get(str(data["target_memory_id"]))
                if source_id is None or target_id is None:
                    continue
                existing_relation = session.scalar(
                    select(MemoryRelationRecord.id).where(
                        MemoryRelationRecord.memory_space_id == memory_space_id,
                        MemoryRelationRecord.source_memory_id == source_id,
                        MemoryRelationRecord.target_memory_id == target_id,
                        MemoryRelationRecord.relation_type == str(data["relation_type"]),
                    )
                )
                if existing_relation is not None:
                    continue
                session.add(
                    MemoryRelationRecord(
                        id=new_id("relation"),
                        memory_space_id=memory_space_id,
                        source_memory_id=source_id,
                        target_memory_id=target_id,
                        relation_type=str(data["relation_type"]),
                        origin=str(data["origin"]),
                        validity_state=str(data["validity_state"]),
                        payload=dict(data["payload"]),
                        created_at=datetime.fromisoformat(str(data["created_at"])),
                    )
                )

            for data in tombstone_data:
                fingerprint = data.get("semantic_fingerprint")
                if fingerprint and fingerprint in local_tombstones:
                    continue
                session.add(
                    TombstoneRecord(
                        id=new_id("tombstone"),
                        memory_space_id=memory_space_id,
                        object_id=imported_ids.get(
                            str(data["object_id"]), str(data["object_id"])
                        ),
                        object_kind=str(data["object_kind"]),
                        semantic_fingerprint=(str(fingerprint) if fingerprint else None),
                        deleted_at=datetime.fromisoformat(str(data["deleted_at"])),
                    )
                )

        return ProtocolImportReport(
            imported=imported,
            skipped_tombstoned=skipped_tombstoned,
            skipped_existing=skipped_existing,
        )

    @classmethod
    def _validate_archive_limits(cls, infos: list[zipfile.ZipInfo]) -> None:
        if len(infos) > cls.max_members:
            raise ProtocolIntegrityError("Package contains too many files")
        total = 0
        for info in infos:
            if info.flag_bits & 0x1:
                raise ProtocolIntegrityError("Encrypted package members are unsupported")
            if info.file_size > cls.max_member_bytes:
                raise ProtocolIntegrityError(f"Package member is too large: {info.filename}")
            total += info.file_size
        if total > cls.max_package_bytes:
            raise ProtocolIntegrityError("Package expands beyond the allowed size")

    @staticmethod
    def _validate_memory_records(
        memories: list[dict[str, object]], memory_space_id: str
    ) -> None:
        required = {
            "id",
            "memory_space_id",
            "type",
            "statement",
            "reason",
            "namespace",
            "sensitivity",
            "epistemic_state",
            "status",
            "semantic_fingerprint",
            "semantic_key",
            "payload",
            "revision",
            "created_at",
            "updated_at",
        }
        seen: set[str] = set()
        for data in memories:
            if not required <= set(data):
                raise ProtocolIntegrityError("Memory record is missing required fields")
            if data["memory_space_id"] != memory_space_id:
                raise ProtocolIntegrityError("Memory belongs to a different MemorySpace")
            memory_id = str(data["id"])
            if not memory_id or len(memory_id) > 64 or memory_id in seen:
                raise ProtocolIntegrityError("Memory record has an invalid or duplicate id")
            seen.add(memory_id)
            statement = data["statement"]
            reason = data["reason"]
            namespace = data["namespace"]
            if not isinstance(statement, str) or not statement or len(statement) > 2000:
                raise ProtocolIntegrityError("Memory statement is invalid")
            if reason is not None and (not isinstance(reason, str) or len(reason) > 4000):
                raise ProtocolIntegrityError("Memory reason is invalid")
            if not isinstance(namespace, str) or not namespace or len(namespace) > 240:
                raise ProtocolIntegrityError("Memory namespace is invalid")
            if not isinstance(data["payload"], dict):
                raise ProtocolIntegrityError("Memory payload must be an object")
            try:
                memory_type = MemoryType(str(data["type"]))
                Sensitivity(str(data["sensitivity"]))
                EpistemicState(str(data["epistemic_state"]))
                datetime.fromisoformat(str(data["created_at"]))
                datetime.fromisoformat(str(data["updated_at"]))
            except (TypeError, ValueError) as error:
                raise ProtocolIntegrityError(
                    "Memory record contains invalid enums or dates"
                ) from error
            if data["status"] != "active":
                raise ProtocolIntegrityError("Unsupported memory status")
            if data["semantic_fingerprint"] not in supported_semantic_fingerprints(
                memory_type, namespace, statement
            ):
                raise ProtocolIntegrityError("Memory semantic fingerprint mismatch")

    @staticmethod
    def _validate_references(
        memories: list[dict[str, object]],
        revisions: list[dict[str, object]],
        relations: list[dict[str, object]],
        tombstones: list[dict[str, object]],
        memory_space_id: str,
    ) -> None:
        memory_ids = {str(memory["id"]) for memory in memories}
        for revision in revisions:
            required = {
                "id",
                "memory_id",
                "revision",
                "snapshot",
                "actor_kind",
                "created_at",
            }
            if not required <= set(revision):
                raise ProtocolIntegrityError("Revision record is missing required fields")
            if str(revision.get("memory_id")) not in memory_ids:
                raise ProtocolIntegrityError("Revision references an unknown memory")
            if not isinstance(revision.get("snapshot"), dict):
                raise ProtocolIntegrityError("Revision snapshot must be an object")
            try:
                datetime.fromisoformat(str(revision["created_at"]))
            except ValueError as error:
                raise ProtocolIntegrityError("Revision date is invalid") from error
        for relation in relations:
            required = {
                "id",
                "memory_space_id",
                "source_memory_id",
                "target_memory_id",
                "relation_type",
                "origin",
                "validity_state",
                "payload",
                "created_at",
            }
            if not required <= set(relation):
                raise ProtocolIntegrityError("Relation record is missing required fields")
            if relation["memory_space_id"] != memory_space_id:
                raise ProtocolIntegrityError("Relation belongs to a different MemorySpace")
            if {
                str(relation["source_memory_id"]),
                str(relation["target_memory_id"]),
            } - memory_ids:
                raise ProtocolIntegrityError("Relation references an unknown memory")
            if not isinstance(relation["payload"], dict):
                raise ProtocolIntegrityError("Relation payload must be an object")
            try:
                datetime.fromisoformat(str(relation["created_at"]))
            except ValueError as error:
                raise ProtocolIntegrityError("Relation date is invalid") from error
        for tombstone in tombstones:
            scope = tombstone.get("memory_space_id", memory_space_id)
            if scope != memory_space_id:
                raise ProtocolIntegrityError("Tombstone belongs to a different MemorySpace")

    @staticmethod
    def _validate_paths(names: list[str]) -> None:
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ProtocolIntegrityError(f"Unsafe package path: {name}")

    @staticmethod
    def _parse_checksums(content: str) -> dict[str, str]:
        checksums: dict[str, str] = {}
        for line in content.splitlines():
            digest, separator, name = line.partition("  ")
            if not separator or len(digest) != 64 or not name:
                raise ProtocolIntegrityError("Invalid checksum manifest")
            checksums[name] = digest
        return checksums

    @staticmethod
    def _read_jsonl(content: bytes) -> list[dict[str, object]]:
        records = []
        for line in content.decode("utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ProtocolIntegrityError("JSONL record must be an object")
                records.append(value)
        return records

    @staticmethod
    def _memory_dict(memory: MemoryObjectRecord) -> dict[str, object]:
        return {
            "id": memory.id,
            "memory_space_id": memory.memory_space_id,
            "type": memory.type,
            "statement": memory.statement,
            "reason": memory.reason,
            "namespace": memory.namespace,
            "sensitivity": memory.sensitivity,
            "epistemic_state": memory.epistemic_state,
            "status": memory.status,
            "semantic_fingerprint": memory.semantic_fingerprint,
            "semantic_key": memory.semantic_key,
            "payload": memory.payload,
            "revision": memory.revision,
            "created_at": memory.created_at.isoformat(),
            "updated_at": memory.updated_at.isoformat(),
        }

    @staticmethod
    def _revision_dict(revision: MemoryRevisionRecord) -> dict[str, object]:
        return {
            "id": revision.id,
            "memory_id": revision.memory_id,
            "revision": revision.revision,
            "snapshot": revision.snapshot,
            "actor_kind": revision.actor_kind,
            "created_at": revision.created_at.isoformat(),
        }

    @staticmethod
    def _relation_dict(relation: MemoryRelationRecord) -> dict[str, object]:
        return {
            "id": relation.id,
            "memory_space_id": relation.memory_space_id,
            "source_memory_id": relation.source_memory_id,
            "target_memory_id": relation.target_memory_id,
            "relation_type": relation.relation_type,
            "origin": relation.origin,
            "validity_state": relation.validity_state,
            "payload": relation.payload,
            "created_at": relation.created_at.isoformat(),
        }

    @staticmethod
    def _tombstone_dict(tombstone: TombstoneRecord) -> dict[str, object]:
        return {
            "id": tombstone.id,
            "memory_space_id": tombstone.memory_space_id,
            "object_id": tombstone.object_id,
            "object_kind": tombstone.object_kind,
            "semantic_fingerprint": tombstone.semantic_fingerprint,
            "deleted_at": tombstone.deleted_at.isoformat(),
        }

    @staticmethod
    def _package_readme() -> bytes:
        return (
            b"# PersonalMemory export package\n\n"
            b"This `.pmemory` file is a ZIP-compatible machine-readable migration package, "
            b"not a plain-text document.\n\n"
            b"- Human-readable view: `views/memories.md`\n"
            b"- Current round-trip subset: space, memories, revisions, relations, tombstones\n"
            b"- Not implemented in protocol 0.1: schemas, profiles, subjects, "
            b"evidence, signatures\n"
            b"- Package metadata: `manifest.json`\n"
            b"- Integrity checks: `checksums.sha256`\n"
        )

    @staticmethod
    def _readable_markdown(
        space: MemorySpaceRecord,
        memories: list[MemoryObjectRecord],
        *,
        generated_at: datetime | None = None,
        with_bom: bool = False,
    ) -> bytes:
        local_time = (generated_at or datetime.now(UTC)).astimezone()
        readable_time = local_time.isoformat(timespec="seconds")
        offset = local_time.strftime("%z")
        timezone_label = f"UTC{offset[:3]}:{offset[3:]}"
        lines = [
            "# PersonalMemory 可阅读导出",
            "",
            "> 这是由权威 Memory Core 生成的阅读视图，不能替代 `.pmemory` 恢复包。",
            "",
            f"- 产品版本：{__version__}",
            f"- 构建修订：{__build_revision__}",
            f"- 导出时间：{readable_time}",
            f"- 导出时区：系统本地时间（{timezone_label}）",
            f"- 记忆空间：{space.name} (`{space.id}`)",
            f"- 记忆数量：{len(memories)}",
            "",
        ]
        for index, memory in enumerate(memories, start=1):
            lines.extend(
                [
                    f"## {index}. {memory.type}",
                    "",
                    f"- ID：`{memory.id}`",
                    f"- Namespace：`{memory.namespace}`",
                    f"- Revision：`{memory.revision}`",
                    f"- 状态：`{memory.epistemic_state}` / `{memory.sensitivity}`",
                    "- 内容：",
                    *MemoryProtocol._markdown_quote(memory.statement),
                ]
            )
            if memory.reason:
                lines.extend(["- 理由：", *MemoryProtocol._markdown_quote(memory.reason)])
            lines.append("")
        encoding = "utf-8-sig" if with_bom else "utf-8"
        return ("\n".join(lines).rstrip() + "\n").encode(encoding)

    @staticmethod
    def _markdown_quote(value: str) -> list[str]:
        return [f"  > {line}" for line in value.splitlines() or [""]]
