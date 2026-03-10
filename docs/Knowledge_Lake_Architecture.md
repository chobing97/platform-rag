# Knowledge Lake + Hybrid RAG + MCP 아키텍처 제안서

**작성일:** 2026.03.06
**목적:** 플랫폼전략본부의 지식 일원화, 정확한 검색, AI 기반 의사결정 지원 시스템 설계

---

## 1. 핵심 요구사항

| # | 요구사항 | 핵심 원칙 |
|---|---------|----------|
| 1 | **데이터 수집** | 지식이 일원화된 한 저장소에 축적. 수집 노력 최소화. 노션, 이메일, 개인 파일(PDF/PPT/Excel) 모두 수집 및 맥락 기반 인덱싱 |
| 2 | **데이터 검색** | (1) 자연어 벡터 검색 + (2) MCP 기반 AI 검색. 정확한 데이터, 누락 없음, 환각 없음 |
| 3 | **AI Agent 판단** | 중앙 집중 데이터로부터 정확한 데이터를 찾아 사람의 의사결정을 돕는 판단 시스템 |

---

## 2. 전체 아키텍처

```
┌──────────────────────────────────────────────────────────┐
│                    DATA SOURCES (원천)                     │
│   Notion  │  Gmail  │  Local Files  │  Slack             │
└──────┬───────────┬──────────────┬───────────────┬────────┘
       │ API Sync  │ API Sync     │ Folder Watch  │ API Sync
       ▼           ▼              ▼               ▼
┌──────────────────────────────────────────────────────────┐
│              INGESTION PIPELINE (수집 파이프라인)           │
│                                                          │
│  ┌─────────┐  ┌───────────┐  ┌──────────┐  ┌─────────┐  │
│  │ Parser  │→ │ Chunker   │→ │ Embedder │→ │ Indexer │  │
│  │(문서해석)│  │(의미 단위) │  │(벡터 변환)│  │(저장)   │  │
│  └─────────┘  └───────────┘  └──────────┘  └─────────┘  │
│                                                          │
│  지원 포맷: PDF, PPT, Excel, MD, HTML, 이메일, 이미지(OCR) │
└────────────────────────┬─────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────┐
│               KNOWLEDGE STORE (중앙 저장소)                │
│                                                          │
│  ┌────────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  Vector DB     │  │ Metadata DB  │  │ Object Store │  │
│  │ (임베딩+검색)   │  │ (출처/태그)   │  │ (원본 파일)   │  │
│  │                │  │              │  │              │  │
│  │  pgvector /    │  │  PostgreSQL  │  │  S3 / local  │  │
│  │  Qdrant        │  │              │  │              │  │
│  └────────────────┘  └──────────────┘  └──────────────┘  │
└────────────────────────┬─────────────────────────────────┘
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
┌───────────────────┐    ┌───────────────────┐
│   Search API      │    │   MCP Server      │
│  (Hybrid RAG)     │    │  (AI 인터페이스)    │
│                   │    │                   │
│ Vector + BM25     │    │ search_knowledge  │
│ + Reranker        │    │ get_document      │
│                   │    │ list_sources      │
└────────┬──────────┘    └────────┬──────────┘
         │                        │
         ▼                        ▼
┌───────────────────┐    ┌───────────────────┐
│  Web UI (어드민)   │    │  Claude (AI Agent)│
│  사람이 검색/확인   │    │  판단/분석/초안    │
└───────────────────┘    └───────────────────┘
```

---

## 3. Layer 1: 데이터 수집 — "노력 최소화"가 핵심

**목표:** 팀원이 추가로 해야 하는 행동이 거의 없어야 한다.

### 3.1 데이터 소스별 수집 방식

