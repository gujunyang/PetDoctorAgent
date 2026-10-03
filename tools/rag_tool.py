"""宠物知识库检索工具。

基于 ``scripts/build_rag.py`` 产出的 RAGMill SQLite 向量库，向 Agent 暴露一个
LangChain tool：``pet_knowledge_search``。

向量库与 embedding 模型均按需惰性加载并缓存为进程内单例。

原始语料为英文，工具默认在检索后调用 LLM 将 top-k 片段一次性翻译为中文
（查询时翻译，成本/延迟低）。可通过环境变量 ``PET_RAG_TRANSLATE=0`` 关闭；
翻译失败或缺少 LLM 配置时自动回退返回英文原文。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from langchain.tools import tool
from pydantic import BaseModel, Field

from config import RAG_DB_PATH, RAG_EMBEDDING_MODEL, get_llm

_lock = threading.Lock()
_model: Any = None
_store: Any = None
_llm: Any = None


class _TranslatedChunks(BaseModel):
    """翻译结果：与输入片段顺序一致的简体中文列表。"""

    translations: list[str] = Field(
        default_factory=list,
        description="与输入片段顺序一一对应的简体中文翻译",
    )


def _ensure_ready() -> tuple[Any, Any]:
    """惰性初始化 embedding 模型与向量库（线程安全）。"""
    global _model, _store

    if _model is not None and _store is not None:
        return _model, _store

    with _lock:
        if not Path(RAG_DB_PATH).exists():
            raise FileNotFoundError(
                f"向量库不存在：{RAG_DB_PATH}，请先运行 scripts/build_rag.py"
            )

        if _model is None:
            from ragmill.embeddings import EmbeddingModel

            _model = EmbeddingModel(model_name=RAG_EMBEDDING_MODEL)

        if _store is None:
            from ragmill.vector_store import VectorStore

            _store = VectorStore(str(RAG_DB_PATH))

    return _model, _store


def _get_translator() -> Any:
    """惰性创建用于翻译的结构化输出 LLM。"""
    global _llm
    if _llm is None:
        with _lock:
            if _llm is None:
                # json_mode 兼容性最好（DeepSeek 等不支持 json_schema/强制 tool_choice）
                _llm = get_llm().with_structured_output(_TranslatedChunks, method="json_mode")
    return _llm


def _translation_enabled() -> bool:
    return os.getenv("PET_RAG_TRANSLATE", "1").strip().lower() not in {"0", "false", "no", "off"}


def _translate(texts: list[str]) -> list[str]:
    """将一组片段翻译为中文；失败时回退原文。"""
    if not texts or not _translation_enabled():
        return texts

    try:
        translator = _get_translator()
        numbered = "\n\n".join(f"[{index}] {text}" for index, text in enumerate(texts, 1))
        prompt = (
            "你是宠物医疗领域的专业翻译。请以 json 格式返回结果，把下面每条检索片段"
            "翻译成简体中文，术语准确、保留关键信息与数字，不增删内容。"
            "translations 字符串数组的顺序必须与输入编号一一对应。\n\n"
            f"{numbered}"
        )
        result = translator.invoke(prompt)
        translations = list(result.translations) if result else []
        if len(translations) != len(texts):
            return texts  # 数量不匹配，回退原文
        return [translated or texts[i] for i, translated in enumerate(translations)]
    except Exception:
        return texts


@tool("pet_knowledge_search", description="搜索宠物疾病、症状、用药相关知识库")
def pet_knowledge_search(query: str, top_k: int = 5) -> str:
    """从宠物医疗知识库中检索相关内容。

    Args:
        query: 检索问题或关键词。
        top_k: 返回的最相关片段数量。
    """
    try:
        model, store = _ensure_ready()
    except Exception as exc:  # 知识库未构建 / 依赖缺失时给出可读提示
        return f"（知识库暂不可用：{exc}）"

    if store.count() == 0:
        return "（知识库为空，请先运行 scripts/build_rag.py 构建）"

    query_vector = model.embed([query])[0]
    results = store.search(query_vector, top_k=top_k)
    if not results:
        return "未检索到相关宠物医疗知识。"

    contents = [item["content"] for item in results]
    translated = _translate(contents)

    parts: list[str] = []
    for rank, item in enumerate(results):
        filename = item.get("metadata", {}).get("filename", "unknown")
        parts.append(f"[{rank + 1}] {translated[rank]}（来源：{filename}）")
    return "\n\n".join(parts)
