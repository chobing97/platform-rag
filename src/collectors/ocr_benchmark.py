"""OCR 메모리 프로파일링 — 파일 타입/사이즈별 워커 메모리 측정.

벤치마크 파일 준비 후, 파일 타입(image/pdf)·사이즈별 OCR 워커의
프로세스 메모리(RSS)를 시계열로 기록하고 matplotlib 그래프를 생성한다.

사용법:
    cd src/collectors
    .venv/bin/python -m ocr_benchmark setup              # 벤치마크 파일 준비
    .venv/bin/python -m ocr_benchmark run [--quick]       # 프로파일링 실행
    .venv/bin/python -m ocr_benchmark run --workers-only  # 워커 스케일링만
"""

import argparse
import logging
import multiprocessing as mp
import os
import shutil
import time
import threading
from collections import defaultdict
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
BENCHMARK_DIR = os.path.join(PROJECT_ROOT, "data", "benchmark-ocr")
RESULTS_DIR = os.path.join(BENCHMARK_DIR, "results")

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
PDF_EXTENSIONS = {".pdf"}
OCR_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS

SIZE_BUCKETS = {
    "tiny":   (0, 0.1),       # 0-100KB
    "small":  (0.1, 0.5),     # 100KB-500KB
    "medium": (0.5, 1.0),     # 500KB-1MB
    "large":  (1.0, 5.0),     # 1MB-5MB
    "xlarge": (5.0, float("inf")),
}
BUCKET_ORDER = ["tiny", "small", "medium", "large", "xlarge"]

TOTAL_MEMORY_GB = psutil.virtual_memory().total / (1024 ** 3)
COOLDOWN_SEC = 8  # 테스트 간 메모리 안정화 대기


# ─── 프로세스 메모리 모니터 ────────────────────────────

class ProcessMemoryMonitor:
    """워커 프로세스 RSS + 시스템 메모리를 시계열로 기록."""

    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self.worker_samples: dict[int, list[tuple[float, float]]] = {}  # pid → [(elapsed, rss_mb)]
        self.system_samples: list[tuple[float, float]] = []
        self._pids: list[int] = []
        self._running = False
        self._thread: threading.Thread | None = None
        self._start_time = 0.0
        self._lock = threading.Lock()

    def start(self):
        self._start_time = time.time()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def add_pid(self, pid: int):
        with self._lock:
            self._pids.append(pid)
            self.worker_samples[pid] = []

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self):
        while self._running:
            elapsed = time.time() - self._start_time

            with self._lock:
                pids = list(self._pids)

            for pid in pids:
                try:
                    proc = psutil.Process(pid)
                    rss = proc.memory_info().rss / (1024 ** 2)
                    for child in proc.children(recursive=True):
                        try:
                            rss += child.memory_info().rss / (1024 ** 2)
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    self.worker_samples[pid].append((elapsed, rss))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            sys_mem = psutil.virtual_memory()
            self.system_samples.append((elapsed, sys_mem.used / (1024 ** 2)))
            time.sleep(self.interval)

    @property
    def peak_worker_rss(self) -> float:
        """단일 워커의 최대 RSS (MB)."""
        peak = 0.0
        for samples in self.worker_samples.values():
            for _, rss in samples:
                peak = max(peak, rss)
        return peak

    @property
    def total_peak_rss(self) -> float:
        """전체 워커 피크 RSS 합 (MB)."""
        return sum(
            max((r for _, r in samples), default=0)
            for samples in self.worker_samples.values()
        )

    @property
    def peak_system_mb(self) -> float:
        return max((m for _, m in self.system_samples), default=0)


# ─── OCR 워커 프로세스 ────────────────────────────────

def _worker_process(file_path: str, result_queue: mp.Queue):
    """벤치마크용 OCR 워커. sidecar 파일 생성하지 않음."""
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


# ─── Setup: 벤치마크 파일 준비 ────────────────────────

