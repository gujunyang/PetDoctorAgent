#!/usr/bin/env bash
# 启动宠物店业务工具 MCP Server（streamable_http，127.0.0.1:8000）
set -euo pipefail
cd "$(dirname "$0")/.."
exec python -m petdoctor.mcp_server.server
