import json
import os
from paddleocr import PaddleOCRVL

cache = r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache"
os.environ["PADDLE_PDX_CACHE_HOME"] = os.path.join(cache, "paddlex-cache")
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["PYTHONPYCACHEPREFIX"] = os.path.join(cache, "pycache")

api_key = os.environ["SFKEY"]
pipeline = PaddleOCRVL(
    vl_rec_backend="vllm-server",
    vl_rec_server_url="https://api.siliconflow.cn/v1",
    vl_rec_api_model_name="PaddlePaddle/PaddleOCR-VL-1.5",
    vl_rec_api_key=api_key,
    use_layout_detection=False,
)
result = pipeline.predict(os.path.join(cache, "2-75-linegroups", "02-07.png"))[0]
output_path = os.path.join(cache, "paddle-test-02-07.json")
with open(output_path, "w", encoding="utf-8") as output_file:
    json.dump(result.json, output_file, ensure_ascii=False, indent=2)
print(output_path)