def setup_benchmark_files(max_files: int = 3):
    """data/raw/에서 타입/사이즈별로 분류하여 data/benchmark-ocr/로 복사."""
    all_files: list[tuple[str, str, str, int]] = []  # (path, file_type, bucket, size)

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

                file_type = "pdf" if ext in PDF_EXTENSIONS else "image"
                size_mb = size / (1024 * 1024)

                for bucket_name, (lo, hi) in SIZE_BUCKETS.items():
                    if lo <= size_mb < hi:
                        all_files.append((fpath, file_type, bucket_name, size))
                        break

    groups: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)
    for fpath, ftype, bucket, size in all_files:
        groups[(ftype, bucket)].append((size, fpath))

    total_copied = 0
    print(f"\n  벤치마크 파일 준비 → {BENCHMARK_DIR}")
    print(f"  타입/사이즈별 최대 {max_files}개 샘플링\n")

    for (ftype, bucket) in sorted(groups.keys()):
        files = groups[(ftype, bucket)]
        files.sort(key=lambda x: x[0])
        n = len(files)

        if n <= max_files:
            selected = [f[1] for f in files]
        else:
            indices = [int(i * (n - 1) / (max_files - 1)) for i in range(max_files)]
            selected = [files[i][1] for i in indices]

        dest_dir = os.path.join(BENCHMARK_DIR, ftype, bucket)
        os.makedirs(dest_dir, exist_ok=True)

        copied = 0
        for src in selected:
            dst = os.path.join(dest_dir, os.path.basename(src))
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
                copied += 1
            total_copied += 1

        sizes = [os.path.getsize(os.path.join(dest_dir, os.path.basename(s))) / 1024 for s in selected]
        avg_kb = sum(sizes) / len(sizes) if sizes else 0
        print(f"  {ftype:>5}/{bucket:<7}: {len(selected)}개 (평균 {avg_kb:.0f}KB)")

    print(f"\n  총 {total_copied}개 파일 → {BENCHMARK_DIR}")


# ─── 프로파일링 실행 ──────────────────────────────────

def _collect_benchmark_files() -> dict[tuple[str, str], list[str]]:
    """data/benchmark-ocr/에서 타입/사이즈별 파일 목록 수집."""
    file_map: dict[tuple[str, str], list[str]] = {}

    for ftype in ("image", "pdf"):
        for bucket in BUCKET_ORDER:
            dir_path = os.path.join(BENCHMARK_DIR, ftype, bucket)
            if not os.path.isdir(dir_path):
                continue
            files = sorted([
                os.path.join(dir_path, f)
                for f in os.listdir(dir_path)
                if Path(f).suffix.lower() in OCR_EXTENSIONS
            ])
            if files:
                file_map[(ftype, bucket)] = files

    return file_map


def profile_single(file_path: str, timeout: float = 180.0) -> dict:
    """단일 파일 OCR — 워커 프로세스 메모리 시계열 기록."""
    result_queue: mp.Queue = mp.Queue()
    monitor = ProcessMemoryMonitor(interval=0.5)
    monitor.start()

    proc = mp.Process(target=_worker_process, args=(file_path, result_queue))
    proc.start()
    monitor.add_pid(proc.pid)

    start_time = time.time()
    while proc.is_alive():
        if time.time() - start_time > timeout:
            proc.kill()
            proc.join(timeout=5)
            monitor.stop()
            return {"status": "timeout", "time": timeout, "chars": 0,
                    "peak_rss_mb": monitor.peak_worker_rss, "monitor": monitor}
        time.sleep(0.5)

    proc.join(timeout=2)
    monitor.stop()

    result = result_queue.get_nowait() if not result_queue.empty() else {
        "status": "error", "time": 0, "chars": 0
    }

    return {
        "status": result.get("status", "error"),
        "time": result.get("time", 0),
        "chars": result.get("chars", 0),
        "peak_rss_mb": monitor.peak_worker_rss,
        "peak_system_mb": monitor.peak_system_mb,
        "monitor": monitor,
    }


