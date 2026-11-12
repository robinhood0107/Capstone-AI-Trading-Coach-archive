# MARS 이전 개발판: 투자 원칙 기반 AI 트레이딩 코치

> 이 저장소는 이전 개발판입니다. 개발은 [현재 저장소](https://github.com/robinhood0107/Capstone-AI-Trading-Coach)로 이어졌습니다. 이 문서의 기능, 명령과 설정은 당시 판본의 기록이며 현행 동작을 보증하지 않습니다. GitHub의 저장소 보관 상태와 개발 중단 여부는 별개입니다.

## 목차

- [프로젝트 소개와 문제](#프로젝트-소개와-문제)
- [주요 기능과 사용 흐름](#주요-기능과-사용-흐름)
- [화면과 시연 영상](#화면과-시연-영상)
- [시스템 구조와 구성 요소](#시스템-구조와-구성-요소)
- [기술 스택](#기술-스택)
- [빠른 시작](#빠른-시작)
- [저장소 구조](#저장소-구조)
- [개발과 검증, 주요 판단과 한계](#개발과-검증-주요-판단과-한계)
- [팀과 기여](#팀과-기여)
- [문서와 라이선스](#문서와-라이선스)

## 프로젝트 소개와 문제


금융 정보와 모델 결과는 초보 투자자가 이해하기 어렵고, 결과만 제시하는 자동매매 서비스는 왜 그런
판단을 했는지 알기 어렵습니다. 이 프로젝트는 이러한 정보 격차를 줄이고 보다 안전하게 투자 판단을
학습할 수 있도록 다음 두 목표를 함께 추구합니다.

### 실제 흐름을 경험하는 AI 자동매매 봇

사용자가 등록한 KIS 투자계좌에서 후보 선정, 위험 검토, 주문, 취소, 체결 대사와 청산까지 하나의
폐루프로 실행합니다. 수익률 계산뿐 아니라 실제 거래 시스템이 어떤 안전장치를 거치는지 직접
확인할 수 있습니다.

### 근거와 위험을 설명하는 교육용 투자 의사결정

- 금융 개념과 위험 지표를 쉬운 설명과 출처로 제공합니다.
- 모델이 BUY, HOLD, SELL을 낸 이유와 서로 다른 모델의 결과를 비교합니다.
- 주문이 허용되거나 차단된 원칙과 RiskEngine 판단을 숨기지 않습니다.
- 손절, 변동성, MDD, ATR과 포트폴리오 한도가 결과에 미치는 영향을 보여 줍니다.
- 모든 판단과 주문 결과를 학습일지에 남겨 자신의 의사결정을 되돌아볼 수 있게 합니다.

이를 통해 사용자가 AI 추천을 그대로 따르기보다 **근거를 확인하고, 위험을 이해하고, 자신의 투자
원칙으로 최종 판단하는 습관**을 기르는 것이 이 프로젝트의 공익적, 교육적 목표입니다. 실제 자금의
손실 위험이 없는 Mock 모드를 기본 학습 경로로 제공하는 것도 같은 이유입니다.

## 주요 기능과 사용 흐름

| 기능 | 설명 |
|---|---|
| 교육용 투자 의사결정 | 금융 개념, 모델 판단, 위험 지표와 주문 차단 이유를 근거와 함께 설명합니다. |
| 투자 원칙 관리 | 프리셋으로 원칙을 만들고 자금 한도, 손절, 익절과 보유 기간을 조정합니다. |
| 모델 신호 비교 | LSTM과 규칙 기반 모델의 BUY, HOLD, SELL 신호를 같은 기준으로 비교합니다. |
| 위험 검토 | 주문 전 RiskEngine이 원칙 위반, 포트폴리오 한도와 Kill Switch를 확인합니다. |
| 근거 있는 AI 설명 | 저장된 자료와 검증된 공개 근거를 바탕으로 설명과 출처를 함께 보여 줍니다. |
| KIS 투자계좌 연결 | 사용자의 App Key, Secret, 계좌번호로 주문 가능 금액, 잔고와 체결 내역을 조회합니다. |
| AI 자동매매 봇 | 검증된 후보만 자동 매수하고 손절, 익절, ATR trailing stop과 보유 만기를 관리합니다. |
| 백테스트 | 거래비용을 포함한 전략 결과와 MDD, Sharpe, 손익 곡선을 확인합니다. |
| 학습일지 | 판단, 주문, AI 답변과 자동운용 실행 기록을 하나의 일지로 연결합니다. |

### 당시 사용 흐름


```mermaid
flowchart LR
    A["로그인"] --> B["투자 원칙 설정"]
    B --> C["모델 신호와 근거 확인"]
    C --> D["주문 후보 입력"]
    D --> E["RiskEngine 검토"]
    E --> F["선택한 KIS 모드로 주문"]
    F --> G["체결·취소·대사"]
    G --> H["포지션 관리"]
    H --> I["학습일지와 리포트"]
```

자동운용에서도 순서는 같습니다. AI는 후보의 순위를 조정하거나 근거가 있는 매수를 막을 수 있지만,
새 종목을 추가하거나 주문 수량을 늘리거나 RiskEngine을 우회할 수 없습니다. 주문 수량과 청산 조건은
항상 서버의 결정적 규칙이 계산합니다.

## 화면과 시연 영상

당시 대시보드는 투자 원칙, 금융 가이드, 모델 비교, 주문 검토, 자동운용과 학습일지를 연결했습니다. 주문 검토는 원칙과 위험 판정을 표시하고 자동운용 화면은 실행 이력과 차단 사유를 보여 줍니다. 금융 Agent는 답변의 출처를 제공하고 질문과 답변을 학습일지에 남길 수 있도록 했습니다.

원본 README에 실린 모의 시연 화면입니다. 화면에 표시된 잔고와 백테스트 수치는 실제 투자 수익이나 검증된 성과의 근거가 아닙니다.

| 자동운용 | 주문 검토 |
|---|---|
| <img src="docs/readme-media/automation.png" width="420" alt="자동운용 화면"> | <img src="docs/readme-media/order-review.png" width="420" alt="주문 검토 화면"> |
| **백테스트** | **금융 Agent** |
| <img src="docs/readme-media/backtest.png" width="420" alt="백테스트 화면"> | <img src="docs/readme-media/agent-answers.png" width="420" alt="금융 Agent 답변 화면"> |


[![MARS 시연 영상 썸네일](https://img.youtube.com/vi/CU7284u1rk0/0.jpg)](https://www.youtube.com/watch?v=CU7284u1rk0)

[YouTube에서 시연 영상 보기](https://www.youtube.com/watch?v=CU7284u1rk0)

다음 영상은 제품 탐색을 기록한 로컬 WebM 파일입니다. 링크에서 파일을 열거나 내려받아 재생할 수 있습니다. 최초 실행 영상은 API 미기동 상태의 부분 프런트 탐색이며, 어느 영상도 촬영만으로 전체 기능 동작을 검증하지 않습니다.

- demo desktop 탐색 영상: [navigation-5cde37df75297008bb7a.webm](docs/readme-media/navigation-5cde37df75297008bb7a.webm)
- demo desktop 탐색 영상: [navigation-3b26eccda4e09af7b4b1.webm](docs/readme-media/navigation-3b26eccda4e09af7b4b1.webm)
- demo mobile 탐색 영상: [navigation-4c7b53fbe4d9063fddc9.webm](docs/readme-media/navigation-4c7b53fbe4d9063fddc9.webm)
- demo mobile 탐색 영상: [navigation-25b132d5a9fc3b27660a.webm](docs/readme-media/navigation-25b132d5a9fc3b27660a.webm)
- experience-dashboard desktop 탐색 영상: [navigation-ad17ae9fcd2bf11bd830.webm](docs/readme-media/navigation-ad17ae9fcd2bf11bd830.webm)
- experience-dashboard mobile 탐색 영상: [navigation-0623ef5d1f3fc316e76b.webm](docs/readme-media/navigation-0623ef5d1f3fc316e76b.webm)
- initial desktop 탐색 영상: [navigation-c977158a33572c2b4a90.webm](docs/readme-media/navigation-c977158a33572c2b4a90.webm)
- initial mobile 탐색 영상: [navigation-86e613c9e66c70eebf51.webm](docs/readme-media/navigation-86e613c9e66c70eebf51.webm)

## 시스템 구조와 구성 요소

```mermaid
flowchart TB
    UI["Experience Dashboard\nNext.js · TypeScript"]
    API["Decision Platform\nSpring Boot · Kotlin"]
    PY["AI·데이터·자동운용 서비스\nPython · gRPC"]
    RE["Return Engine\nLSTM · Rule · Backtest"]
    DB[("PostgreSQL + pgvector")]
    CACHE[("Redis")]
    KIS["KIS Brokerage API\nMock 또는 Live 단일 모드"]
    AI["Vertex AI · Voyage AI"]

    UI -->|same-origin /api| API
    API --> PY
    API --> DB
    PY --> DB
    PY --> CACHE
    RE -->|검증된 artifact| API
    PY --> KIS
    PY --> AI
```

## 기술 스택

기본 실행은 다음 다섯 개 컨테이너로 구성됩니다. `--mock`은 일일 시장데이터 수집기
(`market-data-daily`)를 더해 여섯 개가 되고, `--models`는 모델 런타임 두 개를 더합니다.

1. PostgreSQL
2. Redis
3. 권한 발급 서비스
4. Decision Platform
5. Experience Dashboard

임베딩과 OCR 모델이 필요하면 BGE-M3와 PaddleOCR-VL 컨테이너를 추가할 수 있습니다. 데이터베이스
마이그레이션, Seed 적재와 초기 계정 생성은 `docker compose run --rm` 형식의 일회성 작업으로 실행됩니다.

## 빠른 시작

아래는 이전 README에 기록된 절차입니다. 현재 저장소의 명령과 섞지 마세요. Docker와 Compose v2, Git, Python 3, OpenSSL이 필요했고 Windows에서는 WSL2를 사용했습니다. 과거 README의 clone 대상은 당시 명칭이며, 이 문서에서는 이전 저장소 주소를 명시합니다. 실제 실행은 재검증하지 않았습니다.

```bash
git clone https://github.com/robinhood0107/Capstone-AI-Trading-Coach-archive.git
cd Capstone-AI-Trading-Coach-archive
./capstone doctor
./capstone up
./capstone status
./capstone logs
./capstone down
```

웹 화면은 `http://127.0.0.1:3000`, API 상태는 `http://127.0.0.1:18080/actuator/health`, API 문서는 `http://127.0.0.1:18080/swagger-ui.html`로 안내했습니다. 기본 환경과 별도로 `P1_DEMO_STACK=true ./capstone up`을 이용한 시연 스택이 있었습니다. 종료는 볼륨을 유지하는 `down`을 사용합니다.

당시 모의계좌 연결에는 `KIS_MOCK_APP_KEY`, `KIS_MOCK_APP_SECRET`, `KIS_MOCK_ACCOUNT_NO`가 필요했습니다. 기본 기동 후 `mock configure`가 세 값을 입력받고 `mock doctor`가 로컬 자격 증명 파일 형식과 권한을 검사했습니다. `mock doctor`는 계좌나 주문 API를 호출하지 않는다고 안내했습니다. RAG 답변 생성에는 Voyage 키와 Google Cloud Vertex 서비스 계정 JSON도 필요했습니다.

```bash
./capstone mock configure
./capstone mock doctor
printf 'VOYAGE_API_KEY=%s\n' "<발급받은 key>" >> deploy/p1/.state-app/secrets/rag-v2.env
chmod 640 deploy/p1/.state-app/secrets/rag-v2.env
install -m 640 <다운로드한 파일>.json \
  deploy/p1/.state-app/secrets/pre-s5-vertex-service-account.json
```

처음 연결할 때 다음 인증은 XKRX 거래일 09:10~15:00 KST에 수행하도록 안내했습니다. `mock certify`는 실제 KIS 모의주문과 취소를 호출하고 체결과 계좌를 대사합니다. 상태 조회만 하는 명령이 아니며 실행하면 모의계좌에 주문과 취소가 발생합니다.

```bash
./capstone mock certify --symbol 005930 --quantity 1
./capstone up --mock
./capstone mock readiness
./capstone mock start
```

이전 README에는 Live 모드도 기술돼 있지만 이 문서의 재현 경로에는 포함하지 않았습니다.

### 당시 환경 변수와 실행 옵션

기본 화면은 외부 키 없이 둘러보도록 안내했습니다. 자동매매와 RAG를 함께 이용하는 당시 최소 설정에는 KIS 모의투자 값과 `VOYAGE_API_KEY`, Google Cloud Vertex 서비스 계정 JSON이 필요했습니다. 비밀값은 실제 값을 문서에 적지 않고 전용 저장 위치에 두도록 안내했습니다.



| 변수 | 용도 | 기본값 |
|---|---|---|
| `P1_API_PORT` | API 호스트 포트 | `18080` |
| `P1_DASHBOARD_PORT` | Dashboard 호스트 포트 | `3000` |
| `P1_PROJECT_NAME` | Docker Compose 프로젝트 이름 | `capstone-p1` |
| `P1_STATE_DIR` | 비밀값과 실행 상태 경로 | `deploy/p1/.state-app` |
| `P1_SECRETS_DIR` | secret 파일 경로 | `<state>/secrets` |
| `P1_RAG_RUNTIME_DIR` | RAG 로컬 artifact 루트 | `<state>/rag-v2-root` |
| `P1_KIS_MOCK_RUNTIME_DIR` | 모의투자 인증 영수증 경로 | `<state>/mock` |

```bash
P1_API_PORT=18081 P1_DASHBOARD_PORT=3001 ./capstone up
```

#### 기능 스위치

기본값은 재료가 있으면 켜고 없으면 끄는 자동 판단입니다. 아래 값을 직접 지정하면 자동 판단을
끄고 강제합니다. `true`로 강제했는데 재료가 없으면 기동이 닫힙니다.

| 변수 | 값 | 설명 |
|---|---|---|
| `P1_RAG_V2_ENABLED` | `true`/`false` | RAG v2 검색 |
| `P1_RAG_V2_VERTEX_ENABLED` | `true`/`false` | RAG 답변 생성 |
| `P1_RAG_V2_VERTEX_AUTO_ACTIVATION_ENABLED` | `true`/`false` | Vertex 자동 활성화 정책 |
| `P1_STRONG_LLM_ENABLED` | `true`/`false` | Strong LLM 판단 |
| `P1_AUTOMATION_RUNTIME_ENABLED` | `true`/`false` | 자동운용 runtime 프로세스 |
| `P1_KIS_MOCK_ONLINE_ENABLED` | `true`/`false` | KIS 모의투자 어댑터 |

`--mock` 옵션이 뒤의 세 값을 함께 맞추므로 보통은 직접 지정하지 않습니다.

### 당시 추가 실행 옵션

```bash
./capstone up --models
./capstone up --mock
./capstone up --models --mock
```

`--models`는 당시 로컬 모델 컨테이너를 추가했고 `--mock`은 모의투자 구성을 켰습니다. 인증과 주문 실행은 별도 조건을 요구하며, 여기 적힌 명령의 현행 호환성은 확인하지 않았습니다.

## 저장소 구조

```text
Capstone-AI-Trading-Coach/
├── capstone                         # Linux·WSL 통합 실행 명령
├── capstone.ps1                     # Windows PowerShell 실행 명령
├── deploy/p1/                       # Docker Compose와 배포 스크립트
├── workspaces/
│   ├── decision-platform/           # Spring API, Python AI·데이터·자동운용
│   ├── return-engine/               # LSTM, 규칙 모델, 백테스트
│   └── experience-dashboard/        # Next.js Dashboard
├── contracts/                       # OpenAPI, JSON Schema, protobuf 계약
├── capstone-rag/                    # 공개 RAG 자료와 source card
├── docs/                            # 설계, API와 운영 문서
└── artifacts/                       # 검증 가능한 소형 결과와 예시
```

처음 프로젝트를 살펴본다면 `workspaces/`보다 아래의 **빠른 실행**부터 따라 하는 것이 가장 쉽습니다.

## 개발과 검증, 주요 판단과 한계

### 당시 안전 경계



- 기본 상태는 `DISARMED`입니다.
- 한 거래 세션의 신규 주문은 최대 한 건입니다.
- 동시에 보유할 수 있는 포지션은 최대 다섯 개입니다.
- 고정 손절, ATR trailing stop, 모델 SELL, 익절과 최대 보유 세션을 순서대로 평가합니다.
- 부분체결과 미확정 주문은 새 주문보다 먼저 대사합니다.
- 계좌 상태가 예상과 다르거나 취소가 불확실하면 `HALTED`로 전환합니다.
- KIS 장애를 내부 가상계좌로 자동 전환하지 않습니다.
- Mock과 Live를 동시에 사용하거나 한쪽 실패를 다른 쪽으로 자동 전환하지 않습니다.
- Live는 이전 모드의 OPEN 주문, 포지션, 미해결 대사가 하나라도 있으면 provider 호출 전에 차단합니다.
- AI는 주문을 직접 생성하거나 수량을 늘릴 권한이 없습니다.

### 당시 대표 API



아래는 당시 README가 열거한 API이며 현재 계약과 다를 수 있습니다.

| 영역 | 대표 API |
|---|---|
| 인증 | `POST /api/v1/auth/login` |
| 투자 원칙 | `GET /api/v1/principles`, `POST /api/v1/principles` |
| 위험 판단 | `POST /api/v1/decisions/evaluate-order` |
| RAG | `POST /api/v2/rag/ask`, `GET /api/v2/rag/history` |
| 투자계좌 | `GET /api/v1/brokerage/mock/accounts/{accountId}/balances` |
| 주문 대사 | `POST /api/v1/brokerage/orders/{orderId}/reconcile` |
| 체결 조회 | `GET /api/v1/brokerage/mock/accounts/{accountId}/fills` |
| 내부 가상원장 체결 | `GET /api/v1/brokerage/paper/accounts/{accountId}/fills` |
| 자동운용 | `GET /api/v3/automation/status`, `POST /api/v3/automation/arm` |
| 학습일지 | `POST /api/v1/journals`, `GET /api/v1/journals` |

### 당시 개발과 검증 명령



전체 프로그램 사용자는 이 절을 건너뛰어도 됩니다.

### Python 서비스

```bash
cd workspaces/decision-platform/python-services
uv sync --frozen
uv run pytest -q
```

### Spring API

```bash
cd workspaces/decision-platform/spring-api
./gradlew test
```

### Dashboard

```bash
cd workspaces/experience-dashboard
npm ci
npm run typecheck
npm run lint
npm test
npm run build
```

### Return Engine

```bash
cd workspaces/return-engine
uv sync --frozen
uv run pytest -q
PYTHONPATH=src uv run python -m return_engine --help
```

### 데이터와 문제 해결

비밀값, 계좌번호와 토큰은 저장소나 응답에 넣지 않도록 했습니다. Docker 연결 실패 시 엔진과 `./capstone doctor`를, 포트 충돌 시 해당 포트 사용 여부를, 대시보드가 준비되지 않을 때 `status`와 `logs`를 확인하도록 안내했습니다. 자동운용이 거부되면 상태 API의 blocker를 확인합니다. 주문이 없다는 사실만으로 장애라고 단정할 수 없습니다.

### 이전과 이후

이전 판본은 Mock과 Live를 단일 어댑터의 별도 profile로 설명하고, 모의계좌 인증과 준비 단계를 명시했습니다. 현재 판본은 모의투자 중심의 계좌 연결, 사용자별 키와 원칙 버전, 실행과 대사 기록을 설명합니다. 당시 명령과 기능이 현재 판본에서 동일하게 동작하는지는 이 문서만으로 보증하지 않습니다.

모델과 규칙의 결합 연구는 후보 발생 빈도를 평가한 것이며 수익성 개선을 입증하지 않습니다. LSTM은 당시 평가에서 기준선 미달로 기록됐습니다. 팀의 공동 측정과 시험 결과이며 특정 구성원의 단독 성과로 표현하지 않습니다.

### 당시 README 유지보수 규칙

실행 명령이 바뀌면 빠른 실행과 명령 목록을 함께 고치고, 포트가 바뀌면 주소 표를 갱신하도록 했습니다. 새 기능은 기능 표에 추가하며 비밀값과 개인 로컬 경로는 README에 넣지 않습니다.

## 팀과 기여

초보 투자자가 모델의 결과만 받아들이는 대신 판단 근거와 위험을 확인하도록 만든 3인 졸업과제입니다. 저는 PM과 백엔드 Decision Platform을 맡았습니다. 모델 및 백테스트는 모델 담당 팀원, 화면은 프론트엔드 담당 팀원이 맡았으며 측정과 시험은 팀이 함께했습니다. 수익을 보장하지 않습니다.

## 문서와 라이선스

- [현재 프로젝트 명세](https://github.com/robinhood0107/Capstone-AI-Trading-Coach/blob/main/docs/최종_프로젝트_명세서.md)
- [현재 API 명세](https://github.com/robinhood0107/Capstone-AI-Trading-Coach/blob/main/docs/API_명세서.md)
- [이전 저장소 라이선스: GNU AGPL-3.0](https://github.com/robinhood0107/Capstone-AI-Trading-Coach-archive/blob/main/LICENSE)

> 이전 개발판의 기록입니다. 현재 기능과 실행 방법은 [현재 저장소](https://github.com/robinhood0107/Capstone-AI-Trading-Coach)를 기준으로 확인하세요.


검토에서 확인한 계좌 식별자는 공개용 이미지와 탐색 영상에서 불투명하게 가렸습니다. 원본은 별도로 보존했으며, 그 밖의 화면 내용은 바꾸지 않았습니다.

<!-- MARS_CAPTURE_GALLERY_BEGIN -->
<details>
<summary>실행 화면 전체 갤러리</summary>

화면은 각 실행에서 촬영한 UI 기록입니다. 최초 실행은 API 미기동으로 제품 흐름 검증에 실패했습니다. 다른 실행도 화면 촬영만으로 기능 검증을 뜻하지 않습니다. 화면 수치는 모의 시연값이며 실제 수익 성과의 근거가 아닙니다.

**demo / desktop**

![demo / desktop](docs/readme-media/capture-c68f9a51a756715999dd.png)

**demo / desktop / /principles / page**

![demo / desktop / /principles / page](docs/readme-media/capture-13e06e9fef231d4b9419.png)

**demo / desktop**

![demo / desktop](docs/readme-media/capture-c1c06458598fc1cbad03.png)

**demo / desktop / /principles / ctl_밝게**

![demo / desktop / /principles / ctl_밝게](docs/readme-media/capture-0d0b7f10198bedcd8592.png)

**demo / desktop / /principles / ctl_어둡게**

![demo / desktop / /principles / ctl_어둡게](docs/readme-media/capture-334f6f438846ce4202aa.png)

**demo / desktop**

![demo / desktop](docs/readme-media/capture-f31568b03d2b2036bae1.png)

**demo / desktop**

![demo / desktop](docs/readme-media/capture-aae2917487cf0f8215f4.png)

**demo / desktop / /strategy / page**

![demo / desktop / /strategy / page](docs/readme-media/capture-9785f709c716ffa72cb9.png)

**demo / desktop / /strategy / ctl_밝게**

![demo / desktop / /strategy / ctl_밝게](docs/readme-media/capture-f9783c1e6d723615cd69.png)

**demo / desktop / /strategy / ctl_어둡게**

![demo / desktop / /strategy / ctl_어둡게](docs/readme-media/capture-f3a54a0d800c00283238.png)

**demo / desktop / /strategy / ctl_백테스트리포트**

![demo / desktop / /strategy / ctl_백테스트리포트](docs/readme-media/capture-14042ab6e3f3c29b0b52.png)

**demo / desktop / /strategy / ctl_CAGR**

![demo / desktop / /strategy / ctl_CAGR](docs/readme-media/capture-9e84f738261214824b55.png)

**demo / desktop / /strategy / ctl_MDD**

![demo / desktop / /strategy / ctl_MDD](docs/readme-media/capture-554f9ab570ef7c283cef.png)

**demo / desktop / /strategy / ctl_Sharpe**

![demo / desktop / /strategy / ctl_Sharpe](docs/readme-media/capture-a7d32e7c7a3736416acd.png)

**demo / desktop / /backtest / ctl_밝게**

![demo / desktop / /backtest / ctl_밝게](docs/readme-media/capture-6ab9ce8bb30764f3117b.png)

**demo / desktop / /backtest / ctl_어둡게**

![demo / desktop / /backtest / ctl_어둡게](docs/readme-media/capture-dcc754c86434234cadf4.png)

**demo / desktop / /backtest / ctl_Sharpe**

![demo / desktop / /backtest / ctl_Sharpe](docs/readme-media/capture-9aa9cfd5f831302c62d9.png)

**demo / desktop / /model-evaluation / ctl_CAGR**

![demo / desktop / /model-evaluation / ctl_CAGR](docs/readme-media/capture-959ac9d8fe6213ce83e9.png)

**demo / desktop / /rag / page**

![demo / desktop / /rag / page](docs/readme-media/capture-d64228d1455349b4fcfd.png)

**demo / desktop / /rag / ctl_밝게**

![demo / desktop / /rag / ctl_밝게](docs/readme-media/capture-7ebabc838ae9c0354978.png)

**demo / desktop / /rag / ctl_어둡게**

![demo / desktop / /rag / ctl_어둡게](docs/readme-media/capture-3544b08095f30d983bde.png)

**demo / desktop / /rag / ctl_동의**

![demo / desktop / /rag / ctl_동의](docs/readme-media/capture-7773da7ebad7c19134ef.png)

**demo / desktop / /rag / ctl_짧게**

![demo / desktop / /rag / ctl_짧게](docs/readme-media/capture-76ed693c5d5fa0051f78.png)

**demo / desktop / /rag / ctl_자세히**

![demo / desktop / /rag / ctl_자세히](docs/readme-media/capture-5be01a0ba88a3ccc58b4.png)

**demo / desktop / /rag / ctl_연구근거1개더보기**

![demo / desktop / /rag / ctl_연구근거1개더보기](docs/readme-media/capture-14d8adedac0253b0e40d.png)

**demo / desktop / /automation / page**

![demo / desktop / /automation / page](docs/readme-media/capture-face7043cf106e530de7.png)

**demo / desktop / /automation / ctl_밝게**

![demo / desktop / /automation / ctl_밝게](docs/readme-media/capture-bf00b98d081d3a57b35c.png)

**demo / desktop / /automation / ctl_어둡게**

![demo / desktop / /automation / ctl_어둡게](docs/readme-media/capture-b3e1171bf06e25e18b17.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-d4f261b2788e3d50f744.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-80cd36335a471f9d3068.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-b804dd39a96f37fd04bf.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-8eb54abe926ede10b68a.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-5637d1304a00674fa1d0.png)

**demo / desktop / /automation / ctl_지정가**

![demo / desktop / /automation / ctl_지정가](docs/readme-media/capture-435d9527e330e1acb4bc.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-6bd70484fe329cc2f10b.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-a42fb1954134c08b3003.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-ada3bf26a556d8ba2fb8.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-8b3cf071c64a00e08c88.png)

**demo / desktop / /automation / ctl_지정가**

![demo / desktop / /automation / ctl_지정가](docs/readme-media/capture-033507d89112024840e4.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-a363f6259b3665d19de7.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-8ac906cb5fe8a2146a31.png)

**demo / desktop / /automation / ctl_지정가**

![demo / desktop / /automation / ctl_지정가](docs/readme-media/capture-98ec8bac6eb82b22b44a.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-012d972adead9ce1447c.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-c10b185383f12c049973.png)

**demo / desktop / /automation / ctl_지정가**

![demo / desktop / /automation / ctl_지정가](docs/readme-media/capture-3f09425445794b346aa2.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-33f3d4dc4b602bc1ab57.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-081f4359e799178a5a4b.png)

**demo / desktop / /automation / ctl_지정가**

![demo / desktop / /automation / ctl_지정가](docs/readme-media/capture-efd870c99a348aa46c26.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-96d1e2b3ad49fb3ca438.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-bf22b72f3877a276736e.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-238ed2762f2f321f3654.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-9da386de036b6c1569fd.png)

**demo / desktop / /automation / ctl_지정가**

![demo / desktop / /automation / ctl_지정가](docs/readme-media/capture-d02704678c929cbd943b.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-a9fd2763bc7e1f947f83.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-c78e36ece6633832c529.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-c3e9f03b4aeec4437cc6.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-444401befc2260ac45ba.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-141e9935517914721070.png)

**demo / desktop / /automation / ctl_지정가**

![demo / desktop / /automation / ctl_지정가](docs/readme-media/capture-8f754ed4f61e918bad3d.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-63848cf58c1dcf430234.png)

**demo / desktop / /automation / ctl_지정가**

![demo / desktop / /automation / ctl_지정가](docs/readme-media/capture-35b0f09d7dfb59123447.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-e5703ff717192c67f725.png)

**demo / desktop / /automation / ctl_지정가**

![demo / desktop / /automation / ctl_지정가](docs/readme-media/capture-0825040de491ac1d4dc6.png)

**demo / desktop / /journal / page**

![demo / desktop / /journal / page](docs/readme-media/capture-76fedc51e2969c13fc10.png)

**demo / desktop / /journal / ctl_밝게**

![demo / desktop / /journal / ctl_밝게](docs/readme-media/capture-1ed11faed4541ecd53f8.png)

**demo / desktop / /journal / ctl_어둡게**

![demo / desktop / /journal / ctl_어둡게](docs/readme-media/capture-eb4f64f2246124d0956a.png)

**demo / desktop / /report / page**

![demo / desktop / /report / page](docs/readme-media/capture-032e9e87f48e3245aede.png)

**demo / desktop / /report / ctl_밝게**

![demo / desktop / /report / ctl_밝게](docs/readme-media/capture-a50ce54cf77fd5352303.png)

**demo / desktop / /report / ctl_어둡게**

![demo / desktop / /report / ctl_어둡게](docs/readme-media/capture-af3a82abacd6cd9bf559.png)

**demo / desktop / /settings / page**

![demo / desktop / /settings / page](docs/readme-media/capture-6ff68adc8cb6dc3936fb.png)

**demo / desktop / /settings / ctl_밝게**

![demo / desktop / /settings / ctl_밝게](docs/readme-media/capture-434ea76325cab4039405.png)

**demo / desktop / /settings / ctl_어둡게**

![demo / desktop / /settings / ctl_어둡게](docs/readme-media/capture-a34466b841b2508ff1f4.png)

**demo / desktop / /settings / ctl_MDD최대낙폭**

![demo / desktop / /settings / ctl_MDD최대낙폭](docs/readme-media/capture-2547a5160ecf56a007ef.png)

**demo / desktop / /settings / ctl_Sharpe지수**

![demo / desktop / /settings / ctl_Sharpe지수](docs/readme-media/capture-77e8b1d48c62ba177b10.png)

**demo / desktop / /settings / ctl_ATR**

![demo / desktop / /settings / ctl_ATR](docs/readme-media/capture-4fda9d6c1cea38f354c2.png)

**demo / desktop / /admin / page**

![demo / desktop / /admin / page](docs/readme-media/capture-f2b6223289349d60f947.png)

**demo / desktop / /admin / ctl_밝게**

![demo / desktop / /admin / ctl_밝게](docs/readme-media/capture-2a6b4a278cee47db41fa.png)

**demo / desktop / /admin / ctl_어둡게**

![demo / desktop / /admin / ctl_어둡게](docs/readme-media/capture-7e94a5379b212f836fdf.png)

**demo / desktop / /admin / ctl_검색**

![demo / desktop / /admin / ctl_검색](docs/readme-media/capture-f407398ab96be48c62cc.png)

**demo / desktop / / / page**

![demo / desktop / / / page](docs/readme-media/capture-d77c67326680c68ede1d.png)

**demo / desktop / / / ctl_밝게**

![demo / desktop / / / ctl_밝게](docs/readme-media/capture-9b098805a0aeb4a4a4c7.png)

**demo / desktop / / / ctl_어둡게**

![demo / desktop / / / ctl_어둡게](docs/readme-media/capture-cb26fa8498d955e25f37.png)

**demo / mobile**

![demo / mobile](docs/readme-media/capture-a91c4fe5d1abdc4852be.png)

**demo / mobile**

![demo / mobile](docs/readme-media/capture-17f5dec69964f39192e2.png)

**demo / mobile**

![demo / mobile](docs/readme-media/capture-d79fa57c99238dec8e28.png)

**demo / mobile / /principles / page**

![demo / mobile / /principles / page](docs/readme-media/capture-c5b9544896602c4feb8d.png)

**demo / mobile / /principles / ctl_밝게**

![demo / mobile / /principles / ctl_밝게](docs/readme-media/capture-38b629cc218ff9f5fc4b.png)

**demo / mobile / /principles / ctl_어둡게**

![demo / mobile / /principles / ctl_어둡게](docs/readme-media/capture-e5ccf639a6090004ba7a.png)

**demo / mobile / /strategy / page**

![demo / mobile / /strategy / page](docs/readme-media/capture-1b7c635f78aabef68bdb.png)

**demo / mobile / /strategy / ctl_밝게**

![demo / mobile / /strategy / ctl_밝게](docs/readme-media/capture-f89900a28764f35c3e55.png)

**demo / mobile / /strategy / ctl_어둡게**

![demo / mobile / /strategy / ctl_어둡게](docs/readme-media/capture-53577b420bce31fbba5a.png)

**demo / mobile / /strategy / ctl_백테스트리포트**

![demo / mobile / /strategy / ctl_백테스트리포트](docs/readme-media/capture-d9ffa3ff7f4a8d0cc4ae.png)

**demo / mobile / /strategy / ctl_CAGR**

![demo / mobile / /strategy / ctl_CAGR](docs/readme-media/capture-715bc299162358263601.png)

**demo / mobile / /strategy / ctl_MDD**

![demo / mobile / /strategy / ctl_MDD](docs/readme-media/capture-197568c1149cedd5fbb3.png)

**demo / mobile / /strategy / ctl_Sharpe**

![demo / mobile / /strategy / ctl_Sharpe](docs/readme-media/capture-f858d0b596e06bbe9e2d.png)

**demo / mobile / /backtest / ctl_밝게**

![demo / mobile / /backtest / ctl_밝게](docs/readme-media/capture-17a2966fcbdcada7a377.png)

**demo / mobile / /backtest / ctl_어둡게**

![demo / mobile / /backtest / ctl_어둡게](docs/readme-media/capture-ac2f4502fec97086e066.png)

**demo / mobile / /backtest / ctl_CAGR**

![demo / mobile / /backtest / ctl_CAGR](docs/readme-media/capture-727063599ad0b96d3448.png)

**demo / mobile / /backtest / ctl_MDD**

![demo / mobile / /backtest / ctl_MDD](docs/readme-media/capture-33c9ba265671231afb79.png)

**demo / mobile / /backtest / ctl_Sharpe**

![demo / mobile / /backtest / ctl_Sharpe](docs/readme-media/capture-49f0c6bc3cc400c85e5b.png)

**demo / mobile / /model-evaluation / ctl_Sharpe**

![demo / mobile / /model-evaluation / ctl_Sharpe](docs/readme-media/capture-2b89c75329757b3dbaf5.png)

**demo / mobile / /rag / page**

![demo / mobile / /rag / page](docs/readme-media/capture-18b462d6bd629b4d8a81.png)

**demo / mobile / /rag / ctl_밝게**

![demo / mobile / /rag / ctl_밝게](docs/readme-media/capture-ba66cf62cff30211fd4b.png)

**demo / mobile / /rag / ctl_어둡게**

![demo / mobile / /rag / ctl_어둡게](docs/readme-media/capture-00b27fd4ccbf50f1eb4b.png)

**demo / mobile / /rag / ctl_동의**

![demo / mobile / /rag / ctl_동의](docs/readme-media/capture-0e7819abad3d8c2a6331.png)

**demo / mobile / /rag / ctl_철회**

![demo / mobile / /rag / ctl_철회](docs/readme-media/capture-899a1a2cac690a889c6a.png)

**demo / mobile / /rag / ctl_짧게**

![demo / mobile / /rag / ctl_짧게](docs/readme-media/capture-81e1b8a1a9fd5d985177.png)

**demo / mobile / /rag / ctl_자세히**

![demo / mobile / /rag / ctl_자세히](docs/readme-media/capture-8778375ba5cee384abef.png)

**demo / mobile / /rag / ctl_연구근거1개더보기**

![demo / mobile / /rag / ctl_연구근거1개더보기](docs/readme-media/capture-af90c558f56dd6bf393e.png)

**demo / mobile / /automation / page**

![demo / mobile / /automation / page](docs/readme-media/capture-1782ba429ef2be79b67f.png)

**demo / mobile / /automation / ctl_밝게**

![demo / mobile / /automation / ctl_밝게](docs/readme-media/capture-bc252e036a6294515204.png)

**demo / mobile / /automation / ctl_어둡게**

![demo / mobile / /automation / ctl_어둡게](docs/readme-media/capture-1f7c433b41683c5de14e.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-ac437ae92b5f8850f34f.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-a12523f75c1f9bd3afe6.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-2871df25b0d07ae4ebcd.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-9c37b4ae4ab9e46c4fb5.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-0f1917acd0a6e6118f91.png)

**demo / mobile / /automation / ctl_지정가**

![demo / mobile / /automation / ctl_지정가](docs/readme-media/capture-b3bb8d6ab1b7535257cc.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-ee569bd82fb2dffde545.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-272971d1ae5d2182f594.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-f4d2734be168008beed4.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-733359f369a55a6fa47c.png)

**demo / mobile / /automation / ctl_지정가**

![demo / mobile / /automation / ctl_지정가](docs/readme-media/capture-29be5f4b0642762d89a5.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-b27ea805b6c63fa5f49c.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-993c54fda390182eee97.png)

**demo / mobile / /automation / ctl_지정가**

![demo / mobile / /automation / ctl_지정가](docs/readme-media/capture-c3232bece50fcbe0260b.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-8cd3cfc466ae57165dc3.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-a69f631ee5c601d6b84c.png)

**demo / mobile / /automation / ctl_지정가**

![demo / mobile / /automation / ctl_지정가](docs/readme-media/capture-d76756119f7eecf093a6.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-258bb79df99b8368218e.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-12dda13c664ed8c1601d.png)

**demo / mobile / /automation / ctl_지정가**

![demo / mobile / /automation / ctl_지정가](docs/readme-media/capture-db534bcfa1db90dec996.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-d1b22799d55eadcce3a2.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-e627cd4de1bf48eb0346.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-56f811c1fadb35dbd19b.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-da5674b048cab7eb5b2a.png)

**demo / mobile / /automation / ctl_지정가**

![demo / mobile / /automation / ctl_지정가](docs/readme-media/capture-7018573907d02813c7a6.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-7beb5ce25d1747715963.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-2f62614468254fd46f15.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-c27cbb3ec7d8bd482fb7.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-e227a4281d84589876ad.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-fb1e22d3f9ff6430a12e.png)

**demo / mobile / /automation / ctl_지정가**

![demo / mobile / /automation / ctl_지정가](docs/readme-media/capture-189f131a90c50364d4c5.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-ead470124f74ed333f4d.png)

**demo / mobile / /automation / ctl_지정가**

![demo / mobile / /automation / ctl_지정가](docs/readme-media/capture-cb2aaa1d40581c3b93c4.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-c96fd203074f734f2878.png)

**demo / mobile / /automation / ctl_지정가**

![demo / mobile / /automation / ctl_지정가](docs/readme-media/capture-2673531bf2e1f710f5d6.png)

**demo / mobile / /journal / page**

![demo / mobile / /journal / page](docs/readme-media/capture-05355541eab332040939.png)

**demo / mobile / /journal / ctl_밝게**

![demo / mobile / /journal / ctl_밝게](docs/readme-media/capture-46763071cd8717cf3413.png)

**demo / mobile / /journal / ctl_어둡게**

![demo / mobile / /journal / ctl_어둡게](docs/readme-media/capture-5c4a8c2989a9ceb62d13.png)

**demo / mobile / /report / page**

![demo / mobile / /report / page](docs/readme-media/capture-48d7c53090e00e62156d.png)

**demo / mobile / /report / ctl_밝게**

![demo / mobile / /report / ctl_밝게](docs/readme-media/capture-c7f6da82a1f0d27e2df1.png)

**demo / mobile / /report / ctl_어둡게**

![demo / mobile / /report / ctl_어둡게](docs/readme-media/capture-91b40512c39fd7ffe8e6.png)

**demo / mobile / /settings / page**

![demo / mobile / /settings / page](docs/readme-media/capture-c66ebcf66b5bccc14e23.png)

**demo / mobile / /settings / ctl_밝게**

![demo / mobile / /settings / ctl_밝게](docs/readme-media/capture-5739361bf2d7108d3925.png)

**demo / mobile / /settings / ctl_어둡게**

![demo / mobile / /settings / ctl_어둡게](docs/readme-media/capture-b92f8528bf4eeeccbeec.png)

**demo / mobile / /settings / ctl_MDD최대낙폭**

![demo / mobile / /settings / ctl_MDD최대낙폭](docs/readme-media/capture-1b229fd0d66e0b29c4ec.png)

**demo / mobile / /settings / ctl_Sharpe지수**

![demo / mobile / /settings / ctl_Sharpe지수](docs/readme-media/capture-5f6ef6fe46e36cfe4052.png)

**demo / mobile / /settings / ctl_ATR**

![demo / mobile / /settings / ctl_ATR](docs/readme-media/capture-74cca872f26000c56db8.png)

**demo / mobile / /admin / page**

![demo / mobile / /admin / page](docs/readme-media/capture-821decc7510e6d06a22f.png)

**demo / mobile / /admin / ctl_밝게**

![demo / mobile / /admin / ctl_밝게](docs/readme-media/capture-d4629c906ffa0a166204.png)

**demo / mobile / /admin / ctl_어둡게**

![demo / mobile / /admin / ctl_어둡게](docs/readme-media/capture-68c6f32645e0cc1da72f.png)

**demo / mobile / /admin / ctl_검색**

![demo / mobile / /admin / ctl_검색](docs/readme-media/capture-9e15de4f5b8093b3cae0.png)

**demo / mobile / / / ctl_밝게**

![demo / mobile / / / ctl_밝게](docs/readme-media/capture-54580950a2beab1b8fe5.png)

**initial / desktop / / / page**

![initial / desktop / / / page](docs/readme-media/capture-afdb7f79cb5be9ba9e36.png)

**initial / desktop / / / ctl_로그인**

![initial / desktop / / / ctl_로그인](docs/readme-media/capture-377090df5364d6036316.png)

**initial / desktop / / / ctl_회원가입**

![initial / desktop / / / ctl_회원가입](docs/readme-media/capture-e7afd5dbd17e85326bf1.png)

**initial / desktop / / / ctl_로그인**

![initial / desktop / / / ctl_로그인](docs/readme-media/capture-6c4a93eaf81ad7dd4795.png)

**initial / desktop / / / ctl_테마**

![initial / desktop / / / ctl_테마](docs/readme-media/capture-fd0175ed84893d129397.png)

**initial / desktop / / / ctl_시작하기**

![initial / desktop / / / ctl_시작하기](docs/readme-media/capture-0bb39751cac286cc9151.png)

**initial / desktop / / / ctl_OpenNextjsDevTools**

![initial / desktop / / / ctl_OpenNextjsDevTools](docs/readme-media/capture-045d2fefc626933c63a1.png)

**experience-dashboard / desktop / /intro / page**

![experience-dashboard / desktop / /intro / page](docs/readme-media/capture-bc036eb30adaabcf47c4.png)

**experience-dashboard / desktop / /intro / ctl_테마**

![experience-dashboard / desktop / /intro / ctl_테마](docs/readme-media/capture-af0aad12dc4d6cc9fb47.png)

**experience-dashboard / desktop / /intro / ctl_대시보드**

![experience-dashboard / desktop / /intro / ctl_대시보드](docs/readme-media/capture-fb7dfb79ed3da25f49fb.png)

**experience-dashboard / desktop / /intro / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / /intro / ctl_OpenNextjsDevTools](docs/readme-media/capture-5c462fcf21df6d9fc1d6.png)

**experience-dashboard / desktop / /principles / page**

![experience-dashboard / desktop / /principles / page](docs/readme-media/capture-cd0e0439698bab980e32.png)

**experience-dashboard / desktop / /principles / ctl_로그인**

![experience-dashboard / desktop / /principles / ctl_로그인](docs/readme-media/capture-dd377461106d27e8d787.png)

**experience-dashboard / desktop / /principles / ctl_회원가입**

![experience-dashboard / desktop / /principles / ctl_회원가입](docs/readme-media/capture-8a308908ebd53b7ed553.png)

**experience-dashboard / desktop / /principles / ctl_로그인**

![experience-dashboard / desktop / /principles / ctl_로그인](docs/readme-media/capture-2e9d985ae9d43c50f55e.png)

**experience-dashboard / desktop / /principles / ctl_테마**

![experience-dashboard / desktop / /principles / ctl_테마](docs/readme-media/capture-3f9ab0959fa008e20ca5.png)

**experience-dashboard / desktop / /principles / ctl_시작하기**

![experience-dashboard / desktop / /principles / ctl_시작하기](docs/readme-media/capture-2ac277752e1d0979cec3.png)

**experience-dashboard / desktop / /principles / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / /principles / ctl_OpenNextjsDevTools](docs/readme-media/capture-8571fe44b8a0ca7c88c9.png)

**experience-dashboard / desktop / /strategy / page**

![experience-dashboard / desktop / /strategy / page](docs/readme-media/capture-37cf0cff290435e0316f.png)

**experience-dashboard / desktop / /strategy / ctl_로그인**

![experience-dashboard / desktop / /strategy / ctl_로그인](docs/readme-media/capture-f9ba79735908aed934c1.png)

**experience-dashboard / desktop / /strategy / ctl_회원가입**

![experience-dashboard / desktop / /strategy / ctl_회원가입](docs/readme-media/capture-0200080b8f38cb06c99d.png)

**experience-dashboard / desktop / /strategy / ctl_로그인**

![experience-dashboard / desktop / /strategy / ctl_로그인](docs/readme-media/capture-e03fd632c2a2ac73e4aa.png)

**experience-dashboard / desktop / /strategy / ctl_테마**

![experience-dashboard / desktop / /strategy / ctl_테마](docs/readme-media/capture-84a7ba15599cb868bc95.png)

**experience-dashboard / desktop / /strategy / ctl_시작하기**

![experience-dashboard / desktop / /strategy / ctl_시작하기](docs/readme-media/capture-a1e73cb95482259dd89a.png)

**experience-dashboard / desktop / /strategy / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / /strategy / ctl_OpenNextjsDevTools](docs/readme-media/capture-78765b59c24a2dc284cf.png)

**experience-dashboard / desktop / /backtest / page**

![experience-dashboard / desktop / /backtest / page](docs/readme-media/capture-b40b81ce904f56584e98.png)

**experience-dashboard / desktop / /backtest / ctl_로그인**

![experience-dashboard / desktop / /backtest / ctl_로그인](docs/readme-media/capture-29179a51962226cae9e4.png)

**experience-dashboard / desktop / /backtest / ctl_회원가입**

![experience-dashboard / desktop / /backtest / ctl_회원가입](docs/readme-media/capture-e3d8c9e00bda51e7aa83.png)

**experience-dashboard / desktop / /backtest / ctl_로그인**

![experience-dashboard / desktop / /backtest / ctl_로그인](docs/readme-media/capture-8695809c70ae3a7c0471.png)

**experience-dashboard / desktop / /backtest / ctl_테마**

![experience-dashboard / desktop / /backtest / ctl_테마](docs/readme-media/capture-4b1be9ce3ca67638922f.png)

**experience-dashboard / desktop / /backtest / ctl_시작하기**

![experience-dashboard / desktop / /backtest / ctl_시작하기](docs/readme-media/capture-7f249d07efd837bba2d0.png)

**experience-dashboard / desktop / /backtest / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / /backtest / ctl_OpenNextjsDevTools](docs/readme-media/capture-f86d1e77632d340ea168.png)

**experience-dashboard / desktop / /model-evaluation / page**

![experience-dashboard / desktop / /model-evaluation / page](docs/readme-media/capture-1231c398b83cca33bd85.png)

**experience-dashboard / desktop / /model-evaluation / ctl_로그인**

![experience-dashboard / desktop / /model-evaluation / ctl_로그인](docs/readme-media/capture-962f5b3d65096764af36.png)

**experience-dashboard / desktop / /model-evaluation / ctl_회원가입**

![experience-dashboard / desktop / /model-evaluation / ctl_회원가입](docs/readme-media/capture-24c7528fb8595f5fa882.png)

**experience-dashboard / desktop / /model-evaluation / ctl_로그인**

![experience-dashboard / desktop / /model-evaluation / ctl_로그인](docs/readme-media/capture-3367f939ac1ffb89e0bc.png)

**experience-dashboard / desktop / /model-evaluation / ctl_테마**

![experience-dashboard / desktop / /model-evaluation / ctl_테마](docs/readme-media/capture-5f0bc418dc1066786130.png)

**experience-dashboard / desktop / /model-evaluation / ctl_시작하기**

![experience-dashboard / desktop / /model-evaluation / ctl_시작하기](docs/readme-media/capture-2eefa29cbab699ccb4d2.png)

**experience-dashboard / desktop / /model-evaluation / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / /model-evaluation / ctl_OpenNextjsDevTools](docs/readme-media/capture-adc3efa8d0fc3c726138.png)

**experience-dashboard / desktop / /rag / page**

![experience-dashboard / desktop / /rag / page](docs/readme-media/capture-f81a2affbbfa74681a15.png)

**experience-dashboard / desktop / /rag / ctl_로그인**

![experience-dashboard / desktop / /rag / ctl_로그인](docs/readme-media/capture-123336626cbe4d5c0208.png)

**experience-dashboard / desktop / /rag / ctl_회원가입**

![experience-dashboard / desktop / /rag / ctl_회원가입](docs/readme-media/capture-98de25a3c8b7d41a1e9b.png)

**experience-dashboard / desktop / /rag / ctl_로그인**

![experience-dashboard / desktop / /rag / ctl_로그인](docs/readme-media/capture-643d640476f27c6c6616.png)

**experience-dashboard / desktop / /rag / ctl_테마**

![experience-dashboard / desktop / /rag / ctl_테마](docs/readme-media/capture-65f83d03fc3d2e3c8f8a.png)

**experience-dashboard / desktop / /rag / ctl_시작하기**

![experience-dashboard / desktop / /rag / ctl_시작하기](docs/readme-media/capture-41a5b881958324802c5d.png)

**experience-dashboard / desktop / /rag / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / /rag / ctl_OpenNextjsDevTools](docs/readme-media/capture-2ee30b1b2c9efacc325b.png)

**experience-dashboard / desktop / /automation / page**

![experience-dashboard / desktop / /automation / page](docs/readme-media/capture-72fc7f4589d33d460983.png)

**experience-dashboard / desktop / /automation / ctl_로그인**

![experience-dashboard / desktop / /automation / ctl_로그인](docs/readme-media/capture-6e79c48d67dfa3cf3d2e.png)

**experience-dashboard / desktop / /automation / ctl_회원가입**

![experience-dashboard / desktop / /automation / ctl_회원가입](docs/readme-media/capture-2c7a1f94c6420cc23994.png)

**experience-dashboard / desktop / /automation / ctl_로그인**

![experience-dashboard / desktop / /automation / ctl_로그인](docs/readme-media/capture-2361fb5c300216b101df.png)

**experience-dashboard / desktop / /automation / ctl_테마**

![experience-dashboard / desktop / /automation / ctl_테마](docs/readme-media/capture-6d4a8ba48e982ba505f2.png)

**experience-dashboard / desktop / /automation / ctl_시작하기**

![experience-dashboard / desktop / /automation / ctl_시작하기](docs/readme-media/capture-9123381338310fedf55a.png)

**experience-dashboard / desktop / /automation / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / /automation / ctl_OpenNextjsDevTools](docs/readme-media/capture-1bcd26120d5e3ed024dc.png)

**experience-dashboard / desktop / /journal / page**

![experience-dashboard / desktop / /journal / page](docs/readme-media/capture-185f512735aaa545596d.png)

**experience-dashboard / desktop / /journal / ctl_로그인**

![experience-dashboard / desktop / /journal / ctl_로그인](docs/readme-media/capture-e7c76bbe759603be512f.png)

**experience-dashboard / desktop / /journal / ctl_회원가입**

![experience-dashboard / desktop / /journal / ctl_회원가입](docs/readme-media/capture-bb116f62304d98af936e.png)

**experience-dashboard / desktop / /journal / ctl_로그인**

![experience-dashboard / desktop / /journal / ctl_로그인](docs/readme-media/capture-e3e6000e6740863bebed.png)

**experience-dashboard / desktop / /journal / ctl_테마**

![experience-dashboard / desktop / /journal / ctl_테마](docs/readme-media/capture-c8e47dd57b6af88ff63f.png)

**experience-dashboard / desktop / /journal / ctl_시작하기**

![experience-dashboard / desktop / /journal / ctl_시작하기](docs/readme-media/capture-f54bf702dcdec34cc028.png)

**experience-dashboard / desktop / /journal / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / /journal / ctl_OpenNextjsDevTools](docs/readme-media/capture-8e41dd0ab490cadd7934.png)

**experience-dashboard / desktop / /report / page**

![experience-dashboard / desktop / /report / page](docs/readme-media/capture-c994760e2198d165b70a.png)

**experience-dashboard / desktop / /report / ctl_로그인**

![experience-dashboard / desktop / /report / ctl_로그인](docs/readme-media/capture-bd7249afa671a1c753e6.png)

**experience-dashboard / desktop / /report / ctl_회원가입**

![experience-dashboard / desktop / /report / ctl_회원가입](docs/readme-media/capture-8dc0527f23b05abd47e2.png)

**experience-dashboard / desktop / /report / ctl_로그인**

![experience-dashboard / desktop / /report / ctl_로그인](docs/readme-media/capture-418fafe64e6e3250941f.png)

**experience-dashboard / desktop / /report / ctl_테마**

![experience-dashboard / desktop / /report / ctl_테마](docs/readme-media/capture-ecc710fa4e5e8c16b371.png)

**experience-dashboard / desktop / /report / ctl_시작하기**

![experience-dashboard / desktop / /report / ctl_시작하기](docs/readme-media/capture-963b9826ba6ef41895b9.png)

**experience-dashboard / desktop / /report / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / /report / ctl_OpenNextjsDevTools](docs/readme-media/capture-390aef2a187bf82cc243.png)

**experience-dashboard / desktop / /settings / page**

![experience-dashboard / desktop / /settings / page](docs/readme-media/capture-e9cf4be55b17ff91ccff.png)

**experience-dashboard / desktop / /settings / ctl_로그인**

![experience-dashboard / desktop / /settings / ctl_로그인](docs/readme-media/capture-773b4f9d6987b5c91d9d.png)

**experience-dashboard / desktop / /settings / ctl_로그인**

![experience-dashboard / desktop / /settings / ctl_로그인](docs/readme-media/capture-acf4227f8afe82497ffb.png)

**experience-dashboard / desktop / /settings / ctl_테마**

![experience-dashboard / desktop / /settings / ctl_테마](docs/readme-media/capture-bc1911279e416ea886f1.png)

**experience-dashboard / desktop / /settings / ctl_시작하기**

![experience-dashboard / desktop / /settings / ctl_시작하기](docs/readme-media/capture-e16f01175b1f1f256618.png)

**experience-dashboard / desktop / /admin / page**

![experience-dashboard / desktop / /admin / page](docs/readme-media/capture-bfdfa3ebd3ef184d732f.png)

**experience-dashboard / desktop / /admin / ctl_로그인**

![experience-dashboard / desktop / /admin / ctl_로그인](docs/readme-media/capture-dfeb9f5f92e28233e0b0.png)

**experience-dashboard / desktop / /admin / ctl_회원가입**

![experience-dashboard / desktop / /admin / ctl_회원가입](docs/readme-media/capture-b6206e88d70e2c907b5b.png)

**experience-dashboard / desktop / /admin / ctl_로그인**

![experience-dashboard / desktop / /admin / ctl_로그인](docs/readme-media/capture-12d2b6fd794642170f2b.png)

**experience-dashboard / desktop / /admin / ctl_테마**

![experience-dashboard / desktop / /admin / ctl_테마](docs/readme-media/capture-b1d76d571c8b02fb3da2.png)

**experience-dashboard / desktop / /admin / ctl_시작하기**

![experience-dashboard / desktop / /admin / ctl_시작하기](docs/readme-media/capture-195deee343c7c8a69375.png)

**experience-dashboard / desktop / /admin / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / /admin / ctl_OpenNextjsDevTools](docs/readme-media/capture-0815a85ea0f2bf524cc3.png)

**experience-dashboard / desktop / / / page**

![experience-dashboard / desktop / / / page](docs/readme-media/capture-8c75db6848c827acfb86.png)

**experience-dashboard / desktop / / / ctl_회원가입**

![experience-dashboard / desktop / / / ctl_회원가입](docs/readme-media/capture-de9a75e97603e6b389ab.png)

**experience-dashboard / desktop / / / ctl_로그인**

![experience-dashboard / desktop / / / ctl_로그인](docs/readme-media/capture-d0e70c7cd8e20942ac1a.png)

**experience-dashboard / desktop / / / ctl_테마**

![experience-dashboard / desktop / / / ctl_테마](docs/readme-media/capture-a71f13669be1b03c6a04.png)

**experience-dashboard / desktop / / / ctl_시작하기**

![experience-dashboard / desktop / / / ctl_시작하기](docs/readme-media/capture-ccb5fb4ecf9694ca5446.png)

**experience-dashboard / desktop / / / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / / / ctl_OpenNextjsDevTools](docs/readme-media/capture-091514c21751974f65dd.png)

**experience-dashboard / mobile / /intro / page**

![experience-dashboard / mobile / /intro / page](docs/readme-media/capture-da9acad91642d55a4edb.png)

**experience-dashboard / mobile / /intro / ctl_테마**

![experience-dashboard / mobile / /intro / ctl_테마](docs/readme-media/capture-db417fe21385837bbea1.png)

**experience-dashboard / mobile / /intro / ctl_대시보드**

![experience-dashboard / mobile / /intro / ctl_대시보드](docs/readme-media/capture-450217a0bea2d0fa3f2a.png)

**experience-dashboard / mobile / /intro / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / /intro / ctl_OpenNextjsDevTools](docs/readme-media/capture-f7ab7810ffd1e38af9d3.png)

**experience-dashboard / mobile / /principles / page**

![experience-dashboard / mobile / /principles / page](docs/readme-media/capture-c2caaec30df0051fbb67.png)

**experience-dashboard / mobile / /principles / ctl_로그인**

![experience-dashboard / mobile / /principles / ctl_로그인](docs/readme-media/capture-240b465487b00e393254.png)

**experience-dashboard / mobile / /principles / ctl_회원가입**

![experience-dashboard / mobile / /principles / ctl_회원가입](docs/readme-media/capture-252a264dd6342fa7c5aa.png)

**experience-dashboard / mobile / /principles / ctl_로그인**

![experience-dashboard / mobile / /principles / ctl_로그인](docs/readme-media/capture-ea8e886b0f5482ff2558.png)

**experience-dashboard / mobile / /principles / ctl_테마**

![experience-dashboard / mobile / /principles / ctl_테마](docs/readme-media/capture-166b499d0feb99b79746.png)

**experience-dashboard / mobile / /principles / ctl_시작하기**

![experience-dashboard / mobile / /principles / ctl_시작하기](docs/readme-media/capture-302e388089fa22288d33.png)

**experience-dashboard / mobile / /principles / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / /principles / ctl_OpenNextjsDevTools](docs/readme-media/capture-92e26269ad9ca49ba9ff.png)

**experience-dashboard / mobile / /strategy / page**

![experience-dashboard / mobile / /strategy / page](docs/readme-media/capture-70b43244c8d4b4d8848d.png)

**experience-dashboard / mobile / /strategy / ctl_로그인**

![experience-dashboard / mobile / /strategy / ctl_로그인](docs/readme-media/capture-fea0fcf2619a72d94e97.png)

**experience-dashboard / mobile / /strategy / ctl_회원가입**

![experience-dashboard / mobile / /strategy / ctl_회원가입](docs/readme-media/capture-8b05c4b01aa9f5b8f345.png)

**experience-dashboard / mobile / /strategy / ctl_로그인**

![experience-dashboard / mobile / /strategy / ctl_로그인](docs/readme-media/capture-14de6572d2b24c3e5f9c.png)

**experience-dashboard / mobile / /strategy / ctl_테마**

![experience-dashboard / mobile / /strategy / ctl_테마](docs/readme-media/capture-ce546f213ce9f525cffb.png)

**experience-dashboard / mobile / /strategy / ctl_시작하기**

![experience-dashboard / mobile / /strategy / ctl_시작하기](docs/readme-media/capture-3354db3603fe1a8232a6.png)

**experience-dashboard / mobile / /strategy / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / /strategy / ctl_OpenNextjsDevTools](docs/readme-media/capture-fc7e60a5a4febcc2f795.png)

**experience-dashboard / mobile / /backtest / page**

![experience-dashboard / mobile / /backtest / page](docs/readme-media/capture-6b58f3b1e090892729fa.png)

**experience-dashboard / mobile / /backtest / ctl_로그인**

![experience-dashboard / mobile / /backtest / ctl_로그인](docs/readme-media/capture-a415f3a645104e4c813b.png)

**experience-dashboard / mobile / /backtest / ctl_회원가입**

![experience-dashboard / mobile / /backtest / ctl_회원가입](docs/readme-media/capture-253fcc1ed3ed4274e44f.png)

**experience-dashboard / mobile / /backtest / ctl_로그인**

![experience-dashboard / mobile / /backtest / ctl_로그인](docs/readme-media/capture-03f83eb02c6debcb029a.png)

**experience-dashboard / mobile / /backtest / ctl_테마**

![experience-dashboard / mobile / /backtest / ctl_테마](docs/readme-media/capture-a07e9aec5e97c1d09334.png)

**experience-dashboard / mobile / /backtest / ctl_시작하기**

![experience-dashboard / mobile / /backtest / ctl_시작하기](docs/readme-media/capture-bf320cee1789bed401a5.png)

**experience-dashboard / mobile / /backtest / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / /backtest / ctl_OpenNextjsDevTools](docs/readme-media/capture-7c213e3a957e9fe46b92.png)

**experience-dashboard / mobile / /model-evaluation / page**

![experience-dashboard / mobile / /model-evaluation / page](docs/readme-media/capture-2534351b405a561a862f.png)

**experience-dashboard / mobile / /model-evaluation / ctl_로그인**

![experience-dashboard / mobile / /model-evaluation / ctl_로그인](docs/readme-media/capture-cf170b58d6c6a66da0d0.png)

**experience-dashboard / mobile / /model-evaluation / ctl_회원가입**

![experience-dashboard / mobile / /model-evaluation / ctl_회원가입](docs/readme-media/capture-5112eba83932ddb856ed.png)

**experience-dashboard / mobile / /model-evaluation / ctl_로그인**

![experience-dashboard / mobile / /model-evaluation / ctl_로그인](docs/readme-media/capture-1469bcdb5019b8d9e789.png)

**experience-dashboard / mobile / /model-evaluation / ctl_테마**

![experience-dashboard / mobile / /model-evaluation / ctl_테마](docs/readme-media/capture-46abaae7962e819d76c0.png)

**experience-dashboard / mobile / /model-evaluation / ctl_시작하기**

![experience-dashboard / mobile / /model-evaluation / ctl_시작하기](docs/readme-media/capture-bd3e18b0aaa71c14c5fe.png)

**experience-dashboard / mobile / /model-evaluation / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / /model-evaluation / ctl_OpenNextjsDevTools](docs/readme-media/capture-7576ad7c7b705088b589.png)

**experience-dashboard / mobile / /rag / page**

![experience-dashboard / mobile / /rag / page](docs/readme-media/capture-7a5e054214daef1aad42.png)

**experience-dashboard / mobile / /rag / ctl_로그인**

![experience-dashboard / mobile / /rag / ctl_로그인](docs/readme-media/capture-2998211a6ef1c08f5593.png)

**experience-dashboard / mobile / /rag / ctl_회원가입**

![experience-dashboard / mobile / /rag / ctl_회원가입](docs/readme-media/capture-c2fbe3b2b42122b3ac5b.png)

**experience-dashboard / mobile / /rag / ctl_로그인**

![experience-dashboard / mobile / /rag / ctl_로그인](docs/readme-media/capture-398e7dddb682ff449318.png)

**experience-dashboard / mobile / /rag / ctl_테마**

![experience-dashboard / mobile / /rag / ctl_테마](docs/readme-media/capture-678f2cdee243d41d89cb.png)

**experience-dashboard / mobile / /rag / ctl_시작하기**

![experience-dashboard / mobile / /rag / ctl_시작하기](docs/readme-media/capture-52f0f88d361f23db5e6c.png)

**experience-dashboard / mobile / /rag / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / /rag / ctl_OpenNextjsDevTools](docs/readme-media/capture-159b21ab575ad025ab5d.png)

**experience-dashboard / mobile / /automation / page**

![experience-dashboard / mobile / /automation / page](docs/readme-media/capture-d6e8fdaf659bc671066e.png)

**experience-dashboard / mobile / /automation / ctl_로그인**

![experience-dashboard / mobile / /automation / ctl_로그인](docs/readme-media/capture-737e49141338a4ce3d73.png)

**experience-dashboard / mobile / /automation / ctl_회원가입**

![experience-dashboard / mobile / /automation / ctl_회원가입](docs/readme-media/capture-919750267a94a9c5dfeb.png)

**experience-dashboard / mobile / /automation / ctl_로그인**

![experience-dashboard / mobile / /automation / ctl_로그인](docs/readme-media/capture-c22f47a0a3a3e23693c5.png)

**experience-dashboard / mobile / /automation / ctl_테마**

![experience-dashboard / mobile / /automation / ctl_테마](docs/readme-media/capture-9837507dad91022a570d.png)

**experience-dashboard / mobile / /automation / ctl_시작하기**

![experience-dashboard / mobile / /automation / ctl_시작하기](docs/readme-media/capture-eab8923a3858813cf4e1.png)

**experience-dashboard / mobile / /automation / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / /automation / ctl_OpenNextjsDevTools](docs/readme-media/capture-2a9ab99c2acb55ac4b5d.png)