| 데이터 소스 | 수집 방식 | 팀원의 노력 |
|---|---|---|
| **Notion** | Notion API로 주기적 폴링 (변경된 페이지만 delta sync) | **0** — 자동 |
| **Gmail** | Gmail API로 주기적 수집 (도메인/라벨 필터링) | **0** — 자동 |
| **Slack** | Slack API로 지정 채널 메시지 수집 | **0** — 자동 |
| **개인 파일** (PDF, PPT, Excel) | **공유 폴더 1개 지정** → Folder Watcher가 감지 즉시 수집 | **파일을 폴더에 넣기만 하면 끝** |
| **수동 등록** | Web UI에서 드래그 앤 드롭 업로드 | 필요할 때만 |

### 3.2 수집 파이프라인 상세

```
원본 파일 → Parser → Chunker → Embedder → Indexer
```

**1단계. Parser — 파일 유형별 텍스트 추출**

- PDF/PPT/Excel: `unstructured.io` 또는 `Apache Tika`
- 노션: Notion API → Markdown 변환
- 이메일: 본문 + 첨부파일 분리 처리
- 이미지 포함 문서: OCR (Tesseract / Cloud Vision)

**2단계. Chunker — 의미 단위로 분할**

- 단순 토큰 수 기반이 아닌 **Semantic Chunking** (문단/섹션/의미 경계 기준)
- 청크 크기: 500~1000 토큰, overlap 20%
- **부모-자식 관계 유지**: 청크가 어느 문서의 어느 섹션에서 왔는지 추적

**3단계. Embedder — 벡터 변환**

- 모델: `text-embedding-3-large` (OpenAI) 또는 `bge-m3` (오픈소스, 한영 모두 강함)
- 한국어+영어 혼용 환경이므로 **다국어 임베딩 모델 필수**

**4단계. Indexer — 저장 + 메타데이터 태깅**

- 모든 청크에 자동 부여: `source_type`, `source_id`, `project`, `author`, `date`, `file_path`
- 프로젝트 자동 분류: 파일 경로나 키워드 기반 룰 (IBKR, TradingView 등)

---

## 4. Layer 2: 데이터 검색 — Hybrid RAG + MCP

벡터 검색만으로는 정확도가 부족하고, 키워드 검색만으로는 맥락을 못 잡는다.

### 4.1 검색 파이프라인 (3단계)

```
사용자 질문
    │
    ▼
┌─────────────────────────────┐
│  Stage 1: Hybrid Retrieval  │
│                             │
│  ┌──────────┐ ┌──────────┐  │
│  │ Vector   │ │ BM25     │  │
│  │ Search   │ │ (키워드)  │  │
│  │ (의미)    │ │          │  │
│  └────┬─────┘ └────┬─────┘  │
│       └──────┬─────┘        │
│              ▼              │
│     Reciprocal Rank Fusion  │
│     (두 결과 합산 정렬)       │
└──────────────┬──────────────┘
               ▼
┌─────────────────────────────┐
│  Stage 2: Reranking         │
│                             │
│  Cross-encoder 모델로        │
│  질문-청크 쌍의 관련성 재평가  │
│  (상위 20개 → 상위 5개)      │
└──────────────┬──────────────┘
               ▼
┌─────────────────────────────┐
│  Stage 3: Context Assembly  │
│                             │
│  - 선택된 청크의 부모 문서 확장│
│  - 출처 메타데이터 첨부        │
│  - 원본 링크 생성             │
└──────────────┬──────────────┘
               ▼
          검색 결과 반환
    (청크 내용 + 출처 + 신뢰도)
```

### 4.2 각 단계의 역할

| 단계 | 해결하는 문제 |
|---|---|
| **Vector Search** | "ECACA 계약에서 clearing 관련 조항" 같은 의미 기반 질문 처리 |
| **BM25** | "§7.A" "Execution-Only" 같은 정확한 용어/조항번호 검색 — 벡터 검색이 놓치는 것 |
| **Reranker** | 후보 20개 중 진짜 관련 있는 5개만 골라냄 — **누락과 노이즈 동시 감소** |
| **Context Assembly** | 청크만 보면 맥락이 끊김 → 부모 문서의 앞뒤 섹션까지 확장하여 완전한 맥락 제공 |

### 4.3 MCP Server 구현

