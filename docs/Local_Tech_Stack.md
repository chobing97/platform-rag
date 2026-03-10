# 로컬 PC 기반 기술 스택 정의서

**작성일:** 2026.03.06
**전제:** Knowledge Lake + Hybrid RAG + MCP 아키텍처를 클라우드가 아닌 로컬 PC에 구축

---

## 1. 전제 조건

- **서버:** 사무실 내 공용 Mac 1대 (Apple Silicon M 시리즈 기준)
- **네트워크:** 팀원 4명이 사내 LAN으로 접속
- **외부 API:** Claude API 호출만 허용 (데이터 자체는 로컬에 잔류)

---

## 2. 컴포넌트별 비교 (클라우드 vs 로컬)

| 컴포넌트 | 클라우드 (기존 제안) | 로컬 PC (변경) | 변경 이유 |
|---|---|---|---|
| **Vector DB** | Supabase (pgvector) | **Qdrant** (Docker) | 단독 실행 가능. REST API 기본 제공. 별도 DB 설정 없이 벡터 검색 특화 |
| **Metadata DB** | PostgreSQL (Supabase) | **SQLite** | 설치 불필요. 파일 1개로 동작. 4명 규모에 충분한 동시성 |
| **Object Storage** | S3 / Supabase Storage | **로컬 파일시스템** (`~/knowledge-store/raw/`) | 가장 단순. OS 레벨 백업으로 충분 |
| **Embedding 모델** | OpenAI `text-embedding-3-large` (API) | **Ollama + `bge-m3`** | API 비용 제거. Apple Silicon에서 충분한 속도. 한영 혼용 지원 |
| **Reranker** | Cohere Rerank (API) | **`bge-reranker-v2-m3`** (Python 로컬 실행) | API 의존 제거. CrossEncoder로 로컬 추론 |
| **Document Parser** | `unstructured.io` | **`unstructured.io`** (변경 없음) | 원래 로컬 실행. Python 패키지 설치만으로 동작 |
| **BM25 검색** | PostgreSQL full-text | **`rank_bm25`** (Python 라이브러리) | SQLite와 조합. 가볍고 별도 인프라 불필요 |
| **Ingestion** | Python + cron | **Python + `launchd`** (macOS) | macOS 네이티브 스케줄러. cron보다 안정적 |
| **MCP Server** | TypeScript (Node.js) | **TypeScript (Node.js)** (변경 없음) | Claude MCP SDK 그대로 사용 |
| **Search API** | 별도 서버 | **FastAPI** (Python) | 검색 파이프라인이 Python이므로 통합. 팀원이 LAN에서 접속 |
| **Web UI** | Next.js (Vercel 배포) | **Next.js** (로컬 서버 실행) | `http://192.168.x.x:3000`으로 팀원 접속 |
| **AI (LLM)** | Claude API | **Claude API** (변경 없음) | 로컬 LLM은 판단 품질이 부족. API 호출만 외부 사용 |

---

## 3. 로컬 실행 구조

```
공용 Mac (Apple Silicon)
├── Docker
│   └── Qdrant (port 6333)          ← 벡터 DB
│
├── Python 환경 (venv)
│   ├── Ollama + bge-m3              ← 임베딩 생성 (port 11434)
│   ├── bge-reranker-v2-m3           ← 리랭킹
│   ├── unstructured                 ← 문서 파싱
│   ├── rank_bm25                    ← 키워드 검색
│   ├── FastAPI (port 8000)          ← Search API 서버
│   └── ingestion scripts            ← 수집 파이프라인
│
├── Node.js
│   ├── MCP Server (stdio/SSE)       ← Claude 연결
│   └── Next.js (port 3000)          ← 어드민 Web UI
│
├── SQLite
│   └── metadata.db                  ← 메타데이터 (출처/태그/프로젝트)
│
├── 로컬 파일시스템
│   └── ~/knowledge-store/
│       ├── /raw/                    ← 원본 파일 보관
│       └── /watch/                  ← 팀원이 파일 넣는 공유 폴더
│
└── launchd (macOS 스케줄러)
    ├── 매 30분: Notion delta sync
    ├── 매 30분: Gmail 수집
    └── 매 10분: /watch/ 폴더 신규 파일 감지 → 수집
```

