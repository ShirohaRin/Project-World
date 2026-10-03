"""breseq 工作流的桌面端 JSON 入口（薄壳）：stdin 一个 JSON 对象进，stdout 一个 JSON 对象出。

算法与编排都不在这里——这里只做「JSON ↔ `BreseqConfig`」的翻译，与
`modules/workflow/wrapper.py`（fastp 那条）同一形状：

```bash
python -u modules/workflow/breseq/wrapper.py < request.json
```

成功时 stdout 是**一行** JSON：

```json
{"ok": true, "outcome": {...}, "outputs": [...], "html": "…/report.html", "log": "…/breseq_workflow.json"}
```

失败时是 ``{"ok": false, "error": {"type": …, "message": …}}``，退出码 1——
**不让调用方去解析 stderr 文本**。

三条入口约定：

- **表单送来的数字是字符串**：数值字段按字符串解析，空串取默认值；
- **参考可以给多个文件**（染色体 + 质粒），用中文分号、英文分号或逗号分隔；
- **多态档默认开着**（`polymorphismMode: "off"` 才只跑共识档）——与 Python 侧
  `BreseqConfig` 的默认值一致；上游 breseq 的默认是只跑共识档，这一处按本项目的取舍走。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    # 直接跑脚本时（Electron 就是按脚本调用的），把 modules/ 的上级目录放进 sys.path：
    # 这样 modules.workflow.breseq 与它依赖的 modules.bio_analysis_function.* 都能按包导入，
    # 开发态与打包态是同一套相对结构。
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from modules.bio_analysis_function.submodules.consensus_calling import (  # type: ignore[no-redef]
        ConsensusSettings,
        PolymorphismSettings,
    )
    from modules.workflow.breseq import (  # type: ignore[no-redef]
        BreseqConfig,
        run_breseq_workflow,
    )
    from modules.workflow.breseq.report import (  # type: ignore[no-redef]
        build_breseq_json,
    )
else:
    from ...bio_analysis_function.submodules.consensus_calling import (
        ConsensusSettings,
        PolymorphismSettings,
    )
    from . import BreseqConfig, run_breseq_workflow
    from .report import build_breseq_json

#: 参考路径的分隔符（表单里一个文本框装多个文件）。
_REFERENCE_SEPARATORS = ("；", ";", ",")


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "on", "yes"}:
            return True
        if normalized in {"false", "0", "off", "no", ""}:
            return False
    raise ValueError(f"布尔字段值无效：{value!r}")


def _required_text(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    return value


def _optional_path(payload: dict[str, Any], name: str) -> Path | None:
    value = payload.get(name)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} 必须是字符串或空值")
    return Path(value)


def _number(payload: dict[str, Any], name: str, default: float) -> float:
    """取浮点数。表单控件送来的都是字符串，数值字符串与空串都要能收。"""
    value = payload.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须是数字")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须是数字，当前为 {value!r}") from None


def _integer(payload: dict[str, Any], name: str, default: int) -> int:
    number = _number(payload, name, float(default))
    if number != int(number):
        raise ValueError(f"{name} 必须是整数，当前为 {number!r}")
    return int(number)


def _reference_paths(payload: dict[str, Any]) -> tuple[Path, ...]:
    raw = _required_text(payload, "referencePath")
    parts = [raw]
    for separator in _REFERENCE_SEPARATORS:
        parts = [piece for chunk in parts for piece in chunk.split(separator)]
    paths = tuple(Path(piece.strip()) for piece in parts if piece.strip())
    if not paths:
        raise ValueError("referencePath 必须至少给一个参考文件")
    return paths


def _config(payload: dict[str, Any]) -> tuple[BreseqConfig, Path | None, Path | None]:
    output_dir = _optional_path(payload, "outputDir") or Path("breseq_out")
    max_reads = _integer(payload, "maxReads", 0)
    config = BreseqConfig(
        reference=_reference_paths(payload),
        read1=Path(_required_text(payload, "read1Path")),
        read2=_optional_path(payload, "read2Path"),
        output_dir=output_dir,
        consensus=ConsensusSettings(
            e_value_cutoff=_number(payload, "eValueCutoff", 10.0),
            frequency_cutoff=_number(payload, "frequencyCutoff", 0.8),
        ),
        polymorphism=(
            PolymorphismSettings() if _bool(payload.get("polymorphismMode"), True) else None
        ),
        trim_read_ends=_bool(payload.get("trimReadEnds"), True),
        max_reads=None if max_reads <= 0 else max_reads,
    )
    return (
        config,
        _optional_path(payload, "htmlPath"),
        _optional_path(payload, "logDir"),
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("输入 JSON 顶层必须是对象")
        config, html_path, log_dir = _config(payload)
        outcome = run_breseq_workflow(config, html_path=html_path, log_dir=log_dir)
        result = {
            "ok": True,
            "outcome": build_breseq_json(outcome),
            "outputs": [str(path) for path in outcome.outputs],
            "html": None if outcome.html_path is None else str(outcome.html_path),
            "log": None if outcome.log_path is None else str(outcome.log_path),
        }
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except Exception as error:  # noqa: BLE001 - 入口要把所有失败都翻译成 JSON
        print(
            json.dumps(
                {"ok": False, "error": {"type": type(error).__name__, "message": str(error)}},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
