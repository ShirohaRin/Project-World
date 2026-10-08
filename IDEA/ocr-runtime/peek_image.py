"""用视觉模型识别一张本地图片的内容。用法: python peek_image.py <图片路径> [额外提示]"""
import base64
import os
import sys
from pathlib import Path

import requests

path = Path(sys.argv[1])
prompt = sys.argv[2] if len(sys.argv) > 2 else "这张图片里有什么？如果是文字页面，请说明并转录开头内容。"
data = base64.b64encode(path.read_bytes()).decode("ascii")
api_url = "https://api.siliconflow.cn/v1/chat/completions"
headers = {"Authorization": f"Bearer {os.environ['SILICONFLOW_API_KEY']}", "Content-Type": "application/json"}
payload = {
    "model": "Qwen/Qwen3.6-35B-A3B",
    "temperature": 0,
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
        {"type": "text", "text": prompt},
    ]}],
}
resp = requests.post(api_url, headers=headers, json=payload, timeout=(10, 90))
resp.raise_for_status()
print(resp.json()["choices"][0]["message"]["content"])