**experience-dashboard / mobile / /journal / page**

![experience-dashboard / mobile / /journal / page](docs/readme-media/capture-da69e815c5e773c6a2cb.png)

**experience-dashboard / mobile / /journal / ctl_로그인**

![experience-dashboard / mobile / /journal / ctl_로그인](docs/readme-media/capture-bc194fae813d30689469.png)

**experience-dashboard / mobile / /journal / ctl_회원가입**

![experience-dashboard / mobile / /journal / ctl_회원가입](docs/readme-media/capture-97cecbaf448fad01f596.png)

**experience-dashboard / mobile / /journal / ctl_로그인**

![experience-dashboard / mobile / /journal / ctl_로그인](docs/readme-media/capture-aa31b0e1ea53eaa08401.png)

**experience-dashboard / mobile / /journal / ctl_테마**

![experience-dashboard / mobile / /journal / ctl_테마](docs/readme-media/capture-bdc746ab539230d1b472.png)

**experience-dashboard / mobile / /journal / ctl_시작하기**

![experience-dashboard / mobile / /journal / ctl_시작하기](docs/readme-media/capture-3b1f67df2f8cdcc1c983.png)

**experience-dashboard / mobile / /journal / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / /journal / ctl_OpenNextjsDevTools](docs/readme-media/capture-af9db45ef4067f9d62a1.png)

