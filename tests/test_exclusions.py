"""前置排除规则（01 号文档 §2）的单元测试。"""
from sqa.classify.exclusions import exclusion_reason


def test_st_name_excluded():
    assert exclusion_reason(name="ST康美", industry="中药") is not None
    assert exclusion_reason(name="*ST新海", industry="光伏设备") is not None
    assert exclusion_reason(name="退市未来", industry="综合") is not None


def test_pharma_industry_excluded():
    assert exclusion_reason(name="恒瑞医药", industry="化学制药") is not None
    assert exclusion_reason(name="药明康德", industry="医疗研发外包") is not None
    assert exclusion_reason(name="智飞生物", industry="生物制品") is not None
    # 复合词陷阱："中成药"不含"中药"子串（68 家公司曾漏网）
    assert exclusion_reason(name="某中药公司", industry="中成药") is not None


def test_finance_industry_excluded():
    assert exclusion_reason(name="招商银行", industry="银行") is not None
    assert exclusion_reason(name="中信证券", industry="证券") is not None
    assert exclusion_reason(name="中国人寿", industry="保险") is not None


def test_normal_stock_not_excluded():
    assert exclusion_reason(name="贵州茅台", industry="白酒") is None
    assert exclusion_reason(name="牧原股份", industry="生猪养殖") is None
