"""공개 TC md(docs/contest/docs_test-cases_<ID>.md)를 구조화해 docs/contest/tc_cases.json으로 저장한다.

출력에는 대회 문제 내용이 들어 있으므로 docs/contest/(gitignore) 밖에 두지 않는다.
사용: python tools/tc_cases.py
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTEST = ROOT / "docs" / "contest"
OUT = CONTEST / "tc_cases.json"

sys.path.insert(0, str(ROOT / "bot"))
try:
    from action_specs import SPECS
except Exception:
    SPECS = {}

HEADS = ("고객 질문", "상담 정보", "대화 예시", "상황", "모범 처리", "직접 실행", "이 사례에 나오는 API")
_API = re.compile(r"API-(\d{2})")
_ARG = re.compile(r"(path|query|body)\.([a-z_]+)")
# 인자 뒤에 오는 리터럴 값 (예: body.amount는 34900, body.lane을 LANE2_HANDOFF로)
_ARG_VAL = re.compile(r"(?:path|query|body)\.([a-z_]+)\s*(?:은|는|을|를|에|에는)?\s*([A-Z0-9][A-Za-z0-9_.-]{2,})(?:로|으로|를|을|,|\.|\s|$)")
_LOT = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2,}-[A-Z]{2,}-\d{6}-\d{2}(?![0-9])")


def _sections(lines: list[str]) -> dict[str, list[str]]:
    """탭 제목이 먼저 한 번 나열되고 본문에서 다시 나오므로, 같은 제목의 마지막 등장 이후를 본문으로 본다."""
    idx = [(i, l.strip()) for i, l in enumerate(lines) if l.strip() in HEADS]
    out: dict[str, list[str]] = {}

    for k, (i, h) in enumerate(idx):
        end = idx[k + 1][0] if k + 1 < len(idx) else len(lines)
        body = [l.strip() for l in lines[i + 1 : end] if l.strip()]
        if body or h not in out:
            out[h] = body

    return out


def _method(api: str) -> str:
    return SPECS.get(api, {}).get("method", "?")


def parse_step(step: str) -> tuple[list[dict], list[str]]:
    """단계 문장에서 (호출해야 할 API 목록, 이 상담에서 호출하면 안 되는 API 목록)을 뽑는다."""
    sents = [x for x in re.split(r"(?<=다)\.\s*", step) if x.strip()]
    lots = _LOT.findall(step)
    apis, forbidden = [], []

    for api_no in dict.fromkeys(_API.findall(step)):
        api = f"API-{api_no}"
        mine = [x for x in sents if api in x]
        # 언급된 모든 문장이 "~하지 않는다"이고 "다른 대상/주문번호" 조건이 아니면 금지 API
        if all("않는다" in x for x in mine) and not any("다른" in x for x in mine):
            forbidden.append(api)
            continue

        args = {}
        for x in mine:
            i = x.index(api)
            nxt = _API.search(x, i + 6)
            seg = x[i : nxt.start()] if nxt else x[i:]
            pre = x[:i]
            names = _ARG.findall(seg) or _ARG.findall(pre[-60:])
            for _, n in names:
                args.setdefault(n, None)
            for n, v in _ARG_VAL.findall(seg):
                args[n] = v
        # 인자 설명이 API 언급 뒤 별도 문장에 있는 경우 (body.order_no는 ...)
        if len(dict.fromkeys(_API.findall(step))) == 1:
            for _, n in _ARG.findall(step):
                args.setdefault(n, None)
            for n, v in _ARG_VAL.findall(step):
                args[n] = args.get(n) or v
        for k in args:
            if args[k] is None and k == "lot_id" and lots:
                args[k] = lots[0]
            if args[k] is None and k == "shipment_id" and (m := re.search(r"SHP-[0-9]+-[0-9]+", step)):
                args[k] = m.group(0)
        apis.append({"api": api, "method": _method(api), "args": args})

    return apis, forbidden


def api_examples(tc: str) -> dict[str, str]:
    """API 화면의 curl 예시(처음 상태 데이터 값)를 모은다."""
    out = {}
    for f in sorted(CONTEST.glob(f"docs_api_API-*_tc_{tc}.md")):
        api = re.search(r"API-\d{2}", f.name).group(0)
        text = f.read_text(encoding="utf-8")
        m = re.search(r"Request Example for\s*\n.*?\n.*?\n(curl .*?)(?:\n\n|\nTest Request|\Z)", text, re.S)
        if m:
            ex = re.sub(r"--header 'Authorization[^']*'\s*\\?\n?\s*", "", m.group(1)).strip()
            out[api] = ex[:600]
    return out


def parse(path: Path) -> dict:
    tc = path.stem.replace("docs_test-cases_", "")
    lines = path.read_text(encoding="utf-8").splitlines()
    sec = _sections(lines)

    title = ""
    for i, l in enumerate(lines):
        if l.strip().startswith("공개 TC ·"):
            title = next((x.strip() for x in lines[i + 1 :] if x.strip()), "")
            break

    q = [l for l in sec.get("고객 질문", []) if "원본 열기" not in l]
    has_photo = any("원본 열기" in l for l in sec.get("고객 질문", []))
    dialogue = sec.get("대화 예시", [])
    photo_line = next((l for l in dialogue if l.startswith("[사진")), None)

    steps = sec.get("모범 처리", [])
    expected, forbidden = [], []
    for i, s in enumerate(steps):
        apis, neg = parse_step(s)
        expected += [{**a, "step": i + 1} for a in apis]
        forbidden += neg

    # API 언급이 없는 단계 = 말로 안내·확인해야 하는 내용
    must_say = [s for s in steps if not _API.search(s)]

    return {
        "id": tc,
        "title": title,
        "first_message": "\n".join(q),
        "photo": photo_line or ("[사진]" if has_photo else None),
        "dialogue_example": dialogue,
        "situation": "\n".join(sec.get("상황", [])),
        "expected_steps": steps,
        "expected_apis": expected,
        "forbidden_apis": sorted(set(forbidden)),
        "must_say": must_say,
        "listed_apis": [l.split("·")[0].strip() for l in sec.get("이 사례에 나오는 API", []) if l.startswith("API-")],
        "api_examples": api_examples(tc),
    }


def main():
    files = sorted(p for p in CONTEST.glob("docs_test-cases_*.md") if p.stem != "docs_test-cases")
    cases = [parse(p) for p in files]
    OUT.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    for c in cases:
        apis = ", ".join(f"{a['api']}{'*' if a['method'] == 'POST' else ''}{a['args']}" for a in c["expected_apis"])
        print(f"{c['id']:7} steps={len(c['expected_steps'])} say={len(c['must_say'])} apis: {apis} 금지={c['forbidden_apis']}")
    print(f"→ {OUT} ({len(cases)}건)")


if __name__ == "__main__":
    main()
