"""桌面端 JSON 入口（`wrapper.py`）的用例：走**真正的子进程**跑一遍。

这是入口层唯一能验的东西——契约：stdin 收一个 JSON、stdout 吐一行 JSON、成功 0 失败 1。
上游那条 fastp 工作流的 `wrapper.py` 至今只有人工验收（见 `workflow.md` 的待办），
这里从一开始就把它钉住。
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path

_WRAPPER = Path(__file__).resolve().parents[1] / "wrapper.py"
#: 跑子进程时的工作目录：仓库根（`Project-IDEA/`）。
_ROOT = Path(__file__).resolve().parents[4]


def _run(payload: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-u", str(_WRAPPER)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(_ROOT),
        check=False,
    )


def _request(tmp_path: Path, **overrides: object) -> dict:
    generator = random.Random(11)
    sequence = "".join(generator.choice("ACGT") for _ in range(120))
    reference = tmp_path / "ref.fasta"
    reference.write_text(f">chr\n{sequence}\n", encoding="utf-8")
    reads = tmp_path / "reads.fq"
    reads.write_text(
        "".join(
            f"@r{start}\n{sequence[start:start + 30]}\n+\n{'I' * 30}\n" for start in (0, 30, 60)
        ),
        encoding="utf-8",
    )
    payload = {
        "referencePath": str(reference),
        "read1Path": str(reads),
        "outputDir": str(tmp_path / "out"),
        "htmlPath": str(tmp_path / "report.html"),
        "logDir": str(tmp_path / "logs"),
        "polymorphismMode": "off",  # 字符串表单值也要能收
        "trimReadEnds": "true",
        "maxReads": "0",
        "eValueCutoff": "10",
        "frequencyCutoff": "0.8",
    }
    payload.update(overrides)
    return payload


def test_wrapper_runs_the_workflow_and_reports_success(tmp_path: Path) -> None:
    completed = _run(_request(tmp_path))
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    assert result["outcome"]["workflow"] == "breseq"
    assert "scan" in result["outcome"] and "variants" in result["outcome"]
    assert len(result["outputs"]) == 4
    for path in (*result["outputs"], result["html"], result["log"]):
        assert Path(path).exists(), path
    assert result["log"].endswith("breseq_workflow.json")


def test_wrapper_translates_failures_into_json(tmp_path: Path) -> None:
    completed = _run(_request(tmp_path, read1Path=""))
    assert completed.returncode == 1
    result = json.loads(completed.stdout)
    assert result["ok"] is False
    assert result["error"]["type"] == "ValueError"
    assert "read1Path" in result["error"]["message"]


def test_wrapper_accepts_several_reference_files(tmp_path: Path) -> None:
    payload = _request(tmp_path)
    plasmid = tmp_path / "plasmid.fasta"
    plasmid.write_text(">plasmid\n" + "ACGT" * 15 + "\n", encoding="utf-8")
    completed = _run(_request(tmp_path, referencePath=f"{payload['referencePath']}；{plasmid}"))
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    # 染色体 + 质粒：两条参考序列，总长是两者相加
    assert result["outcome"]["scan"]["seq_ids"] == ["chr", "plasmid"]
    assert result["outcome"]["scan"]["reference_length"] == 120 + 60


def test_wrapper_rejects_a_bad_number(tmp_path: Path) -> None:
    completed = _run(_request(tmp_path, eValueCutoff="不是数字"))
    assert completed.returncode == 1
    result = json.loads(completed.stdout)
    assert result["ok"] is False
    assert "eValueCutoff" in result["error"]["message"]
