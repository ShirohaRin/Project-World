# 调试记录：server-health-startup

状态：[OPEN]

## 症状
执行 `/opt/idea-server/server/restart_from_runtime_env.sh` 后，8900 端口连续健康检查失败，脚本退出。

## 假设
1. `main.py` 启动后立即异常退出。
2. 环境文件加载失败或关键非敏感配置缺失。
3. 8900 端口被占用或 PID 文件状态异常。
4. Python 虚拟环境、工作目录或入口路径错误。

## 安全边界
只收集进程状态、端口状态、退出码、日志错误类别和脱敏配置键名；不收集 API Key、Token、请求正文或响应正文。

## 当前状态
等待云端运行时证据。
