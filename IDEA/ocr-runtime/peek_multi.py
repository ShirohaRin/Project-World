"""并排识别多个裁剪图的内容。用法: python peek_multi.py <图1> <图2> ..."""
import base64
import os
import sys
from pathlib import Path

import requests

paths = [Path(p) for p in sys.argv[1:]]
data = []
for p in paths:
    data.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"}})
data.append({"type": "text", "text": "这些是中文小说图片的逐行切片，每张一行，文字上方有拼音。请按顺序逐张转录汉字和标点（忽略拼音），每张一行输出，格式：图片N: 内容"})
api_url = "https://api.siliconflow.cn/v1/chat/completions"
headers = {"Authorization": f"Bearer {os.environ['SILICONFLOW_API_KEY']}", "Content-Type": "application/json"}
payload = {
    "model": "Qwen/Qwen3.6-35B-A3B",
    "temperature": 0,
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": data}],
}
resp = requests.post(api_url, headers=headers, json=payload, timeout=(10, 90))
resp.raise_for_status()
print(resp.json()["choices"][0]["message"]["content"])
