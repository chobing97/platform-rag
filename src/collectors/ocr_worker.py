"""OCR Leader-Worker — 미디어 파일 텍스트 추출.

Leader: 작업 파일 목록 수집, 메모리 모니터링, Worker 프로세스 관리
Worker: fork된 자식 프로세스, 1파일 처리 후 종료 (메모리 완전 해제)

사용법:
    cd src/collectors
    .venv/bin/python -m ocr_worker [--workers 5] [--max-size 10] [--memory-limit 80]
"""

import argparse
import logging
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ocr_worker")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
NOTION_DIR = os.path.join(PROJECT_ROOT, "data", "notion")
DAOLEMAIL_DIR = os.path.join(PROJECT_ROOT, "data", "daolemail")

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
PDF_EXTENSIONS = {".pdf"}
OCR_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS


# ─── Worker (자식 프로세스) ────────────────────────────

def _worker_process(file_path: str, result_queue: mp.Queue):
    """단일 파일 OCR 처리. 결과를 queue로 전달. 프로세스 종료 시 메모리 해제."""
    try:
        t0 = time.time()
        ext = Path(file_path).suffix.lower()
        basename = os.path.basename(file_path)

        from paddleocr import PaddleOCR
        ocr = PaddleOCR(lang="korean")

        result = ocr.predict(file_path)
        if not result:
            result_queue.put({"path": file_path, "status": "empty", "chars": 0, "time": time.time() - t0})
            return

        # 텍스트 추출
        lines = []
        for page in result:
            if not page:
                continue
            texts = getattr(page, "rec_texts", [])
            scores = getattr(page, "rec_scores", [])
            for text, score in zip(texts, scores):
                if score > 0.5:
                    lines.append(text)

        text = "\n".join(lines)
        if not text.strip():
            result_queue.put({"path": file_path, "status": "empty", "chars": 0, "time": time.time() - t0})
            return

        # .txt sidecar 저장
        sidecar_path = file_path + ".txt"
        with open(sidecar_path, "w", encoding="utf-8") as f:
            f.write(text)

        elapsed = time.time() - t0
        result_queue.put({"path": file_path, "status": "ok", "chars": len(text), "time": elapsed})

    except Exception as e:
        result_queue.put({"path": file_path, "status": "error", "error": str(e), "chars": 0, "time": 0})


# ─── Leader (메인 프로세스) ────────────────────────────

def _get_memory_percent() -> float:
    """시스템 메모리 사용률(%) 반환."""
    try:
        import psutil
        return psutil.virtual_memory().percent
    except ImportError:
        # psutil 없으면 macOS sysctl로 대체
        try:
            import subprocess
            result = subprocess.run(
                ["vm_stat"], capture_output=True, text=True, timeout=5
            )
            lines = result.stdout.strip().split("\n")
            page_size = 16384  # Apple Silicon default
            free = 0
            for line in lines:
                if "Pages free" in line:
                    free = int(line.split(":")[1].strip().rstrip(".")) * page_size
            # 대략적 계산
            import resource
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
            return (1 - free / total) * 100 if total else 50.0
        except Exception:
            return 50.0  # 측정 불가 시 안전한 기본값


def collect_tasks(max_size_mb: float) -> list[str]:
    """OCR 대상 파일 목록 수집. .txt sidecar가 없는 이미지/PDF만."""
    tasks = []
    max_size_bytes = max_size_mb * 1024 * 1024

    for base_dir in (NOTION_DIR, DAOLEMAIL_DIR):
        if not os.path.isdir(base_dir):
            continue
        for root, _dirs, files in os.walk(base_dir):
            for fname in files:
                ext = Path(fname).suffix.lower()
                if ext not in OCR_EXTENSIONS:
                    continue

                fpath = os.path.join(root, fname)

                # sidecar 이미 존재하면 스킵
                if os.path.exists(fpath + ".txt"):
                    continue

                # 크기 제한
                try:
                    size = os.path.getsize(fpath)
                except OSError:
                    continue
                if size > max_size_bytes:
                    logger.info("크기 초과 스킵 (%.1f MB): %s", size / 1024 / 1024, fname)
                    continue

                tasks.append(fpath)

    # 작은 파일부터 처리 (빠른 진행률)
    tasks.sort(key=lambda p: os.path.getsize(p))
    return tasks


def run(max_workers: int = 5, max_size_mb: float = 10.0, memory_limit: float = 80.0):
    """OCR Leader 메인 루프."""
    tasks = collect_tasks(max_size_mb)
    if not tasks:
        logger.info("OCR 대상 파일이 없습니다.")
        return

    total_size = sum(os.path.getsize(p) for p in tasks)
    logger.info(
        "OCR 시작: %d개 파일 (총 %.1f MB), 최대 %d workers",
        len(tasks), total_size / 1024 / 1024, max_workers,
    )

    result_queue: mp.Queue = mp.Queue()
    active: dict[int, mp.Process] = {}  # pid → Process
    task_idx = 0
    stats = {"ok": 0, "empty": 0, "error": 0, "total_chars": 0}

    while task_idx < len(tasks) or active:
        # 완료된 worker 수거
        finished_pids = []
        for pid, proc in active.items():
            if not proc.is_alive():
                proc.join(timeout=1)
                finished_pids.append(pid)

        for pid in finished_pids:
            del active[pid]

        # 결과 수집
        while not result_queue.empty():
            r = result_queue.get_nowait()
            stats[r["status"]] += 1
            stats["total_chars"] += r["chars"]
            basename = os.path.basename(r["path"])
            if r["status"] == "ok":
                logger.info(
                    "OCR 완료 [%d/%d]: %s (%d자, %.1fs)",
                    stats["ok"] + stats["empty"] + stats["error"], len(tasks),
                    basename, r["chars"], r["time"],
                )
            elif r["status"] == "error":
                logger.warning("OCR 실패: %s — %s", basename, r.get("error", ""))
            else:
                logger.debug("OCR 텍스트 없음: %s", basename)

        # 새 worker 생성
        while task_idx < len(tasks) and len(active) < max_workers:
            # 메모리 체크
            mem = _get_memory_percent()
            if mem > memory_limit:
                logger.warning(
                    "메모리 %.1f%% > %.1f%% — 기존 worker 완료 대기 (active: %d)",
                    mem, memory_limit, len(active),
                )
                break

            fpath = tasks[task_idx]
            task_idx += 1
            proc = mp.Process(target=_worker_process, args=(fpath, result_queue))
            proc.start()
            active[proc.pid] = proc
            logger.debug("Worker 시작 (pid=%d): %s", proc.pid, os.path.basename(fpath))

        if active:
            time.sleep(0.5)

    # 남은 결과 수집
    while not result_queue.empty():
        r = result_queue.get_nowait()
        stats[r["status"]] += 1
        stats["total_chars"] += r["chars"]

    logger.info(
        "OCR 완료: 성공 %d, 텍스트없음 %d, 실패 %d (총 %d자 추출)",
        stats["ok"], stats["empty"], stats["error"], stats["total_chars"],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OCR Leader-Worker")
    parser.add_argument("--workers", type=int, default=5, help="최대 동시 worker 수 (기본: 5)")
    parser.add_argument("--max-size", type=float, default=10.0, help="파일 크기 제한 MB (기본: 10)")
    parser.add_argument("--memory-limit", type=float, default=80.0, help="메모리 사용률 제한 %% (기본: 80)")
    args = parser.parse_args()

    run(max_workers=args.workers, max_size_mb=args.max_size, memory_limit=args.memory_limit)
