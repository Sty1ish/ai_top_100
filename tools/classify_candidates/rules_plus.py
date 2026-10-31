"""후보 D: 보강한 단어 규칙 우선, 규칙에 안 걸릴 때만 보조 분류기(임베딩 kNN 기본)를 쓴다.

choose(text, prev) -> [(분류, 점수)]. 규칙으로 고른 분류는 점수 1.0.
CLS_FALLBACK=embed|ollama|none (기본 embed). 숫자·맞장구만 있는 짧은 발화는 분류하지 않는다(세션 누적 분류를 그대로 씀).
"""

import os
import re

FALLBACK = os.getenv("CLS_FALLBACK", "embed")

KEYWORDS = {
    "delivery": r"배송(?!비)|도착|출고|송장|택배|집하|결항|재출고|운송편|다음\s*편|비행기|당일|"
    r"(배송|받는|수령)\S*\s*주소|주소\s*(를|를\s*)?(바꿔|변경|잘못|수정)|주소\s*잘못|\d+동\s*\d+호|"
    r"언제\s*(와|오|받|도착)|(물건|상품|주문|택배|박스|망고|수박|감귤|갈치)\S*\s*(이|가|은|는)?\s*안\s*(와|왔|오)|왔어요|덜\s*왔|만\s*왔|아이스팩",
    "quality": r"곰팡|상했|상한|썩|부패|배탈|하자|시들|브릭스|당도|너무\s*셔|맛이|깨졌|파손|이상해|녹아|녹았|물러|무르|"
    r"설사|토했|구토|두드러기|병원|먹어도|안\s*먹었|먹고|중량|g\s*밖에|모자라|재배송|교환|냄새",
    "refund": r"환불|취소|반품|차액|세일|가격.*내|돌려|변심|마음이\s*바뀌|교환",
    "payment": r"결제|영수증|세금계산서|입금|카드|판매자|사업자|대표자|통신판매|신고번호|피싱|사기|계좌|이체|빠져나|두\s*번\s*(결제|빠)",
    "promo": r"쿠폰|할인|코드|프로모션|이벤트|적립|리워드|혜택|발급|보상|약속|깎아|공구|[A-Z]{3,}\d{2,}|단골|조합원",
    "account": r"회원|로그인|계정|비밀번호|개인정보|연락처|탈퇴|다른\s*사람|남의|남편|아내|가족\s*(주문|계정)|본인\s*확인|인증|가입|아이디",
    "product": r"재고|입고|보관|원산지|산지|상품\s*정보|구독|선물|세트|정기\s*배송|도매|되팔|재판매|해동|품절|수확|다시\s*들어|해지|건너뛰|스킵",
    "event": r"체험|견학|단체|인솔|수학여행|현장학습|농촌|마을\s*방문",
    "other": r"시스템\s*프롬프트|지시.{0,6}무시|프롬프트|^(감사합니다|고맙습니다|안녕하세요)[.!~ ]*$",
}
_RX = {c: re.compile(p) for c, p in KEYWORDS.items()}
# 주문번호·예/아니오만 있는 짧은 후속 발화
_TRIVIAL = re.compile(r"^[\s\d\-.,!~?]*(번|번이요|이요|요|이에요|예요|입니다)?[\s.!~?]*$|^(네|넵|예|아니요|아뇨|맞아요|네 맞아요|좋아요|그래요|알겠어요)[\s.!~?]*$")


def keyword_categories(text: str) -> list[str]:
    return [c for c, rx in _RX.items() if rx.search(text)]


async def choose(text: str, prev: str = "") -> list[tuple[str, float]]:
    kw = keyword_categories(text)
    if kw:
        return [(c, 1.0) for c in kw]
    if _TRIVIAL.match(text.strip()) or FALLBACK == "none":
        return []
    if FALLBACK == "ollama":
        from llm_ollama import choose as fb
    else:
        from embed_knn import choose as fb
    return (await fb(text, prev))[:1]
