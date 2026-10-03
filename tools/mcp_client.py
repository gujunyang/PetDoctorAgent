"""MCP Client：从宠物店 MCP Server 获取 LangChain 工具。

用法（异步）：
    tools = await get_mcp_tools()

用法（在同步的图构建流程中）：
    tools = load_mcp_tools()      # 内部 asyncio.run，失败返回 []
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

def get_server_url() -> str:
    """MCP Server 地址（默认 127.0.0.1，避免 localhost 解析到 IPv6 的问题）。"""
    return os.getenv("PET_STORE_MCP_URL", "http://127.0.0.1:8000/mcp")


async def get_mcp_tools() -> list[BaseTool]:
    """连接 MCP Server 并返回其暴露的全部工具。"""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {
            "pet_store": {
                "transport": "streamable_http",
                "url": get_server_url(),
            }
        }
    )
    return await client.get_tools()


async def get_mcp_tools_safe() -> list[BaseTool]:
    """获取 MCP 工具；连接失败时记录并返回空列表（不阻塞系统启动）。"""
    try:
        tools = await get_mcp_tools()
        names = ", ".join(tool.name for tool in tools)
        print(f"[mcp] 已连接 {get_server_url()}，获取 {len(tools)} 个工具：{names}")
        return tools
    except Exception as exc:  # noqa: BLE001
        print(f"[mcp] 连接 {get_server_url()} 失败（{exc}），跳过 MCP 工具")
        return []


def to_sync_tool(tool: BaseTool) -> BaseTool:
    """把 MCP 异步工具包装为同步工具（每次调用新建事件循环 + 新会话）。

    langgraph 的同步 PostgresSaver/Store 不支持 async 调用，故整图保持同步，
    这里将 MCP 的 async-only 工具桥接为 sync 工具。
    """
    if tool.func is not None:
        return tool

    def _run(**kwargs: Any) -> Any:
        return asyncio.run(tool.ainvoke(kwargs))

    return StructuredTool(
        name=tool.name,
        description=tool.description or "",
        args_schema=tool.args_schema,
        func=_run,
    )


def load_mcp_tools() -> list[BaseTool]:
    """同步入口：在 graph 初始化时获取 MCP 工具并转为同步工具，失败返回 []。"""
    try:
        tools = asyncio.run(get_mcp_tools_safe())
    except RuntimeError as exc:
        # 已处于事件循环中，无法 asyncio.run；调用方应改用 await get_mcp_tools_safe()
        print(f"[mcp] 无法在当前事件循环中同步加载 MCP 工具（{exc}）")
        return []
    return [to_sync_tool(tool) for tool in tools]


def select_tools(tools: list[BaseTool], names: set[str]) -> list[BaseTool]:
    """按名称筛选工具。"""
    return [tool for tool in tools if tool.name in names]
