"""결정 모델의 한국어 의도 분류 정확도와 속도 비교. 의도 설명은 router.INTENTS를 그대로 쓴다.

사용법: python bench_decide.py 이름=포트 ...   (예: julia=8091)
"""

import statistics
import sys
import time

import httpx

from router import INTENTS

CASES = [
    ("갯마을 김씨 고등어 30kg 4500원 정산해줘", "settle"),
    ("배추 120포기 3000원이면 총 얼마야?", "settle"),
    ("이번 달 오징어 판매 금액 합계 알려줘", "settle"),
    ("수수료 3% 빼고 지급액 계산해줘", "settle"),
    ("정산하고 싶은데 어떻게 해?", "settle"),
    ("(30*4500+12*9000)*0.97", "calc"),
    ("1500 곱하기 12는?", "calc"),
    ("고등어 재고 몇 kg 남았어?", "stock"),
    ("오늘 출하할 배추 수량 알려줘", "stock"),
    ("창고에 무 얼마나 있어?", "stock"),
    ("교동마을은 어떤 곳이야?", "village"),
    ("갯마을 특산물이 뭐야?", "village"),
    ("교동마을 위치 알려줘", "village"),
    ("안녕하세요", "help"),
    ("안녕", "help"),
    ("이 봇 어떻게 써?", "help"),
    ("뭘 물어볼 수 있어?", "help"),
    ("도움말", "help"),
    ("오늘 날씨 어때?", "other"),
    ("점심 메뉴 추천해줘", "other"),
    ("조합장님 연락처 알려줘", "other"),
]


def classify(port: int, message: str) -> dict:
    return httpx.post(
        f"http://127.0.0.1:{port}/v1/systemone",
        json={
            "state": {"message": message},
            "questions": {"intent": {"type": "choice", "instructions": "사용자 요청의 종류", "criteria": INTENTS}},
        },
        timeout=60,
    ).json()["answers"]["intent"]


for spec in sys.argv[1:]:
    name, port = spec.split("=")
    classify(int(port), "안녕")  # 데우기

    times, correct, wrong = [], 0, []

    for text, gold in CASES:
        t0 = time.perf_counter()
        ans = classify(int(port), text)
        times.append(time.perf_counter() - t0)

        if ans["choice"] == gold:
            correct += 1
        else:
            wrong.append(f"{text} → {ans['choice']}({ans['probabilities'][ans['choice']]:.2f}), 정답 {gold}")

    print(f"\n===== {name} =====")
    print(f"정확도 {correct}/{len(CASES)} | 중앙값 {statistics.median(times) * 1000:.0f}ms | 최대 {max(times) * 1000:.0f}ms")

    for w in wrong:
        print("  틀림:", w)
