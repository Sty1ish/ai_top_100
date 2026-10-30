# AI_TOP_100 [2026] 카카오톡 봇

교동마을(제철 농산물)과 갯마을(수산물) 조합의 업무를 돕는 카카오톡 채널 챗봇입니다.
모든 모델은 미니 PC(Intel N100, GPU 없음, RAM 16GB, Ubuntu)에서 로컬로 돌고, 외부 LLM API는 쓰지 않습니다.

- 메시지 처리 흐름: [docs/FLOW.md](docs/FLOW.md), [docs/flow.png](docs/flow.png)
- 채널: http://pf.kakao.com/_wINWX/chat

## 구성

| 파일 | 역할 |
|---|---|
| `bot/app.py` | 웹훅 서버(FastAPI). 204를 즉시 돌려주고, 답변은 백그라운드에서 콜백으로 보냄 |
| `bot/router.py` | 결정 모델로 의도를 분류해 처리기로 보냄 (빠른 경로) |
| `bot/decide.py` | 결정 모델(Julia-1) 호출 |
| `bot/tools.py` | 정산·계산 도구. 숫자 계산은 여기서만 함 |
| `bot/agent.py` | 로컬 LLM(Gemma 4) 호출 (느린 경로) |
| `bot/kakao.py` | 카카오 이벤트 파싱, 말풍선 생성, 콜백 호출 |
| `bot/render.py` | LLM의 구조화 답변을 말풍선으로 변환 (선택 기능, `STRUCTURED=1`) |
| `bot/bench_*.py` | 모델 속도·정확도 측정 |

## 서버 준비 (Ubuntu)

### 1. 봇 서버
```bash
mkdir -p ~/ai_top_100 && cd ~/ai_top_100
# bot/ 폴더를 이 위치에 복사
sudo apt-get install -y python3.12-venv
python3 -m venv .venv
.venv/bin/pip install -r bot/requirements.txt
```

`~/ai_top_100/.env` 파일을 만듭니다. 권한은 `chmod 600`으로 두고, 저장소에는 올리지 않습니다.
```
BOT_TOKEN=카카오 디벨로퍼스 [제품 설정] > [카카오톡 봇] > [봇]의 인증 토큰
WEBHOOK_SECRET=임의의 긴 문자열 (웹훅 등록 시 X-Bot-Secret 헤더 값과 같게)
MODEL=gemma4:e2b
```

### 2. LLM: Gemma 4 (ollama)
```bash
curl -fsSL https://ollama.com/install.sh -o /tmp/ollama_install.sh
sudo sh /tmp/ollama_install.sh      # systemd 서비스로 설치되고 127.0.0.1:11434에서 대기
ollama pull gemma4:e2b              # 4.6GB, 실제 사용 모델
ollama list
```
- 모델 파일 위치: `/usr/share/ollama/.ollama/models`
- RAM 16GB에서는 `e2b`나 `e4b`만 올라갑니다. `26b` 이상은 메모리가 부족합니다.
- N100 CPU 기준 e2b는 초당 4~7토큰입니다. 짧은 답도 10초 넘게 걸리므로 빠른 경로에서 처리하지 못한 질문에만 씁니다.

### 3. 결정 모델: Julia-1 (llama.cpp)
결정 모델은 문장을 생성하지 않고 선택지별 확률만 돌려주는 분류용 모델입니다. ollama는 이 방식을 지원하지 않아서 llama.cpp 서버로 띄웁니다.
```bash
mkdir -p ~/ai_top_100/llama/models && cd ~/ai_top_100/llama
sudo apt-get install -y libgomp1
curl -fsSL -o l.tgz https://github.com/ggml-org/llama.cpp/releases/download/b12017/llama-b12017-bin-ubuntu-x64.tar.gz
tar xzf l.tgz && rm l.tgz
curl -fsSL -o models/Julia-1-Q8_0.gguf https://huggingface.co/ggml-org/Julia-1-GGUF/resolve/main/Julia-1-Q8_0.gguf   # 168MB

# 동작 확인
./llama-b12017/llama-server -m models/Julia-1-Q8_0.gguf --host 127.0.0.1 --port 8091 -t 4 &
curl -s 127.0.0.1:8091/v1/systemone -H 'Content-Type: application/json' -d '{
  "state": {"message": "고등어 30kg 정산해줘"},
  "questions": {"intent": {"type": "choice", "instructions": "사용자 요청의 종류",
    "criteria": {"settle": "납품·판매 금액 정산", "other": "그 밖의 질문"}}}}'
```

