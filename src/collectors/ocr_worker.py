"""OCR Leader-Worker — 미디어 파일 텍스트 추출.

Leader: 작업 파일 목록 수집, 메모리 모니터링, Worker 프로세스 관리
Worker: 상주 프로세스, task_queue로 파일 경로를 받아 OCR 처리 (모델 1회 로딩)

사용법:
    cd src/collectors
    .venv/bin/python -m ocr_worker [--workers 1] [--max-size 5] [--memory-limit 50]
"""

import argparse
import logging
import multiprocessing as mp
import os
import queue
import time
from pathlib import Path

from ocr_db import log_skip

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ocr_worker")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
NOTION_DIR = os.path.join(PROJECT_ROOT, "data", "raw", "notion")
DAOLEMAIL_DIR = os.path.join(PROJECT_ROOT, "data", "raw", "daolemail")

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
PDF_EXTENSIONS = {".pdf"}
OCR_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS

OCR_TIMEOUT = 300  # 기본 5분 (CLI --timeout 으로 변경 가능)


# ─── Worker (상주 프로세스) ────────────────────────────

def _worker_loop(task_queue: mp.Queue, result_queue: mp.Queue, total_tasks: int):
    """Worker 루프: task_queue에서 파일 경로를 받아 OCR 처리. None 수신 시 종료."""
    from paddleocr import PaddleOCR

    logger.info("PaddleOCR 모델 로딩 시작 (pid=%d)", os.getpid())
    ocr = PaddleOCR(lang="korean")
    logger.info("PaddleOCR 모델 로딩 완료 (pid=%d)", os.getpid())

    job_count = 1
    while True:
        file_path = task_queue.get()
        if file_path is None:
            break
        # Leader에게 현재 처리 파일 알림
        result_queue.put({"type": "started", "path": file_path, "pid": os.getpid()})
        logger.info("Worker pid=%d: (%d/%d) %s", os.getpid(), job_count, total_tasks, file_path)

        try:
            t0 = time.time()
            result = ocr.predict(file_path)

            if not result:
                result_queue.put({"type": "result", "path": file_path, "status": "empty", "chars": 0, "time": time.time() - t0})
                continue

            lines = []
            for page in result:
                if not page:
                    continue
                texts = page.get("rec_texts", []) if isinstance(page, dict) else getattr(page, "rec_texts", [])
                scores = page.get("rec_scores", []) if isinstance(page, dict) else getattr(page, "rec_scores", [])
                for text, score in zip(texts, scores):
                    if score > 0.5:
                        lines.append(text)

            text = "\n".join(lines)
            if not text.strip():
                result_queue.put({"type": "result", "path": file_path, "status": "empty", "chars": 0, "time": time.time() - t0})
                continue

            sidecar_path = file_path + ".txt"
            with open(sidecar_path, "w", encoding="utf-8") as f:
                f.write(text)

            elapsed = time.time() - t0
            result_queue.put({"type": "result", "path": file_path, "status": "ok", "chars": len(text), "time": elapsed})

        except Exception as e:
            result_queue.put({"type": "result", "path": file_path, "status": "error", "error": str(e), "chars": 0, "time": 0})

        finally:
            job_count += 1
    logger.info("Worker pid=%d 종료", os.getpid())


# ─── Leader (메인 프로세스) ────────────────────────────

def _get_memory_percent() -> float:
    """시스템 메모리 사용률(%) 반환."""
    try:
        import psutil
        return psutil.virtual_memory().percent
    except ImportError:
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
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
            return (1 - free / total) * 100 if total else 50.0
        except Exception:
            return 50.0


def collect_tasks(max_size_mb: float, source: str = "all") -> list[str]:
    """OCR 대상 파일 목록 수집. .txt sidecar가 없는 이미지/PDF만."""
    tasks = []
    max_size_bytes = max_size_mb * 1024 * 1024

    if source == "notion":
        dirs = (NOTION_DIR,)
    elif source == "email":
        dirs = (DAOLEMAIL_DIR,)
    else:
        dirs = (NOTION_DIR, DAOLEMAIL_DIR)

    for base_dir in dirs:
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
                    log_skip(fpath, size, "size_exceeded")
                    continue

                tasks.append(fpath)

    # 작은 파일부터 처리 (빠른 진행률)
    tasks.sort(key=lambda p: os.path.getsize(p))
    return tasks


def _spawn_worker(task_queue, result_queue, total_tasks):
    """Worker 프로세스 생성 및 시작."""
    p = mp.Process(target=_worker_loop, args=(task_queue, result_queue, total_tasks))
    p.start()
    return p


