"""OCR 벤치마크 — 최적 workers/max-size 파라미터 탐색.

시스템 메모리 용량에 맞는 최적의 OCR 동시 워커 수와 파일 크기 한계를 측정한다.
Phase 1: 파일 크기별 단일 워커 메모리 측정
Phase 2: 워커 수 증가에 따른 동시 메모리 측정
결과는 DB에 저장되어 이후 비교 가능.

사용법:
    cd src/collectors
    .venv/bin/python -m ocr_benchmark [--samples 3] [--memory-limit 80]
"""

import argparse
import logging
import multiprocessing as mp
import os
import sys
import time
import threading
from pathlib import Path

import psutil

from ocr_db import log_benchmark

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ocr_benchmark")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
NOTION_DIR = os.path.join(PROJECT_ROOT, "data", "raw", "notion")
DAOLEMAIL_DIR = os.path.join(PROJECT_ROOT, "data", "raw", "daolemail")

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
PDF_EXTENSIONS = {".pdf"}
OCR_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS

# 크기 구간 정의 (MB)
SIZE_BUCKETS = {
    "small":  (0, 1),
    "medium": (1, 5),
    "large":  (5, 15),
}

TOTAL_MEMORY_GB = psutil.virtual_memory().total / (1024 ** 3)
CPU_COUNT = os.cpu_count() or 4


# ─── 유틸리티 ──────────────────────────────────────────

