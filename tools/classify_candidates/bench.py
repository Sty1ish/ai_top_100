"""분류기 후보 벤치마크. 서버에서: .venv/bin/python tools/classify_candidates/bench.py [후보...]"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path[:0] = [str(HERE), str(ROOT / "bot")]

import classify  # noqa: E402
import embed_knn  # noqa: E402
import llm_ollama  # noqa: E402
import rules_plus  # noqa: E402

CASES = json.loads((ROOT / "docs" / "contest" / "classify_cases.json").read_text(encoding="utf-8"))


async def julia_current(text, prev=""):
    ranked = await classify.classify(text, prev)
    kw = classify.keyword_categories(text)
    return [(c, 1.0) for c in dict.fromkeys(kw + [c for c, p in ranked if not kw or p >= 0.6])]


def _use(url, prefix):
    # 모델이 바뀌면 예시 임베딩 캐시를 비운다
    if embed_knn.EMBED_URL != url:
        embed_knn._vecs = None
    embed_knn.EMBED_URL, embed_knn.PREFIX = url, prefix


def knn(url, prefix):
    async def f(text, prev=""):
        _use(url, prefix)
        return await embed_knn.choose(text, prev)
    return f


def ollama(model):
    async def f(text, prev=""):
        llm_ollama.MODEL = model
        return await llm_ollama.choose(text, prev)
    return f


def rules(fallback, sub=None):
    async def f(text, prev=""):
        rules_plus.FALLBACK = fallback
        if sub:
            _use(*sub)
        return await rules_plus.choose(text, prev)
    return f


E5 = ("http://127.0.0.1:8093/v1/embeddings", "query: ")
BGE = ("http://127.0.0.1:8094/v1/embeddings", "")
CANDS = {
    "A_현재(규칙+Julia)": julia_current,
    "B_bgem3_kNN": knn(*BGE),
    "C_exaone2.4b": ollama("exaone3.5:2.4b"),
    "C_qwen3_1.7b": ollama("qwen3:1.7b"),
    "D_보강규칙만": rules("none"),
    "D_보강규칙+bgem3": rules("embed", BGE),
    "D_보강규칙+exaone": rules("ollama"),
}


async def run(name, fn):
    h = m = x = full = 0
    secs, fails = [], []
    for case in CASES["single"]:
        t0 = time.monotonic()
        cats = [c for c, _ in await fn(case["text"])]
        secs.append(time.monotonic() - t0)
        e, g = set(case["expect"]), set(cats)
        h += len(e & g); m += len(e - g); x += len(g - e); full += not (e - g)
        if e - g:
            fails.append(f"{case['text'][:28]}→{cats}(기대 {case['expect']})")
    # 대화 누적: 매 턴 필요한 분류가 누적에 들어 있는지
    conv_ok = conv_n = conv_extra = 0
    for conv in CASES["convos"]:
        acc, prev = [], ""
        for t in conv["turns"]:
            for c, _ in await fn(t["text"], prev):
                if c not in acc:
                    acc.append(c)
            prev = t["text"]
            conv_n += 1
            conv_ok += all(c in acc for c in t["need"])
        conv_extra += len(set(acc) - {c for t in conv["turns"] for c in t["need"]})
    secs.sort()
    n = len(CASES["single"])
    return {
        "name": name, "recall": h / max(1, h + m), "precision": h / max(1, h + x), "full": f"{full}/{n}",
        "noise": x / n, "p50": secs[n // 2], "p95": secs[int(n * 0.95)], "conv": f"{conv_ok}/{conv_n}",
        "conv_extra": conv_extra / len(CASES["convos"]), "fails": fails,
    }


async def main():
    names = sys.argv[1:] or list(CANDS)
    rows = []
    for nm in names:
        fn = CANDS[nm]
        await fn("안녕하세요")  # 모델 적재·예시 임베딩 워밍업
        r = await run(nm, fn)
        rows.append(r)
        print(f"{nm}: R {r['recall']:.2f} P {r['precision']:.2f} 완전 {r['full']} 잡음 {r['noise']:.2f} p50 {r['p50']:.2f}s p95 {r['p95']:.2f}s 대화 {r['conv']} 대화잡음 {r['conv_extra']:.1f}", flush=True)
    print("\n| 후보 | 재현율 | 정밀도 | 완전 포함 | 잡음/건 | p50 | p95 | 대화 턴 충족 | 대화당 잡음 |\n|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['name']} | {r['recall']:.2f} | {r['precision']:.2f} | {r['full']} | {r['noise']:.2f} | {r['p50']:.2f}s | {r['p95']:.2f}s | {r['conv']} | {r['conv_extra']:.1f} |")
    for r in rows:
        print(f"\n### {r['name']} 놓침\n" + "\n".join("- " + f for f in r["fails"]))


if __name__ == "__main__":
    asyncio.run(main())
