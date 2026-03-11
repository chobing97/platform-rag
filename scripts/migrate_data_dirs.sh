#!/bin/bash
# data/ 디렉토리를 raw/index/web 구조로 마이그레이션.
#
# 기존:
#   data/notion/, data/daolemail/, data/sync_state.db, data/daolemail_cookies.json
#   data/bm25_corpus.db, data/click_log.db, data/qdrant_storage/
#
# 변경:
#   data/raw/notion/, data/raw/daolemail/, data/raw/sync_state.db, data/raw/daolemail_cookies.json
#   data/index/bm25_corpus.db, data/index/qdrant_storage/
#   data/web/click_log.db
#
# 사용법: ./scripts/migrate_data_dirs.sh [--dry-run]

set -e

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="$ROOT_DIR/data"

DRY_RUN=false
[ "${1:-}" = "--dry-run" ] && DRY_RUN=true

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

move_item() {
    local src="$1"
    local dst="$2"
    if [ ! -e "$src" ]; then
        return
    fi
    if [ -e "$dst" ]; then
        echo -e "${YELLOW}[SKIP]${NC} 이미 존재: $dst"
        return
    fi
    if $DRY_RUN; then
        echo -e "${YELLOW}[DRY-RUN]${NC} $src → $dst"
    else
        mkdir -p "$(dirname "$dst")"
        mv "$src" "$dst"
        echo -e "${GREEN}[MOVED]${NC} $src → $dst"
    fi
}

echo ""
echo "=== data/ 디렉토리 구조 마이그레이션 ==="
echo ""

# 1. data/raw/ — 수집 원본
move_item "$DATA_DIR/notion"                "$DATA_DIR/raw/notion"
move_item "$DATA_DIR/daolemail"             "$DATA_DIR/raw/daolemail"
move_item "$DATA_DIR/sync_state.db"         "$DATA_DIR/raw/sync_state.db"
move_item "$DATA_DIR/sync_state.db-wal"     "$DATA_DIR/raw/sync_state.db-wal"
move_item "$DATA_DIR/sync_state.db-shm"     "$DATA_DIR/raw/sync_state.db-shm"
move_item "$DATA_DIR/daolemail_cookies.json" "$DATA_DIR/raw/daolemail_cookies.json"

# 2. data/index/ — 검색 인덱스
move_item "$DATA_DIR/bm25_corpus.db"        "$DATA_DIR/index/bm25_corpus.db"
move_item "$DATA_DIR/bm25_corpus.db-wal"    "$DATA_DIR/index/bm25_corpus.db-wal"
move_item "$DATA_DIR/bm25_corpus.db-shm"    "$DATA_DIR/index/bm25_corpus.db-shm"
move_item "$DATA_DIR/qdrant_storage"        "$DATA_DIR/index/qdrant_storage"

# 3. data/web/ — 웹 UI
move_item "$DATA_DIR/click_log.db"          "$DATA_DIR/web/click_log.db"
move_item "$DATA_DIR/click_log.db-wal"      "$DATA_DIR/web/click_log.db-wal"
move_item "$DATA_DIR/click_log.db-shm"      "$DATA_DIR/web/click_log.db-shm"

# 4. data/local/ 은 이미 있으면 유지
if [ -d "$DATA_DIR/local" ]; then
    echo -e "${YELLOW}[KEEP]${NC} data/local/ (기존 유지)"
fi

echo ""
if $DRY_RUN; then
    echo "[DRY-RUN] 실제 이동 없음. --dry-run 없이 다시 실행하세요."
else
    echo "마이그레이션 완료."
fi
echo ""
