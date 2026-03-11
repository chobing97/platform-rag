"""검색 엔진 설정."""

import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

# 수집 원본 데이터
RAW_DIR = os.path.join(DATA_DIR, "raw")
NOTION_DIR = os.path.join(RAW_DIR, "notion")
DAOLEMAIL_DIR = os.path.join(RAW_DIR, "daolemail")

# 검색 인덱스
INDEX_DIR = os.path.join(DATA_DIR, "index")

# 웹 UI 데이터
WEB_DIR = os.path.join(DATA_DIR, "web")

# Qdrant
QDRANT_URL = "http://localhost:6333"
QDRANT_COLLECTION = "knowledge"

# Ollama (bge-m3 embedding)
OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "bge-m3"
EMBED_DIM = 1024

# Reranker
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
RERANKER_ENABLED = True

# Chunking
CHUNK_SIZE = 500      # 문자 기준
CHUNK_OVERLAP = 50

# Search
TOP_K_RETRIEVAL = 30  # Vector + BM25 각각에서 가져올 수
TOP_K_RERANK = 20     # Reranker 후 최종 결과 수 (기본값)
RRF_K = 60            # RRF 파라미터
