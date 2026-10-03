"""编译生物方法模块的原生库。

用法（在 Project-IDEA 目录下执行）：

    python modules/bio_analysis_function/common/native/tools/compile.py
    python modules/bio_analysis_function/common/native/tools/compile.py --debug

产物：

    common/native/lib/bio_native.dll     （Windows）
    common/native/lib/libbio_native.so   （Linux）

产物需要随客户端一起分发；Python 侧通过 ctypes 加载它，见 common/native/abi.py。

**关于脚本名**：它不叫 ``build.py``，因为仓库根 ``.gitignore`` 有 ``build*`` 规则
（本意是拦前端构建产物），那条规则不仅拦 ``build/`` 目录，也拦 ``build.py``
这样的**文件名**——叫 build.py 会静默地进不了版本库。目录同理不叫 build。
本模块不改那条全局规则，而是让文件名避开它。

**为什么用 Python 写构建脚本**：项目本身是 Python 栈，且这样不受 Windows
PowerShell 执行策略限制（.ps1 在默认策略下会被拒绝执行）。

**为什么 C 与 C++ 分开编译**：zlib 是 C89 代码，其中若干写法只在 C 下合法
（例如 ``gzlib.c`` 里把 ``void*`` 隐式转成 ``wchar_t*``，C++ 会直接报错；
还有 C++17 已废弃的 ``register``）。如果交给 g++ 一把梭，这些文件会被当作
C++ 编译而失败。因此每个源文件先各自编译成目标文件，最后统一链接。

**关于 zlib**：只编译 gzFile 接口所需的那部分（见 ZLIB_SOURCES），
用不到的 compress.c / uncompr.c 等不参与，减少编译时间与产物体积。
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

NATIVE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = NATIVE_ROOT / "src"
INCLUDE_DIR = NATIVE_ROOT / "include"
OUTPUT_DIR = NATIVE_ROOT / "lib"
OBJECT_DIR = OUTPUT_DIR / "obj"
THIRD_PARTY_DIR = NATIVE_ROOT / "third_party"

ZLIB_DIR = THIRD_PARTY_DIR / "zlib-1.3.1"

CXX_STANDARD = "c++17"

# 本模块自己的 C++ 源文件
SOURCES = (
    "adapter_detect.cpp",
    "adapter_trim.cpp",
    "api.cpp",
    "dedup.cpp",
    "fastq.cpp",
    "index_filter.cpp",
    "insert_size.cpp",
    "normalize.cpp",
    "overlap.cpp",
    "overrep.cpp",
    "paired_adapter_trim.cpp",
    "paired_base_correction.cpp",
    "paired_merge.cpp",
    "poly_trim.cpp",
    "quality_trim.cpp",
    "read_filter.cpp",
    "read_stats.cpp",
    "two_color.cpp",
    "umi_process.cpp",
    "workflow.cpp",
)

# zlib 中 gzFile（gzopen / gzread / gzwrite / gzclose）所需的 C 源文件。
ZLIB_SOURCES = (
    "adler32.c",
    "crc32.c",
    "deflate.c",
    "gzclose.c",
    "gzlib.c",
    "gzread.c",
    "gzwrite.c",
    "inflate.c",
    "inffast.c",
    "inftrees.c",
    "trees.c",
    "zutil.c",
)


def find_tool(*candidates: str) -> str:
    """在 PATH 中找第一个可用的工具。"""
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    raise SystemExit(f"未找到所需工具（需要以下之一）：{' / '.join(candidates)}")


def library_name() -> str:
    return "bio_native.dll" if platform.system() == "Windows" else "libbio_native.so"


def run(command: list[str], *, verbose: bool) -> None:
    """执行一条命令，失败时把完整输出打出来。"""
    if verbose:
        print("  " + " ".join(str(item) for item in command))
    result = subprocess.run([str(item) for item in command], capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"命令失败（退出码 {result.returncode}）：{command[0]}")


def build(*, debug: bool, verbose: bool) -> Path:
    if not ZLIB_DIR.is_dir():
        raise SystemExit(
            f"未找到 zlib 源码：{ZLIB_DIR}\n"
            "它是 gzip 支持的依赖，不应缺失。请检查 third_party 目录。"
        )

    cxx = find_tool("g++", "clang++")
    cc = find_tool("gcc", "clang")
    is_windows = platform.system() == "Windows"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OBJECT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / library_name()

    optimization = "-O0" if debug else "-O2"
    common_cxx = [cxx, f"-std={CXX_STANDARD}", optimization, "-fPIC"]
    common_c = [cc, "-O2", "-fPIC"]
    if debug:
        common_cxx.append("-g")
        common_c.append("-g")

    objects: list[Path] = []

    print(f"编译器：{cxx} / {cc}")
    print(f"编译 {len(SOURCES)} 个 C++ 源文件 ……")
    for name in SOURCES:
        obj = OBJECT_DIR / (Path(name).stem + ".o")
        run(
            common_cxx
            + ["-I", INCLUDE_DIR, "-I", ZLIB_DIR, "-c", SOURCE_DIR / name, "-o", obj],
            verbose=verbose,
        )
        objects.append(obj)

    print(f"编译 {len(ZLIB_SOURCES)} 个 zlib C 源文件 ……")
    for name in ZLIB_SOURCES:
        obj = OBJECT_DIR / ("zlib_" + Path(name).stem + ".o")
        run(
            common_c + ["-I", ZLIB_DIR, "-c", ZLIB_DIR / name, "-o", obj],
            verbose=verbose,
        )
        objects.append(obj)

    print("链接 ……")
    link = [cxx, "-shared", "-o", output_path]
    if is_windows:
        # 静态链接 C++ 运行时：否则产物会依赖 MinGW 的运行时 DLL，
        # 而目标机器上并没有安装 MinGW，加载会直接失败。
        link += ["-static-libgcc", "-static-libstdc++"]
    link += objects
    run(link, verbose=verbose)

    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="编译生物算法模块的原生库")
    parser.add_argument("--debug", action="store_true", help="关闭优化并生成调试信息")
    parser.add_argument("--quiet", action="store_true", help="不打印每条编译命令")
    args = parser.parse_args()

    output = build(debug=args.debug, verbose=not args.quiet)
    size_kb = output.stat().st_size / 1024
    print(f"已生成：{output}（{size_kb:.1f} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
