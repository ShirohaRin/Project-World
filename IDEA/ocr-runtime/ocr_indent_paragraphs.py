"""通用：缩进段落并发 OCR（硅基流动 Qwen VL）。

用法: python ocr_indent_paragraphs.py <章号> <章节名> [limit]
- 并发 3，断点续跑（已有结果跳过），原子写入 json。
"""
import base64
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

root = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊")
cache = root / "ocr-cache"

chapter_no = sys.argv[1]
chapter_title = sys.argv[2]
manifest = json.loads((cache / f"2-{chapter_no}-indent-paragraphs.json").read_text(encoding="utf-8"))
image_dir = cache / f"2-{chapter_no}-indent-paragraphs"
output_path = cache / f"2-{chapter_no}-indent-paragraph-ocr.json"
limit = int(sys.argv[3]) if len(sys.argv) > 3 else None

api_url = "https://api.siliconflow.cn/v1/chat/completions"
headers = {"Authorization": f"Bearer {os.environ['SILICONFLOW_API_KEY']}", "Content-Type": "application/json"}
existing = {}
if output_path.exists():
    existing = {item["paragraph"]: item for item in json.loads(output_path.read_text(encoding="utf-8"))}

prompt = (
    "这是中文小说的一个完整自然段，文字上方有汉语拼音注音。"
    "请只转录下方的汉字、数字和标点，忽略拼音；保持原文，不解释、不补写。"
    "注意段落末尾若有换行的句号和引号，请按正常语序合并（如\"。”\"）。"
)
def transcribe(record):
    data = base64.b64encode((image_dir / record["image"]).read_bytes()).decode("ascii")
    payload = {
        "model": "Qwen/Qwen3.6-35B-A3B",
        "temperature": 0,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
            {"type": "text", "text": prompt},
        ]}],
    }
    text = ""
    error = None
    for attempt in range(2):
        try:
            response = requests.post(api_url, headers=headers, json=payload, timeout=(10, 45))
            response.raise_for_status()
            text = (response.json()["choices"][0]["message"].get("content") or "").strip()
            if text:
                break
        except Exception as exc:
            error = str(exc)
    result = dict(record)
    result.update({"text": text, "char_count": len(text), "error": error if not text else None})
    return result

selected = manifest[:limit] if limit else manifest
pending = [record for record in selected if not existing.get(record["paragraph"], {}).get("text")]
print(json.dumps({"total": len(selected), "pending": len(pending)}, ensure_ascii=False))
with ThreadPoolExecutor(max_workers=3) as executor:
    futures = {executor.submit(transcribe, record): record for record in pending}
    for index, future in enumerate(as_completed(futures), start=1):
        record = futures[future]
        try:
            result = future.result()
        except Exception as exc:
            result = dict(record)
            result.update({"text": "", "char_count": 0, "error": str(exc)})
        existing[result["paragraph"]] = result
        temporary = output_path.with_suffix(".tmp")
        temporary.write_text(json.dumps([existing[key] for key in sorted(existing)], ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(output_path)
        print(f"paragraph={result['paragraph']} chars={result['char_count']}")
