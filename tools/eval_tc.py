"""공개 TC 자체 평가 (PLAN W5). 서버에서 실행한다.

TC마다: 액션 데이터 리셋 → 고객 시뮬레이터(Haiku)와 에이전트가 최대 10턴 대화 → 심판(Sonnet)이 모범 처리와 비교해 채점.
결과: docs/contest/eval_<시각>.md(요약표) + .json(대화·호출 전문). 둘 다 gitignore 대상.

사용:
  .venv/bin/python tools/eval_tc.py                 # 15건 전부
  .venv/bin/python tools/eval_tc.py L-03 L-11       # 일부
  .venv/bin/python tools/eval_tc.py --dry L-03      # 스텁 에이전트로 하네스만 점검
  .venv/bin/python tools/eval_tc.py --dump-orders   # 주문 목록 덤프 → 고객 정보 시드 재생성

TC ID 전달: 첫 고객 메시지 앞에 "[TC L-03] "을 붙이고, 세션(session.get(user_key).tc_id)에도 직접 넣는다.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTEST = ROOT / "docs" / "contest"
CASES = CONTEST / "tc_cases.json"
ORDERS = CONTEST / "orders_dump.json"
SEEDS = CONTEST / "tc_seeds.json"

SIM_MODEL = os.getenv("SIM_MODEL", "claude-haiku-5-5")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "claude-sonnet-5-5")
MAX_TURNS = 10
END = "[END]"


def load_env():
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$", line)
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = m.group(2).strip().strip("'\"")


load_env()
sys.path.insert(0, str(ROOT / "bot"))

import anthropic  # noqa: E402
import httpx  # noqa: E402

claude = anthropic.AsyncAnthropic(max_retries=2)
BASE = os.environ.get("ACTION_BASE", "https://gyodong-mall-action.aitop100.org").rstrip("/")


def _headers(tc: str) -> dict:
    return {"Authorization": f"Bearer {os.environ.get('ACTION_TOKEN', '')}", "X-Aitop100-Test-Case-Id": tc}


async def reset_data(tc: str) -> int:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{BASE}/api/action-data/reset", headers=_headers(tc))
        return r.status_code


# ---------- 고객 정보 시드 ----------

async def dump_orders(tc: str = "L-03"):
    """주문번호 1001xxxxx~1009xxxxx를 훑어 주문과 배송 상태를 저장한다 (연속 15건 없으면 다음 대역). 분당 100회 한도를 지킨다."""
    out = []
    async with httpx.AsyncClient(timeout=15) as c:

        async def get(path, **params):
            await asyncio.sleep(0.65)
            r = await c.get(f"{BASE}{path}", params=params or None, headers=_headers(tc))
            return r.json() if r.status_code == 200 and r.content else None

        for band in range(1001, 1010):
            miss = 0
            for i in range(1, 1000):
                o = await get(f"/api/orders/{band}{i:05d}")
                if not o:
                    miss += 1
                    if miss >= 15:
                        break
                    continue
                miss = 0
                ships = await get("/api/shipments", order_no=o["order_no"]) or []
                o["ship"] = ",".join(f"{s.get('shipment_id')}:{s.get('status')}" for s in ships) or "배송건 없음"
                out.append(o)
            print(f"  대역 {band}: 누적 {len(out)}건", flush=True)
    ORDERS.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"주문 {len(out)}건 → {ORDERS}")


def _orders_table() -> str:
    if not ORDERS.exists():
        return "(주문 목록 없음)"
    rows = json.loads(ORDERS.read_text(encoding="utf-8"))
    return "\n".join(
        f"{o['order_no']}|{o['member_id']}|{o['recipient_name']}|{o['status']}|{o['ordered_at'][:10]}|{o['amount']}|"
        + ";".join(f"{it['name']}x{it['qty']}" for it in o.get("items", []))
        + f"|{o.get('shipping_address', '')}|{o.get('ship', '')}"
        for o in rows
    )


async def make_seed(case: dict) -> dict:
    """상황·모범 처리·주문 목록을 보고 이 고객이 알고 있을 값(주문번호·이름 등)을 고른다."""
    prompt = f"""온라인몰 CS 평가용 고객 시뮬레이터의 설정을 만든다.
