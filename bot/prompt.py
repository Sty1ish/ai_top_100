"""시스템 프롬프트와 도구 목록을 누적 분류에 맞춰 조립한다.

순서: 도구(분류 순) → 코어 → 분류별 지식 절 → 상담 정보. 앞 세 개에 캐시 지점을 둔다.
지식 파일은 부를 때마다 읽으므로 파일을 고치면 바로 반영된다.
"""

from datetime import date
from pathlib import Path

import actions
from classify import CATEGORIES

KNOWLEDGE = Path(__file__).resolve().parent.parent / "knowledge"
POLICY_DIR = KNOWLEDGE / "policy"
DOCS_DIR = KNOWLEDGE / "docs"
DOC_LIMIT = 6000
WEEKDAYS = "월화수목금토일"

CACHE = {"type": "ephemeral"}

LOCAL_TOOLS = [
    {
        "name": "load_policy",
        "description": "다른 분류의 정책·지식 절을 불러온다. 지금 받은 지식에 답이 없을 때 쓴다.",
        "input_schema": {
            "type": "object",
            "properties": {"category": {"type": "string", "enum": list(CATEGORIES)}},
            "required": ["category"],
        },
    },
    {
        "name": "date_calc",
        "description": "날짜 계산. diff: date1→date2 경과일, add: date1+days, add_bdays: date1+영업일 days(주말 제외, 회신 기한 계산용), weekday: date1의 요일. 날짜 계산은 반드시 이 도구로 한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["diff", "add", "add_bdays", "weekday"]},
                "date1": {"type": "string", "description": "YYYY-MM-DD"},
                "date2": {"type": "string", "description": "YYYY-MM-DD (diff)"},
                "days": {"type": "integer", "description": "더할 일수 (add, 음수 가능)"},
            },
            "required": ["op", "date1"],
        },
    },
]


def doc_names() -> list[str]:
    return sorted(p.stem for p in DOCS_DIR.glob("*.md")) if DOCS_DIR.exists() else []


def read_doc(name: str) -> str:
    path = DOCS_DIR / f"{Path(name).stem}.md"

    if not path.exists():
        return f"없는 문서입니다. 가능한 문서: {', '.join(doc_names())}"

    text = path.read_text(encoding="utf-8")

    return text[:DOC_LIMIT] + ("\n…(이하 생략)" if len(text) > DOC_LIMIT else "")


def doc_tool() -> dict:
    return {
        "name": "load_doc",
        "description": "규정 원문을 불러온다. 지식 절에 없는 세부 조문이 필요할 때만 쓴다. 문서: " + ", ".join(doc_names()),
        "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    }


def read_policy(category: str) -> str:
    path = POLICY_DIR / f"{category}.md"

    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def core() -> str:
    return (KNOWLEDGE / "core.md").read_text(encoding="utf-8").strip()


def ordered(categories: list[str]) -> list[str]:
    return [c for c in CATEGORIES if c in categories]


def build_tools(categories: list[str]) -> list[dict]:
    # 분류가 없어도 이관(API_35)은 늘 쓸 수 있게 한다
    tools = actions.tools_for(ordered(categories) or ["other"]) + LOCAL_TOOLS + ([doc_tool()] if doc_names() else [])
    tools[-1] = {**tools[-1], "cache_control": CACHE}

    return tools


def build_system(session) -> list[dict]:
    sections = [f"## 지식: {c}\n{read_policy(c)}" for c in ordered(session.categories) if read_policy(c)]
    blocks = [{"type": "text", "text": core(), "cache_control": CACHE}]

    if sections:
        blocks.append({"type": "text", "text": "\n\n".join(sections), "cache_control": CACHE})

    blocks.append({"type": "text", "text": state(session)})

    return blocks


def state(session) -> str:
    d = date.fromisoformat(session.today)
    lines = [
        "## 상담 정보",
        f"- 오늘(문의일) = {session.today}({WEEKDAYS[d.weekday()]}). 기간·기한 판단은 이 날짜 기준.",
        f"- TC ID = {session.tc_id or '없음'}",
        "- 답은 한 문단, 150자 이내. 도구 이름·내부 직원 이름은 쓰지 않음. 접수번호를 받은 처리만 완료라고 말함.",
    ]

    for key, values in session.notes.items():
        lines.append(f"- {key}: {', '.join(values[-5:])}")

    return "\n".join(lines)


def date_calc(op: str, date1: str, date2: str | None = None, days: int | None = None) -> dict:
    try:
        d1 = date.fromisoformat(date1)

        if op == "diff":
            return {"days": (date.fromisoformat(date2) - d1).days}

        if op == "add":
            r = date.fromordinal(d1.toordinal() + int(days or 0))

            return {"date": r.isoformat(), "weekday": WEEKDAYS[r.weekday()]}

        if op == "add_bdays":
            r, left = d1, int(days or 0)

            while left > 0:
                r = date.fromordinal(r.toordinal() + 1)
                left -= r.weekday() < 5

            return {"date": r.isoformat(), "weekday": WEEKDAYS[r.weekday()]}

        if op == "weekday":
            return {"weekday": WEEKDAYS[d1.weekday()]}
    except Exception as e:
        return {"error": f"잘못된 입력: {e}"}

    return {"error": "op는 diff, add, weekday 중 하나"}