**experience-dashboard / mobile / /report / page**

![experience-dashboard / mobile / /report / page](docs/readme-media/capture-1372d16d79f100a63f27.png)

**experience-dashboard / mobile / /report / ctl_로그인**

![experience-dashboard / mobile / /report / ctl_로그인](docs/readme-media/capture-08a3e480876b38e91a99.png)

**experience-dashboard / mobile / /report / ctl_회원가입**

![experience-dashboard / mobile / /report / ctl_회원가입](docs/readme-media/capture-14baf5e1a9413cd23812.png)

**experience-dashboard / mobile / /report / ctl_로그인**

![experience-dashboard / mobile / /report / ctl_로그인](docs/readme-media/capture-67356cbeea01ac89992b.png)

**experience-dashboard / mobile / /report / ctl_테마**

![experience-dashboard / mobile / /report / ctl_테마](docs/readme-media/capture-86f6e64676a77aad77d0.png)

**experience-dashboard / mobile / /report / ctl_시작하기**

![experience-dashboard / mobile / /report / ctl_시작하기](docs/readme-media/capture-ec86286a7808e9d30305.png)

**experience-dashboard / mobile / /report / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / /report / ctl_OpenNextjsDevTools](docs/readme-media/capture-73e23b15ec35f515ec04.png)

