"""결정 모델(Julia-1, llama.cpp /v1/systemone)로 사용자 의도를 분류한다.

생성하지 않고 선택지별 확률만 돌려주므로 CPU에서도 1~2초 안에 끝난다.
서버가 꺼져 있거나 느리면 None을 돌려 호출한 쪽이 LLM으로 넘기게 한다.
"""

import logging
import os

import httpx

DECIDE_URL = os.getenv("DECIDE_URL", "http://127.0.0.1:8091/v1/systemone")
TIMEOUT = float(os.getenv("DECIDE_TIMEOUT", "3"))

log = logging.getLogger("bot")


async def choose(message: str, criteria: dict[str, str], instructions: str = "사용자 요청의 종류") -> tuple[str, float] | None:
    body = {
        "state": {"message": message},
        "questions": {"intent": {"type": "choice", "instructions": instructions, "criteria": criteria}},
    }

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            res = await client.post(DECIDE_URL, json=body)
            res.raise_for_status()
    except Exception as e:
        log.warning("decide unavailable: %s", e)

        return None

    answer = res.json()["answers"]["intent"]
    choice = answer["choice"]

    return choice, answer["probabilities"][choice]
