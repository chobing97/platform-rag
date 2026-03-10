# Platform RAG Roadmap

현재 완료된 작업과 향후 계획을 정리한다.

## 완료

- [x] Notion 데이터 수집 (증분 동기화, 코멘트 포함)
- [x] Hybrid Search Engine (Vector + BM25 → RRF → Reranker)
- [x] MeCab 한국어 형태소 분석기 적용 (BM25)
- [x] FastAPI 검색 API
- [x] Next.js 검색 Web UI
- [x] 클릭 로그 기반 랭킹 부스팅
- [x] 검색 성능 최적화 (MPS GPU, TOP_K 튜닝)

## 진행 예정

- [ ] MCP Server 구축 (Claude Agent 연동)
- [ ] Gmail 수집기
- [ ] 로컬 파일 수집기
- [ ] launchd 스케줄러 (Notion 30분, Gmail 30분, 파일 10분)

## 향후 개선 (데이터 축적 후)

- [ ] **Reranker fine-tuning** — 클릭 로그가 충분히 쌓이면 (수백~수천 건) 도메인 특화 학습 데이터로 변환하여 bge-reranker-v2-m3를 fine-tuning. 클릭된 (query, doc) 쌍을 positive, 노출됐으나 클릭되지 않은 쌍을 negative로 활용.
- [ ] Chunk 전략 개선 (semantic chunking, 문서 구조 반영)
- [ ] 검색 품질 평가 체계 (MRR, nDCG 자동 측정)