**experience-dashboard / mobile / /settings / page**

![experience-dashboard / mobile / /settings / page](docs/readme-media/capture-27d146b201c7658e1843.png)

**experience-dashboard / mobile / /settings / ctl_로그인**

![experience-dashboard / mobile / /settings / ctl_로그인](docs/readme-media/capture-143f7e17dd861f232b9f.png)

**experience-dashboard / mobile / /settings / ctl_회원가입**

![experience-dashboard / mobile / /settings / ctl_회원가입](docs/readme-media/capture-f9bb1139361e43aae7ba.png)

**experience-dashboard / mobile / /settings / ctl_로그인**

![experience-dashboard / mobile / /settings / ctl_로그인](docs/readme-media/capture-cace540a4e9bb9a7e8a3.png)

**experience-dashboard / mobile / /settings / ctl_테마**

![experience-dashboard / mobile / /settings / ctl_테마](docs/readme-media/capture-1bf07fb6179c2bd75928.png)

**experience-dashboard / mobile / /settings / ctl_시작하기**

![experience-dashboard / mobile / /settings / ctl_시작하기](docs/readme-media/capture-04d9addcc79f5595d42c.png)

**experience-dashboard / mobile / /settings / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / /settings / ctl_OpenNextjsDevTools](docs/readme-media/capture-2a8f0dce55d36c29e4b6.png)

