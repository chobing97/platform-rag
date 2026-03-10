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
│  Qdrant (:6333)       │  │  SQLite (metadata, BM25)    │
│  Ollama (:11434)      │  │  click_log.db               │
└───────────────────────┘  └─────────────────────────────┘
               ▲
┌──────────────┴─────────────────────────────────────────┐
│              Data Collection Layer                      │
│   Notion (delta sync) · Gmail · 로컬 파일 (launchd)    │
└─────────────────────────────────────────────────────────┘
```

## 프로젝트 구조

```
platform-rag/
├── src/
│   ├── collectors/    # Python — Notion 수집기
│   ├── search/        # Python — FastAPI + Hybrid Search
│   ├── web/           # Next.js — 검색 UI + AI Agent 채팅
│   ├── mcp-server/    # TypeScript — MCP Server
│   └── agent/         # Python — AI Agent (Claude/Gemini 듀얼 LLM)
├── data/              # 공유 데이터 (notion/, DB 파일들)
├── logs/              # 서비스 로그
├── docs/              # 설계 문서
├── platformagent      # 서비스 관리 스크립트
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
./platformagent sync notion         # 증분 동기화
./platformagent sync notion --full  # 전체 재수집
```

## 기술 스택

| 컴포넌트 | 기술 | 포트 |
|----------|------|------|
| Vector DB | Qdrant (Docker) | 6333 |
| Embedding | Ollama + bge-m3 | 11434 |
| Reranker | bge-reranker-v2-m3 | — |
| BM25 | rank_bm25 + MeCab | — |
| 검색 API | FastAPI | 8000 |
| Agent API | FastAPI (SSE) | 8001 |
| MCP Server | TypeScript | 3001 |
| Web UI | Next.js | 3000 |
| Metadata DB | SQLite | — |

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
