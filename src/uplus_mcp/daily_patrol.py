"""Patrol CLI: attaches to the user's logged-in browser and walks every course.

This CLI never launches a browser and never submits answers by itself: it
checks the login session, saves a Markdown report, and dumps objective-question
briefs that an MCP session (or a human) can answer next. Submitted homework is
audited by default for blank answers and partial multi-selects; the repair
brief tells the MCP session what to answer before calling
``uplus_repair_homework``. Subjective, file and experiment-report items are
listed under "takeover".

Usage:
    python -m uplus_mcp.daily_patrol

Requires a browser running with the debug port (see browser.ATTACH_HELP).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .browser import BrowserSession, AuthExpiredError, UplusError
from .config import load_config
from .engine import PatrolEngine
from .operator import HomeworkOperator
from .platform import PlatformClient


def build_engine(config_path: str = "") -> PatrolEngine:
    cfg = load_config(config_path or None)
    session = BrowserSession(cfg)
    client = PlatformClient(session, cfg)
    operator = HomeworkOperator(session, client, cfg)
    return PatrolEngine(cfg, session, client, operator)


def main() -> int:
    parser = argparse.ArgumentParser(description="U+ 巡检（附加到已登录的浏览器，不自动提交）")
    parser.add_argument("--config", default="", help="配置文件路径")
    parser.add_argument("--brief", default="", help="待答题目 brief JSON 保存路径")
    parser.add_argument("--audit", dest="audit", action="store_true", default=True,
                        help="核查已提交作业是否漏答/多选漏选（默认开启，只读不撤回）")
    parser.add_argument("--no-audit", dest="audit", action="store_false",
                        help="跳过已提交作业的完整性核查")
    args = parser.parse_args()

    try:
        engine = build_engine(args.config)
    except UplusError as exc:
        print(f"⚠️ {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - CLI entrypoint: no raw traceback
        print(f"⚠️ 初始化失败：{exc}", file=sys.stderr)
        return 3
    try:
        login = engine.check_login()
        if not login["logged_in"]:
            print("⚠️ 登录态无效：请在浏览器中登录 eduplus.net 后重试。", file=sys.stderr)
            return 2
        result = engine.patrol(auto_answer=False, submit=False, audit=args.audit)
        summary = result["summary"]
        print("=== 巡检摘要 ===")
        print(f"作业已提交 {summary['homework_submitted']}（待批改 {summary['homework_pending']}），"
              f"未完成 {summary['homework_outstanding']}")
        exp_line = f"实验已提交 {summary['experiments_submitted']}，未完成 {summary['experiments_outstanding']}"
        if summary.get("experiments_unknown"):
            exp_line += f"，状态未知 {summary['experiments_unknown']}"
        print(exp_line)
        print(f"待人工接管 {summary['takeover_count']} 项")
        if result["takeover"]:
            print("\n=== 需要人工处理 ===")
            for item in result["takeover"]:
                print(f"- [{item.get('course')}] {item.get('title')}：{item.get('detail')}"
                      + (f"（{item.get('deadline')}）" if item.get("deadline") else ""))
        if result.get("incomplete_homeworks"):
            print("\n=== 已提交但不完整 ===")
            for item in result["incomplete_homeworks"]:
                parts: list[str] = []
                if item["missing"]:
                    orders = "、".join(str(n["order"]) for n in item["missing"][:10])
                    tail = "…" if len(item["missing"]) > 10 else ""
                    parts.append(f"漏答 {len(item['missing'])} 题（题号 {orders}{tail}）")
                if item.get("partial"):
                    porders = "、".join(
                        str(n["order"]) + (
                            f"缺{n.get('missing_letters')}" if n.get("missing_letters") else ""
                        )
                        for n in item["partial"][:10]
                    )
                    ptail = "…" if len(item["partial"]) > 10 else ""
                    parts.append(f"多选漏选 {len(item['partial'])} 题（题号 {porders}{ptail}）")
                hint = "，可撤回后补齐" if item["withdrawable"] else "，已出分无法撤回"
                print(f"- [{item['course_name']}] {item['name']}：{'；'.join(parts)}{hint}")
        audit_summary = result.get("audit_summary") or {}
        if audit_summary.get("checked") and audit_summary.get("unjudged_multiselect"):
            print(f"\n另有 {audit_summary['unjudged_multiselect']} 道多选题因平台尚未公布"
                  "参考答案（待批改）无法核对是否漏选。")
        if result.get("experiment_briefs"):
            print("\n=== 待做实验 ===")
            for brief in result["experiment_briefs"]:
                needed = "、".join(brief.get("needed_sections") or []) or "无"
                print(f"- [{brief['course_name']}] {brief['name']}："
                      f"待写章节 {needed}（{brief.get('deadline', '')}）")
        if result.get("experiment_audits"):
            issues = [a for a in result["experiment_audits"] if a.get("issues")]
            if issues:
                print("\n=== 已提交实验核查 ===")
                for item in issues:
                    print(f"- [{item['course_name']}] {item['name']}："
                          + "；".join(item["issues"]))
        if result["answer_briefs"]:
            brief_path = args.brief or str(Path(result["report_path"]).with_suffix(".brief.json"))
            Path(brief_path).write_text(
                json.dumps(result["answer_briefs"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"\n待答客观题 brief 已保存：{brief_path}")
        if result.get("repair_briefs"):
            repair_path = Path(result["report_path"]).with_suffix(".repair.json")
            repair_path.write_text(
                json.dumps(result["repair_briefs"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            needs = sum(
                1 for hw in result["repair_briefs"]
                for item in hw["items"]
                if item.get("needs_model_answer") and not item.get("optional")
            )
            optional = sum(
                1 for hw in result["repair_briefs"]
                for item in hw["items"] if item.get("optional")
            )
            print(f"\n待补交题目 brief 已保存：{repair_path}")
            if needs:
                print(f"其中 {needs} 题需要模型作答；交给 uplus_repair_homework "
                      "撤回并补齐后重新提交。")
            if optional:
                print(f"另有 {optional} 道待批改多选无法核对是否漏选（optional）；"
                      "批改后复核，或由模型重答后用 verify_unjudged=True 彻底复核。")
        print(f"\n巡检报告：{result['report_path']}")
        return 0
    except AuthExpiredError:
        print("⚠️ 登录态在巡检中途失效，请在浏览器中重新登录后再运行。", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI entrypoint
        print(f"巡检失败：{exc}", file=sys.stderr)
        return 1
    finally:
        if session := getattr(engine, "session", None):
            session.close()


if __name__ == "__main__":
    raise SystemExit(main())
