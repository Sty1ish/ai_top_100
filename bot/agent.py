"""규칙에 걸리지 않은 자유 질문을 로컬 ollama(Gemma 4)로 답한다.

CPU 전용이라 느리므로 답변 길이를 제한하고, 구조화(JSON) 출력은 기본으로 끈다(STRUCTURED=1로 켬).
"""

import json
import logging
import os
from collections import defaultdict, deque

import httpx

from render import REPLY_SCHEMA

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.getenv("MODEL", "gemma4:e2b")
STRUCTURED = os.getenv("STRUCTURED") == "1"
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "120"))

log = logging.getLogger("bot")

SYSTEM_PROMPT = """당신은 교동마을(제철 농산물)과 갯마을(수산물) 조합 업무를 돕는 AI입니다.
한국어로 2~3문장 이내로 짧게 답합니다. 모르면 지어내지 말고 모른다고 합니다.
숫자 계산은 하지 말고 "정산: 품목 수량 단가" 형식으로 물어보라고 안내합니다."""

STRUCTURED_GUIDE = """
답변은 JSON으로 합니다. text(핵심 답변), list(나열), table(항목:값), cards(비교), suggestions(10자 이내 버튼 0~3개).
필요 없는 필드는 null로 둡니다."""

# 사용자별 최근 대화 몇 턴만 기억한다
_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=4))


async def answer(user_id: str, utterance: str) -> dict:
    history = _history[user_id]

    system = SYSTEM_PROMPT + (STRUCTURED_GUIDE if STRUCTURED else "")
    messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": utterance}]

    body = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": -1,
        "options": {"num_predict": MAX_TOKENS * (3 if STRUCTURED else 1), "num_ctx": 2048},
    }

    if STRUCTURED:
        body["format"] = REPLY_SCHEMA

    async with httpx.AsyncClient(timeout=240) as client:
        res = await client.post(f"{OLLAMA_URL}/api/chat", json=body)
        res.raise_for_status()

    content = res.json()["message"]["content"].strip()
    reply = {"text": content}

    if STRUCTURED:
        try:
            reply = json.loads(content)
        except json.JSONDecodeError:
            log.warning("structured reply parse failed, falling back to text")

    history.append({"role": "user", "content": utterance})
    history.append({"role": "assistant", "content": reply.get("text", "")})

    return reply