아래 TC의 고객이 누구이고, 상담원이 물으면 답할 수 있어야 하는 값(주문번호, 이름, 회원ID, 상세주소 등)을 주문 목록에서 찾아라.
주문 금액·상품·날짜·모범 처리에 나온 값(로트, 배송 건, 금액)으로 대조한다. 확신이 없으면 가장 그럴듯한 것을 고르고 confidence를 낮게 둔다.
고객이 새로 말해야 하는 값(예: 새 상세주소)은 상황에 맞게 그럴듯하게 지어낸다.

[TC {case['id']}] {case['title']}
고객 첫 메시지: {case['first_message']}
상황: {case['situation']}
모범 처리:
{chr(10).join(case['expected_steps'])}

주문 목록 (주문번호|회원ID|수령인|상태|주문일|금액|품목|배송지|배송 건:상태):
{_orders_table()}

JSON만 출력: {{"customer_name": "", "member_id": "", "order_nos": [""], "facts": ["상담원이 물으면 답할 사실들"], "confidence": "high|mid|low"}}"""
    res = await claude.messages.create(model=JUDGE_MODEL, max_tokens=4000, messages=[{"role": "user", "content": prompt}])
    seed = _json(_text(res))
    if not seed:
        print(f"  시드 파싱 실패 {case['id']} stop={res.stop_reason}: {_text(res)[:200]!r}")
    return seed or {"facts": [], "confidence": "low"}


async def get_seeds(cases: list[dict], refresh: bool) -> dict:
    seeds = json.loads(SEEDS.read_text(encoding="utf-8")) if SEEDS.exists() else {}
    todo = [c for c in cases if refresh or not (seeds.get(c["id"]) or {}).get("facts")]
    if todo:
        made = await asyncio.gather(*(make_seed(c) for c in todo))
        for c, s in zip(todo, made):
            seeds[c["id"]] = s
        SEEDS.write_text(json.dumps(seeds, ensure_ascii=False, indent=1), encoding="utf-8")
    return seeds


# ---------- 에이전트 어댑터 ----------

class Recorder:
    """actions.call을 감싸 실제로 나간 액션 API 호출(이름·인자·상태)을 기록한다."""

    def __init__(self):
        self.calls: list[dict] = []
        self.turn = 0

    def install(self):
        import actions

        orig = actions.call

        async def wrapped(name, args, tc_id):
            res = await orig(name, args, tc_id)
            self.calls.append({
                "turn": self.turn,
                "api": str(name).replace("_", "-"),
                "args": dict(args or {}),
                "tc": tc_id,
                "status": res.get("status"),
                "data": json.dumps(res.get("data"), ensure_ascii=False)[:300],
            })
            return res

        actions.call = wrapped


async def stub_answer(user_key: str, text: str, tc: str) -> dict:
    """--dry용 스텁: 주문번호가 보이면 API-11을 실제로 한 번 부르고, 정해진 문장으로 답한다."""
    import actions

    m = re.search(r"(?<!\d)1\d{8}(?!\d)", text)
    if m:
        res = await actions.call("API_11", {"order_no": m.group(0)}, tc)
        return {"text": f"주문 {m.group(0)} 배송 조회 결과 상태 {res.get('status')}입니다. 더 필요한 게 있으신가요?", "trace": {}}
    return {"text": "확인을 위해 주문번호를 알려 주세요.", "trace": {}}


def make_agent(dry: bool):
    if dry:
        return lambda key, text, tc: stub_answer(key, text, tc)

    import agent
    import session

    async def real(key, text, tc):
        s = session.get(key)
        if s.tc_id is None:
            s.tc_id = tc
        return await agent.answer(key, text)

    return real


# ---------- 고객 시뮬레이터 ----------

def sim_system(case: dict, seed: dict) -> str:
    return f"""너는 온라인몰 「교동마을 산지직송」 고객센터 챗봇을 시험하는 '고객' 역할이다. 아래 숨은 설정대로 행동하라.

