from __future__ import annotations

import platform
import random
import statistics
import tempfile
import time
from pathlib import Path

from personalmemory.application.context import ContextCompiler
from personalmemory.infrastructure.database.models import MemoryObjectRecord, MemorySpaceRecord
from personalmemory.infrastructure.database.search import MemorySearchIndex
from personalmemory.infrastructure.database.session import Database
from personalmemory.settings import Settings


def build_database(row_count: int) -> tuple[Database, MemorySearchIndex]:
    tmp_dir = Path(tempfile.mkdtemp(prefix="personalmemory-bench-"))
    database = Database(Settings(data_dir=tmp_dir))
    database.initialize()
    with database.session_factory.begin() as session:
        session.add(MemorySpaceRecord(id="space_default", name="Default"))
        session.flush()
        session.add_all(
            MemoryObjectRecord(
                id=f"memory_{i:05d}",
                memory_space_id="space_default",
                type="insight",
                statement=(
                    f"Memory object {i} about durable personal experience token{i} "
                    f"shared across providers; 中文项目{i}强调跨 AI 平台长期经验资产"
                ),
                reason="Benchmark fixture",
                namespace="general",
                sensitivity="personal",
                epistemic_state="asserted",
                status="active",
                semantic_fingerprint=f"fingerprint-{i:05d}",
                payload={},
                revision=1,
            )
            for i in range(row_count)
        )
    index = MemorySearchIndex(database)
    return database, index


def main() -> None:
    row_count = 10_000
    query_count = 300
    rng = random.Random(20260731)

    started = time.perf_counter()
    database, index = build_database(row_count)
    build_seconds = time.perf_counter() - started

    # Mixture of queries: rare token (1 hit), common phrase (10k hits), and no-match.
    rare = [f"token{rng.randrange(row_count)}" for _ in range(query_count // 3)]
    common = ["durable personal" for _ in range(query_count // 3)]
    none = ["nonexistentmarkerzz" for _ in range(query_count - len(rare) - len(common))]
    queries = rare + common + none
    rng.shuffle(queries)

    english_results: list[tuple[float, int]] = []
    index.search_ids("warmup")
    for query in queries:
        t0 = time.perf_counter()
        hits = len(index.search_ids(query))
        english_results.append((time.perf_counter() - t0, hits))

    chinese_rare = [
        f"中文项目{rng.randrange(row_count)} 长期 经验"
        for _ in range(query_count // 3)
    ]
    chinese_common = [
        "跨 AI 平台 长期 经验 资产" for _ in range(query_count // 3)
    ]
    chinese_none = [
        "不存在 中文标记"
        for _ in range(query_count - len(chinese_rare) - len(chinese_common))
    ]
    chinese_queries = chinese_rare + chinese_common + chinese_none
    rng.shuffle(chinese_queries)
    chinese_results: list[tuple[float, int]] = []
    for query in chinese_queries:
        t0 = time.perf_counter()
        hits = len(index.search_ids(query))
        chinese_results.append((time.perf_counter() - t0, hits))

    compiler = ContextCompiler(database)
    context_results: list[tuple[float, int]] = []
    context_tokens: list[int] = []
    for query in chinese_queries:
        t0 = time.perf_counter()
        bundle = compiler.compile(
            query,
            namespaces=["general"],
            token_budget=1600,
            limit=10,
        )
        context_results.append((time.perf_counter() - t0, len(bundle["items"])))
        context_tokens.append(bundle["estimated_tokens"])

    def summarize(results: list[tuple[float, int]]) -> tuple[float, float, float]:
        latencies = sorted(seconds for seconds, _ in results)
        return (
            statistics.median(latencies) * 1000,
            latencies[int(len(latencies) * 0.95) - 1] * 1000,
            latencies[-1] * 1000,
        )

    english_p50, english_p95, english_max = summarize(english_results)
    chinese_p50, chinese_p95, chinese_max = summarize(chinese_results)
    context_p50, context_p95, context_max = summarize(context_results)
    english_hit_counts = [count for _, count in english_results]
    chinese_hit_counts = [count for _, count in chinese_results]
    context_item_counts = [count for _, count in context_results]

    print(f"machine: {platform.machine()} / {platform.processor()}")
    print(f"rows: {row_count}, queries per mode: {query_count}")
    print(f"build + insert seconds: {build_seconds:.2f}")
    print(
        "english FTS hit counts: "
        f"min={min(english_hit_counts)} max={max(english_hit_counts)}"
    )
    print(
        "english FTS latency: "
        f"P50={english_p50:.3f} P95={english_p95:.3f} max={english_max:.3f} ms"
    )
    print(
        "chinese substring hit counts: "
        f"min={min(chinese_hit_counts)} max={max(chinese_hit_counts)}"
    )
    print(
        "chinese substring latency: "
        f"P50={chinese_p50:.3f} P95={chinese_p95:.3f} max={chinese_max:.3f} ms"
    )
    print(
        "context compiler items: "
        f"min={min(context_item_counts)} max={max(context_item_counts)}; "
        f"estimated tokens max={max(context_tokens)} / 1600"
    )
    print(
        "context compiler latency: "
        f"P50={context_p50:.3f} P95={context_p95:.3f} max={context_max:.3f} ms"
    )
    database.dispose()


if __name__ == "__main__":
    main()