---

## 4. 팀원 접속 방식

```
┌─────────────────────────────────────────────────┐
│              사무실 LAN (192.168.x.x)            │
│                                                 │
│  CH Mac ──────┐                                 │
│  지연 PC ──────┤                                 │
│  병초 PC ──────┼──→  공용 Mac (서버)              │
│  영호 Mac ─────┘     │                           │
│                      ├── :3000  Web UI (어드민)   │
│                      ├── :8000  Search API       │
│                      └── Claude CLI + MCP        │
│                          (각자 로컬 PC에서도 가능)  │
└─────────────────────────────────────────────────┘
```

| 접속 방식 | 용도 |
|---|---|
| `http://192.168.x.x:3000` | 어드민 Web UI — 검색, 큐 확인, 문서 업로드 |
| `http://192.168.x.x:8000/docs` | Search API 직접 테스트 (FastAPI Swagger) |
| 각자 PC에서 Claude CLI + MCP | AI에게 질문 시 MCP가 공용 Mac의 Search API를 호출 |
| SMB/AFP 공유 폴더 (`/watch/`) | 파일을 넣으면 자동 수집 |

---

## 5. 핵심 소프트웨어 설치 요약

```bash
# 1. Qdrant (Docker)
docker run -d -p 6333:6333 -v ~/qdrant-data:/qdrant/storage qdrant/qdrant

# 2. Ollama + 임베딩 모델
brew install ollama
ollama pull bge-m3

# 3. Python 환경
python3 -m venv ~/.venv/knowledge
source ~/.venv/knowledge/bin/activate
pip install \
  fastapi uvicorn \
  qdrant-client \
  unstructured[all-docs] \
  rank-bm25 \
  sentence-transformers \   # bge-reranker용
  notion-client \
  google-api-python-client   # Gmail API

# 4. Node.js (MCP + Web UI)
brew install node
npm install @modelcontextprotocol/sdk
cd admin-ui && npm install && npm run build

# 5. SQLite
# 설치 불필요 — Python 표준 라이브러리에 내장
```

---

## 6. 로컬 구축의 장단점

| | 장점 | 단점 |
|---|---|---|
| **비용** | 월 운영비 거의 0원 (Claude API 비용만) | — |
| **보안** | 계약서, 메일 등 민감 데이터가 사내망을 벗어나지 않음 | — |
| **속도** | 임베딩/검색이 네트워크 지연 없이 로컬에서 처리 | 대량 임베딩 초기 적재 시 시간 소요 (1회성) |
| **관리** | — | 공용 Mac이 꺼지면 시스템 전체 중단. 정전/재부팅 대비 자동 시작 설정 필요 |
| **백업** | — | DB/파일 백업을 수동 또는 스크립트로 관리해야 함 (Time Machine + 주간 rsync 권장) |
| **확장** | — | 문서 수만 건 이상 시 Apple Silicon 메모리 한계 도달 가능. 그 시점에 클라우드 전환 검토 |

---

## 7. 리스크 대응

| 리스크 | 대응 |
|---|---|
| 공용 Mac 장애/정전 | `launchd`로 부팅 시 전체 서비스 자동 시작 설정. UPS 연결 |
| 데이터 유실 | Time Machine 자동 백업 + 주간 외장 디스크 rsync |
| 임베딩 속도 (초기 적재) | 기존 문서 일괄 임베딩은 야간에 배치 실행. 이후 증분만 처리하므로 빠름 |
| 팀원이 재택 시 접속 불가 | Tailscale(P2P VPN) 설치 — 무료, 설정 5분. 사내 Mac에 외부에서 안전하게 접속 |

---

**핵심:** 외부 클라우드 의존 없이 Mac 1대로 전체 시스템 운영. Claude API만 외부 호출이고, 나머지는 전부 사내망 안에서 동작한다.
