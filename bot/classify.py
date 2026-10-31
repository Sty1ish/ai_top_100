"""결정 모델(Julia-1)로 고객 메시지의 CS 분류를 고른다. 생성 없이 확률만 받으므로 빠르다.

1순위 확률이 0.6 미만이면 2순위까지 돌려준다. 서버가 죽었으면 빈 목록(코어 + load_policy만 씀).
"""

import logging
import os
import re

import httpx

DECIDE_URL = os.getenv("DECIDE_URL", "http://127.0.0.1:8091/v1/systemone")
TIMEOUT = float(os.getenv("DECIDE_TIMEOUT", "3"))
MIN_CONFIDENCE = 0.6

log = logging.getLogger("bot")

# 순서가 프롬프트 조립 순서다 (캐시 재사용을 위해 고정)
CATEGORIES = {
    "delivery": "배송·도착·출고 일정·배송지 변경",
    "quality": "상품 하자·상함·곰팡이·맛·당도·먹고 탈남",
    "refund": "환불·취소·반품·가격 인하 차액",
    "payment": "결제·현금영수증·세금계산서·판매자 정보",
    "promo": "쿠폰·할인 코드·프로모션·혜택",
    "account": "회원·로그인·개인정보·다른 사람 주문",
    "product": "상품 정보·재고·보관 방법",
    "other": "인사·그 밖의 말",
}


# 결정 모델이 놓치기 쉬운 단어는 규칙으로 더한다 (주소 변경을 하자로 고르는 등)
KEYWORDS = {
    "delivery": r"배송|주소|도착|출고|송장|택배|언제\s*와|안\s*와|왔어요|받을 수|당일|집하|결항",
    "quality": r"곰팡|상했|상한|썩|부패|배탈|하자|시들|브릭스|당도|너무 셔|맛이|깨졌|파손|이상해",
    "refund": r"환불|취소|반품|차액|세일|가격.*내|돌려",
    "payment": r"결제|영수증|세금계산서|입금|카드|판매자|사업자|대표자",
    "promo": r"쿠폰|할인|코드|프로모션|이벤트|적립|리워드|혜택|발급",
    "account": r"회원|로그인|계정|비밀번호|개인정보|연락처|탈퇴|다른 사람|남의",
    "product": r"재고|입고|보관|원산지|산지|상품 정보|구독",
}


def keyword_categories(message: str) -> list[str]:
    return [c for c, pat in KEYWORDS.items() if re.search(pat, message)]


async def classify(message: str, prev: str = "") -> list[tuple[str, float]]:
    text = f"이전 고객 발화: {prev}\n고객 메시지: {message}" if prev else message
    body = {
        "state": {"message": text[:500]},
        "questions": {"category": {"type": "choice", "instructions": "고객 상담 문의의 종류", "criteria": CATEGORIES}},
    }

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            res = await client.post(DECIDE_URL, json=body)
            res.raise_for_status()

        probs = res.json()["answers"]["category"]["probabilities"]
    except Exception as e:
        log.warning("classify unavailable: %s", e)

        return []

    ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)

    return ranked[:1] if ranked[0][1] >= MIN_CONFIDENCE else ranked[:2]
