"""Locate / launch a desktop browser so CDP is actually reachable.

The problem this solves
------------------------
Chromium >= 136 (and therefore Edge 136+, Chrome 136+) ignores
``--remote-debugging-port`` when ``--user-data-dir`` is the *default* profile
directory. Launching

    msedge.exe --remote-debugging-port=9333

on the default profile simply never opens the port, which is why most users hit
"cannot attach" even though they followed instructions.

The workaround is to start the browser against a *dedicated* profile copy:

    msedge.exe --user-data-dir=<copy> --remote-debugging-port=9333

Cookies survive the copy because Edge/Chrome cookie encryption is bound to the
user account, not to the directory path, so the same user opening the copy can
still decrypt them. The copy is made once and reused on later runs.

Cross-platform
--------------
Supported engines: ``edge``, ``chrome``, and ``auto`` (whichever is installed
first). Candidate executable paths are probed per OS; ``chromium`` reserved for
the Playwright bundled kernel (install with ``playwright install chromium``).
No hard-coded Windows-only path remains in the active lookup.
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

DEFAULT_CDP_PORT = 9333
FALLBACK_CDP_PORT = 9222

# Profile directory name used under the user home when none is configured.
PROFILE_COPY_DIRNAME = "uplus-browser-profile"
# Older releases named the copy ``uplus-edge-profile``; reuse it when it still
# exists so an already-copied session keeps working after upgrade.
LEGACY_PROFILE_DIRNAME = "uplus-edge-profile"

# Directories that hold hundreds of MB of caches and must not be copied.
CACHE_DIRS = {
    "Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache",
    "GrShaderCache", "ShaderCache", "GraphiteDawnCache", "Crashpad",
    "Service Worker", "component_crx_cache", "Safe Browsing",
}


# --------------------------------------------------------------------------- #
# candidate executables per OS and engine
# --------------------------------------------------------------------------- #
def _windows_candidates(kind: str) -> list[str]:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    prog = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    progx86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    common: dict[str, list[Path]] = {}
    if kind in ("auto", "edge"):
        common.setdefault("edge", []).extend([
            progx86 / "Microsoft/Edge/Application/msedge.exe",
            prog / "Microsoft/Edge/Application/msedge.exe",
        ])
    if kind in ("auto", "chrome"):
        common.setdefault("chrome", []).extend([
            prog / "Google/Chrome/Application/chrome.exe",
            progx86 / "Google/Chrome/Application/chrome.exe",
            base / "Google/Chrome/Application/chrome.exe",
        ])
    out: list[str] = []
    for candidates in common.values():
        out.extend(str(p) for p in candidates if p.exists())
    return out


def _macos_candidates(kind: str) -> list[str]:
    out: list[str] = []
    if kind in ("auto", "edge"):
        edge = Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
        if edge.exists():
            out.append(str(edge))
    if kind in ("auto", "chrome"):
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if chrome.exists():
            out.append(str(chrome))
    return out


def _linux_candidates(kind: str) -> list[str]:
    paths: dict[str, list[str]] = {
        "edge": ["/usr/bin/microsoft-edge", "/usr/bin/microsoft-edge-stable",
                 "/opt/microsoft/msedge/msedge"],
        "chrome": ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
                   "/opt/google/chrome/chrome"],
    }
    out: list[str] = []
    for engine, candidates in paths.items():
        if kind in ("auto", engine):
            out.extend(p for p in candidates if Path(p).exists())
    return out


def _which_candidates(kind: str) -> list[str]:
    names: dict[str, list[str]] = {
        "edge": ["msedge", "microsoft-edge", "microsoft-edge-stable"],
        "chrome": ["google-chrome", "google-chrome-stable", "chrome", "chromium",
                   "chromium-browser"],
    }
    out: list[str] = []
    for engine, names in names.items():
        if kind in ("auto", engine):
            for name in names:
                found = shutil.which(name)
                if found:
                    out.append(found)
    return out


def find_browser(kind: str = "auto") -> Optional[tuple[str, str]]:
    """Return ``(executable_path, engine)`` for the first installed browser.

    ``engine`` is ``edge`` or ``chrome``. Returns ``None`` when nothing matches.
    """
    platform = sys.platform
    if platform == "win32":
        exe_paths = _windows_candidates(kind)
    elif platform == "darwin":
        exe_paths = _macos_candidates(kind)
    else:
        exe_paths = _linux_candidates(kind)
    exe_paths.extend(_which_candidates(kind))

    for exe in exe_paths:
        engine = "edge" if "edge" in Path(exe).name.lower() else "chrome"
        return exe, engine
    return None


# --------------------------------------------------------------------------- #
# ports
# --------------------------------------------------------------------------- #
def default_ports(primary: int = DEFAULT_CDP_PORT) -> tuple[int, ...]:
    """Primary port first, then the fallback; duplicates removed."""
    ports: list[int] = []
    for port in (primary, FALLBACK_CDP_PORT):
        if port not in ports:
            ports.append(port)
    return tuple(ports)


def cdp_alive(port: int, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=timeout
        ) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001 - any failure means "not ready"
        return False


def first_live_port(primary: int = DEFAULT_CDP_PORT) -> Optional[int]:
    for port in default_ports(primary):
        if cdp_alive(port):
            return port
    return None


# --------------------------------------------------------------------------- #
# profile directories (cross-platform)
# --------------------------------------------------------------------------- #
def default_user_data_dir(engine: str = "chrome") -> Optional[Path]:
    """Default profile root for the installed browser, or ``None`` when absent."""
    home = Path.home()
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
        sub = "Microsoft/Edge/User Data" if engine == "edge" else "Google/Chrome/User Data"
        return Path(local) / sub
    if sys.platform == "darwin":
        sub = ("Library/Application Support/Microsoft Edge" if engine == "edge"
               else "Library/Application Support/Google/Chrome")
        return home / sub
    # Linux
    sub = ".config/microsoft-edge" if engine == "edge" else ".config/google-chrome"
    return home / sub


def dedicated_profile_dir(profile_root: Optional[Path] = None) -> Path:
    """Where the CDP-enabled copy of the profile lives."""
    if profile_root is not None:
        return Path(profile_root)
    env_dir = os.environ.get("UPLUS_PROFILE_DIR")
    if env_dir:
        return Path(env_dir)
    home = Path.home()
    legacy = home / LEGACY_PROFILE_DIRNAME
    if legacy.exists():
        return legacy
    return home / PROFILE_COPY_DIRNAME


def _copy_profile(src: Path, dst: Path) -> None:
    """Copy the browser profile tree, skipping caches that are large and useless."""
    def ignore(directory: str, names: list[str]) -> set[str]:
        return {n for n in names if n in CACHE_DIRS or n.endswith(".tmp")}

    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)


def ensure_profile_copy(force: bool = False, profile_root: Optional[Path] = None,
                        engine: str = "chrome") -> Path:
    """Materialise the CDP profile copy once and return its path.

    When the source default profile does not exist yet (first run on this
    machine), the dedicated directory is created empty instead of raising:
    the user signs in once through this fresh profile on first launch.
    """
    target = dedicated_profile_dir(profile_root)
    marker = target / "Local State"
    if marker.exists() and not force:
        return target
    source = default_user_data_dir(engine)
    if source and source.exists():
        _copy_profile(source, target)
    else:
        target.mkdir(parents=True, exist_ok=True)
    return target


# --------------------------------------------------------------------------- #
# help text per OS
# --------------------------------------------------------------------------- #
def _manual_launch_hint() -> str:
    if sys.platform == "win32":
        exe = '"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"'
        return (
            "B. 手动启动：完全关闭浏览器后运行\n"
            f"   {exe} --user-data-dir=<任意空目录> --remote-debugging-port={DEFAULT_CDP_PORT}\n"
            "   然后在该窗口登录 eduplus.net，再重试。"
        )
    if sys.platform == "darwin":
        return (
            "B. 手动启动：完全关闭浏览器后运行\n"
            '   open -a "Microsoft Edge" --args '
            f"--user-data-dir=<任意空目录> --remote-debugging-port={DEFAULT_CDP_PORT}\n"
            "   然后在该窗口登录 eduplus.net，再重试。"
        )
    return (
        "B. 手动启动：完全关闭浏览器后运行\n"
        f"   microsoft-edge --user-data-dir=<任意空目录> "
        f"--remote-debugging-port={DEFAULT_CDP_PORT}\n"
        "   然后在该窗口登录 eduplus.net，再重试。"
    )


def attach_help_text(primary: int = DEFAULT_CDP_PORT) -> str:
    ports = "/".join(str(p) for p in default_ports(primary))
    return (
        f"无法自动连接到浏览器调试端口（{ports}）。可尝试以下任一方式：\n"
        "A. 让插件自动启动（推荐）：调用 browse_launch_edge 工具，或运行\n"
        "   python -m uplus_mcp.launcher --launch\n"
        "   它会复制一份 profile 到独立目录并用调试端口启动浏览器，登录态保留。\n"
        + _manual_launch_hint() +
        "\n说明：Chromium 136+ 会忽略默认用户目录上的调试端口，因此必须用独立目录。\n"
        "未检测到 Edge/Chrome 时，请先安装任一桌面浏览器，或执行 "
        "`playwright install chromium`。"
    )


ATTACH_HELP_TEXT = attach_help_text(DEFAULT_CDP_PORT)


# --------------------------------------------------------------------------- #
# launch
# --------------------------------------------------------------------------- #
def launch_browser_with_cdp(
    port: int = DEFAULT_CDP_PORT,
    *,
    profile_root: Optional[Path] = None,
    kind: str = "auto",
    url: str = "https://www.eduplus.net/workbench/index/school",
    wait_seconds: float = 20.0,
    refresh_profile: bool = False,
) -> dict:
    """Start an installed browser against a dedicated profile with the port on.

    Returns a small status dict. Raises ``RuntimeError`` when no supported
    browser is found or the port never opens.
    """
    if cdp_alive(port):
        return {"launched": False, "port": port, "reason": "端口已在监听，直接复用"}

    found = find_browser(kind)
    if not found:
        raise RuntimeError(
            "未检测到 Microsoft Edge 或 Google Chrome。请安装任一桌面浏览器，或运行\n"
            "  playwright install chromium\n"
            "完成后重试；也可以手动用 --remote-debugging-port="
            f"{port} --user-data-dir=<任意目录> 启动浏览器后接入。"
        )
    exe, engine = found
    profile = ensure_profile_copy(force=refresh_profile, profile_root=profile_root,
                                  engine=engine)

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
            return {"launched": True, "port": port, "profile": str(profile),
                    "exe": exe, "engine": engine}
        time.sleep(0.5)

    raise RuntimeError(
        f"已经启动浏览器（profile={profile}），但 {wait_seconds:.0f} 秒内端口 {port} 仍未监听。\n"
        "请确认没有其它浏览器进程占用同一 profile，或关闭浏览器后重试。"
    )


def ensure_browser_ready(
    port: int = DEFAULT_CDP_PORT,
    *,
    auto_launch: bool = True,
    profile_root: Optional[Path] = None,
    kind: str = "auto",
) -> Optional[int]:
    """Make a CDP endpoint available; return its port, or ``None`` on failure.

    Order of preference:
      1. an already-listening debug port (primary / fallback) — reuse the browser;
      2. an automatic browser launch against the dedicated profile copy.
    """
    live = first_live_port(port)
    if live:
        return live
    if not auto_launch:
        return None
    try:
        info = launch_browser_with_cdp(port=port, profile_root=profile_root, kind=kind)
        return int(info["port"])
    except Exception:  # noqa: BLE001 - caller raises the friendly help text
        return None


def main(argv: Optional[list[str]] = None) -> int:
    """CLI: ``python -m uplus_mcp.launcher [--launch] [--port 9333] [--browser auto]``."""
    import argparse

    parser = argparse.ArgumentParser(description="启动带 CDP 调试端口的桌面浏览器")
    parser.add_argument("--launch", action="store_true", help="复制 profile 并启动浏览器")
    parser.add_argument("--port", type=int, default=DEFAULT_CDP_PORT, help="调试端口")
    parser.add_argument("--browser", default="auto",
                        choices=["auto", "edge", "chrome"], help="优先浏览器引擎")
    parser.add_argument("--refresh-profile", action="store_true", help="重新复制 profile")
    args = parser.parse_args(argv)

    if not args.launch:
        live = first_live_port(args.port)
        print(f"当前可用的调试端口：{live if live else '无'}")
        return 0 if live else 1
    try:
        info = launch_browser_with_cdp(
            port=args.port, kind=args.browser, refresh_profile=args.refresh_profile
        )
        print(f"浏览器已就绪：{info}")
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI entrypoint
        print(f"启动失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
