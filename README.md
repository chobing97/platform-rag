# Platform RAG

플랫폼 본부(4인 팀, 5개 동시 프로젝트)를 위한 AI 기반 지식 관리 시스템.

노션·이메일·로컬 파일에 분산된 지식을 수집 → Hybrid RAG로 검색 → AI Agent가 의사결정 지원.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Web UI (Next.js :3000)  ←→  Agent API (FastAPI :8001)  │
└──────────────┬──────────────────────┬───────────────────┘
               │                      │
┌──────────────▼──────────────────────▼───────────────────┐
│           Hybrid Search Engine (FastAPI :8000)           │
│   Vector(bge-m3) + BM25 → RRF → Reranker(bge-reranker) │
└──────────────┬──────────────────────┬───────────────────┘
               │                      │
┌──────────────▼────────┐  ┌──────────▼──────────────────┐
│  Qdrant (:6333)       │  │  SQLite (metadata, BM25,    │
│  Ollama (:11434)      │  │  usage logs, chat history)  │
└───────────────────────┘  └─────────────────────────────┘
               ▲
┌──────────────┴─────────────────────────────────────────┐
│              Data Collection Layer                      │
│   Notion (delta sync) · DAOL Email · OCR (PaddleOCR)   │
└─────────────────────────────────────────────────────────┘
```

## 프로젝트 구조

```
platform-rag/
├── src/
│   ├── collectors/        # Python — 데이터 수집기
│   │   ├── notion/        #   Notion API delta sync
│   │   ├── daolemail/     #   DAOL 그룹웨어 이메일 수집
│   │   └── ocr_worker.py  #   OCR 텍스트 추출 (Leader-Worker)
│   ├── search/            # Python — FastAPI + Hybrid Search
│   ├── web/               # Next.js — 검색 UI + AI Agent 채팅 + 이용현황 대시보드
│   ├── mcp-server/        # TypeScript — MCP Server
│   └── agent/             # Python — AI Agent (Claude/Gemini 듀얼 LLM)
├── data/
│   ├── raw/
│   │   ├── notion/        # Notion 수집 데이터 + sync_state.db
│   │   └── daolemail/     # 이메일 수집 데이터 + sync_state.db
│   ├── index/             # 검색 인덱스 (bm25_corpus.db, index_state.db)
│   └── web/               # 웹 UI 데이터 (click_log.db: 이용로그, 대화이력)
├── logs/                  # 서비스 로그
├── docs/                  # 설계 문서
├── platformagent          # 서비스 관리 스크립트
└── CLAUDE.md
```

## 빠른 시작

### 사전 요구사항

- macOS (Apple Silicon)
- Docker (Qdrant용)
- Ollama (`ollama pull bge-m3`)
- Python 3.14+, Node.js 20+

### 설치

```bash
# 각 모듈별 가상환경 설치
cd src/collectors && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cd src/search     && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cd src/agent      && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cd src/web && npm install
```

### Agent 환경변수 설정

```bash
cp src/agent/.env.example src/agent/.env
# .env 파일에 API 키 입력
```

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `LLM_PROVIDER` | LLM 프로바이더 | `claude` |
| `ANTHROPIC_API_KEY` | Anthropic API 키 | — |
| `CLAUDE_MODEL` | Claude 모델 | `claude-sonnet-4-20250514` |
| `GOOGLE_API_KEY` | Google API 키 | — |
| `GEMINI_MODEL` | Gemini 모델 | `gemini-2.5-flash` |
| `SEARCH_API_URL` | 검색 API 주소 | `http://localhost:8000` |

### 서비스 실행

```bash
# 전체 서비스 시작
./platformagent start

# 개별 서비스
./platformagent infra start       # Qdrant + Ollama
./platformagent api start         # 검색 API (port 8000)
./platformagent agent-api start   # Agent API (port 8001)
./platformagent web start         # Web UI (port 3000)

# 상태 확인
./platformagent status
```

### AI Agent 사용

```bash
# CLI 대화형 모드
./platformagent agent --claude
./platformagent agent --gemini

# 특정 모델 지정
./platformagent agent --claude -m claude-haiku-4-5-20251001
./platformagent agent --gemini -m gemini-2.5-pro

# API 서버 모드 (SSE 스트리밍)
./platformagent agent-api start
curl -N -X POST http://localhost:8001/agent/ask \
  -H 'Content-Type: application/json' \
  -d '{"query": "IBKR 계약 관련 문서 찾아줘", "provider": "claude", "model": "claude-sonnet-4-20250514"}'
```

### 데이터 동기화

```bash
# 데이터 수집
./platformagent sync notion         # Notion 증분 동기화
./platformagent sync notion --full  # Notion 전체 재수집
./platformagent sync email          # 이메일 증분 수집
./platformagent sync email --full   # 이메일 전체 재수집
./platformagent sync all            # 전체 소스 증분 동기화

# OCR 텍스트 추출 (이미지/PDF → .txt sidecar)
./platformagent ocr                 # 전체 소스 OCR
./platformagent ocr notion          # Notion 파일만
./platformagent ocr email           # 이메일 첨부파일만

# 검색 인덱스 갱신
./platformagent index               # 증분 인덱싱
./platformagent index --full        # 전체 재인덱싱
```

### 크래시 복구 및 재개

수집 중 시스템이 중단되면, **동일 명령어를 다시 실행**하면 자동으로 재개됩니다.

```bash
# 크래시 후 재실행 — 중단 지점부터 이어서 수집
./platformagent sync email

# 로그 예시:
# "이전 중단된 동기화 1건을 'interrupted'로 정리"
# "이전 중단 지점에서 재개: offset=250/3092"
```

**증분 (기본) vs `--full` 차이:**

