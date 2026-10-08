"""把整张图发给视觉模型，确认图片内容与排版。"""
import base64
import json
import os
from pathlib import Path

import requests

source_path = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache\2-76 归途（中）.png")
data = base64.b64encode(source_path.read_bytes()).decode("ascii")
api_url = "https://api.siliconflow.cn/v1/chat/completions"
headers = {"Authorization": f"Bearer {os.environ['SILICONFLOW_API_KEY']}", "Content-Type": "application/json"}
payload = {
    "model": "Qwen/Qwen3.6-35B-A3B",
    "temperature": 0,
    "max_tokens": 512,
    "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
        {"type": "text", "text": "这张图片里有什么？如果是文字页面，请说明排版方式（横排/竖排/分栏）并转录开头一小段。"},
    ]}],
}
resp = requests.post(api_url, headers=headers, json=payload, timeout=(10, 60))
resp.raise_for_status()
print(resp.json()["choices"][0]["message"]["content"])
