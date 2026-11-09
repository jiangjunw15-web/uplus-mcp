"""题干定位页面题号的单测（页面题序与接口题序不一致的场景）"""
from uplus_mcp.operator import stem_position, text_key


def test_text_key_strips_spaces_and_fullwidth_punctuation():
    assert text_key("开闭原则的含义是一个软件实体（ ）") == "开闭原则的含义是一个软件实体"
    assert text_key("A、开闭原则") == "A开闭原则"
    assert text_key("") == ""


def test_exact_stem_hit_wins():
    positions = {"逻辑内聚的实践原则是根据需求看是否要放到一个模块": 3}
    assert stem_position(positions, "逻辑内聚的实践原则是根据需求看是否要放到一个模块") == 3


def test_page_order_differs_from_api_order():
    # 页面第 3 位是判断题，接口 order 3 是单选题：按题干定位才不会点错题。
    positions = {
        "不应该强迫客户依赖于它们不用的方法是关于的表述A开闭原则B接口隔离原则": 1,
        "下面关于里氏替换原则的描述正确的是": 2,
        "逻辑内聚的实践原则是根据需求看是否要放到一个模块": 3,
    }
    assert stem_position(positions, "逻辑内聚的实践原则是根据需求看是否要放到一个模块") == 3
    assert stem_position(positions, "下面关于里氏替换原则的描述正确的是？") == 2


def test_longest_shared_prefix_wins():
    positions = {
        "以下关于单一职责原则的叙述不正确的是A单一职责原则提高了类之间的耦合性": 6,
        "以下关于依赖倒置原则的叙述不正确的是A依赖倒置原则的简称是DIP": 4,
    }
    assert stem_position(positions, "以下关于依赖倒置原则的叙述不正确的是（ ）") == 4
    assert stem_position(positions, "以下关于单一职责原则的叙述不正确的是（ ）") == 6


def test_unknown_stem_returns_none():
    positions = {"完全无关的题干内容": 1}
    assert stem_position(positions, "另一道毫不相干的题目") is None
    assert stem_position(positions, "") is None
