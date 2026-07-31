FROM python:3.11-slim

WORKDIR /app

# 系统依赖（pypdf 等需要的底层库）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖（清华源加速）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# HuggingFace 镜像（国内服务器必须，否则模型下载失败）
ENV HF_ENDPOINT=https://hf-mirror.com

# 复制代码
COPY rag_api_server.py .

# 创建目录
RUN mkdir -p /app/knowledge_private /app/knowledge_public /app/knowledge_novel /app/knowledge_data /app/vector_data

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')" || exit 1

CMD ["python", "rag_api_server.py", "--port", "8080", "--host", "0.0.0.0"]
