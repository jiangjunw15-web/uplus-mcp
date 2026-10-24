"""First-run login helper: open a visible browser, save the persisted session.

Usage:
    python scripts/login.py
    python scripts/login.py --config config/courses.json

Sign in with your student account in the opened window; the script detects the
workbench, saves ``storage_state.json`` and exits automatically.
"""

from __future__ import annotations

import argparse
import sys
import time

from uplus_mcp.browser import BrowserSession
from uplus_mcp.config import load_config
from uplus_mcp.platform import PlatformClient


def main() -> int:
    parser = argparse.ArgumentParser(description="U+ 首次登录并保存会话状态")
    parser.add_argument("--config", default="", help="配置文件路径，默认 config/courses.json")
    parser.add_argument("--timeout", type=int, default=300, help="等待登录时长（秒）")
    args = parser.parse_args()

    cfg = load_config(args.config or None)
    cfg.headless = False  # login must be visible to the human
    session = BrowserSession(cfg)
    client = PlatformClient(session, cfg)

    try:
        session.start()
        session.open("/workbench/index/school")
        print("请在打开的浏览器窗口中完成 U+ 登录（学号 / 密码 / 扫码均可）……")
        deadline = time.time() + args.timeout
        logged = False
        while time.time() < deadline:
            if session.is_logged_in():
                logged = True
                break
            time.sleep(2)
        if not logged:
            print("超时未检测到登录成功，未保存会话。", file=sys.stderr)
            return 2
        state_path = session.save_state()
        courses = client.list_my_courses()
        print(f"登录成功，会话已保存到 {state_path}")
        print(f"已识别 {len(courses)} 门课程：")
        for c in courses:
            print(f"  - {c.name}（{c.teacher}）")
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI entrypoint
        print(f"登录流程失败：{exc}", file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
