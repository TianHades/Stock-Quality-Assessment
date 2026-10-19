"""行业标签映射（01 号文档 §5.4）的单元测试。"""
from sqa.classify.industry_tag import tag_industry


def test_consumer_tags():
    assert tag_industry("白酒") == "consumer"
    assert tag_industry("白色家电") == "consumer"
    assert tag_industry("食品加工") == "consumer"
    assert tag_industry("纺织制造") == "consumer"
    assert tag_industry("旅游零售") == "consumer"
    # 金标准校准补充：家用电器≠家用+电器，"家电"不是其子串
    assert tag_industry("家用电器") == "consumer"  # 美的集团实际行业字符串


def test_tech_tags():
    assert tag_industry("半导体") == "tech"
    assert tag_industry("软件开发") == "tech"
    assert tag_industry("通信设备") == "tech"
    assert tag_industry("电池") == "tech"
    assert tag_industry("航空装备") == "tech"
    assert tag_industry("光学光电子") == "tech"
    # 金标准校准补充（复合词陷阱）：元器件≠元件+器、电信运营不含"通信"
    assert tag_industry("元器件") == "tech"      # 京东方实际行业字符串
    assert tag_industry("电信运营") == "tech"    # 中国移动实际行业字符串
    assert tag_industry("电气设备") == "tech"    # 宁德时代实际行业字符串
    # 2026-10 校准：宽词"服务"不再抢走科技行业；"电器仪表"归科技
    assert tag_industry("软件服务") == "tech"
    assert tag_industry("互联网") == "tech"
    assert tag_industry("电器仪表") == "tech"
    assert tag_industry("纺织机械") == "tech"
    assert tag_industry("机床制造") == "tech"


def test_consumer_compounds():
    assert tag_industry("乳制品") == "consumer"
    assert tag_industry("红黄酒") == "consumer"
    assert tag_industry("林业") == "consumer"
    assert tag_industry("日用化工") == "consumer"


def test_other_tags():
    assert tag_industry("电力") == "other"
    assert tag_industry("铁路公路") == "other"
    assert tag_industry("煤炭开采") == "other"  # 周期行业不因周期判定改变行业标签
    assert tag_industry("") == "other"


def test_cycle_style_keeps_industry_tag():
    # 行业标签独立于风格：牧原=周期(风格)+消费(标签)，与京东方=周期+科技同模式
    assert tag_industry("生猪养殖") == "consumer"
