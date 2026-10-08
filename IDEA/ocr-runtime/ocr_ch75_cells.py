import base64
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

root = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊")
cache = root / "ocr-cache"
manifest_path = cache / "2-75-cell-groups-tight.json"
image_dir = cache / "2-75-cell-groups-tight"
output_path = cache / "2-75-cell-ocr-tight.json"
limit = int(sys.argv[1]) if len(sys.argv) > 1 else None

api_key = os.environ["SILICONFLOW_API_KEY"]
api_url = "https://api.siliconflow.cn/v1/chat/completions"
headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
records = json.loads(manifest_path.read_text(encoding="utf-8"))
existing = {}
if output_path.exists():
    try:
        existing = {(item["line"], item["group"]): item for item in json.loads(output_path.read_text(encoding="utf-8"))}
    except json.JSONDecodeError:
        output_path.unlink()

prompt = "识别图片中的中文原文，只输出文字和标点，不要解释。"
def transcribe(record):
    data = base64.b64encode((image_dir / record["image"]).read_bytes()).decode("ascii")
    text = ""
    payload = {
        "model": "Qwen/Qwen3-VL-8B-Instruct",
        "temperature": 0,
        "max_tokens": 32,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
            {"type": "text", "text": prompt},
        ]}],
    }
    for attempt in range(2):
        response = requests.post(api_url, headers=headers, json=payload, timeout=(10, 25))
        response.raise_for_status()
        text = (response.json()["choices"][0]["message"].get("content") or "").strip().replace("\n", "")
        if text:
            break
    result = dict(record)
    result.update({"text": text, "char_count": len(text), "valid_length": len(text) == record["expected_chars"]})
    return result

selected = records[:limit] if limit else records
pending = [record for record in selected if not existing.get((record["line"], record["group"]), {}).get("text")]
with ThreadPoolExecutor(max_workers=4) as executor:
    futures = {executor.submit(transcribe, record): record for record in pending}
    for index, future in enumerate(as_completed(futures), start=1):
        record = futures[future]
        try:
            result = future.result()
        except Exception as error:
            result = dict(record)
            result.update({"text": "", "char_count": 0, "valid_length": False, "error": str(error)})
        existing[(result["line"], result["group"])] = result
        if index % 10 == 0 or index == len(pending):
            temporary_path = output_path.with_suffix(".tmp")
            temporary_path.write_text(json.dumps([existing[key] for key in sorted(existing)], ensure_ascii=False, indent=2), encoding="utf-8")
            temporary_path.replace(output_path)
        print(f"line={result['line']} group={result['group']} expected={result['expected_chars']} actual={result['char_count']} text={result['text']}")
