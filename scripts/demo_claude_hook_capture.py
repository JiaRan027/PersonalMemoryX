from __future__ import annotations

import argparse
import json

from personalmemory.agent_hook import build_claude_hook_settings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render a reviewable project-level Claude Code hook configuration."
    )
    parser.add_argument("--executable", required=True)
    parser.add_argument("--connector-id", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            build_claude_hook_settings(args.executable, args.connector_id),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
