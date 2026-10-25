"""Locate / launch Edge so CDP is actually reachable.

The problem this solves
-----------------------
Chromium >= 136 (and therefore Edge 136+) ignores ``--remote-debugging-port``
when ``--user-data-dir`` is the *default* profile directory. Launching

    msedge.exe --remote-debugging-port=9333

simply never opens port 9333, which is why ``browser.ATTACH_HELP`` used to fail
for most users even though they followed it.

The workaround is to start Edge against a *dedicated* copy of the profile:

    msedge.exe --user-data-dir=<copy> --remote-debugging-port=9333

Cookies survive the copy because Edge's cookie encryption is bound to the user
account, not to the directory path, so the same user opening the copy can still
decrypt them. The copy is made once and reused on later runs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

CDP_PORTS = (9333, 9222)
EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
PROFILE_COPY_DIRNAME = "uplus-edge-profile"

ATTACH_HELP_TEXT = (
    "无法自动连接到 Edge 调试端口（9333/9222）。可尝试以下任一方式：\n"
    "A. 让插件自动启动（推荐）：调用 browse_launch_edge 工具，或运行\n"
    "   python -m uplus_mcp.launcher --launch\n"
    "   它会复制一份 profile 到独立目录并用调试端口启动 Edge，登录态保留。\n"
    "B. 手动启动：完全退出 Edge 后运行\n"
    '   "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" '
    "--user-data-dir=<某个空目录> --remote-debugging-port=9333\n"
    "   然后在该窗口登录 eduplus.net，再重试。\n"
    "说明：Edge 136+ 会忽略默认用户目录上的调试端口，因此必须用独立目录。"
)

# Directories that hold hundreds of MB of caches and must not be copied.
CACHE_DIRS = {
    "Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache",
    "GrShaderCache", "ShaderCache", "GraphiteDawnCache", "Crashpad",
    "Service Worker", "component_crx_cache", "Safe Browsing",
}


def find_edge() -> Optional[str]:
    for path in EDGE_CANDIDATES:
        if Path(path).exists():
            return path
    return None


def cdp_alive(port: int, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001 - any failure means "not ready"
        return False


def first_live_port() -> Optional[int]:
    for port in CDP_PORTS:
        if cdp_alive(port):
            return port
    return None


def default_user_data_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "Microsoft" / "Edge" / "User Data"


def dedicated_profile_dir(profile_root: Optional[Path] = None) -> Path:
    """Where the CDP-enabled copy of the profile lives."""
    if profile_root is not None:
        return Path(profile_root)
    return Path(os.environ.get("UPLUS_PROFILE_DIR") or (Path.home() / PROFILE_COPY_DIRNAME))


def _copy_profile(src: Path, dst: Path) -> None:
    """Copy the Edge profile tree, skipping caches that are large and useless."""
    def ignore(directory: str, names: list[str]) -> set[str]:
        return {n for n in names if n in CACHE_DIRS or n.endswith(".tmp")}

    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)


def ensure_profile_copy(force: bool = False, profile_root: Optional[Path] = None) -> Path:
    """Materialise the CDP profile copy once and return its path."""
    target = dedicated_profile_dir(profile_root)
    marker = target / "Local State"
    if marker.exists() and not force:
        return target
    source = default_user_data_dir()
    if not source.exists():
        raise RuntimeError(
            f"找不到 Edge 用户数据目录：{source}。请先正常启动一次 Edge 并登录 U+。"
        )
    _copy_profile(source, target)
    return target


def launch_edge_with_cdp(
    port: int = 9333,
    *,
    profile_root: Optional[Path] = None,
    url: str = "https://www.eduplus.net/workbench/index/school",
    wait_seconds: float = 20.0,
    refresh_profile: bool = False,
) -> dict:
    """Start Edge against a dedicated profile with the debug port enabled.

    Returns a small status dict. Raises ``RuntimeError`` when Edge is missing or
    the port never opens.
    """
    if cdp_alive(port):
        return {"launched": False, "port": port, "reason": "端口已在监听，直接复用"}

    exe = find_edge()
    if not exe:
        raise RuntimeError(
            "未找到 Microsoft Edge。请安装 Edge，或手动用 "
            "--remote-debugging-port=9333 --user-data-dir=<任意目录> 启动后重试。"
        )

    profile = ensure_profile_copy(force=refresh_profile, profile_root=profile_root)

    # A running Edge instance ignores the debug flag, so refuse to fight it.
    args = [
        exe,
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(args, creationflags=creationflags, close_fds=True)

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if cdp_alive(port):
            return {"launched": True, "port": port, "profile": str(profile), "exe": exe}
        time.sleep(0.5)

    raise RuntimeError(
        f"已经启动 Edge（profile={profile}），但 {wait_seconds:.0f} 秒内端口 {port} 仍未监听。\n"
        "请确认没有其它 Edge 进程占用同一 profile，或关闭 Edge 后重试。"
    )


def ensure_browser_ready(
    port: int = 9333,
    *,
    auto_launch: bool = True,
    profile_root: Optional[Path] = None,
) -> Optional[int]:
    """Make a CDP endpoint available; return its port, or ``None`` on failure.

    Order of preference:
      1. an already-listening debug port (9333/9222) — reuse the user's browser;
      2. an automatic Edge launch against the dedicated profile copy.
    """
    live = first_live_port()
    if live:
        return live
    if not auto_launch:
        return None
    try:
        info = launch_edge_with_cdp(port=port, profile_root=profile_root)
        return int(info["port"])
    except Exception:  # noqa: BLE001 - caller raises the friendly ATTACH_HELP
        return None


def main(argv: Optional[list[str]] = None) -> int:
    """CLI: ``python -m uplus_mcp.launcher [--launch] [--port 9333]``."""
    import argparse

    parser = argparse.ArgumentParser(description="启动带 CDP 调试端口的 Edge")
    parser.add_argument("--launch", action="store_true", help="复制 profile 并启动 Edge")
    parser.add_argument("--port", type=int, default=9333, help="调试端口，默认 9333")
    parser.add_argument("--refresh-profile", action="store_true", help="重新复制 profile")
    args = parser.parse_args(argv)

    if not args.launch:
        live = first_live_port()
        print(f"当前可用的调试端口：{live if live else '无'}")
        return 0 if live else 1
    try:
        info = launch_edge_with_cdp(port=args.port, refresh_profile=args.refresh_profile)
        print(f"Edge 已就绪：{info}")
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI entrypoint
        print(f"启动失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