**experience-dashboard / mobile / /admin / page**

![experience-dashboard / mobile / /admin / page](docs/readme-media/capture-09cdab641ead02996522.png)

**experience-dashboard / mobile / /admin / ctl_로그인**

![experience-dashboard / mobile / /admin / ctl_로그인](docs/readme-media/capture-f5bbdffa2c7a212f39f5.png)

**experience-dashboard / mobile / /admin / ctl_회원가입**

![experience-dashboard / mobile / /admin / ctl_회원가입](docs/readme-media/capture-f842984ac2709fcc76c8.png)

**experience-dashboard / mobile / /admin / ctl_로그인**

![experience-dashboard / mobile / /admin / ctl_로그인](docs/readme-media/capture-444dd60d03469bfee671.png)

**experience-dashboard / mobile / /admin / ctl_테마**

![experience-dashboard / mobile / /admin / ctl_테마](docs/readme-media/capture-158ba95b555398f8bf1f.png)

**experience-dashboard / mobile / /admin / ctl_시작하기**

![experience-dashboard / mobile / /admin / ctl_시작하기](docs/readme-media/capture-549b43d5250e85a0053b.png)

**experience-dashboard / mobile / / / page**

![experience-dashboard / mobile / / / page](docs/readme-media/capture-0d168b3e2c8c04949284.png)