def profile_workers(files: list[str], num_workers: int, timeout: float = 180.0) -> dict:
    """여러 워커 동시 실행 — 프로세스별 메모리 시계열 기록."""
    result_queue: mp.Queue = mp.Queue()
    monitor = ProcessMemoryMonitor(interval=0.5)
    monitor.start()

    procs = []
    batch = (files * num_workers)[:num_workers]  # 워커 수만큼 파일 확보
    for fpath in batch:
        proc = mp.Process(target=_worker_process, args=(fpath, result_queue))
        proc.start()
        monitor.add_pid(proc.pid)
        procs.append(proc)

    start_time = time.time()
    while any(p.is_alive() for p in procs):
        if time.time() - start_time > timeout:
            for p in procs:
                if p.is_alive():
                    p.kill()
            break
        time.sleep(0.5)

    for p in procs:
        p.join(timeout=5)
    monitor.stop()

    results = []
    while not result_queue.empty():
        results.append(result_queue.get_nowait())

    return {
        "peak_rss_mb": monitor.peak_worker_rss,
        "total_peak_rss_mb": monitor.total_peak_rss,
        "peak_system_mb": monitor.peak_system_mb,
        "monitor": monitor,
        "results": results,
    }


# ─── 그래프 생성 ──────────────────────────────────────

