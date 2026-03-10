# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Platform RAG** — 플랫폼 본부(4인 팀, 5개 동시 프로젝트)를 위한 AI 기반 지식 관리 시스템.

핵심 목표: 노션·이메일·로컬 파일에 분산된 지식을 수집 → Hybrid RAG로 검색 → AI Agent가 의사결정 지원.

## Project Structure

```
platform rag/
├── src/
│   ├── collectors/    # Python — Notion 수집기 (own .venv)
│   ├── search/        # Python — FastAPI + Hybrid Search (own .venv)
│   ├── web/           # Next.js — 검색 UI (own node_modules)
│   ├── mcp-server/    # TypeScript — MCP Server
│   └── agent/         # Python — AI Agent, Claude/Gemini 듀얼 LLM (own .venv)
├── data/              # 공유 데이터 (notion/, DB 파일들)
├── docs/              # 설계 문서
└── run.sh             # 서비스 관리 스크립트
```

## Architecture (3-Layer)

1. **Data Collection Layer** — Notion API delta sync, Gmail API, 로컬 파일 감시(launchd), 웹 UI 업로드
2. **Hybrid Search Engine** — Vector(bge-m3) + BM25(rank_bm25) → RRF 합산 → Cross-encoder Reranker(bge-reranker-v2-m3) → Context Assembly
3. **AI Agent Layer** — Claude MCP Server가 `search_knowledge`, `get_document`, `list_sources`, `get_related` 도구 제공

## Target Tech Stack (Local-first, Apple Silicon Mac)

| Component | Choice | Notes |
|-----------|--------|-------|
| Vector DB | Qdrant (Docker, :6333) | REST API, standalone |
| Metadata DB | SQLite | 단일 파일, 충분한 동시성 |
| Embedding | Ollama + bge-m3 (:11434) | 로컬, API 비용 없음 |
| Reranker | bge-reranker-v2-m3 | 로컬 Python |
| BM25 | rank_bm25 (Python) | 경량 |
| Parser | unstructured.io | PDF/PPT/Excel 처리 |
| Search API | FastAPI (:8000) | Python |
| MCP Server | TypeScript (Node.js) | Claude MCP SDK |
| Web UI | Next.js (:3000) | 팀 LAN 접근 |
| Scheduler | launchd | Notion 30분, Gmail 30분, 파일 10분 |

## Key Documents in `docs/`

- `플랫폼 본부 두뇌 설계서*.md` — 마스터 설계서 (문제 정의, 9개 운영 시나리오, 팀 구조, SSOT 규칙, 4주 구현 계획, KPI)
- `Knowledge_Lake_Architecture.md` — 3-Layer 아키텍처, 검색 파이프라인, MCP 도구 스펙, 할루시네이션 방지 전략
- `Local_Tech_Stack.md` — 로컬 배포 스펙, 컴포넌트 선택 근거, 설치 명령어, 팀 접근 구조
- `Dense_Sparse_MultiVector_Explained.md` — Dense/Sparse/Multi-vector 검색 비교, 3단계 퍼널 전략
- `RAG_vs_AI_Agent.md` — RAG vs Agent 개념 구분, 실제 시나리오 예시

## Design Principles

- **SSOT (Single Source of Truth)**: 구조화된 Markdown이 유일한 정보 원천
- **로컬 우선**: 민감 데이터는 사내 네트워크 밖으로 나가지 않음 (Claude API 호출만 예외)
- **출처 필수**: 모든 AI 응답에 source_id, file_path, chunk_location 첨부 → 할루시네이션 방지
- **증분 동기화**: `last_edited_time` 기반 delta sync로 불필요한 재수집 방지

## Language & Conventions

- 문서는 **한국어**로 작성 (기술 용어는 영문 병기)
- 코드 구현 시: Python(데이터 파이프라인/검색), TypeScript(MCP 서버/웹 UI)