Claude가 이 검색 엔진을 직접 사용할 수 있도록 MCP 서버를 구축한다.

```typescript
// MCP Server가 노출하는 Tool 목록

tools: [
  {
    name: "search_knowledge",
    description: "자연어 질문으로 팀 지식베이스를 검색합니다",
    parameters: {
      query: string,           // "ECACA에서 clearing 관련 조항은?"
      project?: string,        // "IBKR" (선택 - 범위 좁히기)
      source_type?: string,    // "contract" | "email" | "notion" | "file"
      date_range?: { from, to },
      top_k?: number           // 반환할 결과 수 (기본 5)
    }
  },
  {
    name: "get_document",
    description: "특정 문서의 전체 내용을 가져옵니다",
    parameters: {
      source_id: string        // 검색 결과에서 받은 문서 ID
    }
  },
  {
    name: "list_sources",
    description: "특정 조건의 문서 목록을 조회합니다",
    parameters: {
      project?: string,
      source_type?: string,
      author?: string,
      date_range?: { from, to }
    }
  },
  {
    name: "get_related",
    description: "특정 문서와 관련된 다른 문서들을 찾습니다",
    parameters: {
      source_id: string,
      top_k?: number
    }
  }
]
```

### 4.4 환각 방지 메커니즘

| 방법 | 구현 |
|---|---|
| **출처 강제 첨부** | MCP 응답에 항상 `source_id`, `file_path`, `chunk_location`을 포함. AI가 근거 없이 답하는 것을 구조적으로 차단 |
| **신뢰도 점수** | Reranker 점수가 임계값 미만이면 "관련 자료를 찾지 못했습니다"를 명시적으로 반환 |
| **검색 결과 없음 처리** | 검색 결과가 0건이면 AI에게 "추측하지 말고 데이터 부재를 알려라"는 시스템 프롬프트 적용 |
| **교차 검증** | 중요 판단 시 `search_knowledge`를 다른 키워드로 2~3회 호출하여 누락 체크 |

---

## 5. Layer 3: AI Agent 판단 시스템

### 5.1 시스템 구조

```
┌────────────────────────────────────────────┐
│            Claude (System Prompt)           │
│                                            │
│  "너는 플랫폼전략본부의 분석 보조관이다.       │
│   반드시 search_knowledge로 근거를 찾고,     │
│   출처를 명시한 후에만 답변하라.              │
│   데이터가 없으면 '해당 데이터 없음'이라 하라." │
│                                            │
│  MCP 연결: Knowledge Store                  │
│                                            │
│  ┌──────────────────────────────────┐       │
│  │  사용 가능 Tool:                  │       │
│  │  - search_knowledge              │       │
│  │  - get_document                  │       │
│  │  - list_sources                  │       │
│  │  - get_related                   │       │
│  └──────────────────────────────────┘       │
└────────────────────────────────────────────┘
```

### 5.2 작동 예시: IBKR Microcap 이슈

```
CH: "IBKR이 우리를 Execution-Only로 분류했는데,
     ECACA 계약상 이걸 반박할 근거가 있어?"

Claude 내부 동작:

  1. search_knowledge("ECACA Execution-Only clearing", project="IBKR")
     → §7.A, §4A, Preamble 청크 반환 (출처 포함)

  2. search_knowledge("IBKR product scope clearing relationship")
     → 과거 분석 Decision, IBKR 메일 이력 반환

  3. get_document(source_id="ecaca_addendum1")
     → Addendum 1 전문 확인

  4. 근거를 조합하여 분석:
     "ECACA §7.A에 따르면 [원문 인용]...
      따라서 Product Scope ≠ Clearing Relationship 논거가 성립합니다.

      [출처]
      - contract:ECACA_§7.A (Addendum 1)
      - contract:ECACA_§4A (Preamble)
      - email:ibkr_2026-03-02 (Stacy 회신)"
```

**핵심: AI가 자체 지식으로 답하는 것이 아니라, 반드시 Knowledge Store에서 검색한 데이터를 근거로만 답한다.**

