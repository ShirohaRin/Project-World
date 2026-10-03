"""性能基准：Python 实现 vs 原生（C++）实现，并对比单线程与多线程。

用法（在 Project-IDEA 目录下）：

    py -3.11 modules/bio_analysis_function/common/native/tools/benchmark.py
    py -3.11 modules/bio_analysis_function/common/native/tools/benchmark.py --reads 200000
    py -3.11 modules/bio_analysis_function/common/native/tools/benchmark.py --threads 4

生成一份合成的测序数据（序列随机、质量随位置下降，模拟真实分布），
分别用 Python 实现与原生实现跑同一组参数，报告耗时与加速比。测两种输入：
**未压缩** 与 **gzip**——真实数据几乎总是 gzip，而解压与压缩本身也要花时间，
两种场景的瓶颈并不相同。

原生实现跑两遍：**单线程**（衡量纯 C++ 相对 Python 的收益）与
**多线程**（衡量流水线把读取、计算、写出重叠起来的收益）。
两份输出的字节必须与 Python 一致，表格最后一列会核对这一点。

**为什么要合成数据**：模块里固定的真实数据只有 9 条 read，
不足以体现大规模下的差异；而基准要看的正是数据量上去之后的表现。
"""

from __future__ import annotations

import argparse
import gzip
import random
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from modules.bio_analysis_function.common.native import abi  # noqa: E402
from modules.bio_analysis_function.submodules.quality_trimming import (  # noqa: E402
    QualityCutConfig,
    trim_fastq,
)

_BASES = "ACGT"


def generate_fastq(path: Path, count: int, length: int, seed: int) -> None:
    """写出一份合成 FASTQ：序列随机，质量随位置单调下降。"""
    rng = random.Random(seed)
    # 质量只与位置有关，因此整份文件共用同一个质量串，不必逐条构造。
    quality = "".join(
        chr(40 - int(30 * (position / length) ** 2) + 33) for position in range(length)
    )
    # 序列一次性批量生成再切片，避免逐条 join 的开销。
    block = "".join(rng.choices(_BASES, k=length * count))
    with path.open("w", encoding="ascii", newline="\n") as handle:
        for index in range(count):
            start = index * length
            handle.write("@read")
            handle.write(str(index))
            handle.write("\n")
            handle.write(block[start : start + length])
            handle.write("\n+\n")
            handle.write(quality)
            handle.write("\n")


def gzip_copy(source: Path, destination: Path) -> None:
    """把文件压成 gzip（单成员，与常见工具的输出形态一致）。"""
    with source.open("rb") as raw, gzip.open(destination, "wb") as packed:
        while chunk := raw.read(1 << 20):
            packed.write(chunk)


def format_seconds(value: float) -> str:
    return f"{value:.2f}s" if value >= 1 else f"{value * 1000:.0f}ms"


def read_output(path: Path) -> bytes:
    """读回产物内容：gzip 就解压，纯文本直接读。"""
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    return data


def time_call(function, *args, **kwargs) -> tuple[float, object]:
    started = time.perf_counter()
    result = function(*args, **kwargs)
    return time.perf_counter() - started, result


def main() -> int:
    parser = argparse.ArgumentParser(description="Python 实现与原生实现的性能对比")
    parser.add_argument("--reads", type=int, default=1_000_000, help="合成 read 条数，默认 100 万")
    parser.add_argument("--length", type=int, default=150, help="每条 read 的碱基数，默认 150")
    parser.add_argument("--quality", type=int, default=30, help="cut_tail 的质量阈值，默认 30")
    parser.add_argument(
        "--threads",
        type=int,
        default=8,
        help="原生多线程那一行用的线程数，默认 8（原生默认是单线程，见 --help 里的说明）",
    )
    parser.add_argument("--keep", action="store_true", help="保留生成的输入文件")
    args = parser.parse_args()

    native_kwargs = {
        "enabled_tail": True,
        "window_size_tail": 4,
        "quality_tail": args.quality,
    }
    python_config = QualityCutConfig(
        enabled_tail=True, window_size_tail=4, quality_tail=args.quality
    )

    with tempfile.TemporaryDirectory() as workspace:
        workdir = Path(workspace)
        plain = workdir / "synthetic.fq"

        print(f"生成合成数据：{args.reads} 条 × {args.length} bp ……", flush=True)
        elapsed, _ = time_call(generate_fastq, plain, args.reads, args.length, 20260917)
        plain_mb = plain.stat().st_size / 1024 / 1024
        print(f"  完成：{plain_mb:.1f} MB，耗时 {format_seconds(elapsed)}")

        print("\n压缩成 gzip ……", flush=True)
        packed = workdir / "synthetic.fq.gz"
        elapsed, _ = time_call(gzip_copy, plain, packed)
        packed_mb = packed.stat().st_size / 1024 / 1024
        print(f"  完成：{packed_mb:.1f} MB，耗时 {format_seconds(elapsed)}")

        # 每个输入场景跑三遍：Python、原生单线程、原生多线程。
        inputs = [("未压缩", plain, plain_mb), ("gzip", packed, packed_mb)]
        rows: list[tuple[str, str, float, float, object, bytes]] = []

        for scenario, source, size_mb in inputs:
            python_elapsed, python_stats = time_call(
                trim_fastq, source, workdir / f"py_{scenario}.fq", config=python_config
            )
            python_output = read_output(workdir / f"py_{scenario}.fq")
            rows.append((scenario, "Python", size_mb, python_elapsed, python_stats, python_output))

            for label, threads in (("原生 1 线程", 1), ("原生多线程", args.threads)):
                native_elapsed, native_stats = time_call(
                    abi.quality_trim_fastq,
                    source,
                    workdir / f"native_{scenario}_{threads}.fq",
                    threads=threads,
                    **native_kwargs,
                )
                output = read_output(workdir / f"native_{scenario}_{threads}.fq")
                rows.append((scenario, label, size_mb, native_elapsed, native_stats, output))
                print(
                    f"  {scenario} / {label}：{format_seconds(native_elapsed)}", flush=True
                )

        baselines = {row[0]: row[3] for row in rows if row[1] == "Python"}

        print(f"\n{'场景':<8}{'实现':<12}{'耗时':>10}{'相对 Python':>13}{'一致':>8}")
        for scenario, label, size_mb, elapsed, stats, output in rows:
            baseline = baselines[scenario]
            speedup = baseline / elapsed if elapsed > 0 else 0.0
            same = output == next(
                row[5] for row in rows if row[0] == scenario and row[1] == "Python"
            ) and stats == next(
                row[4] for row in rows if row[0] == scenario and row[1] == "Python"
            )
            print(
                f"{scenario:<8}{label:<12}{format_seconds(elapsed):>10}"
                f"{speedup:>12.1f}×{'是' if same else '否':>8}"
            )

        python_plain = next(row for row in rows if row[0] == "未压缩" and row[1] == "Python")
        native_plain = next(
            row for row in rows if row[0] == "未压缩" and row[1] == "原生多线程"
        )
        print(f"\n单线程 → 多线程：{format_seconds(next(row for row in rows if row[0] == '未压缩' and row[1] == '原生 1 线程')[3])} → "
              f"{format_seconds(native_plain[3])}（Python 为 {format_seconds(python_plain[3])}）")
        print(f"保留 reads：{native_plain[4].kept_reads}，去掉碱基：{native_plain[4].bases_removed}")

        if args.keep:
            kept = Path.cwd() / "benchmark_input.fq"
            kept.write_bytes(plain.read_bytes())
            print(f"输入已保留：{kept}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

