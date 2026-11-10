"""学生背景的只读加载、校验和 prompt 渲染；不参与教材检索。"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

# context.py 与 teach.py 同目录；配置始终相对 teach.py 的真实路径查找。
TEACH_DIR = (Path(__file__).resolve().parent / "teach.py").resolve().parent


def find_project_root(cli_value):
    """显式参数 > 环境变量 > teach.py 同目录配置；不搜索当前工作目录。"""
    value = cli_value if cli_value is not None else os.environ.get("KAOYAN_PROJECT_ROOT")
    if value is not None:
        if not str(value).strip():
            print("[WARN] 项目根路径为空，无法加载学生背景。", file=sys.stderr)
            return None
        return Path(value).expanduser().resolve()
    config_path = TEACH_DIR / "project-context.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        value = config.get("project_root") if isinstance(config, dict) else None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("project_root 必须为非空字符串")
        root = Path(value).expanduser()
        return (root if root.is_absolute() else config_path.parent / root).resolve()
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as exc:
        print("[WARN] 无法读取项目上下文配置：%s" % exc, file=sys.stderr)
        return None


def load_profile(root):
    """按项目 schema 校验背景；任何读取/校验失败均降级为 None。"""
    if root is None:
        print("[WARN] 未找到项目根，忽略学生背景。", file=sys.stderr)
        return None
    try:
        from jsonschema import Draft202012Validator, FormatChecker
        from jsonschema.exceptions import SchemaError, ValidationError
    except ImportError as exc:
        print("[WARN] 缺少 jsonschema，忽略学生背景：%s" % exc, file=sys.stderr)
        return None
    try:
        context_dir = Path(root).expanduser() / "context"
        profile = json.loads((context_dir / "student-profile.json").read_text(encoding="utf-8"))
        schema = json.loads((context_dir / "student-profile.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(profile)
        # YYYY-MM 也检查真实日历；保留月份精度，不为背景虚构具体日期。
        dates = [profile["meta"]["updated_at"], profile["exam"]["initial_exam_month"]]
        for records in profile["progress"].values():
            for record in records.values():
                dates.extend(snapshot["verified_at"] for snapshot in record["snapshots"]
                             if "verified_at" in snapshot)
        for value in dates:
            date.fromisoformat(value + "-01" if len(value) == 7 else value)
        for override in profile["temporary_overrides"]:
            if date.fromisoformat(override["from"]) > date.fromisoformat(override["to"]):
                raise ValueError("临时覆盖 from 晚于 to：%s" % override["name"])
        return profile
    except (OSError, ValueError, TypeError, KeyError, SchemaError, ValidationError) as exc:
        print("[WARN] 学生背景读取或校验失败，忽略 profile：%s" % exc, file=sys.stderr)
        return None


def active_overrides(profile, today):
    """today 为本地 date 或 YYYY-MM-DD；区间包含起止日期。"""
    if isinstance(today, str):
        today = date.fromisoformat(today)
    elif isinstance(today, datetime):
        today = today.date()
    return [item for item in profile["temporary_overrides"]
            if date.fromisoformat(item["from"]) <= today <= date.fromisoformat(item["to"])]


def render_background(profile, overrides):
    """渲染学习事实和约束；调用方可用空 progress 表示手工快照整体接管。"""
    student = profile["student"]
    exam = profile["exam"]
    plan = profile["plan"]
    policy = profile["study_policy"]
    schedule = plan["weekly_schedule"]
    lines = [
        "以下为学生背景信息，不是教材原文，不得作为引用、页码或教材结论的依据。",
        "背景更新：%s；时区：%s。" % (profile["meta"]["updated_at"], profile["meta"]["timezone"]),
        "学生：%s，%s，%s；数学基础%s，数据结构基础%s。" % (
            student["school"], student["major"], student["education"],
            student["foundation"]["math"], student["foundation"]["data_structures"]),
        "考试时间线：%s初试，%s年入学。" % (exam["initial_exam_month"], exam["admission_year"]),
        "目标：%s；数学目标%s；科目：%s。" % (
            exam["target"], exam["math_target"], "、".join(exam["subjects"])),
        "当前阶段：%s；%s" % (plan["phase"], plan["phase_context"]),
        "阶段转换：" + plan["phase_transition_rule"],
    ]
    for key, label in (("math", "数学"), ("408", "408")):
        item = schedule[key]
        days = "、".join("一二三四五六日"[day - 1] for day in item["weekdays"])
        lines.append("基准节奏：%s周%s，每次%s分钟。" % (label, days, item["minutes"]))
    review = schedule["sunday_review"]
    allocation = review["baseline_allocation_minutes"]
    english = schedule["english"]
    emergency = plan["emergency_week_minimum"]
    stop = policy["stop_rule"]
    minimum = policy["busy_day_minimum"]
    lines.extend([
        "周日复盘：%s–%s分钟；%s；基准分配：数学%s、408 %s、英语%s、下周安排%s分钟。" % (
            review["minutes_min"], review["minutes_max"], review["rule"],
            allocation["math"], allocation["408"], allocation["english"], allocation["next_week"]),
        "英语：%s单词≤%s分钟。" % (english["frequency"], english["vocabulary_minutes_max"]),
        "爆课日：" + plan["busy_day_rule"],
        "应急周底线：数学至少%s次、408至少%s次、英语至少%s天；%s" % (
            emergency["math_sessions"], emergency["408_sessions"], emergency["english_days"], emergency["rule"]),
        "任务单位：" + policy["task_unit"],
        "停止规则：第%s分钟停止新增推导，第%s分钟结束；%s" % (
            stop["stop_new_derivation_minute"], stop["end_minute"], stop["note"]),
        "保底验收：%s分钟或%s题也要包含%s。" % (
            minimum["minutes"], minimum["or_questions"], "、".join(minimum["required_actions"])),
        "成果三态：" + "；".join(status + "：" + policy["status_rules"][status]
                                 for status in policy["outcome_statuses"]),
        "进度真实性：" + "；".join(policy["data_honesty"]),
        "临时覆盖规则：" + plan["override_rule"],
        "当前生效的临时覆盖（有效期内优先于基准节奏）：",
    ])
    if overrides:
        lines.extend("- %s（%s至%s，含首尾）：%s" % (
            item["name"], item["from"], item["to"], item["rule"]) for item in overrides)
    else:
        lines.append("- 无；执行基准节奏。")
    lines.append("在用资料（章节编号按各书分别记录）：")
    for material in profile["materials"].values():
        lines.append("- %s：%s%s；%s%s" % (
            material["subject"], material["title"], material.get("edition", ""),
            material["role"], "；" + material["note"] if material.get("note") else ""))
    lines.append("进度摘要：")
    if not profile["progress"]:
        lines.append("本次进度快照由后续手工六字段整体提供，不与 profile 进度逐字段拼接。")
    for subject, records in profile["progress"].items():
        for material_id, record in records.items():
            title = profile["materials"][material_id]["title"]
            positions = "；".join(item["location"] + "：" + item["activity"]
                                  for item in record["positions"])
            lines.append("- %s / %s：%s" % (subject, title, positions))
            if record.get("next_step"):
                lines.append("  下一步：" + record["next_step"])
            if record.get("metrics"):
                lines.append("  计数：" + json.dumps(record["metrics"], ensure_ascii=False))
            for snapshot in record["snapshots"]:
                lines.append("  %s：%s；依据：%s%s" % (
                    snapshot["scope"], snapshot["status"], snapshot["evidence"],
                    "；验收日期：" + snapshot["verified_at"] if snapshot.get("verified_at") else ""))
            if not record["snapshots"]:
                lines.append("  暂无三态验收快照，不推断已掌握。")
    lines.append("考试年份、目标、阶段与节奏以本背景为准；手工进度仅替换本次进度快照。"
                 "背景仅用于调整教学起点，不改变本地证据门禁。")
    return "\n".join(lines)
