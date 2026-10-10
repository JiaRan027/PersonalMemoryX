"""Bounded transport receipts; no prompts, responses, or memory content."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

_LOCK = Lock()


def load_hook_receipts(data_dir: Path) -> dict[str, dict[str, str]]:
    path = data_dir / "runtime" / "hook-receipts.json"
    try:
        if path.stat().st_size > 512_000:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return {}
        return {
            key: {
                event: stamp
                for event, stamp in item.items()
                if event in {"user_message", "assistant_message"} and isinstance(stamp, str)
            }
            for key, item in value.items()
            if isinstance(key, str) and isinstance(item, dict)
        }
    except (OSError, ValueError):
        return {}


def record_hook_receipt(data_dir: Path, connector_id: str, fragment_kind: str) -> None:
    if fragment_kind not in {"user_message", "assistant_message"}:
        return
    with _LOCK:
        receipts = load_hook_receipts(data_dir)
        receipt = receipts.pop(connector_id, {})
        receipt[fragment_kind] = datetime.now(UTC).isoformat()
        receipts[connector_id] = receipt
        receipts = dict(list(receipts.items())[-1000:])
        folder = data_dir / "runtime"
        folder.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=folder, delete=False
            ) as stream:
                temporary = stream.name
                json.dump(receipts, stream)
            os.replace(temporary, folder / "hook-receipts.json")
            temporary = None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
