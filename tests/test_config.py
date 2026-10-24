from pathlib import Path

from uplus_mcp.config import EXAMPLE_CONFIG_PATH, load_config


def test_default_config_has_empty_courses_without_file():
    cfg = load_config(None)
    assert cfg.base_url == "https://www.eduplus.net"
    assert cfg.headless is True
    # Personal courses live in the gitignored config; default is empty and the
    # engine falls back to the live account course list.
    assert cfg.courses == []


def test_example_config_loads_placeholder_courses():
    cfg = load_config(str(EXAMPLE_CONFIG_PATH))
    assert cfg.deadline_warning_hours == 72
    assert len(cfg.courses) == 2
    assert all(c.id.startswith("replace-with-your-course-id") for c in cfg.courses)


def test_resolve_paths_are_relative_to_project_root():
    cfg = load_config(None)
    report = cfg.report_folder
    assert isinstance(report, Path)
    assert report.name == "reports"
