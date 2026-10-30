"""카카오톡 봇 웹훅 서버. nginx가 https://naeae.cloud/kakao/ 를 이 서버(8090)로 넘긴다.

카카오는 웹훅에 1초 안에 2xx를 요구하고, 답장은 콜백 토큰(5분 유효)으로 따로 보낸다.
그래서 204를 먼저 돌려주고, 답변 생성과 콜백 호출은 백그라운드에서 한다.
"""

import asyncio
import logging
import os
import time
from collections import OrderedDict

from fastapi import FastAPI, Request, Response

import agent
import kakao
import render
import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

WEBHOOK_SECRET_HEADER = "x-bot-secret"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# 콜백 토큰 만료(5분)보다 여유 있게 끊는다
ANSWER_DEADLINE = 270

FALLBACK = "지금 요청이 많아 답변이 늦어지고 있어요. 잠시 후 다시 물어봐 주세요."
GREETING = "안녕하세요! 교동마을·갯마을 조합 업무를 돕는 AI 에이전트입니다. 궁금한 것을 물어보세요."

# CPU 하나로 모델을 돌리므로 한 번에 하나씩만 생성한다
llm_lock = asyncio.Semaphore(1)

_seen_ids: OrderedDict[str, None] = OrderedDict()

app = FastAPI()


@app.get("/health")
async def health():
    return {"ok": True, "model": agent.MODEL, "bot_token": bool(kakao.BOT_TOKEN)}


@app.post("/webhook")
async def webhook(request: Request):
    if WEBHOOK_SECRET and request.headers.get(WEBHOOK_SECRET_HEADER) != WEBHOOK_SECRET:
        log.warning("webhook rejected: bad secret header")

        return Response(status_code=401)

    event = kakao.parse_event(await request.json())

    if event and not _is_duplicate(event.message_id):
        log.info("event=%s user=%s group=%s content=%r", event.type, event.user_key, event.group_key, event.content[:100])
        asyncio.create_task(_handle(event, time.monotonic()))

    return Response(status_code=204)


def _is_duplicate(message_id: str | None) -> bool:
    if message_id is None:
        return False

    if message_id in _seen_ids:
        return True

    _seen_ids[message_id] = None

    if len(_seen_ids) > 1000:
        _seen_ids.popitem(last=False)

    return False


async def _handle(event: kakao.Event, received_at: float):
    if not event.callback_token:
        return

    if event.type in ("ENTRANCE", "CHAT_JOIN"):
        await _send(event, kakao.text_response(GREETING))

        return

    if event.type != "MESSAGE_CREATE" or not event.content:
        return

    # 빠른 경로: 규칙에 걸리면 LLM 없이 즉답
    try:
        if (response := await router.handle(event.content)) is not None:
            log.info("fast path answered in %.2fs", time.monotonic() - received_at)
            await _send(event, response)

            return
    except Exception:
        log.exception("router failed, falling back to LLM")

    try:
        async with llm_lock:
            remaining = ANSWER_DEADLINE - (time.monotonic() - received_at)
            reply = await asyncio.wait_for(agent.answer(event.user_key or event.group_key, event.content), remaining)

        response = render.to_skill_response(reply)
    except Exception:
        log.exception("answer failed")
        response = kakao.text_response(FALLBACK)

    await _send(event, response)


async def _send(event: kakao.Event, response: dict):
    try:
        res = await kakao.send_callback(event.callback_token, response)
        log.info("callback status=%s body=%s", res.status_code, res.text[:200])

        if res.status_code != 200:
            log.warning("rejected response: %s", str(response)[:500])
    except Exception:
        log.exception("callback failed")
