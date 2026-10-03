"""从 HuggingFace 下载宠物问诊原始数据，保存为 ``data/raw/`` 下的文本文件。

来源：
1. karenwky/pet-health-symptoms-dataset  （含 condition / record_type 字段）
2. SparkleDark/Everything_about_dogs      （仅 text 字段，养犬百科分句）

每条记录保存为一个文件，文件名格式：``{condition}_{index}.txt``。

用法：
    python scripts/download_data.py            # 下载全部
    python scripts/download_data.py --limit 50 # 每源只下载前 N 条（调试用）
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from datasets import load_dataset

# 允许以 `python scripts/download_data.py` 直接运行时导入项目根目录模块
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from petdoctor.config import RAW_DIR  # noqa: E402

SOURCES = [
    {"name": "karenwky/pet-health-symptoms-dataset", "split": "train"},
    {"name": "SparkleDark/Everything_about_dogs", "split": "train"},
]

# HuggingFace 原始数据集中没有 condition 字段的来源，按关键词推断一个分类
_CHAPTER_KEYWORDS: dict[str, str] = {
    "administering": "administering_medicine",
    "breeding": "breeding",
    "distemper": "distemper",
    "hydrophobia": "hydrophobia",
    "rabies": "hydrophobia",
    "feeding": "feeding",
    "food": "feeding",
    "drug": "drugs",
    "medicine": "drugs",
    "disease": "diseases",
    "symptom": "diseases",
    "medical": "medical_terms",
    "term": "medical_terms",
}

# 文件名安全化：仅保留字母、数字、下划线与中文
_UNSAFE_CHARS = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")

# 用于识别 condition 的候选字段（按优先级）
_CONDITION_KEYS = ("condition", "disease", "label", "category", "topic", "title", "record_type")


def _sanitize(value: object, fallback: str = "record") -> str:
    """把任意字段值转成文件系统安全、长度受限的小写标识。"""
    text = _UNSAFE_CHARS.sub("_", str(value).strip()).strip("_")
    return (text[:60].lower()) or fallback


def _guess_condition(text: str) -> str:
    """在没有 condition 字段时，根据文本关键词推断分类。"""
    lowered = text.lower()
    for keyword, condition in _CHAPTER_KEYWORDS.items():
        if keyword in lowered:
            return condition
    return "dog_general"


def _record_condition(record: dict) -> str:
    """从记录中提取 condition；缺失则根据 text 推断。"""
    for key in _CONDITION_KEYS:
        value = record.get(key)
        if value:
            return _sanitize(value)
    return _guess_condition(record.get("text", "") or "")


def _format_record(record: dict, source: str) -> str:
    """将一条记录序列化为便于 RAG chunking 的文本。"""
    lines = [f"# source: {source}"]
    for key, value in record.items():
        if value is None or value == "":
            continue
        if isinstance(value, (list, tuple)):
            value = "; ".join(str(item) for item in value)
        lines.append(f"{key}: {value}")
    return "\n".join(lines)


def _unique_path(directory: Path, filename: str, seen: set[str]) -> Path:
    """保证文件名唯一，冲突时追加来源前缀。"""
    if filename not in seen:
        seen.add(filename)
        return directory / filename
    stem, suffix = filename.rsplit(".", 1)
    candidate = f"{stem}_dup{suffix}"
    counter = 1
    while candidate in seen:
        candidate = f"{stem}_dup{counter}.{suffix}"
        counter += 1
    seen.add(candidate)
    return directory / candidate


def download(limit: int | None = None) -> int:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    total = 0

    for source in SOURCES:
        name = source["name"]
        print(f"[download] 加载数据集：{name} ...")
        dataset = load_dataset(name, split=source["split"])

        if limit is not None:
            dataset = dataset.select(range(min(limit, len(dataset))))

        written = 0
        for index, record in enumerate(dataset):
            text = (record.get("text") or "").strip()
            if not text:
                continue  # 跳过空记录（如养犬百科中的空行）

            condition = _record_condition(record)
            path = _unique_path(RAW_DIR, f"{condition}_{index}.txt", seen)
            path.write_text(_format_record(record, name), encoding="utf-8")
            written += 1

        total += written
        print(f"[download] {name}: 写入 {written} 条（跳过空记录）")

    print(f"[download] 完成，共写入 {total} 个文件到：{RAW_DIR}")
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="下载宠物问诊原始数据到 data/raw/")
    parser.add_argument("--limit", type=int, default=None, help="每个数据集最多下载的条数")
    args = parser.parse_args()
    download(limit=args.limit)


if __name__ == "__main__":
    main()
