from __future__ import annotations

import json

from personalmemory.application.review_assistance import (
    REVIEW_ASSISTANCE_FILE_NAME,
    REVIEW_ASSISTANCE_SCHEMA_VERSION,
    load_review_assistance,
)

from service.tests.api.test_memories_create import auth_headers, make_client


def test_review_assistance_is_display_only_and_fail_closed(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / REVIEW_ASSISTANCE_FILE_NAME
    path.write_text(
        json.dumps(
            {
                "schema_version": REVIEW_ASSISTANCE_SCHEMA_VERSION,
                "locale": "zh-CN",
                "items": {
                    "memory_valid": {
                        "summary": "运行日志不是长期记忆。",
                        "explanation": "原文描述的是一次排障过程。",
                        "recommendation": "remove",
                        "recommendation_reason": "来源仍保留在证据层。",
                    },
                    "memory_invalid": {
                        "summary": "缺少有效建议",
                        "recommendation": "guess",
                        "recommendation_reason": "无效枚举值",
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = load_review_assistance(data_dir)

    assert result == {
        "memory_valid": {
            "locale": "zh-CN",
            "summary": "运行日志不是长期记忆。",
            "explanation": "原文描述的是一次排障过程。",
            "recommendation": "remove",
            "recommendation_reason": "来源仍保留在证据层。",
        }
    }

    path.write_text("{not-json", encoding="utf-8")
    assert load_review_assistance(data_dir) == {}


def test_memory_list_attaches_review_assistance_without_changing_memory(tmp_path) -> None:
    with make_client(tmp_path) as client:
        created = client.post(
            "/api/v1/memories",
            headers=auth_headers(),
            json={"type": "insight", "statement": "Operational note in English."},
        ).json()["memory"]
        path = client.app.state.settings.data_dir / REVIEW_ASSISTANCE_FILE_NAME
        path.write_text(
            json.dumps(
                {
                    "schema_version": REVIEW_ASSISTANCE_SCHEMA_VERSION,
                    "locale": "zh-CN",
                    "items": {
                        created["id"]: {
                            "summary": "这是一条英文运维记录。",
                            "recommendation": "remove",
                            "recommendation_reason": "不属于长期用户记忆。",
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        listed = client.get("/api/v1/memories", headers=auth_headers()).json()["items"][0]

    assert listed["statement"] == "Operational note in English."
    assert listed["revision"] == 1
    assert listed["review_assistance"] == {
        "locale": "zh-CN",
        "summary": "这是一条英文运维记录。",
        "explanation": None,
        "recommendation": "remove",
        "recommendation_reason": "不属于长期用户记忆。",
    }
