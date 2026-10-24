"""Patrol CLI: attaches to the user's logged-in Edge and walks every course.

This CLI never launches a browser and never submits answers by itself: it
checks the login session, saves a Markdown report, and dumps objective-question
briefs that an MCP session (or a human) can answer next. Subjective, file and
experiment-report items are listed under "takeover".

Usage:
    python -m uplus_mcp.daily_patrol

Requires Edge running with the debug port (see browser.ATTACH_HELP).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .browser import BrowserSession, AuthExpiredError, UplusError, ATTACH_HELP
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
    parser = argparse.ArgumentParser(description="U+ 巡检（附加到已登录的 Edge，不自动提交）")
    parser.add_argument("--config", default="", help="配置文件路径")
    parser.add_argument("--brief", default="", help="待答题目 brief JSON 保存路径")
    args = parser.parse_args()

    try:
        engine = build_engine(args.config)
    except UplusError as exc:
        print(f"⚠️ {exc}", file=sys.stderr)
        return 3
    try:
        login = engine.check_login()
        if not login["logged_in"]:
            print("⚠️ 登录态无效：请在浏览器中登录 eduplus.net 后重试。", file=sys.stderr)
            return 2
        result = engine.patrol(auto_answer=False, submit=False)
        summary = result["summary"]
        print("=== 巡检摘要 ===")
        print(f"作业已提交 {summary['homework_submitted']}（待批改 {summary['homework_pending']}），"
              f"未完成 {summary['homework_outstanding']}")
        print(f"实验已提交 {summary['experiments_submitted']}，未完成 {summary['experiments_outstanding']}")
        print(f"待人工接管 {summary['takeover_count']} 项")
        if result["takeover"]:
            print("\n=== 需要人工处理 ===")
            for item in result["takeover"]:
                print(f"- [{item.get('course')}] {item.get('title')}：{item.get('detail')}"
                      + (f"（{item.get('deadline')}）" if item.get("deadline") else ""))
        if result["answer_briefs"]:
            brief_path = args.brief or str(Path(result["report_path"]).with_suffix(".brief.json"))
            Path(brief_path).write_text(
                json.dumps(result["answer_briefs"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"\n待答客观题 brief 已保存：{brief_path}")
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
