"""Auto-register the uplus MCP server in local AI client configs.

Why this exists
---------------
The package installs into a virtualenv (``D:\\u+小助手\\.venv``). The venv
Python and the ``uplus-mcp`` console script work, but the AI clients
(Claude Desktop, Cursor, Claude Code) do not know about them until an
``uplus`` entry is added to each client's MCP config. Without that entry
the server only runs when invoked manually from a terminal — the
"mcp 未注册，只能用本地 CLI" symptom.

This script writes the correct ``mcpServers.uplus`` block into each
client's config file, creating the file when it does not exist and
preserving every other key the user already has. It is idempotent:
running it twice produces the same config.

Usage
-----
    python scripts/register_mcp.py              # all detected clients
    python scripts/register_mcp.py --client claude-desktop
    python scripts/register_mcp.py --client cursor
    python scripts/register_mcp.py --client claude-code
    python scripts/register_mcp.py --dry-run    # show what would change

The script resolves paths from the environment (``%APPDATA%``, ``~/.cursor``,
``~/.claude``) and falls back to the documented per-OS locations. It never
touches a config file it cannot find or cannot parse — a broken JSON is
reported and skipped, not overwritten.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional


# --------------------------------------------------------------------------- #
# resolved install paths (venv python + console script)
# --------------------------------------------------------------------------- #
def _project_root() -> Path:
    """The directory containing ``pyproject.toml``.

    When running inside a git worktree (``.claude/worktrees/<name>/``), the
    worktree path is ephemeral and should not be used as the MCP server's
    ``cwd``. Detect this and return the main project root instead.
    """
    here = Path(__file__).resolve().parent
    candidate = here.parent

    # Check if we're in a worktree: look for ``.claude/worktrees`` in the path
    parts = candidate.parts
    for i, part in enumerate(parts):
        if part == "worktrees" and i >= 2 and parts[i - 1] == ".claude":
            # Found ``.claude/worktrees``; the main project root is two levels up
            return Path(*parts[: i - 1])

    return candidate


def _find_venv(start: Path) -> Optional[Path]:
    """Walk up from ``start`` looking for a ``.venv`` directory.

    A git worktree sits under ``<project>/.claude/worktrees/<name>/`` and has
    no venv of its own; the real venv lives at the project root. Walking up
    covers both the normal checkout (venv next to ``pyproject.toml``) and a
    worktree (venv two levels above the worktree, at the main project root).
    """
    exe_name = "python.exe" if sys.platform == "win32" else "python"
    cur = start.resolve()
    for _ in range(6):
        candidate = cur / ".venv"
        if candidate.is_dir() and (candidate / ("Scripts" if sys.platform == "win32" else "bin") / exe_name).exists():
            return candidate
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return None


def _venv_python() -> Path:
    """The venv Python that owns the ``uplus_mcp`` install.

    The script is normally invoked with the global interpreter (that is the
    whole point: the user cannot yet activate the venv from the AI client),
    so ``sys.executable`` is not useful. Resolve from the project root's
    ``.venv`` directory, walking up to handle git worktrees, and fall back
    to the running interpreter only when no venv is found.
    """
    venv = _find_venv(_project_root())
    if venv is not None:
        if sys.platform == "win32":
            return venv / "Scripts" / "python.exe"
        return venv / "bin" / "python"
    return Path(sys.executable)


def _venv_console_script() -> Optional[Path]:
    """The ``uplus-mcp`` console script, when installed."""
    root = _project_root()
    if sys.platform == "win32":
        candidate = root / ".venv" / "Scripts" / "uplus-mcp.exe"
    else:
        candidate = root / ".venv" / "bin" / "uplus-mcp"
    return candidate if candidate.exists() else None


# --------------------------------------------------------------------------- #
# per-client config paths
# --------------------------------------------------------------------------- #
def _claude_desktop_config() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        return base / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def _cursor_config() -> Path:
    return Path.home() / ".cursor" / "mcp.json"


def _claude_code_settings() -> Path:
    return Path.home() / ".claude" / "settings.json"


# --------------------------------------------------------------------------- #
# the uplus entry we write
# --------------------------------------------------------------------------- #
def _uplus_entry(*, use_console_script: bool) -> dict:
    """The ``mcpServers.uplus`` block.

    ``use_console_script=True`` uses the shorter ``uplus-mcp`` entry; it is
    preferred when the venv is activated or on PATH. ``False`` uses the
    venv Python absolute path, which works from any cwd without activation.
    """
    root = _project_root()
    if use_console_script:
        script = _venv_console_script()
        if script is None:
            return _uplus_entry(use_console_script=False)
        return {
            "command": str(script),
            "cwd": str(root),
            "env": {"UPLUS_CONFIG": "config/courses.json"},
        }
    return {
        "command": str(_venv_python()),
        "args": ["-m", "uplus_mcp.server"],
        "cwd": str(root),
        "env": {"UPLUS_CONFIG": "config/courses.json"},
    }


# --------------------------------------------------------------------------- #
# config file editing
# --------------------------------------------------------------------------- #
def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{path}: 无法读取或解析（{exc}）") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{path}: 顶层不是 JSON 对象，拒绝覆盖")
    return data


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    path.write_text(payload + "\n", encoding="utf-8")


def _apply_entry(data: dict, key: str, entry: dict) -> bool:
    """Write ``entry`` under ``data.mcpServers[key]``; return True when changed."""
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        data["mcpServers"] = servers
    existing = servers.get(key)
    if existing == entry:
        return False
    servers[key] = entry
    return True


# --------------------------------------------------------------------------- #
# per-client registration
# --------------------------------------------------------------------------- #
CLIENTS = {
    "claude-desktop": {
        "label": "Claude Desktop",
        "config": _claude_desktop_config,
        "restart_hint": "重启 Claude Desktop 后生效。",
    },
    "cursor": {
        "label": "Cursor",
        "config": _cursor_config,
        "restart_hint": "重启 Cursor（或重新打开项目）后生效。",
    },
    "claude-code": {
        "label": "Claude Code",
        "config": _claude_code_settings,
        "restart_hint": "重启 Claude Code 后生效。",
    },
}


def register_client(name: str, *, dry_run: bool = False,
                    use_console_script: bool = False) -> str:
    """Register ``uplus`` in one client's config. Returns a status line."""
    spec = CLIENTS[name]
    path = spec["config"]()
    entry = _uplus_entry(use_console_script=use_console_script)
    try:
        data = _read_json(path)
    except RuntimeError as exc:
        return f"  {spec['label']}: 跳过 — {exc}"
    changed = _apply_entry(data, "uplus", entry)
    if dry_run:
        action = "会写入" if changed else "已是最新（无需改动）"
        return f"  {spec['label']} ({path}): {action}"
    if not changed:
        return f"  {spec['label']} ({path}): 已是最新"
    _write_json(path, data)
    return f"  {spec['label']} ({path}): 已写入 uplus 条目。{spec['restart_hint']}"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="把 uplus MCP 服务注册到本地 AI 客户端（Claude Desktop / Cursor / Claude Code）。"
    )
    parser.add_argument(
        "--client", choices=list(CLIENTS), default=None,
        help="只注册指定的客户端；默认注册全部检测到的客户端。",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只打印将要写入的内容，不实际修改文件。",
    )
    parser.add_argument(
        "--use-console-script", action="store_true",
        help="优先使用 uplus-mcp 控制台入口（需要 venv 在 PATH 或激活）。",
    )
    args = parser.parse_args(argv)

    print(f"项目根目录: {_project_root()}")
    print(f"虚拟环境 Python: {_venv_python()}")
    script = _venv_console_script()
    print(f"控制台入口: {script or '（未安装，使用 python -m uplus_mcp.server）'}")
    print()

    targets = [args.client] if args.client else list(CLIENTS)
    for name in targets:
        print(register_client(name, dry_run=args.dry_run,
                              use_console_script=args.use_console_script))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
