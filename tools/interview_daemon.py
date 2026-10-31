"""관계자별 질문 대기열(knowledge/queue/<이름>.txt)을 계속 비우는 상주 프로세스.

python tools/interview_daemon.py [종료시각 HH:MM]
- 대기열 파일에 한 줄에 질문 하나씩 추가하면, 그 사람의 이전 답을 받은 뒤 순서대로 보낸다.
- 보낸 질문은 대기열에서 지우고, 질문과 답은 knowledge/raw/<이름>.md와 knowledge/queue/answers.md에 남는다.
- 종료시각이 지나면 새 질문을 보내지 않고 끝난다.
"""

import datetime
import pathlib
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import kakao_pc as k

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
QDIR = ROOT / "knowledge" / "queue"
QDIR.mkdir(parents=True, exist_ok=True)
STOP = sys.argv[1] if len(sys.argv) > 1 else "14:10"
NAMES = ["이민준", "구방만", "고길동", "홍보라", "박수복", "홍이장", "박민아", "조억만", "김청년", "홍보걸"]
out_lock = threading.Lock()


def stopped():
    return datetime.datetime.now().strftime("%H:%M") >= STOP


def pop(name):
    f = QDIR / f"{name}.txt"
    with out_lock:
        if not f.exists():
            return None
        lines = [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not lines:
            return None
        f.write_text("\n".join(lines[1:]) + ("\n" if lines[1:] else ""), encoding="utf-8")
        return lines[0].strip()


def worker(name):
    while not stopped():
        q = pop(name)
        if q is None:
            time.sleep(4)
            continue
        try:
            a = k.ask(name, q[:200])
        except BaseException as e:  # noqa: BLE001
            a = f"(오류: {e})"
        with out_lock:
            with open(QDIR / "answers.md", "a", encoding="utf-8") as f:
                f.write(f"\n##### {datetime.datetime.now():%H:%M} {name} ← {q}\n{a}\n")
            print(f"{name} 완료: {q[:40]}", flush=True)


threads = [threading.Thread(target=worker, args=(n,), daemon=True) for n in NAMES]
for t in threads:
    t.start()
while not stopped() or any(t.is_alive() for t in threads):
    time.sleep(5)
    if stopped():
        break