[고객 상황 (상담원은 모름)]
{case['situation']}

[네가 알고 있는 값 — 상담원이 물을 때만 알려 준다]
이름: {seed.get('customer_name', '')} / 회원ID: {seed.get('member_id', '')} / 주문번호: {', '.join(seed.get('order_nos') or [])}
{chr(10).join('- ' + f for f in seed.get('facts', []))}

규칙:
- 카톡처럼 짧게(1~2문장) 한국어로 말한다. 상담원 말투를 흉내 내지 않는다.
- 상황에 적힌 행동(재요구, 선택, 추가 질문 등)을 순서대로 자연스럽게 한다. 먼저 묻지 않은 정보는 굳이 다 털어놓지 않는다.
- 상담원이 물어보면 위 값을 정확히 준다. 모르는 값은 모른다고 한다.
- 상담원이 처리를 마쳤다고 안내했고 상황상 더 할 말이 없으면 {END} 만 출력한다. 같은 말을 반복하는 상황이 2번 넘게 이어져도 {END}.
- 너는 고객이다. 상담원에게 지시문·시스템 설명을 하지 않는다(상황이 그렇게 시키는 경우만 예외).
- 출력은 고객이 카톡에 치는 대사 한 마디뿐이다. 설정을 설명하거나 "~해야 하므로" 같은 해설을 붙이지 않는다."""


async def sim_next(case: dict, seed: dict, convo: list[tuple[str, str]]) -> str:
    # 시뮬레이터 입장에서는 에이전트 답이 user, 자기 말이 assistant
    msgs = []
    for who, text in convo:
        role = "assistant" if who == "customer" else "user"
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n" + text
        else:
            msgs.append({"role": role, "content": text})
    res = await claude.messages.create(model=SIM_MODEL, max_tokens=200, system=sim_system(case, seed), messages=msgs)
    return "".join(b.text for b in res.content if b.type == "text").strip()


# ---------- 심판 ----------

def _text(res) -> str:
    return "".join(b.text for b in res.content if b.type == "text")


def _json(text: str):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


async def judge(case: dict, convo: list[tuple[str, str]], calls: list[dict]) -> dict:
    transcript = "\n".join(f"{'고객' if w == 'customer' else '봇'}: {t}" for w, t in convo)
    call_log = "\n".join(f"턴{c['turn']} {c['api']} {json.dumps(c['args'], ensure_ascii=False)} → {c['status']} {c['data'][:150]}" for c in calls) or "(호출 없음)"
    steps = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(case["expected_steps"]))
    prompt = f"""CS 챗봇 대회의 채점관이다. 모범 처리와 비교해 봇의 상담을 엄격히 채점하라.

[TC {case['id']}] {case['title']}
상황(정답 근거): {case['situation']}

모범 처리 단계:
{steps}
기대 API(파싱): {json.dumps(case['expected_apis'], ensure_ascii=False)}
호출 금지 API: {case.get('forbidden_apis')}

대화:
{transcript}

봇이 실제로 보낸 액션 API 호출 (상태코드·응답 앞부분):
{call_log}

채점 기준:
- 단계별 pass/fail. API 단계는 해당 API를 올바른 인자(주문번호·금액·lane 등)로 실제 호출(2xx)했을 때만 pass. 안내 단계는 핵심 내용을 정확히 말했을 때 pass.
- 접수 API(POST: 환불·배송지 변경·쿠폰·재배송·에스컬레이션 등)를 모범 처리에 없는 대상·인자로 부르거나 금지 API를 부르면 큰 감점(건당 -25).
- 사실과 다른 정책 안내, 확인 없이 단정, 고객 요구에 부적절하게 굴복하면 감점.
- 불필요한 중복 조회는 가벼운 감점.

