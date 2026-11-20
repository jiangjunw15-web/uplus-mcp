from uplus_mcp import utils


def test_normalize_letters_sorts_dedupes_uppercases():
    assert utils.normalize_letters("b,c,d") == "BCD"
    assert utils.normalize_letters(" A C D ") == "ACD"
    assert utils.normalize_letters("dca") == "ACD"
    assert utils.normalize_letters(None) == ""


def test_normalize_bool_handles_text_and_cn():
    assert utils.normalize_bool("true") is True
    assert utils.normalize_bool("错误") is False
    assert utils.normalize_bool("yes") is True
    assert utils.normalize_bool("garbage") is None
    assert utils.normalize_bool(None) is None


def test_parse_bool_reads_strings_numbers_and_chinese():
    assert utils.parse_bool(True) is True
    assert utils.parse_bool("false") is False
    assert utils.parse_bool("0") is False
    assert utils.parse_bool(0) is False
    assert utils.parse_bool("true") is True
    assert utils.parse_bool("1") is True
    assert utils.parse_bool("错误") is False
    assert utils.parse_bool("正确") is True
    assert utils.parse_bool(None) is None
    assert utils.parse_bool("") is None
    assert utils.parse_bool("not-a-flag") is None


def test_coerce_bool_uses_the_caller_default_only_when_unparseable():
    assert utils.coerce_bool("false", True) is False
    assert utils.coerce_bool("0", True) is False
    assert utils.coerce_bool(None, True) is True
    assert utils.coerce_bool("garbage", False) is False


def test_strip_html_cleans_whitespace_and_entities():
    assert utils.strip_html("<p>先来先服务&nbsp;</p>") == "先来先服务"
    assert utils.strip_html(None) == ""


def test_safe_filename_removes_paths_and_windows_device_names():
    assert utils.safe_filename("../../evil.docx") == "_.._evil.docx"
    assert utils.safe_filename("CON.docx") == "_CON.docx"
    assert utils.safe_filename("nul") == "_nul"
    assert utils.safe_filename('a:b*c?.txt') == "a_b_c_.txt"
    assert utils.safe_filename("报告.docx...") == "报告.docx"
    assert utils.safe_filename("", "fallback") == "fallback"
    assert utils.safe_filename(".", "fallback") == "fallback"
    assert len(utils.safe_filename("x" * 200, "fallback")) == 120


def test_deadline_labels():
    now = utils.now_ms()
    assert utils.deadline_label(now - 1000, now) == "已截止"
    label = utils.deadline_label(now + 2 * 3600_000, now)
    assert label.startswith("剩余 2.0 小时")
    assert utils.is_expired(now - 1000, now)
    assert not utils.is_expired(now + 1000, now)