---

## 6. 기술 스택 제안

4명 팀의 현실을 고려하여, 관리 부담이 적은 조합을 제안한다.

| 컴포넌트 | 추천 | 이유 |
|---|---|---|
| **Vector DB + Metadata DB** | **Supabase (pgvector)** | PostgreSQL 하나로 벡터+관계형 모두 해결. 호스팅 관리 불필요. Row Level Security로 접근 제어 |
| **Object Storage** | Supabase Storage 또는 S3 | 원본 파일 보관 |
| **Embedding** | `bge-m3` (self-host) 또는 OpenAI `text-embedding-3-large` | 한영 혼용 필수. bge-m3는 다국어+하이브리드 검색 모두 지원 |
| **Reranker** | `bge-reranker-v2-m3` 또는 Cohere Rerank | 정확도 핵심 |
| **Document Parser** | `unstructured.io` | PDF/PPT/Excel/HTML 모두 처리. 오픈소스 |
| **Ingestion Orchestration** | Python 스크립트 + cron | 단순하게. Airflow 같은 건 과도 |
| **MCP Server** | TypeScript (Node.js) | Claude MCP 공식 SDK가 TypeScript 기반 |
| **Web UI (어드민)** | Next.js | 프론트 담당자가 빠르게 구축 가능 |
| **AI** | Claude API (Opus/Sonnet) + MCP | 판단 품질 + 도구 사용 |

---

## 7. 구축 우선순위 (현실적 순서)

### Phase 1 (2주): 수집 + 저장 기반 구축

- Supabase 셋업 (pgvector 활성화)
- 문서 파서 파이프라인 구축 (PDF, Excel, PPT → 텍스트 → 청크 → 임베딩)
- Notion API 커넥터 (IBKR 프로젝트 페이지만 우선)
- Gmail API 커넥터 (IBKR 관련 메일만 우선)
- 공유 폴더 Watcher (계약서 PDF 등)

### Phase 2 (2주): 검색 엔진 + MCP

- Hybrid Search API 구현 (Vector + BM25 + Reranker)
- MCP Server 구현 (search_knowledge, get_document 등)
- Claude와 MCP 연결 테스트
- 환각 방지 시스템 프롬프트 튜닝

### Phase 3 (2주): Web UI + 실사용

- 어드민에 검색 UI 탑재
- 팀 전원 실사용 시작 (IBKR 프로젝트 중심)
- 검색 품질 피드백 루프 (못 찾는 케이스 수집 → 청킹/임베딩 개선)
- 타 프로젝트 데이터 확장 수집

---

## 8. 기존 설계서 대비 차이점

| 기존 설계서 (두뇌 설계서 v6.0) | 이 제안 |
|---|---|
| 마크다운 파일 기반 SSOT | **DB 기반 Knowledge Store** — 검색 정확도와 확장성이 근본적으로 다름 |
| 팀원이 "저장해"로 수동 입력 | **자동 수집** — Notion/Gmail/폴더에서 알아서 긁어옴. 팀원 추가 행동 최소화 |
| 노션 MCP의 RAG 한계를 우회 | **자체 RAG 구축** — 청킹/임베딩/리랭킹을 직접 제어하므로 정확도 통제 가능 |
| 5개 에이전트 역할 분리 | **1개 통합 검색 MCP + AI 판단** — 에이전트를 나누기보다 데이터 검색의 정확도에 집중 |
| 해시 기반 파일 동시성 관리 | **DB 트랜잭션** — 동시성 문제가 구조적으로 해결됨 |

**핵심 차이:** 기존 설계서는 "AI 에이전트가 일을 해준다"에 초점이 있고, 이 제안은 "정확한 데이터를 정확하게 찾는다"에 초점이 있다. 정확한 검색이 되면, 그 위에 어떤 에이전트든 올릴 수 있다. 기반 없이 에이전트를 먼저 만들면 환각과 부정확한 답변으로 신뢰를 잃게 된다.