**experience-dashboard / mobile / / / ctl_로그인**

![experience-dashboard / mobile / / / ctl_로그인](docs/readme-media/capture-71f870ae37f2d7e4ccab.png)

**experience-dashboard / mobile / / / ctl_회원가입**

![experience-dashboard / mobile / / / ctl_회원가입](docs/readme-media/capture-5629e885f653ea5b61d2.png)

**experience-dashboard / mobile / / / ctl_로그인**

![experience-dashboard / mobile / / / ctl_로그인](docs/readme-media/capture-2d99723410708c5a3bea.png)

**experience-dashboard / mobile / / / ctl_테마**

![experience-dashboard / mobile / / / ctl_테마](docs/readme-media/capture-9c4237689b57d8f20987.png)

**experience-dashboard / mobile / / / ctl_시작하기**

![experience-dashboard / mobile / / / ctl_시작하기](docs/readme-media/capture-2e3c64fd2a35db6fc502.png)

**experience-dashboard / mobile / / / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / / / ctl_OpenNextjsDevTools](docs/readme-media/capture-afe8311d6b38443bcbeb.png)

**initial / mobile / / / page**

![initial / mobile / / / page](docs/readme-media/capture-947179ba5694b1242de0.png)

**initial / mobile / / / ctl_로그인**

![initial / mobile / / / ctl_로그인](docs/readme-media/capture-1a6e27046c9dd6d8e2f3.png)

**initial / mobile / / / ctl_회원가입**

![initial / mobile / / / ctl_회원가입](docs/readme-media/capture-46413f49454207071fe0.png)

**initial / mobile / / / ctl_로그인**

![initial / mobile / / / ctl_로그인](docs/readme-media/capture-aded85210db5b8019b55.png)

**initial / mobile / / / ctl_테마**

![initial / mobile / / / ctl_테마](docs/readme-media/capture-b7f2dd16c8f599418559.png)

**initial / mobile / / / ctl_시작하기**

![initial / mobile / / / ctl_시작하기](docs/readme-media/capture-af120cd2851d68f23fde.png)

**initial / mobile / / / ctl_OpenNextjsDevTools**

![initial / mobile / / / ctl_OpenNextjsDevTools](docs/readme-media/capture-6ae3e49bacb268c446a3.png)

</details>
<!-- MARS_CAPTURE_GALLERY_END -->
