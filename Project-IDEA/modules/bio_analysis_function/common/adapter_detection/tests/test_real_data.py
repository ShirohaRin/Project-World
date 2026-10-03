"""在真实测序数据上验证接头检测。

数据来源同其它预处理子模块：fastp 仓库自带的 ``testdata/R1.fq``（MIT 许可），
已固定为模块共享测试数据 ``tests/data/fastp_R1.fq``——真实 Illumina 单端数据，
9 条记录（1 条长度为 0），其余 8 条长度均为 151bp。

这份数据只有 9 条 read，远低于检测所需的样本量（1 万条），因此它验证的是
**门槛与稳健性**，而不是"能不能检出接头"：

- 样本不足时必须明确说"样本不足"，而不是硬报一个接头；
- 把这批数据里的零长度 read 喂进扫描逻辑也不能崩。
"""

from __future__ import annotations

from pathlib import Path

from modules.bio_analysis_function.common.adapter_detection.algorithm import (
    AdapterDetectionConfig,
)
from modules.bio_analysis_function.common.adapter_detection.runner import (
    detect_adapter_from_file,
    sample_sequences,
)
from modules.bio_analysis_function.common.adapter_detection.tests.test_algorithm import (
    SMALL_TABLE,
)

DATA_DIR = Path(__file__).parents[3] / "tests" / "data"
_READS = DATA_DIR / "fastp_R1.fq"


def test_real_data_is_too_small_for_detection() -> None:
    """9 条 read 不够，必须如实说明，而不是硬报一个接头。"""
    result = detect_adapter_from_file(_READS)

    assert not result.detected
    assert "样本不足" in result.reason
    assert result.sampled_reads == 9


def test_real_data_yields_no_adapter_even_when_threshold_is_lowered() -> None:
    """把门槛降到 9 条、表缩到几条也不会凭空报出接头。

    这批数据是 DNA 重测序数据，理论上不该有接头残留；这里顺带覆盖了
    "真实文件 → 采样 → 两段判定全跑一遍"的完整链路（样本很小，因此很快）。
    """
    result = detect_adapter_from_file(
        _READS, config=AdapterDetectionConfig(min_reads=9, adapters=SMALL_TABLE)
    )

    assert not result.detected


def test_sampling_keeps_every_record_including_the_empty_one() -> None:
    sequences = sample_sequences(_READS, AdapterDetectionConfig(min_reads=1))

    assert len(sequences) == 9
    assert [len(sequence) for sequence in sequences] == [0, 151, 151, 151, 151, 151, 151, 151, 151]
