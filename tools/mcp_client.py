"""MCP Client：从宠物店 MCP Server 获取 LangChain 工具。

MCP 工具是「异步专属」的（只有 coroutine、没有 func），无法直接同步调用。
为让整图保持同步（``app.invoke(...)``），``load_mcp_tools()`` 会把每个 MCP 工具
桥接为一个同时提供 sync(func) 与 async(coroutine) 入口的 ``StructuredTool``：
- 同步调用：内部 ``asyncio.run(tool.ainvoke(...))``
- 异步调用：直接 ``await tool.ainvoke(...)``

用法（异步）：
    tools = await get_mcp_tools()
用法（同步，图构建时）：
    tools = load_mcp_tools()      # 已桥接为可同步调用的工具；失败返回 []
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

MCP_SERVER_URL = os.getenv("PET_STORE_MCP_URL", "http://localhost:8000/mcp")


async def get_mcp_tools() -> list[BaseTool]:
    """连接 MCP Server 并返回其暴露的原始（异步）工具。"""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {
            "pet_store": {
                "transport": "streamable_http",
                "url": MCP_SERVER_URL,
            }
        }
    )
    return await client.get_tools()


async def get_mcp_tools_safe() -> list[BaseTool]:
    """获取原始 MCP 工具；连接失败时记录并返回空列表（不阻塞系统启动）。"""
    try:
        tools = await get_mcp_tools()
        names = ", ".join(tool.name for tool in tools)
        print(f"[mcp] 已连接 {MCP_SERVER_URL}，获取 {len(tools)} 个工具：{names}")
        return tools
    except Exception as exc:  # noqa: BLE001
        print(f"[mcp] 连接 {MCP_SERVER_URL} 失败（{exc}），跳过 MCP 工具")
        return []


def _to_sync_tool(tool: BaseTool) -> BaseTool:
    """把异步 MCP 工具桥接为同时支持 sync/async 调用的工具。"""

    def _run(**kwargs: Any) -> Any:
        return asyncio.run(tool.ainvoke(kwargs))

    async def _arun(**kwargs: Any) -> Any:
        return await tool.ainvoke(kwargs)

    return StructuredTool.from_function(
        func=_run,
        coroutine=_arun,
        name=tool.name,
        description=tool.description or "",
        args_schema=tool.args_schema,
    )


def load_mcp_tools() -> list[BaseTool]:
    """同步入口：获取 MCP 工具并桥接为可同步调用，失败返回 []。"""
    try:
        raw_tools = asyncio.run(get_mcp_tools_safe())
    except RuntimeError as exc:
        print(f"[mcp] 无法在当前事件循环中同步加载 MCP 工具（{exc}）")
        return []
    return [_to_sync_tool(tool) for tool in raw_tools]


def select_tools(tools: list[BaseTool], names: set[str]) -> list[BaseTool]:
    """按名称筛选工具。"""
    return [tool for tool in tools if tool.name in names]
