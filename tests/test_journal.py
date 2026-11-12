"""Local answer journal used by the completeness audit and the repair flow."""

from uplus_mcp.config import Config
from uplus_mcp import journal


def test_journal_roundtrip_and_empty_filter(tmp_path):
    cfg = Config(report_dir=str(tmp_path))
    journal.record(cfg, task_id="t1", homework_id="h1", name="作业",
                   answers={"q1": "A", "q2": "AB", "q3": ""})
    assert journal.answers_for(cfg, "t1", "h1") == {"q1": "A", "q2": "AB"}
    assert journal.answers_for(cfg, "t1", "another-homework") == {}


def test_journal_survives_a_corrupt_file(tmp_path):
    cfg = Config(report_dir=str(tmp_path))
    path = journal.journal_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert journal.answers_for(cfg, "t1", "h1") == {}
    journal.record(cfg, task_id="t1", homework_id="h1", name="作业",
                   answers={"q1": "A"})
    assert journal.answers_for(cfg, "t1", "h1") == {"q1": "A"}
