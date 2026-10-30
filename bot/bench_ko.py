"""모델별 한국어 답변 속도·품질 비교. 사용법: python bench_ko.py 모델1 모델2 ...

실제 봇과 같은 시스템 프롬프트와 답변 길이 제한(num_predict)을 쓴다. 모델마다 한 번 데워 둔 뒤 측정한다.
"""

import statistics
import sys
import time

import httpx

from agent import MAX_TOKENS, SYSTEM_PROMPT

QUESTIONS = [
    "교동마을은 어떤 곳이야?",
    "갯마을 수산물 신선도를 유지하려면 어떻게 해야 해?",
    "조합 재고 관리가 엑셀이라 자꾸 틀려. 어떻게 개선할까?",
    "가을에 많이 나는 농산물 알려줘",
]


def ask(model: str, q: str) -> dict:
    return httpx.post(
        "http://127.0.0.1:11434/api/chat",
        json={
            "model": model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": q}],
            "stream": False,
            "think": False,
            "keep_alive": "10m",
            "options": {"num_predict": MAX_TOKENS, "num_ctx": 2048},
        },
        timeout=300,
    ).json()


for model in sys.argv[1:]:
    ask(model, "안녕")  # 모델 적재
    times = []

    print(f"\n===== {model} =====")

    for q in QUESTIONS:
        t0 = time.time()
        r = ask(model, q)
        times.append(time.time() - t0)
        tps = r["eval_count"] / (r["eval_duration"] / 1e9)
        print(f"[{times[-1]:.1f}s, {tps:.1f} tok/s] {q}\n  {r['message']['content'].strip()[:200]}")

    print(f"--> 중앙값 {statistics.median(times):.1f}s, 최대 {max(times):.1f}s")

    httpx.post("http://127.0.0.1:11434/api/generate", json={"model": model, "keep_alive": 0})
