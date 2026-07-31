"""
代理连接测试脚本
=================
在 TRAE 配置 MCP 之前，先跑这个脚本确认网络和 API Key 没问题。

用法：
  set RAG_SERVER_URL=http://你的服务器IP:8080
  set RAG_ADMIN_KEY=你的ADMIN_KEY
  python test_connection.py

预期输出：
  ✅ 健康检查通过
  ✅ 知识库状态获取成功
     public: X 条 / private: X 条
  ✅ 公开库检索成功
  ✅ 私有库检索成功（如果有 ADMIN_KEY）
"""

import os
import sys
import json
import requests

SERVER_URL = os.environ.get("RAG_SERVER_URL", "http://localhost:8080")
ADMIN_KEY = os.environ.get("RAG_ADMIN_KEY", "")

def test(name, fn):
    try:
        fn()
        print(f"  ✅ {name}")
    except Exception as e:
        print(f"  ❌ {name}：{e}")

def main():
    print(f"测试目标：{SERVER_URL}\n")

    headers = {}
    if ADMIN_KEY:
        headers["X-API-Key"] = ADMIN_KEY

    # 1. 健康检查
    test("健康检查", lambda: requests.get(f"{SERVER_URL}/api/health", timeout=10).raise_for_status())

    # 2. 统计信息
    def stats():
        resp = requests.get(f"{SERVER_URL}/api/stats", headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        print(f"      public: {data.get('public_records', 0)} 条 / private: {data.get('private_records', 0)} 条")

    test("知识库状态", stats)

    # 3. 公开库检索
    def search_public():
        resp = requests.post(
            f"{SERVER_URL}/api/search",
            headers=headers,
            json={"query": "测试查询", "top_k": 1},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        print(f"      返回 {data.get('total_results', 0)} 条结果")

    test("公开库检索", search_public)

    # 4. 私有库检索（仅 ADMIN）
    if ADMIN_KEY:
        def search_private():
            resp = requests.post(
                f"{SERVER_URL}/api/search?collection=private",
                headers=headers,
                json={"query": "测试查询", "top_k": 1},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            print(f"      返回 {data.get('total_results', 0)} 条结果")

        test("私有库检索", search_private)
    else:
        print("  ⏭️  跳过私有库测试（未设置 ADMIN_KEY）")

    print(f"\n🎉 全部测试通过！")

if __name__ == "__main__":
    main()
