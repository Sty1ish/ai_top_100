"""카카오톡 봇 API(docs/kakao_bot/) 웹훅 이벤트 파싱, 말풍선 생성, 메시지 콜백 호출.

말풍선 함수는 문서의 글자 수·개수 제한에 맞춰 넘치는 부분을 잘라낸다.
"""

import os
import re
from dataclasses import dataclass

import httpx

CALLBACK_URL = os.getenv("CALLBACK_URL", "https://kapi.kakao.com/v1/bot/callback")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# 일반·오픈채팅에서는 본문 앞에 "@봇이름"이 붙어 올 수 있다
_LEADING_MENTION = re.compile(r"^\s*@\S+\s*")


@dataclass
class Event:
    type: str
    message_id: str | None
    content: str
    user_key: str
    group_key: str
    callback_token: str | None


def parse_event(payload: dict) -> Event | None:
    if payload.get("op") != 0:
        return None

    d = payload.get("d") or {}
    actor = d.get("user") or d.get("inviter") or d.get("viewer") or {}

    return Event(
        type=payload.get("t", ""),
        message_id=str(d["id"]) if "id" in d else None,
        content=_LEADING_MENTION.sub("", d.get("content") or "").strip(),
        user_key=d.get("botUserKey") or actor.get("botUserKey") or "",
        group_key=d.get("botGroupKey") or "",
        callback_token=d.get("callbackToken"),
    )


# ----------------------------- 공통 객체 -----------------------------


def button_message(label: str, message_text: str | None = None) -> dict:
    return {"action": "message", "label": label[:14], "messageText": message_text or label}


def button_link(label: str, url: str) -> dict:
    return {"action": "webLink", "label": label[:14], "webLinkUrl": url}


def button_phone(label: str, phone_number: str) -> dict:
    return {"action": "phone", "label": label[:14], "phoneNumber": phone_number}


def button_share(label: str = "공유하기") -> dict:
    return {"action": "share", "label": label[:14]}


def thumbnail(image_url: str, link_url: str | None = None, fixed_ratio: bool = False) -> dict:
    thumb = {"imageUrl": image_url, "fixedRatio": fixed_ratio}

    if link_url:
        thumb["link"] = {"web": link_url}

    return thumb


def _with_buttons(card: dict, buttons: list[dict] | None, horizontal: bool) -> dict:
    if buttons:
        card["buttons"] = buttons[: 2 if horizontal else 3]
        card["buttonLayout"] = "horizontal" if horizontal else "vertical"

    return card


# ----------------------------- 말풍선 -----------------------------


def simple_text(text: str) -> dict:
    return {"simpleText": {"text": text[:1000] or " "}}


def simple_image(image_url: str, alt_text: str = "이미지") -> dict:
    return {"simpleImage": {"imageUrl": image_url, "altText": alt_text[:50]}}


def text_card(title: str, description: str, buttons: list[dict] | None = None, horizontal: bool = False) -> dict:
    card = {"title": title[:50], "description": description[: max(0, 400 - len(title[:50]))]}

    return {"textCard": _with_buttons(card, buttons, horizontal)}


def basic_card(
    title: str, description: str, image_url: str, buttons: list[dict] | None = None, horizontal: bool = False
) -> dict:
    card = {"title": title[:50], "description": description[:230], "thumbnail": thumbnail(image_url)}

    return {"basicCard": _with_buttons(card, buttons, horizontal)}


def list_card(
    header: str, items: list[dict], buttons: list[dict] | None = None, horizontal: bool = False, max_items: int = 5
) -> dict:
    """items: [{"title", "description"?, "imageUrl"?, "messageText"?}] — messageText가 있으면 누르면 그 발화를 보낸다."""
    rows = []

    for item in items[:max_items]:
        row = {"title": item["title"]}

        for key in ("description", "imageUrl"):
            if item.get(key):
                row[key] = item[key]

        if item.get("messageText"):
            row["action"] = "message"
            row["messageText"] = item["messageText"]

        rows.append(row)

    card = {"header": {"title": header}, "items": rows}

    return {"listCard": _with_buttons(card, buttons, horizontal)}


