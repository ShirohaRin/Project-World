"""生物方法模块的**原生层**：C++ 算法核心 + ctypes 封装。

它**不是算法**，而是本模块公共层的性能部分——和 ``common/fastq.py``、
``common/io.py`` 同级，只是用 C++ 写的。两者按职责分工：

- ``common/*.py``：与算法无关的公共能力（表格读写、FASTQ 流式管道等），纯 Python；
- ``common/native/``：逐碱基计算与大规模 I/O 的高速实现，供 ``submodules/`` 下的
  算法子模块调用。子模块保持 Python 规格（可读、可逐位对拍）+ 文档 + 广场契约，
  真正跑数据时走这一层。

测序数据基数极大，逐碱基扫描需要原生速度，因此核心用 C++ 实现；对外只暴露
**C ABI**（见 ``include/bio_native.h``），由本包用 ctypes 加载，不绑定 Python ABI，
也不依赖 pybind11。

**使用前需要先编译原生库**：

    python modules/bio_analysis_function/common/native/tools/compile.py

产物落在 ``common/native/lib/``，需要随客户端一起分发。
"""

from .abi import (
    NativeAdapterDetection,
    NativeFilterSummary,
    adapter_detect_fastq,
    analyze_overlap,
    library_path,
    native_version,
    poly_trim_fastq,
    quality_trim_fastq,
    read_filter_fastq,
    trim_adapter_fastq,
)

__all__ = [
    "NativeAdapterDetection",
    "NativeFilterSummary",
    "adapter_detect_fastq",
    "analyze_overlap",
    "library_path",
    "native_version",
    "poly_trim_fastq",
    "quality_trim_fastq",
    "read_filter_fastq",
    "trim_adapter_fastq",
]