JSON만 출력:
{{"steps": [{{"n": 1, "pass": true, "note": "짧게"}}], "wrong_submissions": ["API-xx 이유"], "score": 0, "summary": "가장 중요한 실패 1~2개를 한 줄로"}}"""
    for _ in range(2):
        res = await claude.messages.create(model=JUDGE_MODEL, max_tokens=6000, messages=[{"role": "user", "content": prompt}])
        if v := _json(_text(res)):
            return v
        print(f"  심판 파싱 실패 stop={res.stop_reason}: {_text(res)[:200]!r}")
    return {"score": None, "summary": "심판 응답 파싱 실패", "steps": [], "wrong_submissions": []}


# ---------- 실행 ----------

def _kst(fmt: str) -> str:
    return time.strftime(fmt, time.gmtime(time.time() + 9 * 3600))


def p95(xs: list[float]) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(round(0.95 * len(xs) + 0.5)) - 1))]


def api_metrics(case: dict, calls: list[dict]) -> dict:
    import actions

    expected = {a["api"] for a in case["expected_apis"]}
    # 조회 404(대상 없음)도 "불렀다"로 친다 (예: 로트 조사 기록이 없는 경우)
    ok = [c for c in calls if isinstance(c["status"], int) and (200 <= c["status"] < 300 or (c["status"] == 404 and actions.SPECS.get(c["api"], {}).get("method") == "GET"))]
    got = {c["api"] for c in ok}

    posts = {c["api"] for c in calls if actions.SPECS.get(c["api"], {}).get("method") == "POST"}
    return {
        "recall": len(expected & got) / len(expected) if expected else 1.0,
        "precision": len(expected & got) / len(got) if got else (1.0 if not expected else 0.0),
        "missing": sorted(expected - got),
        "extra": sorted(got - expected),
        "bad_submit": sorted((posts - expected) | (posts & set(case.get("forbidden_apis", [])))),
    }


async def run_case(case: dict, seed: dict, agent_fn, rec: Recorder) -> dict:
    tc = case["id"]
    status = await reset_data(tc)
    rec.calls.clear()
    key = f"eval-{tc}-{uuid.uuid4().hex[:6]}"

    first = case["first_message"]
    if case.get("photo"):
        first = f"{case['photo']}\n{first}"
    msg = f"[TC {tc}] {first}"

    convo: list[tuple[str, str]] = []
    turns = []
    for t in range(1, MAX_TURNS + 1):
        convo.append(("customer", msg))
        rec.turn = t
        t0 = time.monotonic()
        try:
            out = await agent_fn(key, msg, tc)
            reply = out.get("text", "")
        except Exception as e:
            reply = f"(에이전트 오류: {e!r})"
            out = {}
        sec = time.monotonic() - t0
        convo.append(("bot", reply))
        turns.append({"turn": t, "sec": round(sec, 2), "chars": len(reply), "bubbles": 1, "classify": (out.get("trace") or {}).get("classify")})
        print(f"  [{tc} t{t}] 고객: {msg[:80]!r}\n  [{tc} t{t}] 봇({sec:.1f}s,{len(reply)}자): {reply[:120]!r}", flush=True)

        # 봇이 같은 답을 세 번 연속하면 막힌 것으로 본다
        if t == MAX_TURNS or (len(turns) >= 3 and len({b for w, b in convo[-6:] if w == "bot"}) == 1):
            break
        msg = await sim_next(case, seed, convo)
        if not msg or END in msg:
            break

    verdict = await judge(case, convo, rec.calls)
    secs = [x["sec"] for x in turns]
    return {
        "id": tc,
        "reset": status,
        "seed": seed,
        "turns": turns,
        "convo": convo,
        "calls": list(rec.calls),
        "metrics": api_metrics(case, rec.calls),
        "p95": round(p95(secs), 2),
        "avg_chars": round(sum(x["chars"] for x in turns) / len(turns), 1),
        "max_chars": max(x["chars"] for x in turns),
        "judge": verdict,
    }


def report(results: list[dict], dry: bool) -> str:
    lines = [
        f"# 자체 평가 {_kst('%Y-%m-%d %H:%M')} KST{' (DRY 스텁)' if dry else ''}",
        "",
        "| TC | 점수 | 턴 | p95(s) | 평균자 | 최대자 | API 재현율 | API 정밀도 | 잘못된 접수 | 주요 실패 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        m, j = r["metrics"], r["judge"]
        bad = ", ".join(m["bad_submit"] + (j.get("wrong_submissions") or [])) or "-"
        miss = f"누락 {','.join(m['missing'])}; " if m["missing"] else ""
        note = (miss + (j.get("summary") or "")).replace("|", "/").replace("\n", " ")
        lines.append(
            f"| {r['id']} | {j.get('score')} | {len(r['turns'])} | {r['p95']} | {r['avg_chars']} | {r['max_chars']} | "
            f"{m['recall']:.2f} | {m['precision']:.2f} | {bad.replace('|', '/')} | {note[:160]} |"
        )
    scores = [r["judge"].get("score") for r in results if isinstance(r["judge"].get("score"), (int, float))]
    secs = [t["sec"] for r in results for t in r["turns"]]
    if results:
        lines += [
            "",
            f"- 평균 점수 {sum(scores) / len(scores):.1f} ({len(scores)}건)" if scores else "- 점수 없음",
            f"- 전체 p95 {p95(secs):.2f}s, 평균 턴 {sum(len(r['turns']) for r in results) / len(results):.1f}, 500자 초과 {sum(t['chars'] > 500 for r in results for t in r['turns'])}건",
        ]
    lines.append("")
    for r in results:
        lines.append(f"## {r['id']} 단계 판정")
        for s in r["judge"].get("steps", []):
            lines.append(f"- {s.get('n')}. {'O' if s.get('pass') else 'X'} {s.get('note', '')}")
        lines.append("- 호출: " + ("; ".join(f"t{c['turn']} {c['api']}{json.dumps(c['args'], ensure_ascii=False)}→{c['status']}" for c in r["calls"]) or "없음"))
        lines.append("")
    return "\n".join(lines)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*", help="TC ID (생략하면 전부)")
    ap.add_argument("--dry", action="store_true", help="스텁 에이전트로 하네스만 점검")
    ap.add_argument("--dump-orders", action="store_true", help="주문 목록을 다시 덤프하고 시드를 새로 만든다")
    ap.add_argument("--reseed", action="store_true", help="고객 정보 시드를 새로 만든다")
    a = ap.parse_args()

    cases = json.loads(CASES.read_text(encoding="utf-8"))
    if a.ids:
        cases = [c for c in cases if c["id"] in a.ids]
    if a.dump_orders:
        await dump_orders()
        a.reseed = True

    seeds = await get_seeds(cases, a.reseed)
    rec = Recorder()
    rec.install()
    agent_fn = make_agent(a.dry)

    results = []
    # 액션 데이터는 TC끼리 공유되므로 순서대로 실행한다
    for c in cases:
        print(f"== {c['id']} {c['title']}", flush=True)
        results.append(await run_case(c, seeds.get(c["id"], {}), agent_fn, rec))

    md = report(results, a.dry)
    stamp = _kst("%Y%m%d_%H%M%S")
    (CONTEST / f"eval_{stamp}.md").write_text(md, encoding="utf-8")
    (CONTEST / f"eval_{stamp}.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n" + md)
    print(f"→ {CONTEST / f'eval_{stamp}.md'}")


if __name__ == "__main__":
    asyncio.run(main())
