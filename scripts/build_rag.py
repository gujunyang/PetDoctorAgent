"""使用 RAGMill 构建本地 RAG 向量库（SQLite）。

完整流程：ingest → chunk → embed → store。

- ingest/chunk：``RAGEngine.execute_pipeline`` 读取目录并做语义切分
- embed：``EmbeddingModel`` 本地 ONNX 离线向量化（首次使用会下载模型）
- store：``VectorStore``（SQLite，RAGMill 默认）

产物路径由 ``config.RAG_DB_PATH`` 定义（默认 data/rag/pet_knowledge.db），
供 ``petdoctor/tools/rag.py`` 中的 Agent 检索工具调用。

用法：
    python scripts/build_rag.py            # 库为空则构建，已存在则跳过
    python scripts/build_rag.py --rebuild  # 删除并重建
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许以 `python scripts/build_rag.py` 直接运行时导入项目根目录模块
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from petdoctor.config import (  # noqa: E402
    MANUAL_DIR,
    RAG_CHUNK_SIZE,
    RAG_DB_PATH,
    RAG_EMBEDDING_MODEL,
    RAG_OVERLAP,
    RAW_DIR,
)
from ragmill import RAGEngine  # noqa: E402
from ragmill.embeddings import EmbeddingModel  # noqa: E402
from ragmill.vector_store import VectorStore  # noqa: E402


def _collect_payloads(engine: RAGEngine) -> list[dict]:
    payloads: list[dict] = []
    for directory in (RAW_DIR, MANUAL_DIR):
        if not directory.exists():
            continue
        if not any(directory.rglob("*.txt")):
            continue
        batch = engine.execute_pipeline(str(directory))
        print(f"[build] {directory}: {len(batch)} 个 chunk")
        payloads.extend(batch)
    return payloads


def build(rebuild: bool = False) -> Path:
    if not RAW_DIR.exists() or not any(RAW_DIR.rglob("*.txt")):
        raise SystemExit(
            f"未找到原始数据，请先运行 scripts/download_data.py（目录：{RAW_DIR}）"
        )

    RAG_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    if rebuild and RAG_DB_PATH.exists():
        RAG_DB_PATH.unlink()
        print(f"[build] 已删除旧向量库：{RAG_DB_PATH}")

    store = VectorStore(str(RAG_DB_PATH))
    try:
        if store.count() > 0 and not rebuild:
            print(f"[build] 向量库已存在（{store.count()} 个 chunk），跳过。使用 --rebuild 重建。")
            return RAG_DB_PATH

        engine = RAGEngine(chunk_size=RAG_CHUNK_SIZE, overlap=RAG_OVERLAP)
        payloads = _collect_payloads(engine)
        if not payloads:
            raise SystemExit("没有可入库的文本 chunk。")
        print(f"[build] 共 {len(payloads)} 个 chunk，开始向量化 ...")

        model = EmbeddingModel(model_name=RAG_EMBEDDING_MODEL)
        embeddings = model.embed([payload["content"] for payload in payloads])
        print(f"[build] embeddings shape: {embeddings.shape}")

        with store.batch():
            store.add(payloads, embeddings)
        print(f"[build] 完成，向量库：{RAG_DB_PATH}（{store.count()} 个 chunk）")
        return RAG_DB_PATH
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 RAGMill 本地向量库")
    parser.add_argument("--rebuild", action="store_true", help="删除并重建向量库")
    args = parser.parse_args()
    build(rebuild=args.rebuild)


if __name__ == "__main__":
    main()