class MemoryMonitor:
    """별도 스레드에서 시스템 메모리를 주기적으로 샘플링. 피크 기록."""

    def __init__(self, interval: float = 0.3):
        self.interval = interval
        self.peak_mb: float = 0.0
        self.peak_pct: float = 0.0
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self):
        self._running = True
        self.peak_mb = 0.0
        self.peak_pct = 0.0
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> tuple[float, float]:
        """모니터링 종료. (peak_mb, peak_pct) 반환."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        return self.peak_mb, self.peak_pct

    def _loop(self):
        while self._running:
            mem = psutil.virtual_memory()
            used_mb = mem.used / (1024 ** 2)
            if used_mb > self.peak_mb:
                self.peak_mb = used_mb
                self.peak_pct = mem.percent
            time.sleep(self.interval)


def _get_baseline_memory() -> float:
    """현재 시스템 사용 메모리(MB) 반환."""
    return psutil.virtual_memory().used / (1024 ** 2)


def _worker_process(file_path: str, result_queue: mp.Queue):
    """벤치마크용 단일 OCR 워커. ocr_worker와 동일 로직."""
    try:
        t0 = time.time()
        from paddleocr import PaddleOCR
        ocr = PaddleOCR(lang="korean")

        result = ocr.predict(file_path)
        chars = 0
        if result:
            for page in result:
                if not page:
                    continue
                texts = getattr(page, "rec_texts", [])
                scores = getattr(page, "rec_scores", [])
                for text, score in zip(texts, scores):
                    if score > 0.5:
                        chars += len(text)

        elapsed = time.time() - t0
        result_queue.put({"path": file_path, "status": "ok", "chars": chars, "time": elapsed})
    except Exception as e:
        result_queue.put({"path": file_path, "status": "error", "error": str(e), "time": time.time() - t0})


# ─── 파일 수집 ─────────────────────────────────────────

def _collect_sample_files(samples_per_bucket: int) -> dict[str, list[str]]:
    """크기 구간별 샘플 파일 수집."""
    buckets: dict[str, list[tuple[int, str]]] = {k: [] for k in SIZE_BUCKETS}

    for base_dir in (NOTION_DIR, DAOLEMAIL_DIR):
        if not os.path.isdir(base_dir):
            continue
        for root, _, files in os.walk(base_dir):
            for fname in files:
                ext = Path(fname).suffix.lower()
                if ext not in OCR_EXTENSIONS:
                    continue
                fpath = os.path.join(root, fname)
                try:
                    size = os.path.getsize(fpath)
                except OSError:
                    continue
                size_mb = size / (1024 * 1024)

                for bucket_name, (lo, hi) in SIZE_BUCKETS.items():
                    if lo <= size_mb < hi:
                        buckets[bucket_name].append((size, fpath))
                        break

    # 각 구간에서 중간 크기 위주로 샘플 선택
    result = {}
    for bucket_name, files in buckets.items():
        if not files:
            continue
        files.sort(key=lambda x: x[0])
        n = len(files)
        if n <= samples_per_bucket:
            result[bucket_name] = [f[1] for f in files]
        else:
            # 균등 간격 샘플링
            indices = [int(i * (n - 1) / (samples_per_bucket - 1)) for i in range(samples_per_bucket)]
            result[bucket_name] = [files[i][1] for i in indices]

    return result


# ─── 벤치마크 실행 ─────────────────────────────────────

def _run_batch(files: list[str], num_workers: int, memory_limit: float, worker_timeout: float = 120.0) -> dict | None:
    """파일 리스트를 num_workers 동시 실행. 결과 반환. 메모리 초과 시 None."""
    baseline_mb = _get_baseline_memory()
    monitor = MemoryMonitor(interval=0.3)
    monitor.start()

    result_queue: mp.Queue = mp.Queue()
    active: dict[int, tuple[mp.Process, float]] = {}  # pid → (Process, start_time)
    task_idx = 0
    results = []
    aborted = False

    while task_idx < len(files) or active:
        # 완료된 worker 수거
        finished = []
        for pid, (proc, start_t) in active.items():
            if not proc.is_alive():
                proc.join(timeout=2)
                finished.append(pid)
            elif time.time() - start_t > worker_timeout:
                logger.warning("워커 타임아웃 (pid=%d, %.0fs) — 강제 종료", pid, worker_timeout)
                proc.kill()
                proc.join(timeout=5)
                finished.append(pid)
                results.append({"path": "timeout", "status": "error", "error": "timeout", "time": worker_timeout})

        for pid in finished:
            del active[pid]

        # 결과 수집
        while not result_queue.empty():
            results.append(result_queue.get_nowait())

        # 메모리 체크
        current_pct = psutil.virtual_memory().percent
        if current_pct > memory_limit:
            logger.warning("메모리 %.1f%% > %.1f%% — 배치 중단", current_pct, memory_limit)
            aborted = True
            # 활성 worker 강제 종료
            for pid, (proc, _) in active.items():
                proc.kill()
                proc.join(timeout=5)
            active.clear()
            break

        # 새 worker 생성
        while task_idx < len(files) and len(active) < num_workers:
            fpath = files[task_idx]
            task_idx += 1
            proc = mp.Process(target=_worker_process, args=(fpath, result_queue))
            proc.start()
            active[proc.pid] = (proc, time.time())

        if active:
            time.sleep(0.5)

    # 남은 결과 수집
    while not result_queue.empty():
        results.append(result_queue.get_nowait())

    peak_mb, peak_pct = monitor.stop()

    if aborted:
        return None

    times = [r["time"] for r in results if r["status"] == "ok"]
    avg_time = sum(times) / len(times) if times else 0
    total_time = sum(times)

    return {
        "baseline_mb": baseline_mb,
        "peak_mb": peak_mb,
        "delta_mb": peak_mb - baseline_mb,
        "peak_pct": peak_pct,
        "avg_time": avg_time,
        "total_time": total_time,
        "file_count": len(files),
        "results": results,
    }


def run_benchmark(samples_per_bucket: int = 3, memory_limit: float = 85.0):
    """전체 벤치마크 실행."""
    print("=" * 60)
    print("  OCR 벤치마크")
    print("=" * 60)
    print(f"  시스템: {TOTAL_MEMORY_GB:.1f}GB RAM, {CPU_COUNT} CPU cores")
    print(f"  메모리 제한: {memory_limit}%")
    print(f"  구간별 샘플: {samples_per_bucket}개")
    print("=" * 60)

    samples = _collect_sample_files(samples_per_bucket)
    if not samples:
        logger.error("OCR 대상 파일이 없습니다.")
        return

    for bucket, files in samples.items():
        sizes = [os.path.getsize(f) / 1024 / 1024 for f in files]
        print(f"\n  [{bucket}] {len(files)}개 샘플 (평균 {sum(sizes)/len(sizes):.1f}MB)")

    # ─── Phase 1: 파일 크기별 단일 워커 ─────────────────
    print("\n" + "─" * 60)
    print("  Phase 1: 파일 크기별 단일 워커 메모리 측정")
    print("─" * 60)

    phase1_results: dict[str, dict] = {}

    for bucket in ("small", "medium", "large"):
        if bucket not in samples:
            print(f"\n  [{bucket}] 샘플 없음 — 건너뜀")
            continue

        files = samples[bucket]
        print(f"\n  [{bucket}] {len(files)}개 파일 테스트 중...")

        result = _run_batch(files, num_workers=1, memory_limit=memory_limit)
        if result is None:
            print(f"  ⚠️  [{bucket}] 메모리 초과 — 단일 워커로도 위험")
            phase1_results[bucket] = {"aborted": True}
            continue

        phase1_results[bucket] = result
        print(f"  메모리: 베이스 {result['baseline_mb']:.0f}MB → 피크 {result['peak_mb']:.0f}MB (Δ{result['delta_mb']:.0f}MB)")
        print(f"  시간: 평균 {result['avg_time']:.1f}s/file, 시스템 {result['peak_pct']:.1f}%")

        log_benchmark(
            workers=1, file_count=len(files), size_bucket=bucket,
            peak_memory_mb=result["peak_mb"], baseline_memory_mb=result["baseline_mb"],
            avg_time_sec=result["avg_time"], total_time_sec=result["total_time"],
            system_memory_pct=result["peak_pct"],
        )

    # ─── Phase 2: 워커 수 증가 (small 파일 사용) ────────
    print("\n" + "─" * 60)
    print("  Phase 2: 동시 워커 수별 메모리 측정 (small 파일)")
    print("─" * 60)

    # small 파일이 없으면 가장 작은 구간 사용
    test_bucket = "small" if "small" in samples else next(iter(samples))
    test_files = samples[test_bucket]

    # 테스트할 파일이 부족하면 반복 사용
    if len(test_files) < 3:
        test_files = (test_files * 3)[:3]

    phase2_results: dict[int, dict | None] = {}
    max_safe_workers = 0

    for n_workers in (1, 2, 3):
        # 워커 수만큼 파일 필요
        batch_files = (test_files * n_workers)[:n_workers]
        print(f"\n  workers={n_workers}: {len(batch_files)}개 파일 동시 처리...")

        result = _run_batch(batch_files, num_workers=n_workers, memory_limit=memory_limit)
        phase2_results[n_workers] = result

        if result is None:
            print(f"  ⚠️  workers={n_workers} 메모리 초과 — 이 워커 수는 위험")
            break

        max_safe_workers = n_workers
        print(f"  메모리: 피크 {result['peak_mb']:.0f}MB ({result['peak_pct']:.1f}%), Δ{result['delta_mb']:.0f}MB")
        print(f"  시간: 평균 {result['avg_time']:.1f}s/file")

        log_benchmark(
            workers=n_workers, file_count=len(batch_files), size_bucket=test_bucket,
            peak_memory_mb=result["peak_mb"], baseline_memory_mb=result["baseline_mb"],
            avg_time_sec=result["avg_time"], total_time_sec=result["total_time"],
            system_memory_pct=result["peak_pct"],
        )

    # ─── 결과 요약 및 권장 설정 ──────────────────────────
    print("\n" + "=" * 60)
    print("  결과 요약")
    print("=" * 60)

    # 최대 안전 파일 크기: Phase 1에서 성공한 가장 큰 구간 기준
    # Phase 1이 모두 실패해도 Phase 2에서 1 worker 성공이면 small 파일은 처리 가능
    max_safe_size = 10.0  # 기본
    phase1_passed = False
    for bucket in ("large", "medium", "small"):
        if bucket in phase1_results and not phase1_results[bucket].get("aborted"):
            _, (_, hi) = next((b for b in SIZE_BUCKETS.items() if b[0] == bucket))
            max_safe_size = hi
            phase1_passed = True
            print(f"  [{bucket}] 구간까지 안전 → max-size={hi}MB")
            break

    if not phase1_passed:
        # Phase 1 모두 실패 → Phase 2 workers=1 결과로 판단
        if max_safe_workers >= 1 and phase2_results.get(1):
            # workers=1이 성공했으므로 파일 크기가 아닌 메모리 누적이 문제
            # small 파일은 개별적으로 처리 가능
            max_safe_size = 5.0
            print("  Phase 1 연속 처리 시 메모리 누적 — 개별 처리는 가능")
            print(f"  workers=1 기준 Δ{phase2_results[1]['delta_mb']:.0f}MB → max-size=5MB 권장")
        else:
            max_safe_size = 1.0
            print("  ⚠️  단일 워커도 실패 — max-size를 1MB로 제한")

    # 메모리 여유를 고려한 워커 수 (Phase 2 기준)
    if max_safe_workers > 0 and phase2_results.get(max_safe_workers):
        peak_pct = phase2_results[max_safe_workers]["peak_pct"]
        if peak_pct > 80:
            max_safe_workers = max(1, max_safe_workers - 1)

    # memory_limit 권장: 워커 피크 + 10% 여유
    if max_safe_workers > 0 and phase2_results.get(max_safe_workers):
        recommended_mem_limit = min(90.0, phase2_results[max_safe_workers]["peak_pct"] + 10)
    else:
        recommended_mem_limit = 80.0

    print(f"\n  ┌───────────────────────────────────────┐")
    print(f"  │  권장 설정                              │")
    print(f"  ├───────────────────────────────────────┤")
    print(f"  │  --workers {max_safe_workers:<30}│")
    print(f"  │  --max-size {max_safe_size:<29.0f}│")
    print(f"  │  --memory-limit {recommended_mem_limit:<24.0f}│")
    print(f"  └───────────────────────────────────────┘")
    print()
    print(f"  실행 예:")
    print(f"  ./platformagent ocr --workers {max_safe_workers} --max-size {max_safe_size:.0f} --memory-limit {recommended_mem_limit:.0f}")
    print()

    return {
        "max_workers": max_safe_workers,
        "max_size_mb": max_safe_size,
        "memory_limit": recommended_mem_limit,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OCR 벤치마크 — 최적 파라미터 탐색")
    parser.add_argument("--samples", type=int, default=3, help="구간별 샘플 파일 수 (기본: 3)")
    parser.add_argument("--memory-limit", type=float, default=85.0, help="메모리 사용률 제한 %% (기본: 85)")
    args = parser.parse_args()

    run_benchmark(samples_per_bucket=args.samples, memory_limit=args.memory_limit)