| | 증분 (기본) | `--full` |
|--|------------|----------|
| 이메일 | 저장된 커서(offset)부터 재개, 수집 완료된 메일은 스킵 | 커서 삭제 + 전체 메일 처음부터 재수집 |
| Notion | `page_state.last_edited` 비교 → 변경된 페이지만 수집 | `page_state` 초기화 → 전체 재수집 |
| 용도 | 일상적인 동기화, 크래시 복구 | frontmatter 형식 변경, 데이터 정합성 재확인 시 |

내부 동작:
- **sync_log 정리**: 이전에 크래시로 `running` 상태로 남은 기록을 `interrupted`로 자동 전환
- **Atomic write**: 파일 쓰기 시 `.tmp` → `os.replace()` 패턴으로 불완전 파일 방지
- **페이지네이션 커서**: 이메일 수집 시 50건 페이지마다 offset을 DB에 저장, 정상 완료 시 삭제

## 검색 기능

### Hybrid Search

Vector(bge-m3) + BM25(MeCab 토크나이저) → RRF 합산 → Cross-encoder Reranker(bge-reranker-v2-m3)

### 검색 필터

| 필터 | 설명 | 예시 |
|------|------|------|
| `source` | 데이터 소스 | `notion`, `daolemail` |
| `source_type` | 콘텐츠 유형 | `document`, `email_body`, `email_attachment` |
| `sender` | 발신자 이메일 | `user@example.com` |
| `recipient` | 수신자 이메일 (To+CC) | `user@example.com` |
| `participant` | 참여자 (발신+수신+참조) | `user@example.com` |

사용 가능한 필터 값은 `GET /filters` 또는 MCP `get_search_filters` 도구로 조회 가능.

### MCP 도구

| 도구 | 설명 |
|------|------|
| `search_knowledge` | 자연어 검색 (필터 지원) |
| `get_document` | 문서 전체 내용 조회 |
| `list_sources` | 수집된 문서 목록 |
| `get_related` | 관련 문서 검색 (벡터 유사도) |
| `list_email_contacts` | 이메일 인물 조회 |
| `get_search_filters` | 사용 가능한 필터 옵션 조회 |

## Web UI 기능

### 탭 구성

| 탭 | 설명 |
|------|------|
| AI Agent | Claude/Gemini 듀얼 LLM 채팅 (SSE 스트리밍) |
| 검색 | Hybrid RAG 직접 검색 + 필터 |
| API 테스트 | FastAPI 엔드포인트 테스트 |
| MCP 테스트 | MCP 서버 도구 테스트 |
| 대시보드 | 이용현황 대시보드 (검색, 클릭, 채팅 통계) |

### 이용현황 대시보드

검색/클릭/채팅 이벤트를 `click_log.db`에 자동 수집하고, 대시보드 탭에서 시각화.

- **KPI 카드**: 오늘 검색 수, 클릭률(CTR), 오늘 채팅 수, 이번 주 검색 수
- **일별 검색 추이** (최근 30일, LineChart)
- **인기 검색어 Top 10** (BarChart)
- **평균 응답시간 추이** (최근 7일, LineChart)
- **Agent 프로바이더 비율** (PieChart)
- **가장 많이 클릭된 문서 Top 10** (테이블)

통계 API: `GET /stats/summary`, `/stats/daily`, `/stats/top-queries`, `/stats/top-docs`, `/stats/timings`, `/stats/providers`

### 대화 이력 유지

Agent 채팅 메시지를 서버에 저장하여 페이지 새로고침 후에도 대화가 복원됨.

- 세션 ID를 localStorage에 저장하여 브라우저 세션 유지
- 재접속 시 최신 5건 자동 복원
- 스크롤 상단 도달 시 이전 5건씩 추가 로드 (커서 기반 페이지네이션)
- "초기화" 클릭 시 새 세션 시작

## 기술 스택

| 컴포넌트 | 기술 | 포트 |
|----------|------|------|
| Vector DB | Qdrant (Docker) | 6333 |
| Embedding | Ollama + bge-m3 | 11434 |
| Reranker | bge-reranker-v2-m3 | — |
| BM25 | rank_bm25 + MeCab | — |
| OCR | PaddleOCR (Korean) | — |
| 검색 API | FastAPI | 8000 |
| Agent API | FastAPI (SSE) | 8001 |
| MCP Server | TypeScript | 3001 |
| Web UI | Next.js | 3000 |
| Metadata DB | SQLite (수집기별 분리) | — |

## 설계 원칙

- **SSOT**: 플랫폼전략팀의 모든 데이터는 여기에 집적되어야 한다.
- **로컬 우선**: 민감 데이터는 사내 네트워크 밖으로 나가지 않음 (LLM API 호출만 예외)
- **출처 필수**: 모든 AI 응답에 source_id, file_path, chunk_location 첨부
- **증분 동기화**: `last_edited_time` 기반 delta sync

## 설계 문서

- [마스터 설계서](docs/플랫폼%20본부%20두뇌%20설계서%2031a83b647f9d80e1ada7fb559b807055.md) — 문제 정의, 운영 시나리오, 구현 계획
- [Knowledge Lake Architecture](docs/Knowledge_Lake_Architecture.md) — 3-Layer 아키텍처, 검색 파이프라인
- [Local Tech Stack](docs/Local_Tech_Stack.md) — 로컬 배포 스펙, 컴포넌트 선택 근거
- [Dense/Sparse/MultiVector 비교](docs/Dense_Sparse_MultiVector_Explained.md) — 검색 방식 비교
- [RAG vs AI Agent](docs/RAG_vs_AI_Agent.md) — 개념 구분, 시나리오 예시
- [ROADMAP](docs/ROADMAP.md) — 진행 현황 및 향후 계획
