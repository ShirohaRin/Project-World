"""在真实测序数据上验证 polyG / polyX。

数据来源同 quality_trimming：fastp 仓库自带的 ``testdata/R1.fq``（MIT 许可），
已固定为模块共享测试数据 ``tests/data/fastp_R1.fq``——真实 Illumina 单端数据，
9 条记录（1 条长度为 0），其余 8 条长度均为 151bp。

这两条用例的价值在于验证**不误伤**与**真识别**两侧：

- 这是一批 DNA 重测序数据，理论上不该出现 polyG 假象（那是双色合成仪器的产物），
  因此 polyG 在 8 条 read 上都应当判定为"没有尾巴"；
- 其中一条确实带有一支真实的 polyA 尾巴，polyX 应当把它识别出来。
"""

from pathlib import Path

import pytest

from modules.bio_analysis_function.common.fastq import read_fastq
from modules.bio_analysis_function.submodules.poly_trimming import (
    trim_poly_g,
    trim_poly_x,
)

DATA_DIR = Path(__file__).parents[3] / "tests" / "data"
_READS = DATA_DIR / "fastp_R1.fq"


@pytest.fixture(scope="module")
def non_empty():
    return [record for record in read_fastq(_READS) if record.length > 0]


def test_poly_g_does_not_trigger_on_real_dna_reads(non_empty) -> None:
    """DNA 数据上不该出现 polyG 尾巴，因此一条都不应被裁剪。"""
    assert len(non_empty) == 8

    trimmed = [trim_poly_g(record.sequence, min_length=10).trimmed_bases for record in non_empty]

    assert trimmed == [0] * 8


def test_poly_x_finds_the_one_real_poly_a_tail(non_empty) -> None:
    """8 条 read 中只有最后一条带真实 polyA 尾巴，应当被识别并裁剪。

    该 read 的末尾是一长串连续的 A（原始末尾 50 个碱基为
    ``GTACATCACAAGT`` 再接 37 个 A）。polyX 判定为 polyA，
    从扫描范围内第一个 A 的位置开始裁剪，共去掉 46 个碱基，保留 105 个。

    其余 7 条是普通基因组序列，没有同种碱基的长尾巴，不应被触碰——
    这一点比"识别出尾巴"更重要：**噪声数据上的假阳性才是这类修剪算法的风险所在**。
    """
    trimmed = [trim_poly_x(record.sequence, min_length=10).trimmed_bases for record in non_empty]

    assert trimmed == [0, 0, 0, 0, 0, 0, 0, 46]

    last = non_empty[-1]
    result = trim_poly_x(last.sequence, min_length=10)
    assert result.poly_base == b"A"
    assert len(result.sequence) == 105
    # 裁剪后序列不应再以长串 A 结尾
    assert not result.sequence.endswith(b"A" * 10)
