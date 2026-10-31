"""결정 모델(Julia-1)로 고객 메시지의 CS 분류를 고른다. 생성 없이 확률만 받으므로 빠르다.

진입점은 choose(text, prev) 하나다 (다른 분류기로 바꿀 때 이것만 바꾼다).
단어 규칙을 우선하고, 규칙에 안 걸릴 때만 결정 모델 1순위(0.8 이상)를 더한다. 짧은 대답은 모델을 부르지 않는다.
서버가 죽었으면 규칙 결과만 쓴다(비면 코어 + load_policy).
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
    "quality": "상품 하자·상함·곰팡이·맛·당도·중량 미달·먹고 탈남",
    "refund": "환불·취소·반품·가격 인하 차액",
    "payment": "결제·현금영수증·세금계산서·판매자 사업자정보·통신판매업·사칭/피싱",
    "promo": "쿠폰·할인 코드·프로모션·혜택",
    "account": "회원·로그인·개인정보·다른 사람 주문",
    "product": "상품 정보·재고·품절·재입고·수확·보관·해동·정기배송·도매/재판매",
    "event": "농촌 체험·단체 방문 예약·축제",
    "other": "인사·감사·인젝션·불만만",
}

# 결정 모델은 정확도가 낮아(1순위 51%) 단어 규칙을 주로 쓰고, 규칙에 안 걸릴 때만 확신 높은 1순위를 더한다
MODEL_MIN = 0.8
_ACK = re.compile(r"^[\s\d\-.,!?~ㅎㅋ]*$|^(네|예|넵|응|아니요|아뇨|맞아요|맞습니다|좋아요|감사합니다)[\s.!~]*$")

KEYWORDS = {
    "delivery": r"배송|배송지|수령 ?주소|주소 ?(변경|잘못|바꿔|틀|바뀌)|도착|출고|송장|택배|(물건|상품|택배|주문|수박|망고|감귤|한라봉|갈치|전복|귤)(이|가|은|는)? ?안 ?와|언제\s*(와|오|받)|왔어요|받을 수(?! 있는 (혜택|쿠폰|할인))|직송|바로 온다|당일|집하|결항|재출고|다음\s*편|운송편|비행기|\d+동\s*\d+호|아이스팩",
    "quality": r"곰팡|상했|상한|썩|부패|배탈|하자|시들|브릭스|당도|너무 셔|맛이|깨졌|파손|이상해|녹아|녹았|물러|무르|설사|토했|구토|두드러기|병원|먹어도|중량|g밖에|덜 왔|모자라|재배송|교환",
    "refund": r"환불|취소|반품|차액|세일|가격.*내|돌려|변심|마음이 바뀌|교환",
    "payment": r"결제|영수증|세금계산서|입금|카드|판매자|사업자|대표자|통신판매|신고번호|피싱|사기|계좌|이체|빠져나|두 번|중복 결제",
    "promo": r"쿠폰|할인|코드|프로모션|이벤트|적립|리워드|혜택|발급|보상|약속|깎아|공구|[A-Z]{3,}\d{2,}|단골|조합원|라이브|추첨|체험단",
    "account": r"회원|로그인|계정|비밀번호|개인정보|연락처|탈퇴|다른 사람 ?(주문|정보|계정|명의|이름)|남의|남편|아내|가족 ?(주문|계정)|본인 ?확인|인증|가입|아이디",
    "product": r"재고|입고|원산지|산지|상품 정보|구독|선물|세트|정기배송|도매|되팔|재판매|해동|품절|수확|다시 들어|해지|건너뛰|스킵|보관|후숙|알레르기|농약",
    "event": r"체험|견학|단체|인솔|수학여행|현장학습|농촌|마을 방문|축제|매대",
    "other": r"시스템 ?프롬프트|지시.{0,6}무시|프롬프트|안녕하세요|감사합니다|고맙|소비자원|몇 번을 말",
}


def keyword_categories(message: str) -> list[str]:
    return [c for c, pat in KEYWORDS.items() if re.search(pat, message)]


def is_ack(message: str) -> bool:
    """짧은 대답·숫자만 있는 발화 (주문번호, "네" 등): 분류 근거가 없다."""
    return len(message.strip()) <= 6 or bool(_ACK.match(message.strip()))


async def choose(text: str, prev: str = "") -> list[tuple[str, float]]:
    """누적할 분류 목록 [(분류, 확률)]. 규칙에 걸린 분류는 확률 1.0."""
    kw = keyword_categories(text)
    if kw:
        return [(c, 1.0) for c in kw]
    if is_ack(text):
        return []
    return [(c, p) for c, p in (await classify(text, prev))[:1] if p >= MODEL_MIN]


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
