"""稳定的 JSON CLI 入口，供 Electron 调用。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    # 直接跑脚本时（Electron 就是按脚本调用的），把 modules/ 的上级目录放进 sys.path：
    # 这样本包（modules.workflow）与它依赖的 modules.bio_analysis_function.common.*
    # 都能按包导入，开发态与打包态是同一套相对结构。
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from modules.workflow import (  # type: ignore[no-redef]
        CutSpec,
        WorkflowConfig,
        run_workflow,
    )
else:
    from . import CutSpec, WorkflowConfig, run_workflow


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


def _int(payload: dict[str, Any], name: str, default: int = 0) -> int:
    """取整数。表单控件送来的都是字符串，所以数值字符串与空串都要能收。"""
    value = payload.get(name, default)
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须是整数")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError as error:
            raise ValueError(f"{name} 必须是整数，当前为 {value!r}") from error
    raise ValueError(f"{name} 必须是整数")


def _cut_spec(mode: Any) -> CutSpec:
    """把广场上的「剪切模式」文本换成本模块的剪切断点。

    口径与生物模块的 ``submodules/quality_trimming`` 一致：``3' 端剪切`` 对应
    ``cut_tail``（CutSpec 里是 ``tail``），不是 ``cut_right``。
    """
    if mode is None or mode in {"", "不做质量剪切"}:
        return CutSpec()
    if mode == "3' 端剪切":
        return CutSpec(tail=True)
    if mode == "5' 端与 3' 端剪切":
        return CutSpec(front=True, tail=True)
    raise ValueError(f"qualityCutMode 值无效：{mode!r}")


def _compress(value: Any) -> bool | None:
    if value is None or value in {"", "follow"}:
        return None
    if value == "gzip":
        return True
    if value == "plain":
        return False
    raise ValueError(f"compress 值无效：{value!r}")


def _config(payload: dict[str, Any]) -> tuple[WorkflowConfig, Path | None, Path | None]:
    read2 = _optional_path(payload, "read2Path")
    output2 = _optional_path(payload, "output2Path")
    if (read2 is None) != (output2 is None):
        raise ValueError("read2Path 与 output2Path 必须同时提供或同时留空")

    trim_poly_g = payload.get("trimPolyG", "auto")
    if trim_poly_g not in {"auto", "on", "off"}:
        raise ValueError(f"trimPolyG 值无效：{trim_poly_g!r}")
    dedup = payload.get("dedup", "none")
    if dedup not in {"none", "evaluate", "filter"}:
        raise ValueError(f"dedup 值无效：{dedup!r}")
    merge = _bool(payload.get("merge"), False)
    split_records = _int(payload, "splitRecords")
    if merge and split_records > 0:
        raise ValueError(
            "分卷模式下只写主输出，无法同时产出合并结果；请去掉分卷或合并其中的一项。"
        )
    output1 = Path(_required_text(payload, "output1Path"))
    merged_out = output1.with_name(f"{output1.stem}.merged{output1.suffix}") if merge else None

    html_path = payload.get("htmlPath")
    log_dir = payload.get("logDir")
    html = Path(html_path) if isinstance(html_path, str) and html_path else None
    logs = Path(log_dir) if isinstance(log_dir, str) and log_dir else None
    return (
        WorkflowConfig(
            read1=Path(_required_text(payload, "read1Path")),
            output1=output1,
            read2=read2,
            output2=output2,
            cut=_cut_spec(payload.get("qualityCutMode")),
            trim_poly_g=None if trim_poly_g == "auto" else trim_poly_g == "on",
            adapter_trimming=_bool(payload.get("adapterTrimming"), True),
            detect_adapter=(
                _bool(payload.get("detectAdapterForPE"), False) if read2 is not None else None
            ),
            correction=_bool(payload.get("correction")),
            dedup=dedup == "filter",
            dedup_evaluate=dedup in {"evaluate", "filter"},
            merge=merge,
            merged_out=merged_out,
            split_records=split_records,
            threads=_int(payload, "threads"),
            compress=_compress(payload.get("compress")),
        ),
        html,
        logs,
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return {key: _jsonable(getattr(value, key)) for key in value.__dataclass_fields__}
    return value


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("输入 JSON 顶层必须是对象")
        config, html_path, log_dir = _config(payload)
        outcome = run_workflow(config, html_path=html_path, log_dir=log_dir)
        result = {
            "ok": True,
            "outcome": _jsonable(outcome),
            "outputs": [str(path) for path in outcome.outputs],
            "html": None if outcome.html_path is None else str(outcome.html_path),
            "log": None if outcome.log_path is None else str(outcome.log_path),
        }
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except Exception as error:
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
