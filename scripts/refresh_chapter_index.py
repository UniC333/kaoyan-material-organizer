#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

from common import ensure_learning_dirs, is_placeholder, load_json, markdown_list, normalize_context, save_json, vault_root_from_context_path

STRUCTURE_MD = "02_章节结构索引.md"
SUMMARY_MD = "03_片段归纳总表.md"
Q_MD = "00_本章后续追问索引.md"
C_MD = "01_易混点与卡点索引.md"
E_MD = "02_例题与题型索引.md"
QA_MD = "03_知识点问答入口.md"
INDEX_JSON = "chapter_knowledge_index.json"
SAVED_QA_JSON = "saved_qa_registry.json"
CHUNK_PLAN_JSON = "00_分片计划.json"

PLACEHOLDER_TOKENS = (
    "待补充",
    "待判定",
    "待整理",
    "待确认",
    "待细化",
    "至少保留 1 到 2 个",
    "优先保留 2 到 3 个",
    "优先提炼 1^∞",
    "如果本段以例题为主",
    "先明确本段主线",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-json", required=True)
    return parser.parse_args()


def dedupe_preserve_order(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def text_needs_refresh(text: str) -> bool:
    value = str(text or "").strip()
    if not value or is_placeholder(value):
        return True
    return any(token in value for token in PLACEHOLDER_TOKENS)


def chunk_plan_map(chunk_plan: dict) -> dict[str, dict]:
    return {chunk.get("chunk_id", ""): chunk for chunk in chunk_plan.get("chunks", []) if chunk.get("chunk_id")}


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


def infer_usage(chunk: dict, plan_chunk: dict) -> str:
    usage_hints = [str(ref.get("usage_hint", "")).strip() for ref in plan_chunk.get("image_refs", [])]
    joined = " ".join(
        [
            chunk.get("section", ""),
            chunk.get("focus_summary", ""),
            plan_chunk.get("section_guess", ""),
            " ".join(usage_hints),
        ]
    )
    if any(token in joined for token in ("题解", "解析")):
        return "analysis"
    if any(token in joined for token in ("习题", "练习", "题型", "试题", "题目")):
        return "exercise"
    if "例题" in joined:
        return "example"
    if any(token in joined for token in ("定理", "公式", "法则", "规则")):
        return "rule"
    if any(token in joined for token in ("归纳", "总结", "拓展")):
        return "summary"
    return "concept"


def derive_topic(section: str, chapter_title: str) -> str:
    cleaned_section = clean_section_name(section, chapter_title)
    cleaned_title = clean_chapter_title(chapter_title)
    topic = cleaned_section
    if cleaned_title and cleaned_section.startswith(cleaned_title):
        tail = cleaned_section[len(cleaned_title) :].strip(" ：:-")
        if tail:
            topic = f"{cleaned_title}{tail}"
        else:
            topic = cleaned_title
    topic = topic.replace("题型训练", "").replace("题解与解析", "").strip(" ：:-")
    return topic or cleaned_title or "本章内容"


def synthesize_focus_summary(section: str, chapter_title: str, usage: str) -> str:
    topic = derive_topic(section, chapter_title)
    display_section = clean_section_name(section, chapter_title)
    if usage == "exercise":
        return f"这一段围绕 {topic} 的题型训练展开，整理时应先按题型分组，再提炼每类题的起手判断、关键步骤和易错点。"
    if usage == "analysis":
        return f"这一段围绕 {topic} 的题解与解析展开，整理时应优先串起题型信号、解题步骤和常见易错点，再回看对应题目。"
    if usage == "example":
        return f"这一段以 {topic} 的代表例题为主，整理时应抓住题目触发信号、标准解法和可迁移的做题套路。"
    if usage == "rule":
        return f"这一段主要梳理 {topic} 的关键规则与公式，整理时应把适用条件、结论形式和常见误用放在一起看。"
    if usage == "summary":
        return f"这一段主要对 {topic} 做归纳总结，整理时应把前面分散的概念、规则和题型收束成可回看的主线。"
    return f"这一段主要建立 {display_section} 的核心概念和基础口径，整理时应先把定义边界、关键关系和常见混淆点理顺。"


def synthesize_concept_name(section: str, chapter_title: str, usage: str) -> str:
    topic = derive_topic(section, chapter_title)
    if usage == "exercise":
        return f"{topic}题型起手"
    if usage == "analysis":
        return f"{topic}解析主线"
    if usage == "example":
        return f"{topic}代表例题"
    if usage == "rule":
        return f"{topic}关键规则"
    if usage == "summary":
        return f"{topic}归纳主线"
    return f"{topic}核心概念"


def synthesize_key_rule(section: str, chapter_title: str, usage: str) -> str:
    topic = derive_topic(section, chapter_title)
    if usage == "exercise":
        return f"先判断这道题属于 {topic} 的哪类题型，再决定起手步骤和所用规则。"
    if usage == "analysis":
        return f"先识别题型信号，再按 {topic} 的标准步骤复盘解题过程。"
    if usage == "example":
        return f"不要只记答案，要把 {topic} 例题的触发条件和迁移方式一起记住。"
    if usage == "rule":
        return f"先确认适用条件，再写 {topic} 的对应规则或公式。"
    if usage == "summary":
        return f"回看 {topic} 时，优先串起主线，再补容易混的细节。"
    return f"先把 {topic} 的定义边界、核心关系和常见混淆点说顺。"


def synthesize_question_prompt(section: str, chapter_title: str, usage: str) -> str:
    topic = derive_topic(section, chapter_title)
    if usage == "exercise":
        return f"{topic}这类题最稳的起手判断是什么？"
    if usage == "analysis":
        return f"{topic}的解析里，最值得复盘的步骤切换点是什么？"
    if usage == "example":
        return f"{topic}代表例题最值得迁移的解题套路是什么？"
    if usage == "rule":
        return f"{topic}的关键规则最容易在哪个条件上用错？"
    if usage == "summary":
        return f"回顾 {topic} 时，最值得先串起来的是哪几条主线？"
    return f"{topic}最容易和哪个相邻概念混在一起？"


def synthesize_example_type(section: str, chapter_title: str, usage: str) -> str:
    topic = derive_topic(section, chapter_title)
    if usage == "exercise":
        return f"{topic}代表题型"
    if usage == "analysis":
        return f"{topic}解析步骤复盘题"
    if usage == "example":
        return f"{topic}代表例题"
    if usage == "rule":
        return f"{topic}规则辨析题"
    if usage == "summary":
        return f"{topic}归纳总结题"
    return f"{topic}概念辨析题"


def synthesize_confusion(section: str, chapter_title: str, usage: str) -> str:
    topic = derive_topic(section, chapter_title)
    if usage == "exercise":
        return f"{topic}这类题最容易出现“题目能看懂，但不会自己起手”的问题。"
    if usage == "analysis":
        return f"{topic}这一段最容易出现“会看解析，但不会自己复现步骤”的问题。"
    if usage == "example":
        return f"{topic}代表例题最容易只记答案，不记触发条件和迁移方式。"
    if usage == "rule":
        return f"{topic}的规则最容易在适用条件还没核对清楚时就直接套用。"
    if usage == "summary":
        return f"{topic}回顾时最容易出现“每个点都见过，但主线串不起来”的问题。"
    return f"{topic}最容易在定义边界和相邻概念上混淆。"


def is_generic_concept_name(name: str, section: str) -> bool:
    value = str(name or "").strip()
    cleaned_section = clean_section_name(section, "")
    if text_needs_refresh(value):
        return True
    if value.endswith("主线") and (cleaned_section in value or "图片批次" in value):
        return True
    return False


def normalized_concept_entry(concept: dict, chunk: dict, chapter_title: str, usage: str) -> dict:
    section = chunk["section"]
    name = str(concept.get("name", "")).strip()
    if not name or is_placeholder(name):
        name = synthesize_concept_name(section, chapter_title, usage)
    elif is_generic_concept_name(name, section):
        name = synthesize_concept_name(section, chapter_title, usage)

    summary = str(concept.get("summary", "")).strip()
    if text_needs_refresh(summary):
        summary = chunk["focus_summary"]

    key_rule = str(concept.get("key_rule", "")).strip()
    if text_needs_refresh(key_rule):
        key_rule = synthesize_key_rule(section, chapter_title, usage)

    followup_questions = [
        prompt.strip()
        for prompt in concept.get("followup_questions", [])
        if prompt and not text_needs_refresh(prompt)
    ]
    if not followup_questions:
        followup_questions = [synthesize_question_prompt(section, chapter_title, usage)]

    confusions = [item.strip() for item in concept.get("confusions", []) if item and not text_needs_refresh(item)]
    if not confusions:
        confusions = [synthesize_confusion(section, chapter_title, usage)]

    return {
        "name": name,
        "summary": summary,
        "key_rule": key_rule,
        "chunk_id": chunk["chunk_id"],
        "section": chunk["section"],
        "page_start": chunk["source_refs"]["page_start"],
        "page_end": chunk["source_refs"]["page_end"],
        "card_file": f"{name}.md",
        "followup_questions": followup_questions,
        "confusions": confusions,
    }


def normalized_chunk(chunk: dict, plan_chunk: dict, chapter_title: str) -> dict:
    usage = infer_usage(chunk, plan_chunk)
    section = clean_section_name(chunk.get("section", ""), chapter_title)
    focus = str(chunk.get("focus_summary", "")).strip()
    if text_needs_refresh(focus):
        focus = synthesize_focus_summary(section, chapter_title, usage)
    return {
        **chunk,
        "section": section,
        "focus_summary": focus,
        "usage": usage,
    }


def build_major_sections(chunks: list[dict]) -> list[dict]:
    return [
        {
            "chunk_id": chunk["chunk_id"],
            "section": chunk["section"],
            "focus_summary": chunk["focus_summary"],
            "page_start": chunk["source_refs"]["page_start"],
            "page_end": chunk["source_refs"]["page_end"],
        }
        for chunk in chunks
    ]


def build_chapter_overview(chunks: list[dict]) -> str:
    focus_summaries = [chunk.get("focus_summary", "").strip() for chunk in chunks[:3] if chunk.get("focus_summary", "").strip()]
    return "；".join(focus_summaries)


def build_learning_path(chunks: list[dict], concept_index: list[dict]) -> list[str]:
    section_to_concepts: dict[str, list[str]] = {}
    for concept in concept_index:
        section = concept.get("section", "").strip()
        name = concept.get("name", "").strip()
        if not section or not name:
            continue
        section_to_concepts.setdefault(section, [])
        if name not in section_to_concepts[section]:
            section_to_concepts[section].append(name)

    steps: list[str] = []
    for idx, chunk in enumerate(chunks[: max(4, min(6, len(chunks)))], start=1):
        section = chunk.get("section", "").strip()
        concepts = section_to_concepts.get(section, [])[:2]
        concept_text = "、".join(concepts) if concepts else synthesize_concept_name(section, chunk.get("chapter_title", ""), chunk.get("usage", "concept"))
        steps.append(f"第{idx}步：先看 {section}，抓住 {concept_text}。")
    steps = dedupe_preserve_order(steps)
    if len(steps) < 3 and concept_index:
        priority = "、".join([concept.get("name", "").strip() for concept in concept_index[:2] if concept.get("name")])
        if priority:
            steps.append(f"第{len(steps)+1}步：回看本章优先知识点，重点把 {priority} 串成一条主线。")
    if len(steps) < 3:
        final_section = chunks[-1].get("section", "本章重点") if chunks else "本章重点"
        steps.append(f"第{len(steps)+1}步：最后回到 {final_section}，把题型信号、易混点和后续追问整理成自己的复盘口径。")
    return steps


def build_priority_concepts(concept_index: list[dict]) -> list[str]:
    return dedupe_preserve_order([concept.get("name", "").strip() for concept in concept_index])[:5]


def render_structure_index(chunks: list[dict]) -> str:
    lines = [
        "# 章节结构索引",
        "",
        "| 片段 | 小节 | 主旨 | 图片范围 |",
        "| --- | --- | --- | --- |",
    ]
    for chunk in chunks:
        lines.append(
            f"| {chunk['chunk_id']} | {chunk['section']} | {chunk['focus_summary']} | {chunk['source_refs']['image_start']}-{chunk['source_refs']['image_end']} |"
        )
    return "\n".join(lines) + "\n"


def render_chunk_summary(chunks: list[dict]) -> str:
    lines = [
        "# 片段归纳总表",
        "",
        "| 片段 | 核心概念数 | 规则数 | 题型数 | 易混点数 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for chunk in chunks:
        lines.append(
            f"| {chunk['chunk_id']} | {len(chunk['core_concepts'])} | {len(chunk['rules_or_formulas'])} | {len(chunk['example_types'])} | {len(chunk['confusions'])} |"
        )
    return "\n".join(lines) + "\n"


def render_qa_entry(
    chunks: list[dict],
    concept_index: list[dict],
    question_prompts: list[str],
    learning_path: list[str],
    saved_questions: list[str],
) -> str:
    lines = [
        "# 知识点问答入口",
        "",
        "## 这一章适合怎么问",
        "",
        "- 先问章节主线：这一章主要讲了什么，几个大块之间怎么连接。",
        "- 再问概念边界：某个概念、定理、方法和相邻内容最容易混在哪里。",
        "- 最后问下一步：当前最值得先追哪几个点，先练哪类题最合适。",
    ]
    if learning_path:
        lines.extend(["", "## 建议学习顺序", ""])
        lines.extend(f"- {item}" for item in learning_path)

    lines.extend(["", "## 本章可直接追问的知识点", ""])
    for concept in concept_index[:20]:
        lines.append(f"- {concept['name']}：见 {concept['section']}（{concept['chunk_id']}）")

    lines.extend(["", "## 本章优先追问清单", ""])
    lines.extend(f"- {item}" for item in question_prompts[:12]) if question_prompts else lines.append("- 待补充")

    lines.extend(["", "## 最近已问过的问题", ""])
    if saved_questions:
        for item in saved_questions[:8]:
            lines.append(f"- {item}")
    else:
        lines.append("- 当前还没有本章的问答沉淀。")

    lines.extend(["", "## 分片入口", ""])
    for chunk in chunks:
        lines.append(f"- {chunk['chunk_id']}：{chunk['section']}，页段 {chunk['source_refs']['page_start']} - {chunk['source_refs']['page_end']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    context_path = Path(args.context_json)
    context = normalize_context(load_json(context_path))
    chapter_title = context["chapter_title"]
    dirs = ensure_learning_dirs(context_path.parent)
    chunk_files = sorted(dirs["chunk_extracts"].glob("chunk-*.json"))
    if not chunk_files:
        raise SystemExit("[ERROR] no chunk extracts found")

    chunk_plan_path = dirs["chunk_plan"] / CHUNK_PLAN_JSON
    plan_map = chunk_plan_map(load_json(chunk_plan_path)) if chunk_plan_path.exists() else {}
    vault_root = vault_root_from_context_path(context_path)
    qa_registry_path = vault_root / "99_索引与状态" / SAVED_QA_JSON
    qa_registry = load_json(qa_registry_path) if qa_registry_path.exists() else {"chapters": []}
    saved_qa_summary = next(
        (
            item
            for item in qa_registry.get("chapters", [])
            if item.get("subject", "") == context.get("subject", "")
            and item.get("chapter_title", "") == context.get("chapter_title", "")
        ),
        {},
    )
    saved_questions = saved_qa_summary.get("recent_questions", [])
    saved_weak_spots = saved_qa_summary.get("saved_weak_spots", [])
    saved_next_questions = saved_qa_summary.get("saved_next_questions", [])

    raw_chunks = [load_json(path) for path in chunk_files]
    chunks = [normalized_chunk(chunk, plan_map.get(chunk.get("chunk_id", ""), {}), chapter_title) for chunk in raw_chunks]

    concept_index = []
    question_prompts = []
    confusions = []
    example_types = []

    for chunk in chunks:
        plan_chunk = plan_map.get(chunk.get("chunk_id", ""), {})
        usage = chunk["usage"]

        chunk_concepts = chunk.get("core_concepts", []) or [{}]
        for concept in chunk_concepts:
            entry = normalized_concept_entry(concept, chunk, chapter_title, usage)
            concept_index.append(entry)
            for prompt in entry["followup_questions"][:3]:
                if prompt and prompt not in question_prompts:
                    question_prompts.append(prompt)
            for item in entry["confusions"]:
                if item and item not in confusions:
                    confusions.append(item)

        raw_prompts = [str(prompt).strip() for prompt in chunk.get("question_prompts", []) if prompt]
        good_prompts = [prompt for prompt in raw_prompts if not text_needs_refresh(prompt)]
        if not good_prompts:
            good_prompts = [synthesize_question_prompt(chunk["section"], chapter_title, usage)]
        for prompt in good_prompts:
            if prompt not in question_prompts:
                question_prompts.append(prompt)

        raw_confusions = [str(item).strip() for item in chunk.get("confusions", []) if item]
        good_confusions = [item for item in raw_confusions if not text_needs_refresh(item)]
        if not good_confusions:
            good_confusions = [synthesize_confusion(chunk["section"], chapter_title, usage)]
        for item in good_confusions:
            if item not in confusions:
                confusions.append(item)

        raw_examples = [str(item.get("name", "")).strip() for item in chunk.get("example_types", []) if item.get("name")]
        good_examples = [item for item in raw_examples if not text_needs_refresh(item)]
        if not good_examples:
            good_examples = [synthesize_example_type(chunk["section"], chapter_title, usage)]
        for item in good_examples:
            if item not in example_types:
                example_types.append(item)

        if not plan_chunk.get("image_refs") and synthesize_example_type(chunk["section"], chapter_title, usage) not in example_types:
            example_types.append(synthesize_example_type(chunk["section"], chapter_title, usage))

    question_prompts = dedupe_preserve_order(question_prompts)
    confusions = dedupe_preserve_order(confusions)
    example_types = dedupe_preserve_order(example_types)
    if saved_weak_spots:
        confusions = dedupe_preserve_order(confusions + saved_weak_spots)
    if saved_next_questions:
        question_prompts = dedupe_preserve_order(question_prompts + saved_next_questions)
    concept_index = [
        concept
        for index, concept in enumerate(concept_index)
        if concept["name"] not in {item["name"] for item in concept_index[:index]}
    ]

    major_sections = build_major_sections(chunks)
    chapter_overview = build_chapter_overview(chunks)
    learning_path = build_learning_path(chunks, concept_index)
    priority_concepts = build_priority_concepts(concept_index)

    (dirs["chapter_notes"] / STRUCTURE_MD).write_text(render_structure_index(chunks), encoding="utf-8")
    (dirs["chapter_notes"] / SUMMARY_MD).write_text(render_chunk_summary(chunks), encoding="utf-8")
    (dirs["question_index"] / Q_MD).write_text("# 本章后续追问索引\n\n" + markdown_list(question_prompts[:10], empty_text="待补充") + "\n", encoding="utf-8")
    (dirs["question_index"] / C_MD).write_text("# 易混点与卡点索引\n\n" + markdown_list(confusions, empty_text="待补充") + "\n", encoding="utf-8")
    (dirs["question_index"] / E_MD).write_text("# 例题与题型索引\n\n" + markdown_list(example_types, empty_text="待补充") + "\n", encoding="utf-8")
    (dirs["question_index"] / QA_MD).write_text(render_qa_entry(chunks, concept_index, question_prompts, learning_path, saved_questions), encoding="utf-8")
    save_json(
        dirs["question_index"] / INDEX_JSON,
        {
            "chapter_title": context["chapter_title"],
            "batch_id": context["batch_id"],
            "chapter_overview": chapter_overview,
            "learning_path": learning_path,
            "priority_concepts": priority_concepts,
            "major_sections": major_sections,
            "chunks": [
                {
                    "chunk_id": chunk["chunk_id"],
                    "section": chunk["section"],
                    "focus_summary": chunk["focus_summary"],
                    "page_start": chunk["source_refs"]["page_start"],
                    "page_end": chunk["source_refs"]["page_end"],
                    "learning_status": chunk.get("learning_status", {}),
                }
                for chunk in chunks
            ],
            "concept_index": concept_index,
            "question_prompts": question_prompts[:10],
            "example_types": example_types,
            "weak_spots": confusions,
            "saved_qa_count": int(saved_qa_summary.get("saved_qa_count", 0)),
            "recent_saved_questions": saved_questions[:8],
            "saved_weak_spots": saved_weak_spots[:8],
            "saved_next_questions": saved_next_questions[:8],
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
