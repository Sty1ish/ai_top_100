"""추천안: 단어 규칙 우선, 규칙에 안 걸릴 때만 Julia-1 1순위 하나를 쓴다. 새 프로세스가 필요 없다.

choose(text, prev) -> [(분류, 점수)]. 규칙으로 고른 분류는 1.0.
- 규칙(bot/classify.KEYWORDS)에 걸리면 모델 결과는 더하지 않는다 (지금 잡음의 주원인).
- 주문번호·네/아니요 같은 짧은 후속 발화는 분류하지 않는다 (세션 누적 분류를 그대로 씀).
agent.py에서는 `ranked = await choose(text, s.last_user_text)` 후 `s.add_categories([c for c, _ in ranked])`로 쓰면 된다.
"""

import re

import classify

_TRIVIAL = re.compile(
    r"^[\s\d\-.,!~?]*(번|번이요|이요|요|이에요|예요|입니다)?[\s.!~?]*$"
    r"|^(네|넵|예|아니요|아뇨|맞아요|네 맞아요|좋아요|그래요|알겠어요)[\s.!~?]*$"
)


async def choose(text: str, prev: str = "") -> list[tuple[str, float]]:
    kw = classify.keyword_categories(text)
    if kw:
        return [(c, 1.0) for c in kw]
    if _TRIVIAL.match(text.strip()):
        return []
    return (await classify.classify(text, prev))[:1]
