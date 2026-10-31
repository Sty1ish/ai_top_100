"""user_key별 상담 세션(메모리). 30분 동안 말이 없으면 새로 시작한다.

TC ID와 문의일은 처음 찾은 값으로 고정하고, 분류는 늘기만 한다.
"""

import asyncio
import re
import time
from dataclasses import dataclass, field

IDLE_RESET = 30 * 60
MAX_TURNS = 10
DEFAULT_DATE = "2037-06-30"

_TC_ID = re.compile(r"\b(?:[LRY]-\d{2}(?:-[a-z])?|[A-Z]{1,2}-\d{2,3}(?:-[a-z])?)\b")
_FULL_DATE = re.compile(r"(20\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})")
# 연도 없는 날짜는 "문의일·오늘" 같은 표지가 붙을 때만 문의일로 본다 (배송일 등과 헷갈리지 않게)
_MD_DATE = re.compile(r"(?:문의|접수|오늘|상담)[^0-9\n]{0,8}(\d{1,2})\s*월\s*(\d{1,2})\s*일|(\d{1,2})\s*월\s*(\d{1,2})\s*일[^\n]{0,4}(?:문의|접수)")
_DATE_HINT = re.compile(r"문의|접수|오늘|상담")


@dataclass
class Session:
    user_key: str
    turns: list[list[dict]] = field(default_factory=list)  # 턴별 메시지 묶음 (anthropic 형식)
    tc_id: str | None = None
    inquiry_date: str | None = None
    categories: list[str] = field(default_factory=list)  # 추가된 순서 유지
    notes: dict[str, list[str]] = field(default_factory=dict)  # 확인된 주문번호·회원·접수번호
    looked_up: set[str] = field(default_factory=set)
    called: set[str] = field(default_factory=set)  # 이미 한 도구 호출 (이름+인자)
    escalated: set[str] = field(default_factory=set)  # 이관한 lane:주문  # 조회해 본 주문번호 (접수 전 확인용)
    last_user_text: str = ""
    last_at: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def today(self) -> str:
        return self.inquiry_date or DEFAULT_DATE

    def history(self) -> list[dict]:
        return [m for turn in self.turns[-MAX_TURNS:] for m in turn]

    def add_turn(self, messages: list[dict]):
        self.turns.append(messages)
        del self.turns[:-MAX_TURNS]

    def note(self, key: str, value: str):
        values = self.notes.setdefault(key, [])

        if value not in values:
            values.append(value)

    def add_categories(self, cats: list[str]) -> list[str]:
        added = [c for c in dict.fromkeys(cats) if c not in self.categories]
        self.categories += added

        return added

    def observe(self, text: str):
        """메시지에서 TC ID·문의일을 찾아 처음 값으로 고정한다."""
        first = not self.turns

        if self.tc_id is None and (m := _TC_ID.search(text)):
            self.tc_id = m.group(0)

        if self.inquiry_date is None:
            self.inquiry_date = _find_date(text, first)


def _find_date(text: str, first: bool) -> str | None:
    for m in _FULL_DATE.finditer(text):
        around = text[max(0, m.start() - 12) : m.end() + 6]

        if first or _DATE_HINT.search(around):
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    if m := _MD_DATE.search(text):
        month, day = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))

        return f"2037-{int(month):02d}-{int(day):02d}"

    return None


_sessions: dict[str, Session] = {}


def get(user_key: str) -> Session:
    now = time.monotonic()
    s = _sessions.get(user_key)

    # 처리 중(lock 잡힘)인 세션은 초기화하지 않는다
    if s is None or (now - s.last_at > IDLE_RESET and not s.lock.locked()):
        s = _sessions[user_key] = Session(user_key)

    s.last_at = now

    return s


def reset(user_key: str):
    _sessions.pop(user_key, None)
