FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY main.py .
COPY config.yaml .
COPY agents/ ./agents/
COPY memory/ ./memory/

# 创建运行时目录
RUN mkdir -p logs memory

EXPOSE 8900

CMD ["python", "main.py"]
