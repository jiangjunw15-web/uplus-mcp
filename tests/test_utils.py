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


def test_strip_html_cleans_whitespace_and_entities():
    assert utils.strip_html("<p>先来先服务&nbsp;</p>") == "先来先服务"
    assert utils.strip_html(None) == ""


def test_deadline_labels():
    now = utils.now_ms()
    assert utils.deadline_label(now - 1000, now) == "已截止"
    label = utils.deadline_label(now + 2 * 3600_000, now)
    assert label.startswith("剩余 2.0 小时")
    assert utils.is_expired(now - 1000, now)
    assert not utils.is_expired(now + 1000, now)
