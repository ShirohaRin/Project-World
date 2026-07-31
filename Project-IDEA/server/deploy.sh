#!/bin/bash
# ==============================================
# IDEA — 智能体调度中心 部署脚本
# 使用方法: chmod +x deploy.sh && ./deploy.sh
# ==============================================

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

echo "========================================"
echo "  IDEA 智能体调度中心 — 部署脚本"
echo "========================================"

# [1/5] Python 检查
echo -e "${YELLOW}[1/5] 检查 Python...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}请安装 Python 3.10+${NC}"; exit 1
fi
echo -e "${GREEN}✓ Python $(python3 --version | cut -d' ' -f2)${NC}"

# [2/5] 虚拟环境
echo -e "${YELLOW}[2/5] 创建虚拟环境...${NC}"
python3 -m venv venv && source venv/bin/activate
echo -e "${GREEN}✓ venv 就绪${NC}"

# [3/5] 安装依赖
echo -e "${YELLOW}[3/5] 安装依赖...${NC}"
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo -e "${GREEN}✓ 依赖完成${NC}"

# [4/5] 环境变量
echo -e "${YELLOW}[4/5] 检查环境变量...${NC}"
if [ ! -f .env ]; then
    echo -e "${YELLOW}未发现 .env；请从环境变量或安全的部署配置提供 IDEA_AUTH_TOKEN。${NC}"
fi

# [5/6] 目录
echo -e "${YELLOW}[5/6] 创建运行时目录...${NC}"
mkdir -p logs memory
echo -e "${GREEN}✓ 目录就绪${NC}"

# [6/6] 完成
SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "YOUR_SERVER_IP")
echo ""
echo -e "${GREEN}========================================"
echo "  部署完成！"
echo "========================================${NC}"
echo ""
echo "启动:"
echo "  source venv/bin/activate && python main.py"
echo ""
echo "然后访问:"
echo -e "  ${YELLOW}http://${SERVER_IP}:8900${NC}  ← Web 对话界面"
echo "  http://${SERVER_IP}:8900/health        ← 健康检查"
echo "  http://${SERVER_IP}:8900/docs          ← API 文档"
echo ""
echo "systemd 部署（生产环境）:"
echo "  sudo cp idea-mcp.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable idea-mcp"
echo "  sudo systemctl start idea-mcp"
echo ""
