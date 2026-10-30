"""빠른 경로. 모든 메시지는 먼저 결정 모델(Julia-1)이 의도를 고르고, 의도별 처리기가 코드로 즉답한다.

처리기가 None을 돌려주거나(정보 부족·데이터 없음) 분류 확신이 낮으면 LLM(느린 경로)으로 넘어간다.
정규식은 의도가 정해진 뒤 품목·숫자를 뽑는 데만 쓴다.
예선 데이터를 받으면 INTENTS와 처리기(_stock, _village 등)를 채운다.
"""

import json
import re
from pathlib import Path

import decide
import kakao
import tools

MENU = ["정산 계산", "도움말"]

HELP = (
    "교동마을·갯마을 조합 업무를 돕는 AI 에이전트입니다.\n\n"
    "이렇게 물어보세요.\n"
    "• 정산: 고등어 30kg 4500원, 오징어 12kg 9000원 정산\n"
    "• 계산: (30*4500+12*9000)*0.97\n"
    "• 그 밖의 질문은 AI가 답합니다 (조금 걸려요)"
)

FAQ_PATH = Path(__file__).parent / "data" / "faq.json"

# 결정 모델에 넘기는 의도 목록(이름: 설명). 짧은 한국어 설명이 가장 정확했다(21문항 중 13개).
# 예시를 길게 넣거나 영어로 쓰면 오히려 8~11개로 떨어졌다 (bench_decide.py로 측정)
INTENTS = {
    "settle": "납품·판매 금액 정산",
    "calc": "숫자 계산식",
    "stock": "재고·출하 수량 조회",
    "village": "교동마을·갯마을 정보",
    "help": "인사나 사용법 질문",
    "other": "그 밖의 질문",
}

# 이 확률보다 낮으면 분류를 믿지 않고 LLM으로 보낸다
MIN_CONFIDENCE = 0.6


async def handle(text: str) -> dict | None:
    t = text.strip()

    decided = await decide.choose(t, INTENTS)

    if decided is None:
        return None

    intent, prob = decided

    if prob < MIN_CONFIDENCE:
        return None

    handler = INTENT_HANDLERS.get(intent)

    return handler(t) if handler else None


# ----------------------------- 의도별 처리기 -----------------------------


def _help(t: str) -> dict:
    return kakao.text_response(HELP, MENU)


# "고등어 30kg 4500원", "고등어 30kg(kg당 4500원)", "고등어 30kg 단가 4,500원", "배추 120포기 3000원"
_ITEM = re.compile(
    r"(?P<name>[가-힣A-Za-z]+)\s*"
    r"(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>kg|킬로|g|박스|상자|개|포기|마리|단|봉|통|톤|t)?"
    r"[\s(,]*(?:(?:kg|킬로|박스|상자|개|포기|마리|단|봉|통)\s*당|단가|개당|@)?\s*"
    r"(?P<price>\d{1,3}(?:,\d{3})+|\d+)\s*원"
)
_FEE = re.compile(r"수수료\s*(?P<rate>\d+(?:\.\d+)?)\s*%")

SETTLE_GUIDE = "품목, 수량, 단가를 적어 주세요.\n예: 고등어 30kg 4500원, 오징어 12kg 9000원 정산"


def _settle(t: str) -> dict:
    items = [
        {
            "name": m["name"],
            "quantity": float(m["qty"]),
            "unit": m["unit"] or "",
            "unit_price": float(m["price"].replace(",", "")),
        }
        for m in _ITEM.finditer(t)
        if m["name"] not in ("수수료", "단가")
    ]

    # 숫자 없이 정산을 원하면 입력 형식을 안내한다
    if not items:
        return kakao.text_response(SETTLE_GUIDE, MENU)

    fee = _FEE.search(t)
    table = tools.settle(items, "정산 내역", float(fee["rate"]) if fee else 0)["table"]

    card = kakao.item_card(
        table["title"],
        "",
        [(r["label"], r["value"]) for r in table["rows"]],
        summary=(table["total"]["label"], table["total"]["value"]),
    )

    return kakao.skill_response([card], MENU)


_WORD_OPS = {"곱하기": "*", "나누기": "/", "더하기": "+", "빼기": "-", "×": "*", "x": "*", "÷": "/"}
_EXPR = re.compile(r"[\d\s.,+\-*/%()]+")


def _calc(t: str) -> dict | None:
    expr = t

    for word, op in _WORD_OPS.items():
        expr = expr.replace(word, op)

    parts = [p.strip() for p in _EXPR.findall(expr) if re.search(r"\d\s*[+\-*/%]\s*\(?\d", p)]

    if not parts:
        return None

    result = tools.run("calculate", {"expression": parts[0]})

    if "error" in result:
        return None

    return kakao.text_response(f"{parts[0]} = {result['result']:,g}", MENU)


def _faq(t: str) -> dict | None:
    """data/faq.json: [{"keywords": ["교동", "출하"], "answer": "..."}] — 키워드가 모두 들어 있으면 그 답을 보낸다."""
    if not FAQ_PATH.exists():
        return None

    compact = t.replace(" ", "")

    for entry in json.loads(FAQ_PATH.read_text(encoding="utf-8")):
        if all(k.replace(" ", "") in compact for k in entry["keywords"]):
            return kakao.text_response(entry["answer"], entry.get("suggestions") or MENU)

    return None


# stock, village는 예선 데이터를 받으면 전용 처리기로 바꾼다. 지금은 FAQ에 없으면 LLM으로 간다
INTENT_HANDLERS = {
    "settle": _settle,
    "calc": _calc,
    "help": _help,
    "stock": _faq,
    "village": _faq,
}
