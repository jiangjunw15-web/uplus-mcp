"""Cross-platform launcher and generic-browser config tests.

These cover the parts that must work on Windows / macOS / Linux without touching
the real network: port candidates, browser engine normalization, legacy config
compatibility and environment overrides.
"""

from __future__ import annotations

from uplus_mcp import launcher
from uplus_mcp.config import PROJECT_ROOT, load_config


# --------------------------------------------------------------------------- #
# port candidates
# --------------------------------------------------------------------------- #
def test_default_ports_primary_then_fallback_without_duplicates():
    assert launcher.default_ports(9333) == (9333, 9222)
    # When the primary already equals the fallback, the list collapses to one.
    assert launcher.default_ports(9222) == (9222,)


# --------------------------------------------------------------------------- #
# browser engine detection must not raise on any platform
# --------------------------------------------------------------------------- #
def test_find_browser_returns_none_or_a_pair_of_path_and_engine():
    found = launcher.find_browser("auto")
    if found is not None:
        exe, engine = found
        assert engine in ("edge", "chrome")
        assert exe


def test_find_browser_respects_engine_filter_without_crash():
    # Filtering to a browser that is not installed simply returns None; it must
    # not raise on any OS.
    launcher.find_browser("edge")
    launcher.find_browser("chrome")


def test_default_user_data_dir_is_a_path_when_probed():
    for engine in ("edge", "chrome"):
        path = launcher.default_user_data_dir(engine)
        assert path is not None
        assert str(path)


# --------------------------------------------------------------------------- #
# config: defaults, legacy field, environment overrides
# --------------------------------------------------------------------------- #
def test_default_browser_settings():
    cfg = load_config(None)
    assert cfg.browser == "auto"
    assert cfg.cdp_port == 9333
    assert cfg.auto_launch_browser is True
    # Legacy alias mirrors the generic flag for old callers.
    assert cfg.auto_launch_edge is True


def test_legacy_auto_launch_edge_field_is_mapped(tmp_path):
    cfg_file = tmp_path / "courses.json"
    cfg_file.write_text(
        '{"auto_launch_edge": false, "courses": []}', encoding="utf-8"
    )
    cfg = load_config(str(cfg_file))
    assert cfg.auto_launch_browser is False
    assert cfg.auto_launch_edge is False


def test_new_auto_launch_browser_takes_precedence_over_legacy(tmp_path):
    cfg_file = tmp_path / "courses.json"
    cfg_file.write_text(
        '{"auto_launch_browser": true, "auto_launch_edge": false, "courses": []}',
        encoding="utf-8",
    )
    cfg = load_config(str(cfg_file))
    assert cfg.auto_launch_browser is True


def test_invalid_browser_engine_falls_back_to_auto(tmp_path):
    cfg_file = tmp_path / "courses.json"
    cfg_file.write_text(
        '{"browser": "firefox", "courses": []}', encoding="utf-8"
    )
    cfg = load_config(str(cfg_file))
    assert cfg.browser == "auto"


def test_environment_overrides_port_and_browser(tmp_path, monkeypatch):
    cfg_file = tmp_path / "courses.json"
    cfg_file.write_text('{"courses": []}', encoding="utf-8")
    monkeypatch.setenv("UPLUS_CDP_PORT", "9222")
    monkeypatch.setenv("UPLUS_BROWSER", "chrome")
    cfg = load_config(str(cfg_file))
    assert cfg.cdp_port == 9222
    assert cfg.browser == "chrome"


def test_environment_profile_dir_override(monkeypatch, tmp_path):
    target = tmp_path / "my-profile"
    monkeypatch.setenv("UPLUS_PROFILE_DIR", str(target))
    cfg = load_config(None)
    assert cfg.profile_dir == str(target)
    assert launcher.dedicated_profile_dir() == target


def test_example_config_loads_new_browser_fields():
    cfg = load_config(str(PROJECT_ROOT / "config" / "courses.example.json"))
    assert cfg.browser == "auto"
    assert cfg.cdp_port == 9333
    assert len(cfg.courses) == 2
