"""관계자별 질문 대기열을 동시에 진행한다. 한 사람에게는 답을 받은 뒤에만 다음 질문을 보낸다.

python tools/interview_batch.py <질문파일>
질문파일 형식: 한 줄에 "이름<TAB>질문" (200자 이하). 같은 이름의 질문은 위에서부터 순서대로 보낸다.
결과는 knowledge/raw/<이름>.md에 기록되고, 표준출력에도 요약을 찍는다.
"""

import collections
import sys
import threading

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
import kakao_pc as k

sys.stdout.reconfigure(encoding="utf-8")
queues = collections.OrderedDict()
for line in open(sys.argv[1], encoding="utf-8"):
    if "\t" in line:
        name, q = line.rstrip("\n").split("\t", 1)
        queues.setdefault(name.strip(), []).append(q.strip())

print_lock = threading.Lock()


def run(name, qs):
    for q in qs:
        try:
            a = k.ask(name, q)
        except BaseException as e:  # noqa: BLE001 — 한 사람 실패가 전체를 멈추지 않게
            a = f"(오류: {e})"
        with print_lock:
            print(f"\n##### {name} ← {q}\n{a[:700]}", flush=True)


threads = [threading.Thread(target=run, args=(n, qs)) for n, qs in queues.items()]
for t in threads:
    t.start()
for t in threads:
    t.join()
