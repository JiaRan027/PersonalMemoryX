"""Create a read-only, revision-bound review gold set from a running service.

The script deliberately does not call review or mutation endpoints. It combines
the service's triage/provenance/quality view with the optional external review
assistance and local user decisions, then writes a deterministic JSON snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_ENDPOINT = "http://127.0.0.1:8000"
DEFAULT_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PersonalMemoryData"
SCHEMA_VERSION = "personalmemory.review-gold-set.v1"
PROJECT_TIMEZONE = "Asia/Shanghai (UTC+08:00)"


def _json_request(endpoint: str, token: str) -> dict[str, Any]:
    request = Request(
        endpoint.rstrip("/") + "/api/v1/memories?limit=1000",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310 - endpoint is explicit local input
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise RuntimeError(f"cannot read memories from {endpoint}: {error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise RuntimeError("service returned an invalid memories payload")
    return payload


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read {path}: {error}") from error
    return value if isinstance(value, dict) else default


def _external_recommendation(assistance: dict[str, Any], memory_id: str) -> str | None:
    item = assistance.get("items", {}).get(memory_id)
    if not isinstance(item, dict):
        return None
    recommendation = item.get("recommendation")
    return recommendation if isinstance(recommendation, str) and recommendation else None


def _stable_sample(items: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """Pick the same records on every run without depending on row ordering."""
    ranked = sorted(
        items,
        key=lambda item: hashlib.sha256(str(item["id"]).encode("utf-8")).hexdigest(),
    )
    return ranked[:count]


def _selection_reasons(
    item: dict[str, Any], assistance: dict[str, Any]
) -> list[str]:
    triage = item.get("review_triage") or {}
    issues = item.get("quality_issues") or []
    reason_codes = {str(code) for code in triage.get("reason_codes", [])}
    issue_codes = {
        str(issue.get("code"))
        for issue in issues
        if isinstance(issue, dict) and issue.get("code")
    }
    reasons: list[str] = []
    if triage.get("overridden_by_user"):
        reasons.append("USER_OVERRIDE")
    if triage.get("requires_user_review"):
        reasons.append("HIGH_RISK_EXCEPTION")
    recommendation = _external_recommendation(assistance, str(item["id"]))
    suggested = triage.get("suggested_verdict")
    if recommendation and suggested and recommendation != suggested:
        reasons.append("SYSTEM_EXTERNAL_DISAGREEMENT")
    if reason_codes & {
        "AGENT_WORKFLOW_NOTE",
        "OPERATIONAL_INDEX",
        "TRANSIENT_PROCESS",
        "TEST_SOURCE",
        "NON_CANONICAL_DUPLICATE",
    }:
        reasons.append("HISTORICAL_POLLUTION_OR_DUPLICATE")
    if issue_codes & {
        "AGENT_WORKFLOW_NOTE",
        "OPERATIONAL_INDEX",
        "CROSS_NAMESPACE_EXACT_DUPLICATE",
    }:
        reasons.append("QUALITY_ISSUE")
    return reasons


def build_gold_set(
    memories: list[dict[str, Any]],
    *,
    assistance: dict[str, Any],
    decisions: dict[str, Any],
    sample_per_type: int,
) -> dict[str, Any]:
    selected: dict[str, dict[str, Any]] = {}

    def add(item: dict[str, Any], reasons: list[str]) -> None:
        memory_id = str(item.get("id", ""))
        if not memory_id:
            return
        record = selected.setdefault(
            memory_id,
            {
                "id": memory_id,
                "statement": item.get("statement"),
                "type": item.get("type"),
                "namespace": item.get("namespace"),
                "revision": item.get("revision"),
                "suggested_verdict": (item.get("review_triage") or {}).get("suggested_verdict"),
                "effective_verdict": (item.get("review_triage") or {}).get("effective_verdict"),
                "review_triage": item.get("review_triage"),
                "review_decision": decisions.get(memory_id) or item.get("review_decision"),
                "review_assistance": (assistance.get("items") or {}).get(memory_id),
                "quality_issues": item.get("quality_issues") or [],
                "provenance": item.get("provenance") or {},
                "selection_reasons": [],
            },
        )
        for reason in reasons:
            if reason not in record["selection_reasons"]:
                record["selection_reasons"].append(reason)

    for item in memories:
        reasons = _selection_reasons(item, assistance)
        if reasons:
            add(item, reasons)

    strata: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in memories:
        if str(item.get("id")) not in selected:
            strata[str(item.get("type") or "unknown")].append(item)
    for memory_type, items in sorted(strata.items()):
        for item in _stable_sample(items, sample_per_type):
            add(item, [f"STRATIFIED_SAMPLE:{memory_type}"])

    output_items = sorted(selected.values(), key=lambda item: str(item["id"]))
    counts: dict[str, int] = defaultdict(int)
    for item in output_items:
        counts[str(item.get("effective_verdict") or "unknown")] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(),
        "timezone": PROJECT_TIMEZONE,
        "source": {"kind": "running_service_snapshot", "memory_total": len(memories)},
        "selection_policy": {
            "all_high_risk_exceptions": True,
            "system_external_disagreements": True,
            "historical_pollution_or_duplicates": True,
            "sample_per_type": sample_per_type,
            "mutates_memory": False,
        },
        "summary": {
            "gold_set_size": len(output_items),
            "effective_verdict_counts": dict(sorted(counts.items())),
            "user_override_count": sum(
                1
                for item in output_items
                if (item.get("review_triage") or {}).get("overridden_by_user")
            ),
        },
        "items": output_items,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--endpoint", default=os.environ.get("PERSONALMEMORY_ENDPOINT", DEFAULT_ENDPOINT)
    )
    parser.add_argument("--token", default=os.environ.get("PERSONALMEMORY_API_TOKEN", ""))
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("PERSONALMEMORY_DATA_DIR", DEFAULT_DATA_DIR)),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-per-type", type=int, default=2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.token:
        connection_path = args.data_dir / "runtime" / "connection.json"
        connection = _load_json(connection_path, {})
        args.endpoint = str(connection.get("endpoint") or args.endpoint)
        args.token = str(connection.get("token") or "")
    if not args.token:
        print(
            "missing API token; pass --token or configure runtime/connection.json",
            file=sys.stderr,
        )
        return 2
    if args.sample_per_type < 0:
        print("--sample-per-type must be >= 0", file=sys.stderr)
        return 2

    payload = _json_request(args.endpoint, args.token)
    assistance = _load_json(args.data_dir / "review-assistance.zh-CN.json", {"items": {}})
    decisions_payload = _load_json(args.data_dir / "review-decisions.json", {"items": {}})
    decisions = decisions_payload.get("items", {})
    if not isinstance(decisions, dict):
        decisions = {}
    result = build_gold_set(
        [item for item in payload["items"] if isinstance(item, dict)],
        assistance=assistance,
        decisions=decisions,
        sample_per_type=args.sample_per_type,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
