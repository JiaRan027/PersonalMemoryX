"""Evaluate the fixed Stage 42 lexical retrieval baseline on synthetic data."""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from personalmemory.application.context import ContextCompiler
from personalmemory.infrastructure.database.models import (
    MemoryObjectRecord,
    MemorySpaceRecord,
)
from personalmemory.infrastructure.database.session import Database
from personalmemory.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "service" / "tests" / "context" / "fixtures" / "lexical_baseline_v1.json"


def load_fixture(path: Path = FIXTURE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_database(fixture: dict[str, Any], data_dir: Path) -> Database:
    database = Database(Settings(data_dir=data_dir, enable_background_sync=False))
    database.initialize()
    with database.session_factory.begin() as session:
        session.add(MemorySpaceRecord(id="space_default", name="Baseline"))
        session.flush()
        session.add_all(
            MemoryObjectRecord(
                id=memory["id"],
                memory_space_id="space_default",
                type=memory["type"],
                statement=memory["statement"],
                reason=memory["reason"],
                namespace=memory["namespace"],
                sensitivity=memory.get("sensitivity", "personal"),
                epistemic_state="asserted",
                status="active",
                semantic_fingerprint=f"baseline-{memory['id']}",
                payload={},
                revision=1,
            )
            for memory in fixture["memories"]
        )
    return database


def percentile(values: list[float], rank: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * rank) - 1))
    return ordered[index]


def evaluate(fixture: dict[str, Any], database: Database) -> dict[str, Any]:
    compiler = ContextCompiler(database)
    cases: list[dict[str, Any]] = []
    latencies: list[float] = []
    known_expected = 0
    known_hits = 0
    known_returned = 0
    unknown_safe = 0
    receipts: set[tuple[str, str, str, str]] = set()
    for case in fixture["cases"]:
        started = time.perf_counter()
        bundle = compiler.compile(
            case["query"],
            namespaces=case["namespaces"],
            token_budget=800,
            limit=6,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        latencies.append(latency_ms)
        returned = [item["id"] for item in bundle["items"]]
        expected = set(case["expected"])
        hits = expected.intersection(returned)
        if expected:
            known_expected += len(expected)
            known_hits += len(hits)
            known_returned += len(returned)
        elif not returned:
            unknown_safe += 1
        receipt = bundle["delivery_receipt"]
        receipts.add(
            (
                receipt["requested_mode"],
                receipt["effective_mode"],
                receipt["status"],
                receipt["cache_class"],
            )
        )
        cases.append(
            {
                "id": case["id"],
                "expected": sorted(expected),
                "returned": returned,
                "hits": sorted(hits),
                "estimated_tokens": bundle["estimated_tokens"],
                "latency_ms": round(latency_ms, 3),
                "receipt": receipt,
            }
        )
    return {
        "fixture_version": fixture["fixture_version"],
        "mode": "lexical",
        "cache_class": "dynamic",
        "token_budget": 800,
        "case_count": len(cases),
        "known_case_recall": round(known_hits / known_expected, 4)
        if known_expected
        else 0.0,
        "known_case_precision": round(known_hits / known_returned, 4)
        if known_returned
        else 0.0,
        "unknown_safe_cases": unknown_safe,
        "unknown_case_count": sum(not case["expected"] for case in fixture["cases"]),
        "delivery_receipts": [
            {
                "requested_mode": requested,
                "effective_mode": effective,
                "status": status,
                "cache_class": cache_class,
            }
            for requested, effective, status, cache_class in sorted(receipts)
        ],
        "latency_ms": {
            "p50": round(statistics.median(latencies), 3),
            "p95": round(percentile(latencies, 0.95), 3),
            "max": round(max(latencies), 3),
        },
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    fixture = load_fixture()
    with tempfile.TemporaryDirectory(prefix="personalmemory-lexical-baseline-") as raw:
        database = build_database(fixture, Path(raw))
        try:
            result = evaluate(fixture, database)
        finally:
            database.dispose()
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
