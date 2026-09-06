from __future__ import annotations

import re


def clean_chapter_title(title: str) -> str:
    value = str(title or "").strip()
    value = value.replace("图片批次验收", "").replace("图片批次", "").strip()
    return value or "本章"


def clean_section_name(section: str, chapter_title: str) -> str:
    value = str(section or "").strip()
    if not value:
        return clean_chapter_title(chapter_title)
    value = value.replace("待细化", "").strip(" -+")
    value = value.replace("题目段", "题型训练")
    value = value.replace("解析段", "题解与解析")
    value = re.sub(r"\s+", " ", value).strip(" -+")
    if value.endswith("图片批次"):
        value = value[:-4].strip()
    return value or clean_chapter_title(chapter_title)
