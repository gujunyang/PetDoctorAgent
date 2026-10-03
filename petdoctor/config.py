"""项目共享配置：环境变量加载与 LLM 实例。"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from pydantic import SecretStr


def _enable_utf8_stdout() -> None:
    """确保 Windows 控制台也能正常输出中文/Unicode。"""
    try:
        getattr(sys.stdout, "reconfigure")(encoding="utf-8")
    except Exception:
        pass


load_dotenv()
_enable_utf8_stdout()

# ── 路径与 RAG 配置 ────────────────────────────────────────────────────────
# config.py 位于 petdoctor/ 包内，项目根目录是其上一级
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"  # download_data.py 写入
MANUAL_DIR = DATA_DIR / "manual"  # 人工补充资料
RAG_DIR = DATA_DIR / "rag"  # build_rag.py 产出的向量库

# 向量库路径（Agent 通过该常量调用 RAGMill 检索）
RAG_DB_PATH = Path(os.getenv("PET_RAG_DB", str(RAG_DIR / "pet_knowledge.db")))
# 本地离线多语言 embedding 模型（首次使用会下载到 ~/.cache/ragmill）
# Xenova/paraphrase-multilingual-MiniLM-L12-v2：384 维、50+ 语言、无需前缀，中英检索兼顾
RAG_EMBEDDING_MODEL = os.getenv("RAGMILL_EMBEDDING_MODEL", "Xenova/paraphrase-multilingual-MiniLM-L12-v2")
RAG_CHUNK_SIZE = int(os.getenv("RAGMILL_CHUNK_SIZE", "500"))
RAG_OVERLAP = int(os.getenv("RAGMILL_OVERLAP", "50"))


def _thinking_extra_body(model: str, base_url: str) -> dict | None:
    """DeepSeek 思考模式不支持强制 tool_choice（create_agent 的结构化输出会用到）。

    对 DeepSeek 默认关闭 thinking；其他提供商不受影响。
    可用环境变量 ``LLM_DISABLE_THINKING=0`` 关闭该行为（恢复思考模式）。
    """
    if os.getenv("LLM_DISABLE_THINKING", "1").strip().lower() in {"0", "false", "no", "off"}:
        return None
    if "deepseek" not in f"{model} {base_url}".lower():
        return None
    return {"thinking": {"type": "disabled"}}


def get_llm() -> ChatOpenAI:
    """创建并返回 ChatOpenAI 实例，参数来自 .env。"""
    model = os.getenv("LLM_MODEL_ID", "gpt-4o-mini")
    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")

    extra: dict = {}
    extra_body = _thinking_extra_body(model, base_url)
    if extra_body is not None:
        extra["extra_body"] = extra_body

    return ChatOpenAI(
        model=model,
        api_key=SecretStr(os.getenv("LLM_API_KEY", "")),
        base_url=base_url,
        temperature=0.7,
        **extra,
    )
