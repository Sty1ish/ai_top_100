"""카카오 없이 에이전트를 프로세스 안에서 돌려 본다 (서버에서 실행).

사용법: cd ~/ai_top_100 && set -a && . ./.env && set +a && .venv/bin/python tools/chat_local.py <user> "메시지1" "메시지2" ...
        메시지 대신 -f 파일을 주면 줄마다 한 메시지로 읽는다.
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot"))

import agent  # noqa: E402

logging.basicConfig(level=logging.WARNING)


async def main(user: str, messages: list[str]):
    for msg in messages:
        t0 = time.monotonic()
        res = await agent.answer(user, msg)
        tr = res["trace"]

        print(f"\n고객> {msg}")
        print(f"봇 ({time.monotonic() - t0:.1f}s, {len(res['text'])}자)> {res['text']}")
        print(f"  분류={tr['classify']} ({tr.get('classify_sec')}s) 누적={tr['categories']}")

        for c in tr["calls"]:
            print(f"  도구 {c['name']} {json.dumps(c['args'], ensure_ascii=False)} -> {c['status']} ({c['sec']}s)")

        for u in tr["usage"]:
            print(f"  토큰 {u}")


if __name__ == "__main__":
    user, rest = sys.argv[1], sys.argv[2:]

    if rest[:1] == ["-f"]:
        rest = [l.strip() for l in Path(rest[1]).read_text(encoding="utf-8").splitlines() if l.strip()]

    asyncio.run(main(user, rest))
