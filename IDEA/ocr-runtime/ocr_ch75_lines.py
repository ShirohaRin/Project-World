import base64
import json
import os
import sys
from pathlib import Path

from openai import OpenAI

root = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊")
cache = root / "ocr-cache"
metadata_path = cache / "2-75-visual-lines.json"
line_dir = cache / "2-75-hanzi-lines"
output_path = cache / "2-75-line-ocr.json"
limit = int(sys.argv[1]) if len(sys.argv) > 1 else None

api_key = os.environ["SILICONFLOW_API_KEY"]
client = OpenAI(api_key=api_key, base_url="https://api.siliconflow.cn/v1")
metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
existing = {}
if output_path.exists():
    existing = {item["line"]: item for item in json.loads(output_path.read_text(encoding="utf-8"))}

prompt = "识别图片中的这一行中文，只输出原文，不要解释。"
records = metadata["records"][:limit] if limit else metadata["records"]
for record in records:
    line = record["line"]
    if line in existing and existing[line].get("text"):
        continue
    image_bytes = (line_dir / record["hanzi_image"]).read_bytes()
    image_data = base64.b64encode(image_bytes).decode("ascii")
    text = ""
    for attempt in range(2):
        response = client.chat.completions.create(
            model="Qwen/Qwen3.6-35B-A3B",
            temperature=0,
            max_tokens=96,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_data}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        text = (response.choices[0].message.content or "").strip().replace("\n", "")
        if text:
            break
    existing[line] = {
        "line": line,
        "text": text,
        "char_count": len(text),
        "indent_chars": record["indent_chars"],
        "first_x": record["first_x"],
        "image": record["hanzi_image"],
    }
    output_path.write_text(json.dumps([existing[key] for key in sorted(existing)], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"line={line} chars={len(text)} text={text}")