후보 모델을 같은 한국어 의도 분류 문항으로 비교한 결과입니다. `bench_decide.py`로 측정했고, 측정 당시 CPU를 다른 모델과 나눠 써서 속도는 참고용입니다.

| 모델 | 크기 | 정확도 (16문항) | 속도 중앙값 | 비고 |
|---|---|---|---|---|
| **Julia-1** | 144M | 11/16 | 0.15초 (단독 실행 시) | 다국어, 채택 |
| Kev-4B | 4B | 13/16 | 15초 | 영어 위주, 너무 느림 |
| Laya | 421M | 6/16 | 6초 | 영어 전용, 한국어 부적합 |

### 4. 서비스 등록 (재부팅 시 자동 시작)
`/etc/systemd/system/decide-julia.service`
```ini
[Unit]
Description=AI_TOP_100 decision model (Julia-1, llama.cpp)
After=network-online.target

[Service]
User=stylish1379
WorkingDirectory=/home/stylish1379/ai_top_100/llama
ExecStart=/home/stylish1379/ai_top_100/llama/llama-b12017/llama-server -m models/Julia-1-Q8_0.gguf --host 127.0.0.1 --port 8091 -t 4
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/kakao-bot.service`
```ini
[Unit]
Description=AI_TOP_100 KakaoTalk bot
After=network-online.target ollama.service decide-julia.service
Wants=ollama.service decide-julia.service

[Service]
User=stylish1379
WorkingDirectory=/home/stylish1379/ai_top_100/bot
EnvironmentFile=/home/stylish1379/ai_top_100/.env
ExecStart=/home/stylish1379/ai_top_100/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8090
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now decide-julia kakao-bot
```

### 5. HTTPS (nginx)
카카오 웹훅은 공인 인증서가 있는 HTTPS만 받습니다. Let's Encrypt 인증서는 [lego](https://github.com/go-acme/lego)로 TLS-ALPN 방식으로 받았습니다(80번 포트가 막혀 있어서).
```bash
sudo systemctl stop nginx
sudo lego --accept-tos --email admin@도메인 --domains 도메인 --tls --tls.port :443 --path /etc/lego run
sudo systemctl start nginx
```
nginx의 443 서버 블록에 아래 내용을 추가합니다.
```nginx
ssl_certificate     /etc/lego/certificates/도메인.crt;
ssl_certificate_key /etc/lego/certificates/도메인.key;

location /kakao/ {
    proxy_pass http://localhost:8090/;
    proxy_read_timeout 60s;
}
```

## 카카오 설정
1. 카카오 디벨로퍼스 앱 → [제품 설정] > [카카오톡 봇] > [봇]에서 봇을 만들고 인증 토큰을 `.env`에 넣습니다.
2. 같은 화면에서 웹훅을 등록합니다.
   - URL: `https://도메인/kakao/webhook`
   - 헤더: `X-Bot-Secret: <WEBHOOK_SECRET>`
3. 카카오비즈니스 채널 설정
   - 채널 **공개**, **채팅 사용**
   - 채팅 시간 **매일 00:00~23:59**: 기본값이 평일 9~18시라서 주말에는 부재중 안내만 나가고 웹훅이 오지 않습니다.
   - 검색 허용은 **끔** (대회 규칙)

## 운영 확인
```bash
curl -s https://도메인/kakao/health        # {"ok":true,...,"bot_token":true}
journalctl -u kakao-bot -f                 # 수신 이벤트, 콜백 결과
```
봇을 재시작하면 생성 중이던 답변이 사라지므로, 심사 기간에는 재시작을 피합니다.