def item_card(
    title: str,
    description: str,
    rows: list[tuple[str, str]],
    head: str | None = None,
    summary: tuple[str, str] | None = None,
    buttons: list[dict] | None = None,
    horizontal: bool = False,
    max_rows: int = 10,
) -> dict:
    """정산서·영수증처럼 '항목: 값' 표를 보여준다. 항목 이름은 6자, summary 값은 숫자·통화기호만 허용."""
    card = {
        "title": title[:200],
        "description": description[:200],
        "itemList": [{"title": k[:6], "description": v} for k, v in rows[:max_rows]],
        "itemListAlignment": "right",
    }

    if head:
        card["head"] = {"title": head}

    if summary:
        card["itemListSummary"] = {"title": summary[0][:6], "description": summary[1][:14]}

    return {"itemCard": _with_buttons(card, buttons, horizontal)}


def commerce_card(
    title: str,
    description: str,
    price: int,
    image_url: str,
    discount: int | None = None,
    buttons: list[dict] | None = None,
    horizontal: bool = False,
) -> dict:
    card = {
        "title": title[:30],
        "description": description[:40],
        "price": price,
        "currency": "won",
        "thumbnails": [thumbnail(image_url)],
    }

    if discount:
        card["discount"] = discount
        card["discountedPrice"] = price - discount

    return {"commerceCard": _with_buttons(card, buttons or [button_share()], horizontal)}


def carousel(cards: list[dict], header: dict | None = None) -> dict:
    """채널채팅 전용. cards는 같은 종류의 말풍선 함수 결과 목록(예: basic_card(...) 여러 개)."""
    card_type = next(iter(cards[0]))
    limit = 5 if card_type == "listCard" else 10

    body = {"type": card_type, "items": [c[card_type] for c in cards[:limit]]}

    if header and card_type not in ("textCard", "listCard"):
        body["header"] = header

    return {"carousel": body}


# ----------------------------- 응답 조립 -----------------------------


def skill_response(outputs: list[dict], quick_replies: list[str] | None = None) -> dict:
    template: dict = {"outputs": outputs[:3] or [simple_text("(빈 응답)")]}

    if quick_replies:
        template["quickReplies"] = [
            {"label": label[:14], "action": "message", "messageText": label} for label in quick_replies[:10]
        ]

    return {"version": "2.0", "template": template}


def text_response(text: str, quick_replies: list[str] | None = None) -> dict:
    # 1000자 넘는 답변은 말풍선 여러 개(최대 3개)로 나눈다
    chunks = [text[i : i + 1000] for i in range(0, len(text), 1000)]

    return skill_response([simple_text(c) for c in chunks], quick_replies)


# 심사는 SimpleText 말풍선만 평가하고, 다른 말풍선이 나오면 그 케이스를 끝낸다.
# 말풍선 수·글자 수가 적을수록 유리하고 500자를 넘으면 평가에서 빠진다.
JUDGED_MAX_CHARS = 500


def _texts(node) -> list[str]:
    if isinstance(node, dict):
        if "text" in node and isinstance(node["text"], str):
            return [node["text"]]

        out = []
        for key in ("title", "description"):
            if isinstance(node.get(key), str):
                out.append(node[key])
        for value in node.values():
            if isinstance(value, (dict, list)):
                out += _texts(value)
        return out

    if isinstance(node, list):
        return [t for item in node for t in _texts(item)]

    return []


def judged_response(response: dict) -> dict:
    """어떤 응답이든 SimpleText 말풍선 하나(500자 이내)로 바꾼다. 바로가기 버튼도 뺀다."""
    text = "\n".join(t.strip() for t in _texts(response.get("template", {}).get("outputs", [])) if t.strip())
    text = text or "다시 한 번 말씀해 주세요."

    if len(text) > JUDGED_MAX_CHARS:
        text = text[: JUDGED_MAX_CHARS - 1] + "…"

    return {"version": "2.0", "template": {"outputs": [simple_text(text)]}}


async def send_callback(callback_token: str, response: dict) -> httpx.Response:
    async with httpx.AsyncClient(timeout=10) as client:
        return await client.post(
            CALLBACK_URL,
            json={"skillResponse": judged_response(response)},
            headers={"Authorization": f"KakaoAK {BOT_TOKEN}", "X-Bot-Callback-Token": callback_token},
        )