def _generate_profile_graph(title: str, monitor: ProcessMemoryMonitor, output_path: str):
    """워커 RSS + 시스템 메모리 시계열 그래프."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax1 = plt.subplots(figsize=(12, 5))

    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"]
    for i, (pid, samples) in enumerate(monitor.worker_samples.items()):
        if samples:
            times, rss = zip(*samples)
            ax1.plot(times, rss, color=colors[i % len(colors)],
                     linewidth=2, label=f"Worker {i + 1} RSS (pid={pid})")

    ax1.set_xlabel("Time (seconds)", fontsize=11)
    ax1.set_ylabel("Worker RSS (MB)", fontsize=11, color="#1f77b4")
    ax1.tick_params(axis="y", labelcolor="#1f77b4")

    ax2 = ax1.twinx()
    if monitor.system_samples:
        times, used = zip(*monitor.system_samples)
        ax2.plot(times, used, "r--", alpha=0.35, linewidth=1, label="System Used")
    ax2.set_ylabel("System Memory (MB)", fontsize=11, color="r")
    ax2.tick_params(axis="y", labelcolor="r")

    ax1.set_title(title, fontsize=13, fontweight="bold")
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=9)

    fig.tight_layout()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    plt.savefig(output_path, dpi=100, bbox_inches="tight")
    plt.close()


def _generate_summary_chart(results: list[dict], output_path: str):
    """타입/사이즈별 피크 RSS 비교 막대 그래프."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    # 타입별 집계: (ftype, bucket) → avg peak_rss
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in results:
        if r.get("peak_rss_mb", 0) > 0:
            grouped[(r["ftype"], r["bucket"])].append(r["peak_rss_mb"])

    image_data: dict[str, float] = {}
    pdf_data: dict[str, float] = {}
    for (ftype, bucket), vals in grouped.items():
        avg = sum(vals) / len(vals)
        if ftype == "image":
            image_data[bucket] = avg
        else:
            pdf_data[bucket] = avg

    buckets = [b for b in BUCKET_ORDER if b in image_data or b in pdf_data]
    if not buckets:
        return

    x = np.arange(len(buckets))
    width = 0.35

    fig, ax = plt.subplots(figsize=(10, 5))

    img_vals = [image_data.get(b, 0) for b in buckets]
    pdf_vals = [pdf_data.get(b, 0) for b in buckets]

    if any(v > 0 for v in img_vals):
        bars1 = ax.bar(x - width / 2, img_vals, width, label="Image", color="#4CAF50")
        for bar, val in zip(bars1, img_vals):
            if val > 0:
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 30,
                        f"{val:.0f}", ha="center", va="bottom", fontsize=9)
    if any(v > 0 for v in pdf_vals):
        bars2 = ax.bar(x + width / 2, pdf_vals, width, label="PDF", color="#2196F3")
        for bar, val in zip(bars2, pdf_vals):
            if val > 0:
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 30,
                        f"{val:.0f}", ha="center", va="bottom", fontsize=9)

    ax.set_xlabel("Size Bucket", fontsize=12)
    ax.set_ylabel("Peak Worker RSS (MB)", fontsize=12)
    ax.set_title("OCR Worker Memory by File Type & Size", fontsize=14, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(buckets)
    ax.legend(fontsize=11)
    ax.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    plt.savefig(output_path, dpi=100, bbox_inches="tight")
    plt.close()


def _generate_workers_chart(worker_results: dict[int, dict], output_path: str):
    """워커 수별 피크 RSS 비교 차트."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    workers = sorted(worker_results.keys())
    single_peaks = [worker_results[w]["peak_rss_mb"] for w in workers]
    total_peaks = [worker_results[w]["total_peak_rss_mb"] for w in workers]
    sys_peaks = [worker_results[w]["peak_system_mb"] for w in workers]

    fig, ax1 = plt.subplots(figsize=(8, 5))

    x = range(len(workers))
    ax1.bar([i - 0.15 for i in x], single_peaks, 0.3, label="Max Single Worker RSS", color="#4CAF50")
    ax1.bar([i + 0.15 for i in x], total_peaks, 0.3, label="Total Workers RSS", color="#2196F3")

    for i, (s, t) in enumerate(zip(single_peaks, total_peaks)):
        ax1.text(i - 0.15, s + 30, f"{s:.0f}", ha="center", fontsize=9)
        ax1.text(i + 0.15, t + 30, f"{t:.0f}", ha="center", fontsize=9)

    ax1.set_xlabel("Number of Workers", fontsize=12)
    ax1.set_ylabel("RSS (MB)", fontsize=12)
    ax1.set_xticks(list(x))
    ax1.set_xticklabels([str(w) for w in workers])

    ax2 = ax1.twinx()
    ax2.plot(list(x), sys_peaks, "r--o", alpha=0.6, label="System Peak")
    ax2.set_ylabel("System Memory (MB)", fontsize=11, color="r")

    ax1.set_title("OCR Memory by Worker Count", fontsize=14, fontweight="bold")
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=9)

    fig.tight_layout()
    plt.savefig(output_path, dpi=100, bbox_inches="tight")
    plt.close()


# ─── 메인 프로파일링 ─────────────────────────────────

def run_profiling(quick: bool = False, workers_only: bool = False):
    """전체 프로파일링 실행."""
    file_map = _collect_benchmark_files()
    if not file_map:
        print("벤치마크 파일이 없습니다. 먼저 'setup'을 실행하세요.")
        return

    os.makedirs(RESULTS_DIR, exist_ok=True)

    print("=" * 60)
    print("  OCR 메모리 프로파일링")
    print("=" * 60)
    print(f"  시스템: {TOTAL_MEMORY_GB:.1f}GB RAM")
    print(f"  벤치마크 파일:")
    for (ftype, bucket), files in sorted(file_map.items()):
        sizes = [os.path.getsize(f) / 1024 for f in files]
        print(f"    {ftype:>5}/{bucket:<7}: {len(files)}개 (평균 {sum(sizes) / len(sizes):.0f}KB)")

    all_results: list[dict] = []

    # ─── Phase 1: 타입/사이즈별 단일 워커 프로파일 ────
    if not workers_only:
        print("\n" + "-" * 60)
        print("  Phase 1: 타입/사이즈별 단일 워커 메모리 프로파일")
        print("-" * 60)

        test_count = 0
        for (ftype, bucket), files in sorted(file_map.items()):
            test_files = files[:1] if quick else files

            for i, fpath in enumerate(test_files):
                fname = os.path.basename(fpath)
                size_kb = os.path.getsize(fpath) / 1024
                label = f"{ftype}/{bucket}"

                test_count += 1
                print(f"\n  [{label}] ({i + 1}/{len(test_files)}) {fname} ({size_kb:.0f}KB)...")

                if test_count > 1:
                    print(f"    메모리 안정화 대기 ({COOLDOWN_SEC}s)...")
                    time.sleep(COOLDOWN_SEC)

                result = profile_single(fpath)

                if result["status"] == "ok":
                    print(f"    피크 RSS: {result['peak_rss_mb']:.0f}MB, "
                          f"시간: {result['time']:.1f}s, "
                          f"추출: {result['chars']}자")

                    graph_path = os.path.join(
                        RESULTS_DIR, f"profile_{ftype}_{bucket}_{i + 1}.png"
                    )
                    _generate_profile_graph(
                        f"OCR Profile: {ftype}/{bucket} ({fname}, {size_kb:.0f}KB)",
                        result["monitor"],
                        graph_path,
                    )
                    print(f"    그래프: {graph_path}")

                    log_benchmark(
                        workers=1, file_count=1, size_bucket=f"{ftype}/{bucket}",
                        peak_memory_mb=result["peak_rss_mb"],
                        baseline_memory_mb=0,
                        avg_time_sec=result["time"],
                        total_time_sec=result["time"],
                        system_memory_pct=result["peak_system_mb"] / (TOTAL_MEMORY_GB * 1024 / 100),
                    )
                else:
                    print(f"    {result['status']}: 피크 RSS {result.get('peak_rss_mb', 0):.0f}MB")

                all_results.append({
                    "ftype": ftype,
                    "bucket": bucket,
                    "fname": fname,
                    "size_kb": size_kb,
                    "peak_rss_mb": result.get("peak_rss_mb", 0),
                    "time": result.get("time", 0),
                    "status": result["status"],
                })

        # 요약 그래프
        summary_path = os.path.join(RESULTS_DIR, "summary_by_type_size.png")
        _generate_summary_chart(all_results, summary_path)
        print(f"\n  요약 그래프: {summary_path}")

    # ─── Phase 2: 워커 수 스케일링 ───────────────────
    print("\n" + "-" * 60)
    print("  Phase 2: 동시 워커 수별 메모리 측정")
    print("-" * 60)

    # 가장 파일이 많은 small 버킷 사용
    test_key = None
    for bucket in ("small", "tiny", "medium"):
        for ftype in ("image", "pdf"):
            if (ftype, bucket) in file_map:
                test_key = (ftype, bucket)
                break
        if test_key:
            break
    if not test_key:
        test_key = next(iter(file_map))

    test_files = file_map[test_key]
    print(f"  테스트 파일: {test_key[0]}/{test_key[1]} ({len(test_files)}개)")

    worker_results: dict[int, dict] = {}

    for n_workers in (1, 2, 3):
        print(f"\n  workers={n_workers}...")
        print(f"    메모리 안정화 대기 ({COOLDOWN_SEC}s)...")
        time.sleep(COOLDOWN_SEC)

        result = profile_workers(test_files, n_workers)
        worker_results[n_workers] = result

        print(f"    최대 단일 워커 RSS: {result['peak_rss_mb']:.0f}MB")
        print(f"    전체 워커 RSS 합:   {result['total_peak_rss_mb']:.0f}MB")
        print(f"    시스템 피크:         {result['peak_system_mb']:.0f}MB")

        graph_path = os.path.join(RESULTS_DIR, f"workers_{n_workers}.png")
        _generate_profile_graph(
            f"OCR Workers={n_workers} Memory Profile",
            result["monitor"],
            graph_path,
        )
        print(f"    그래프: {graph_path}")

        log_benchmark(
            workers=n_workers, file_count=n_workers,
            size_bucket=f"{test_key[0]}/{test_key[1]}",
            peak_memory_mb=result["peak_rss_mb"],
            baseline_memory_mb=0,
            avg_time_sec=0,
            total_time_sec=0,
            system_memory_pct=result["peak_system_mb"] / (TOTAL_MEMORY_GB * 1024 / 100),
        )

        # 시스템 메모리 85% 초과 시 중단
        sys_pct = result["peak_system_mb"] / (TOTAL_MEMORY_GB * 1024) * 100
        if sys_pct > 85:
            print(f"    시스템 메모리 {sys_pct:.0f}% > 85% — 스케일링 중단")
            break

    # 워커 스케일링 그래프
    if worker_results:
        workers_chart = os.path.join(RESULTS_DIR, "workers_scaling.png")
        _generate_workers_chart(worker_results, workers_chart)
        print(f"\n  워커 스케일링 그래프: {workers_chart}")

    # ─── 결과 요약 ───────────────────────────────────
    print("\n" + "=" * 60)
    print("  프로파일링 결과 요약")
    print("=" * 60)

    if all_results:
        print(f"\n  {'타입':>5}  {'사이즈':<7}  {'파일명':<40}  {'크기(KB)':>8}  {'RSS(MB)':>8}  {'시간(s)':>7}")
        print("  " + "-" * 80)

        for r in all_results:
            if r["status"] == "ok":
                print(f"  {r['ftype']:>5}  {r['bucket']:<7}  {r['fname']:<40}  "
                      f"{r['size_kb']:>8.0f}  {r['peak_rss_mb']:>8.0f}  {r['time']:>7.1f}")

        # 타입/사이즈별 메모리 차이 분석
        ok_results = [r for r in all_results if r["status"] == "ok" and r["peak_rss_mb"] > 0]
        if len(ok_results) >= 2:
            rss_values = [r["peak_rss_mb"] for r in ok_results]
            min_rss, max_rss = min(rss_values), max(rss_values)
            variance_pct = (max_rss - min_rss) / min_rss * 100 if min_rss > 0 else 0

            print(f"\n  RSS 범위: {min_rss:.0f}MB ~ {max_rss:.0f}MB (편차: {variance_pct:.1f}%)")

            # 타입별 평균 비교
            by_type: dict[str, list[float]] = defaultdict(list)
            for r in ok_results:
                by_type[r["ftype"]].append(r["peak_rss_mb"])

            for ftype, vals in by_type.items():
                avg = sum(vals) / len(vals)
                print(f"  {ftype} 평균 RSS: {avg:.0f}MB (n={len(vals)})")

            # 사이즈별 평균 비교
            by_size: dict[str, list[float]] = defaultdict(list)
            for r in ok_results:
                by_size[r["bucket"]].append(r["peak_rss_mb"])

            for bucket in BUCKET_ORDER:
                if bucket in by_size:
                    vals = by_size[bucket]
                    avg = sum(vals) / len(vals)
                    print(f"  {bucket} 평균 RSS: {avg:.0f}MB (n={len(vals)})")

            if variance_pct < 20:
                print("\n  결론: 파일 타입/사이즈에 따른 메모리 차이 미미 (< 20%)")
                print("  → Adaptive worker 불필요, 고정 worker 수 권장")
            else:
                print("\n  결론: 파일 타입/사이즈에 따른 메모리 차이 유의미")
                print("  → Adaptive worker 스케줄링 고려 가치 있음")

    if worker_results:
        print(f"\n  워커 스케일링:")
        for n, r in sorted(worker_results.items()):
            print(f"    workers={n}: 단일 피크 {r['peak_rss_mb']:.0f}MB, "
                  f"합계 {r['total_peak_rss_mb']:.0f}MB, "
                  f"시스템 {r['peak_system_mb']:.0f}MB")

    print(f"\n  그래프 디렉토리: {RESULTS_DIR}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OCR 메모리 프로파일링")
    sub = parser.add_subparsers(dest="command")

    setup_p = sub.add_parser("setup", help="벤치마크 파일 준비")
    setup_p.add_argument("--max-files", type=int, default=3,
                         help="타입/사이즈별 최대 파일 수 (기본: 3)")

    run_p = sub.add_parser("run", help="프로파일링 실행")
    run_p.add_argument("--quick", action="store_true",
                       help="빠른 모드 (구간별 1개 파일만)")
    run_p.add_argument("--workers-only", action="store_true",
                       help="워커 스케일링만 테스트")

    args = parser.parse_args()

    if args.command == "setup":
        setup_benchmark_files(max_files=args.max_files)
    elif args.command == "run":
        run_profiling(quick=args.quick, workers_only=args.workers_only)
    else:
        parser.print_help()