def run(max_workers: int = 1, max_size_mb: float = 5.0, memory_limit: float = 50.0, timeout: int = OCR_TIMEOUT, source: str = "all"):
    """OCR Leader 메인 루프."""
    tasks = collect_tasks(max_size_mb, source=source)
    if not tasks:
        logger.info("OCR 대상 파일이 없습니다.")
        return

    total_size = sum(os.path.getsize(p) for p in tasks)
    logger.info(
        "OCR 시작: %d개 파일 (총 %.1f MB), 최대 %d workers",
        len(tasks), total_size / 1024 / 1024, max_workers,
    )

    task_queue: mp.Queue = mp.Queue()
    result_queue: mp.Queue = mp.Queue()

    # 작업 큐에 모든 파일 투입
    for fpath in tasks:
        task_queue.put(fpath)

    # Worker 시작 (메모리 체크하면서 점진적으로)
    num_workers = min(max_workers, len(tasks))
    # worker 상태 추적: pid → {"proc", "file", "started_at"}
    worker_state: dict = {}

    for i in range(num_workers):
        if i > 0:
            time.sleep(15)
            mem = _get_memory_percent()
            if mem > memory_limit:
                logger.warning(
                    "메모리 %.1f%% > %.1f%% — worker %d개로 제한",
                    mem, memory_limit, len(worker_state),
                )
                break

        p = _spawn_worker(task_queue, result_queue, len(tasks))
        worker_state[p.pid] = {"proc": p, "file": None, "started_at": None}
        task_queue.put(None)  # 각 worker에 대응하는 sentinel
        logger.info("Worker %d 시작 (pid=%d)", i + 1, p.pid)

    # 결과 수집 + 타임아웃 감시
    done = 0
    stats = {"ok": 0, "empty": 0, "error": 0, "total_chars": 0}

    while done < len(tasks):
        # 타임아웃 체크
        now = time.time()
        for pid, state in list(worker_state.items()):
            if state["file"] and state["started_at"] and (now - state["started_at"]) > timeout:
                timed_out_file = state["file"]
                logger.warning(
                    "Worker pid=%d 타임아웃 (%ds): %s — kill & 재시작",
                    pid, timeout, os.path.basename(timed_out_file),
                )

                # Worker kill
                state["proc"].kill()
                state["proc"].join(timeout=5)
                del worker_state[pid]

                # 타임아웃 파일 기록
                done += 1
                stats["error"] += 1
                try:
                    fsize = os.path.getsize(timed_out_file)
                except OSError:
                    fsize = 0
                log_skip(timed_out_file, fsize, "timeout", f"OCR timeout ({timeout}s)")
                logger.info("OCR 타임아웃 [%d/%d]: %s", done, len(tasks), os.path.basename(timed_out_file))

                # 새 Worker 생성 (killed worker의 sentinel은 큐에 남아있음)
                p = _spawn_worker(task_queue, result_queue, len(tasks))
                worker_state[p.pid] = {"proc": p, "file": None, "started_at": None}
                logger.info("Worker 재시작 (pid=%d)", p.pid)

        # 결과 수집
        try:
            r = result_queue.get(timeout=1)
        except queue.Empty:
            if not any(s["proc"].is_alive() for s in worker_state.values()):
                logger.warning("모든 worker 종료됨 — 처리 중단")
                break
            continue

        # Worker가 파일 처리 시작을 알림
        if r.get("type") == "started":
            pid = r["pid"]
            if pid in worker_state:
                worker_state[pid]["file"] = r["path"]
                worker_state[pid]["started_at"] = time.time()
            continue

        # 처리 결과
        done += 1
        # Worker 상태 초기화
        for state in worker_state.values():
            if state["file"] == r["path"]:
                state["file"] = None
                state["started_at"] = None
                break

        stats[r["status"]] += 1
        stats["total_chars"] += r["chars"]
        basename = os.path.basename(r["path"])

        if r["status"] == "ok":
            logger.info(
                "OCR 완료 [%d/%d]: %s (%d자, %.1fs)",
                done, len(tasks), basename, r["chars"], r["time"],
            )
        elif r["status"] == "error":
            logger.warning("OCR 실패: %s — %s", basename, r.get("error", ""))
            try:
                fsize = os.path.getsize(r["path"])
            except OSError:
                fsize = 0
            log_skip(r["path"], fsize, "error", r.get("error", ""))
        else:
            logger.debug("OCR 텍스트 없음: %s", basename)
            try:
                fsize = os.path.getsize(r["path"])
            except OSError:
                fsize = 0
            log_skip(r["path"], fsize, "empty")

    # Worker 종료 대기
    for pid, state in worker_state.items():
        state["proc"].join(timeout=30)
        if state["proc"].is_alive():
            logger.warning("Worker pid=%d 강제 종료", pid)
            state["proc"].kill()
            state["proc"].join(timeout=5)

    # 미처리 작업 기록 (큐에 남은 파일)
    unprocessed = []
    while not task_queue.empty():
        try:
            item = task_queue.get_nowait()
            if item is not None:
                unprocessed.append(item)
        except queue.Empty:
            break

    for fpath in unprocessed:
        try:
            fsize = os.path.getsize(fpath)
        except OSError:
            fsize = 0
        log_skip(fpath, fsize, "memory_limit")

    logger.info(
        "OCR 완료: 성공 %d, 텍스트없음 %d, 실패 %d, 미처리 %d (총 %d자 추출)",
        stats["ok"], stats["empty"], stats["error"], len(unprocessed), stats["total_chars"],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OCR Leader-Worker")
    parser.add_argument("source", nargs="?", default="all", choices=["notion", "email", "all"], help="대상 소스 (기본: all)")
    parser.add_argument("--workers", type=int, default=1, help="최대 동시 worker 수 (기본: 1)")
    parser.add_argument("--max-size", type=float, default=5.0, help="파일 크기 제한 MB (기본: 5)")
    parser.add_argument("--memory-limit", type=float, default=50.0, help="메모리 사용률 제한 %% (기본: 50)")
    parser.add_argument("--timeout", type=int, default=OCR_TIMEOUT, help="파일당 OCR 타임아웃 초 (기본: 300)")
    args = parser.parse_args()

    run(max_workers=args.workers, max_size_mb=args.max_size, memory_limit=args.memory_limit, timeout=args.timeout, source=args.source)
