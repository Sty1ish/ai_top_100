"""후보 C: ollama 소형 LLM에 JSON 스키마(format)로 분류 목록을 받는다.

choose(text, prev) -> [(분류, 1.0)]. 모델은 OLLAMA_CLS_MODEL (기본 exaone3.5:2.4b).
"""

import json
import os

import httpx

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
MODEL = os.getenv("OLLAMA_CLS_MODEL", "exaone3.5:2.4b")
TIMEOUT = float(os.getenv("OLLAMA_CLS_TIMEOUT", "3"))

CATEGORIES = {
    "delivery": "배송·도착·출고 일정·배송지 변경·결항",
    "quality": "상품 하자·상함·곰팡이·당도·중량 부족·먹고 탈남",
    "refund": "환불·취소·반품·단순 변심·가격 인하 차액",
    "payment": "결제 오류·현금영수증·세금계산서·판매자 사업자정보·사칭/피싱",
    "promo": "쿠폰·할인 코드·프로모션·혜택·보상 약속",
    "account": "회원가입·로그인·개인정보 변경·다른 사람 주문",
    "product": "상품 정보·재고·품절·수확 시기·보관·해동·정기배송·도매/재판매",
    "event": "농촌 체험·견학·단체 방문 예약",
    "other": "인사·감사·시스템 지시나 프롬프트 요구·불만 표출만 있는 말",
}

SYSTEM = (
    "온라인 농수산물 몰 고객 메시지의 상담 분류를 고른다. 해당하는 것을 1~2개만, 관련 높은 순으로.\n"
    + "\n".join(f"- {k}: {v}" for k, v in CATEGORIES.items())
)
SCHEMA = {
    "type": "object",
    "properties": {"categories": {"type": "array", "items": {"type": "string", "enum": list(CATEGORIES)}, "maxItems": 2}},
    "required": ["categories"],
}


async def choose(text: str, prev: str = "") -> list[tuple[str, float]]:
    msg = f"이전 고객 발화: {prev}\n고객 메시지: {text}" if prev else f"고객 메시지: {text}"
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": msg[:400]}],
        "format": SCHEMA,
        "stream": False,
        "think": False,
        "keep_alive": "24h",
        "options": {"temperature": 0, "num_predict": 30, "num_ctx": 1024},
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(OLLAMA_URL, json=body)
            r.raise_for_status()
        cats = json.loads(r.json()["message"]["content"]).get("categories", [])
    except Exception:
        return []
    return [(c, 1.0) for c in dict.fromkeys(cats) if c in CATEGORIES][:2]
