"""연습 데이터 초기화: POST /api/action-data/reset (서버에서 .env를 불러온 뒤 실행)."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot"))

import actions  # noqa: E402


async def main():
    r = await actions.call("resetMyActionData", {}, None)
    print("reset", r["status"])
    return r["status"]


if __name__ == "__main__":
    sys.exit(0 if 200 <= asyncio.run(main()) < 300 else 1)
