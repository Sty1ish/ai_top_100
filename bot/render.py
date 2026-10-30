"""에이전트의 구조화된 답변(dict)을 카카오 말풍선 응답으로 바꾼다.

답변 형식(REPLY_SCHEMA)은 ollama의 format으로 강제하고, 여기서는 있는 필드만 골라 말풍선을 만든다.
한 응답의 말풍선은 최대 3개이므로 text → 표/목록/카드 중 1~2개 순으로 채운다.
"""

import kakao

REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "사용자에게 하는 핵심 답변, 짧게"},
        "list": {
            "type": ["object", "null"],
            "description": "여러 항목을 나열할 때(품목, 일정, 담당자 등)",
            "properties": {
                "title": {"type": "string"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"title": {"type": "string"}, "description": {"type": "string"}},
                        "required": ["title"],
                    },
                },
            },
            "required": ["title", "items"],
        },
        "table": {
            "type": ["object", "null"],
            "description": "정산·수량처럼 '항목: 값'으로 보여줄 때",
            "properties": {
                "title": {"type": "string"},
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"label": {"type": "string"}, "value": {"type": "string"}},
                        "required": ["label", "value"],
                    },
                },
                "total": {
                    "type": ["object", "null"],
                    "properties": {"label": {"type": "string"}, "value": {"type": "string"}},
                },
            },
            "required": ["title", "rows"],
        },
        "cards": {
            "type": ["array", "null"],
            "description": "서로 비교할 선택지를 좌우로 넘겨 보게 할 때(마을별, 상품별 등)",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "description": {"type": "string"}},
                "required": ["title", "description"],
            },
        },
        "suggestions": {
            "type": "array",
            "description": "사용자가 이어서 누를 버튼 문구 0~3개, 각 10자 이내",
            "items": {"type": "string", "maxLength": 14},
        },
    },
    "required": ["text"],
}


def to_skill_response(reply: dict) -> dict:
    outputs = []

    if text := (reply.get("text") or "").strip():
        outputs.append(kakao.simple_text(text))

    if table := reply.get("table"):
        rows = [(r["label"], r["value"]) for r in table.get("rows") or [] if r.get("label")]
        total = table.get("total") or {}

        if rows:
            outputs.append(
                kakao.item_card(
                    table.get("title") or "내역",
                    "",
                    rows,
                    summary=(total["label"], total["value"]) if total.get("label") and _is_amount(total.get("value")) else None,
                )
            )

    if lst := reply.get("list"):
        items = [i for i in lst.get("items") or [] if i.get("title")]

        if items:
            outputs.append(_list_bubble(lst.get("title") or "목록", items))

    if cards := [c for c in reply.get("cards") or [] if c.get("title")]:
        outputs.append(kakao.carousel([kakao.text_card(c["title"], c.get("description") or "") for c in cards]))

    return kakao.skill_response(outputs[:3], (reply.get("suggestions") or [])[:3])


def _list_bubble(title: str, items: list[dict]) -> dict:
    rows = [{"title": i["title"], "description": i.get("description") or ""} for i in items]

    # 5개를 넘으면 4개씩 끊어 리스트카드 캐러셀로 보여준다
    if len(rows) <= 5:
        return kakao.list_card(title, rows)

    pages = [rows[i : i + 4] for i in range(0, len(rows), 4)]

    return kakao.carousel([kakao.list_card(f"{title} ({n + 1}/{len(pages)})", p, max_items=4) for n, p in enumerate(pages)])


def _is_amount(value) -> bool:
    # itemListSummary 설명은 통화기호·숫자·콤마·소수점·공백만 허용된다
    return bool(value) and all(ch in "0123456789,. 원₩$" for ch in str(value))
