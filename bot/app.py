"""카카오톡 봇 웹훅 서버. nginx가 https://naeae.cloud/kakao/ 를 이 서버(8090)로 넘긴다.

카카오는 웹훅에 1초 안에 2xx를 요구하고, 답장은 콜백 토큰(5분 유효)으로 따로 보낸다.
그래서 204를 먼저 돌려주고, 답변 생성과 콜백 호출은 백그라운드에서 한다.
"""

import asyncio
import json
import logging
import os
import re
import time
from collections import OrderedDict

from fastapi import FastAPI, Request, Response

import agent
import kakao

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

WEBHOOK_SECRET_HEADER = "x-bot-secret"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# 심사는 60초 안에 답이 없으면 케이스를 끝낸다. 에이전트는 45초 안에 답하고, 여기서는 그보다 조금 더 기다린다
ANSWER_DEADLINE = 55

FALLBACK = "확인에 시간이 걸리고 있어요. 잠시 후 다시 한 번 말씀해 주세요."

_IMAGE_URL = re.compile(r"https?://\S+?\.(?:jpe?g|png|gif|webp)(?:\?\S*)?", re.I)
_STD_KEYS = {"id", "content", "isChannelChatroom", "botGroupKey", "botUserKey", "timestamp", "callbackToken"}

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

    payload = await request.json()
    event = kakao.parse_event(payload)

    if event and not _is_duplicate(event.message_id):
        log.info("event=%s user=%s group=%s content=%r", event.type, event.user_key, event.group_key, event.content[:100])
        images = _image_urls(payload) if event.type == "MESSAGE_CREATE" else []

        # 사진·음성 등 텍스트가 아닌 메시지의 모양을 알아내기 위해 원본을 남긴다 (콜백 토큰은 가림)
        d = payload.get("d") or {}
        if event.type == "MESSAGE_CREATE" and (images or not event.content or set(d) - _STD_KEYS or "http" in event.content):
            raw = {**payload, "d": {k: ("***" if k == "callbackToken" else v) for k, v in d.items()}}
            log.info("raw non-text payload: %s", json.dumps(raw, ensure_ascii=False)[:2000])

        asyncio.create_task(_handle(event, time.monotonic(), images))

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


def _image_urls(payload: dict) -> list[str]:
    """본문이나 페이로드 어디든 이미지 URL이 있으면 모은다."""
    found: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and v.startswith("http") and ("image" in k.lower() or _IMAGE_URL.fullmatch(v)):
                    found.append(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            found.extend(_IMAGE_URL.findall(node))

    walk(payload.get("d") or {})

    return list(dict.fromkeys(found))


async def _handle(event: kakao.Event, received_at: float, images: list[str] | None = None):
    if not event.callback_token:
        return

    # 입장 인사는 보내지 않는다 (말풍선 수·글자 수가 점수에 들어감)
    if event.type != "MESSAGE_CREATE" or not (event.content or images):
        return

    try:
        remaining = ANSWER_DEADLINE - (time.monotonic() - received_at)
        result = await asyncio.wait_for(
            agent.answer(event.user_key or event.group_key, event.content, images, received_at), remaining
        )
        text = result["text"]
        log.info("answered in %.2fs: %r", time.monotonic() - received_at, text[:200])
    except Exception:
        log.exception("answer failed")
        text = FALLBACK

    await _send(event, kakao.text_response(text))


async def _send(event: kakao.Event, response: dict):
    try:
        res = await kakao.send_callback(event.callback_token, response)
        log.info("callback status=%s body=%s", res.status_code, res.text[:200])

        if res.status_code != 200:
            log.warning("rejected response: %s", str(response)[:500])
    except Exception:
        log.exception("callback failed")
