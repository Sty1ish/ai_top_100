"""분류(결정 모델 Julia-1 + 단어 규칙) 정확도 점검. 서버에서 실행한다.

agent.py와 같은 합성 규칙을 쓴다: 단어 규칙 결과 + (규칙이 비었거나 확률 0.6 이상인) 모델 분류.
사용: .venv/bin/python tools/eval_classify.py [docs/contest/classify_cases.json]
"""

import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bot"))

import classify  # noqa: E402


async def route(text: str, prev: str = "") -> dict:
    t0 = time.monotonic()
    ranked = await classify.classify(text, prev)
    sec = time.monotonic() - t0
    kw = classify.keyword_categories(text)
    model = [c for c, p in ranked if not kw or p >= classify.MIN_CONFIDENCE]
    cats = list(dict.fromkeys(kw + model))
    src = "kw+model" if kw and model else "kw" if kw else "model" if model else "none"
    return {"cats": cats, "kw": kw, "ranked": [(c, round(p, 2)) for c, p in ranked], "model_used": model, "src": src, "sec": sec}


def score(cats: list[str], expect: list[str]) -> tuple[int, int, int]:
    hit = len(set(cats) & set(expect))
    return hit, len(set(expect) - set(cats)), len(set(cats) - set(expect))


async def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "contest" / "classify_cases.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    tot = Counter()
    per = {c: Counter() for c in classify.CATEGORIES}
    srcs, secs, misses, noisy = Counter(), [], [], []
    top1_ok = top1_n = 0

    for case in data["single"]:
        r = await route(case["text"])
        exp = case["expect"]
        hit, miss, extra = score(r["cats"], exp)
        tot.update(hit=hit, miss=miss, extra=extra, n=1, full=int(miss == 0))
        srcs[r["src"]] += 1
        secs.append(r["sec"])
        if exp and r["ranked"]:
            top1_n += 1
            top1_ok += r["ranked"][0][0] in exp
        for c in classify.CATEGORIES:
            e, g = c in exp, c in r["cats"]
            per[c].update(tp=e and g, fn=e and not g, fp=g and not e)
        if miss:
            misses.append((case, r))
        elif extra >= 2:
            noisy.append((case, r))

    rec = tot["hit"] / max(1, tot["hit"] + tot["miss"])
    prec = tot["hit"] / max(1, tot["hit"] + tot["extra"])
    secs.sort()
    print(f"# 분류 점검 ({tot['n']}건)")
    print(f"- 재현율 {rec:.2f}, 정밀도 {prec:.2f}, 필요한 분류 모두 포함 {tot['full']}/{tot['n']}, 메시지당 잡음 분류 {tot['extra'] / tot['n']:.2f}")
    print(f"- 모델 1순위가 기대 분류에 속함: {top1_ok}/{top1_n}")
    print(f"- 결정 경로: {dict(srcs)}")
    if secs:
        print(f"- 모델 지연: 중앙 {secs[len(secs) // 2]:.2f}s, p95 {secs[int(len(secs) * 0.95)]:.2f}s, 최대 {secs[-1]:.2f}s")
    print("\n| 분류 | TP | FN(놓침) | FP(잡음) | 재현율 | 정밀도 |\n|---|---|---|---|---|---|")
    for c, k in per.items():
        print(f"| {c} | {k['tp']} | {k['fn']} | {k['fp']} | {k['tp'] / max(1, k['tp'] + k['fn']):.2f} | {k['tp'] / max(1, k['tp'] + k['fp']):.2f} |")

    print("\n## 놓친 경우")
    for case, r in misses:
        print(f"- [{case['src']}] {case['text'][:50]!r} 기대={case['expect']} 결과={r['cats']} kw={r['kw']} 모델={r['ranked']}")
    print("\n## 잡음 2개 이상")
    for case, r in noisy:
        print(f"- [{case['src']}] {case['text'][:50]!r} 기대={case['expect']} 결과={r['cats']} kw={r['kw']} 모델={r['ranked']}")

    print("\n## 대화 누적")
    for conv in data["convos"]:
        acc, prev = [], ""
        print(f"### {conv['name']}")
        for i, t in enumerate(conv["turns"], 1):
            r = await route(t["text"], prev)
            acc += [c for c in r["cats"] if c not in acc]
            prev = t["text"]
            lack = [c for c in t["need"] if c not in acc]
            print(f"- t{i} {t['text'][:40]!r} → 이번 {r['cats']} (모델 {r['ranked']}) 누적 {acc}" + (f" **부족 {lack}**" if lack else ""))


if __name__ == "__main__":
    asyncio.run(main())
