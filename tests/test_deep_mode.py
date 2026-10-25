"""深度模式离线测试：证据抽取、规则判定、PDF 下载/文本工具（不联网）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from sqa.minesweeper.deep import extract as E
from sqa.minesweeper.deep import rules as R
from sqa.minesweeper.models import MsVerdict
from sqa.minesweeper.deep import pdf_download as D


# ---------- 通用片段检索 / 数值解析 ----------

def test_snippets_and_numbers():
    pages = ["销售费用明细：运输费 1,234,567.00 1,111,111.00，同比增长 11%",
             "无关键信息"]
    hits = E.snippets(pages, ("运输费",), max_hits=3)
    assert len(hits) == 1 and hits[0]["page"] == 1
    values = E.numbers(hits[0]["snippet"], min_abs=10)
    assert values[:2] == [1234567.0, 1111111.0]
    assert 11.0 not in values  # 百分比不参与数值抽取


# ---------- 1.3 运费 ----------

def test_extract_freight_and_rule():
    pages = ["销售费用附注：运输费 1,200.00 1,000.00"]
    freight = E.extract_freight(pages)
    assert freight["status"] == "ok"
    assert freight["growth"] == pytest.approx(0.2)
    # 营收增速 40% → 运费增速/营收增速 = 0.5 → PASS（边界）
    assert R.rule_1_3(freight, 0.4).verdict is MsVerdict.PASS
    # 营收增速 100% → ratio 0.2 <25% → FAIL
    assert R.rule_1_3(freight, 1.0).verdict is MsVerdict.FAIL
    # 营收增速 50% → ratio 0.4 → WARN
    assert R.rule_1_3(freight, 0.5).verdict is MsVerdict.WARN
    # 营收增速 ≤0 → SKIP
    assert R.rule_1_3(freight, -0.1).verdict is MsVerdict.SKIP


def test_extract_freight_missing_or_ambiguous():
    assert E.extract_freight(["本页没有披露物流支出"])["status"] == "missing"
    ambiguous = E.extract_freight(["运输费系物流支出，详见附注"])
    assert ambiguous["status"] in ("ambiguous", "missing")
    assert R.rule_1_3(ambiguous, 0.2).verdict is MsVerdict.SKIP


def test_extract_freight_table_layout():
    """实盘版式：运输费 金额 占比 金额 占比（茅台 2025 年报抽取形态）。"""
    pages = ["销售费用 运输费 326,866,386.45 2.21 311,524,380.58 2.29 4.92"]
    data = E.extract_freight(pages)
    assert data["status"] == "ok"
    assert data["current"] == pytest.approx(326866386.45)
    assert data["previous"] == pytest.approx(311524380.58)
    assert data["growth"] == pytest.approx(0.0492, abs=1e-4)


# ---------- 5.5 客户集中度 ----------

def test_extract_customer_concentration_and_rule():
    pages = ["前五名客户销售额合计占年度销售总额的 25.3%，第一大客户占比 8.1%。"]
    data = E.extract_customer_concentration(pages)
    assert data["status"] == "ok"
    assert data["total"] == pytest.approx(25.3)
    assert R.rule_5_5(data).verdict is MsVerdict.PASS

    high = E.extract_customer_concentration(
        ["前五名客户销售额合计占年度销售总额的 62.0%。"])
    assert R.rule_5_5(high).verdict is MsVerdict.WARN

    single = E.extract_customer_concentration(
        ["前五名客户合计占比 55.0%，第一大客户占比 51.2%。"])
    assert R.rule_5_5(single).verdict is MsVerdict.FAIL

    mid = E.extract_customer_concentration(
        ["前五名客户合计占比 40.0%。"])
    assert R.rule_5_5(mid).verdict is MsVerdict.SKIP


# ---------- 5.3 / 5.4 人员变动 ----------

def test_cfo_and_independent_director():
    cfo_found = E.extract_cfo_changes(["财务负责人王某某因个人原因离任。"])
    assert cfo_found["status"] == "found"
    assert R.rule_5_3(cfo_found).verdict is MsVerdict.WARN
    cfo_clear = E.extract_cfo_changes(["公司财务负责人未发生变动。"])
    assert cfo_clear["status"] == "clear"
    assert R.rule_5_3(cfo_clear).verdict is MsVerdict.PASS
    # 实盘版式：表格里“财务总监 … 聘任”，变动词只在附近出现
    table = E.extract_cfo_changes(
        ["余思明 财务总监、代行董事会秘书职责 聘任 工作调动 张德芹 董事长、董事 离任"])
    assert table["status"] == "found"

    ind = E.extract_independent_director(["独立董事李某辞职。"])
    assert ind["status"] == "found"
    assert R.rule_5_4(ind).verdict is MsVerdict.WARN


# ---------- 5.6 跨行业收购 ----------

def test_cross_industry():
    found = E.extract_cross_industry(["报告期内公司实施跨行业收购。"])
    assert R.rule_5_6(found).verdict is MsVerdict.WARN
    ambiguous = E.extract_cross_industry(["报告期内公司收购某公司 30% 股权。"])
    assert R.rule_5_6(ambiguous).verdict is MsVerdict.SKIP
    # “跨行业项目/标杆项目”不是收购信号（美的实盘误报案例）
    project = E.extract_cross_industry(["公司成功落地一批跨行业标杆项目，覆盖多个领域。"])
    assert R.rule_5_6(project).verdict is MsVerdict.SKIP


# ---------- 5.9 监管处罚 ----------

def test_regulatory():
    fail = E.extract_regulatory(["公司控股股东被证监会立案调查。"])
    assert fail["status"] == "fail"
    assert R.rule_5_9(fail).verdict is MsVerdict.FAIL

    warn = E.extract_regulatory(["公司收到证监局警示函。"])
    assert warn["status"] == "warn"
    assert R.rule_5_9(warn).verdict is MsVerdict.WARN

    clear = E.extract_regulatory(["公司不存在应披露的监管措施。"])
    assert clear["status"] == "clear"
    assert R.rule_5_9(clear).verdict is MsVerdict.PASS


# ---------- 6.2 研发资本化 ----------

def test_rd_capitalization():
    data = E.extract_rd_capitalization(["报告期研发投入资本化的比重 32.5%"])
    assert data["status"] == "ok"
    assert R.rule_6_2(data).verdict is MsVerdict.WARN
    # 年报表头版式：百分号在表头，数值不带 %
    table = E.extract_rd_capitalization(["研发投入资本化的比重（%） 16.00"])
    assert table["status"] == "ok" and table["ratio"] == pytest.approx(0.16)
    assert R.rule_6_2(table).verdict is MsVerdict.PASS
    assert R.rule_6_2({"status": "ok", "ratio": 0.55}).verdict is MsVerdict.FAIL
    assert R.rule_6_2({"status": "ok", "ratio": 0.10}).verdict is MsVerdict.PASS
    assert R.rule_6_2({"status": "missing"}).verdict is MsVerdict.SKIP


# ---------- 5.8 补充检查 ----------

def test_non_operating_occupation():
    clear = E.extract_non_operating_occupation(["是否存在非经营性占用：√不适用"])
    assert R.supplement_5_8(clear) is None
    found = E.extract_non_operating_occupation(["是否存在非经营性占用：√适用 金额 1.2 亿元"])
    result = R.supplement_5_8(found)
    assert result is not None and result.verdict is MsVerdict.FAIL


# ---------- PDF 下载工具 ----------

def test_validate_url_and_filename():
    assert D.validate_url("https://static.cninfo.com.cn/finalpage/2026-03-28/1.PDF")[0]
    assert D.validate_url("https://stockn.xueqiu.com/2025/600519.pdf")[0]
    assert not D.validate_url("https://example.com/report.pdf")[0]
    assert D.build_filename("600519", 2025) == "600519_年报_2025.pdf"


def test_looks_like_pdf(tmp_path: Path):
    good = tmp_path / "a.pdf"
    good.write_bytes(b"%PDF-1.4\n" + b"x" * 2000)
    ok, _ = D.looks_like_pdf(good)
    assert ok
    bad = tmp_path / "b.pdf"
    bad.write_bytes(b"not a pdf")
    assert not D.looks_like_pdf(bad)[0]


# ---------- PDF 文本抽取（可选依赖 pypdf） ----------

def _minimal_pdf(text: str) -> bytes:
    """手工构造单页 ASCII PDF（Helvetica），用于离线测试抽取后端。"""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n").encode()
    return bytes(out)


def test_extract_pdf_with_pypdf(tmp_path: Path):
    pytest.importorskip("pypdf")
    from sqa.minesweeper.deep.pdf_text import extract_pdf

    sentence = ("Financial report text for extraction test. " * 20)
    path = tmp_path / "mini.pdf"
    path.write_bytes(_minimal_pdf(sentence))
    doc = extract_pdf(path, refresh=True)
    assert doc.page_count >= 1
    assert "Financial report text" in doc.text


# ---------- 2026-10-19 深度模式加固回归 ----------

def test_freight_cross_row_is_rejected():
    # 运输费行只有本期+占比，下一行是"折旧费"的金额：不得拼成上期
    data = E.extract_freight(["运输费 326,866,386.45 2.21\n折旧费 311,524,380.58 2.29"])
    assert data["status"] != "ok"


def test_customer_concentration_cross_page_single_customer():
    pages = [
        "公司前 5 大客户资料",
        "1 第一名 60.00%\n合计 65.00%",
    ]
    data = E.extract_customer_concentration(pages)
    assert data["status"] == "ok"
    assert data["total"] == pytest.approx(65.0)
    assert data["single"] == pytest.approx(60.0)
    assert R.rule_5_5(data).verdict is MsVerdict.FAIL


def test_customer_concentration_ignores_supplier_total():
    pages = [
        "前五名客户销售额合计占年度销售总额的 10.0%。",
        "前五名供应商采购总额 55.0%。",
    ]
    data = E.extract_customer_concentration(pages)
    assert data["status"] == "ok"
    assert data["total"] == pytest.approx(10.0)
    assert R.rule_5_5(data).verdict is MsVerdict.PASS


def test_cfo_and_director_narrative_forms():
    cfo = E.extract_cfo_changes(["王某某因个人原因辞去公司财务总监职务。"])
    assert cfo["status"] == "found"
    assert R.rule_5_3(cfo).verdict is MsVerdict.WARN

    # PDF 表格把职称与变动词拆到相邻行：窄窗口应能兜住
    table = E.extract_cfo_changes(["余思明 财务总监\n代行董事会秘书职责 聘任 工作调动"])
    assert table["status"] == "found"

    ind = E.extract_independent_director(["独立董事张某辞职。独立董事李某辞任。"])
    assert ind["status"] == "found" and ind["count"] >= 2
    assert R.rule_5_4(ind).verdict is MsVerdict.FAIL


def test_regulatory_checkbox_and_context():
    # 问答式"□是 √否"不得判 FAIL
    qa = E.extract_regulatory(["公司是否被中国证监会立案调查：□是 √否"])
    assert qa["status"] != "fail"
    assert R.rule_5_9(qa).verdict is not MsVerdict.FAIL
    # 否定词跨句不得吞掉真处罚
    neg = E.extract_regulatory(["公司不存在应披露而未披露事项；控股股东被证监会立案调查。"])
    assert neg["status"] == "fail"
    # 留置/受到处罚纳入关键词
    warn = E.extract_regulatory(["报告期内公司高级管理人员被实施留置。"])
    assert warn["status"] == "warn"
    # 一般性环保罚款（上游 C 类）不报警
    env = E.extract_regulatory(["报告期内公司收到环保部门行政处罚 10 万元，属于一般性合规整改。"])
    assert env["status"] == "clear"


def test_rd_capitalization_binding():
    growth = E.extract_rd_capitalization(["研发投入资本化金额 2 亿元，较上年增长 60%。"])
    assert growth["status"] != "ok"  # 增长率不得当资本化比例
    adjacent = E.extract_rd_capitalization(
        ["研发投入总额占营业收入比例（%） 研发投入资本化的比重（%） 3.2 60.0"])
    assert adjacent["status"] != "ok"  # 多列相邻数值无法确认列归属
    amount = E.extract_rd_capitalization(
        ["本期资本化研发投入 1.5 亿元 研发投入合计 10 亿元"])
    assert amount["status"] == "ok"
    assert amount["ratio"] == pytest.approx(0.15)


def test_non_operating_occupation_checkbox_forms():
    # 标准问卷：勾选"适用"才算 found；未勾选项的"不适用"不得提前 clear
    found = E.extract_non_operating_occupation(
        ["是否存在非经营性占用：√适用 □不适用 金额 1.2 亿元"])
    assert R.supplement_5_8(found) is not None
    clear = E.extract_non_operating_occupation(
        ["是否存在非经营性占用：□适用 √不适用"])
    assert R.supplement_5_8(clear) is None
    unrelated = E.extract_non_operating_occupation(
        ["非经营性占用情况：否。本制度适用于公司全体股东。"])
    assert R.supplement_5_8(unrelated) is None


def test_pdf_failures_are_degraded_not_raised(tmp_path: Path):
    from sqa.minesweeper.deep.pipeline import run_deep, _trim_hits
    # 目录被当成 PDF 路径：应返回 error，不抛异常
    result = run_deep("600519", "贵州茅台", 2025, 0.1, pdf_path=str(tmp_path))
    assert result["status"] == "error"
    # 证据优先级：fails > warns > hits
    payload = {"hits": [{"snippet": "公司不存在被立案调查的情形"}],
               "fails": [{"snippet": "控股股东被立案调查"}]}
    assert _trim_hits(payload)[0]["snippet"] == "控股股东被立案调查"


def test_url_whitelist_rejects_lookalike_domain():
    assert not D.validate_url("https://evilcninfo.com.cn/a.pdf")[0]
    assert not D.validate_url("https://cninfo.com.cn.evil.com/a.pdf")[0]
    assert D.validate_url("https://static.cninfo.com.cn/finalpage/x.PDF")[0]


def test_pdftotext_short_output_falls_back_to_pypdf(tmp_path: Path, monkeypatch):
    pytest.importorskip("pypdf")
    from sqa.minesweeper.deep import pdf_text

    path = tmp_path / "fake.pdf"
    path.write_bytes(b"%PDF-1.4\n" + b"x" * 100)
    monkeypatch.setattr(pdf_text, "_pdftotext_pages", lambda p: ["short"])
    monkeypatch.setattr(pdf_text, "_pypdf_pages", lambda p: ["长文本" * 500])
    doc = pdf_text.extract_pdf(path, refresh=True)
    assert doc.backend == "pypdf"
    assert doc.page_count == 1


def test_blank_pages_preserve_physical_page_numbers(tmp_path: Path, monkeypatch):
    from sqa.minesweeper.deep import pdf_text

    path = tmp_path / "fake2.pdf"
    path.write_bytes(b"%PDF-1.4\n" + b"x" * 100)
    monkeypatch.setattr(pdf_text, "_pdftotext_pages", lambda p: [])
    monkeypatch.setattr(pdf_text, "_pypdf_pages", lambda p: ["", "正文内容" * 200])
    doc = pdf_text.extract_pdf(path, refresh=True)
    assert doc.page_count == 2 and doc.pages[0] == ""


def test_low_page_coverage_rejected(tmp_path: Path, monkeypatch):
    from sqa.minesweeper.deep import pdf_text
    from sqa.minesweeper.deep.pdf_text import PdfTextError

    path = tmp_path / "fake3.pdf"
    path.write_bytes(b"%PDF-1.4\n" + b"x" * 100)
    pages = ["正文" * 300] + [""] * 19  # 20 页仅 1 页有文本
    monkeypatch.setattr(pdf_text, "_pdftotext_pages", lambda p: [])
    monkeypatch.setattr(pdf_text, "_pypdf_pages", lambda p: pages)
    with pytest.raises(PdfTextError):
        pdf_text.extract_pdf(path, refresh=True)


# ---------- 2026-10-25 复核修正：6.2 空白单元格 / 5.9 章节标题 ----------

def test_rd_capitalization_blank_cell_derives_zero():
    """睿创微纳式：资本化研发投入为空白单元格，不得串行取下一行合计数。"""
    pages = [
        "费用化研发投入 1,153,401,424.60 860,733,802.61 34.00\n"
        "资本化研发投入\n"
        "研发投入合计 1,153,401,424.60 860,733,802.61 34.00\n"
        "研发投入资本化的比重（%）"
    ]
    data = E.extract_rd_capitalization(pages)
    assert data["status"] == "ok"
    assert data["basis"] == "amount_derived"
    assert data["ratio"] == pytest.approx(0.0)
    assert R.rule_6_2(data).verdict is MsVerdict.PASS


def test_rd_capitalization_total_minus_expensed():
    pages = ["费用化研发投入 8.00 亿元\n"
             "资本化研发投入\n"
             "研发投入合计 10.00 亿元"]
    data = E.extract_rd_capitalization(pages)
    assert data["status"] == "ok"
    assert data["ratio"] == pytest.approx(0.2)
    assert R.rule_6_2(data).verdict is MsVerdict.PASS


def test_rd_capitalization_next_line_amount_still_supported():
    """pypdf 常见版式：标签行 + 下一行纯数字仍可绑定。"""
    pages = ["资本化研发投入\n150,000,000.00\n研发投入合计 1,500,000,000.00"]
    data = E.extract_rd_capitalization(pages)
    assert data["status"] == "ok"
    assert data["ratio"] == pytest.approx(0.1)


def test_rd_capitalization_real_percent_binding_unchanged():
    data = E.extract_rd_capitalization(["研发投入资本化的比重（%） 16.00"])
    assert data["status"] == "ok" and data["ratio"] == pytest.approx(0.16)


def test_rd_capitalization_mixed_units_rejected():
    # 合计用亿元、费用化无单位（8.00 实为 8 亿元）：不得按 10亿−8元 推导出 100%
    pages = ["研发投入合计 10.00 亿元\n资本化研发投入\n费用化研发投入 8.00"]
    data = E.extract_rd_capitalization(pages)
    assert data["status"] != "ok"
    # 资本化金额与合计相等且未披露费用化金额：无法确认，转 ambiguous
    pages2 = ["资本化研发投入 10.00 亿元\n研发投入合计 10.00 亿元"]
    data2 = E.extract_rd_capitalization(pages2)
    assert data2["status"] != "ok"
    assert R.rule_6_2(data2).verdict is MsVerdict.SKIP


def test_rd_capitalization_negative_amount_rejected():
    # 负金额属于报表口径异常/符号解析错误：不得取绝对值后当成有效资本化
    pages = ["资本化研发投入 -1.50 亿元\n研发投入合计 10.00 亿元"]
    data = E.extract_rd_capitalization(pages)
    assert data["status"] != "ok"
    assert R.rule_6_2(data).verdict is MsVerdict.SKIP


def test_rd_capitalization_percent_not_treated_as_amount():
    # 百分比行（同行或续行）不得被当作资本化金额
    same_line = E.extract_rd_capitalization(
        ["资本化研发投入 16.00%\n研发投入合计 10.00 亿元"])
    assert same_line["status"] != "ok"
    continuation = E.extract_rd_capitalization(
        ["资本化研发投入\n16.00%\n研发投入合计 10.00 亿元"])
    assert continuation["status"] != "ok"


def test_rd_capitalization_derivation_only_when_cap_missing():
    # 资本化金额存在但单位不一致时，不得用 合计−费用化 推导（否则误判 100%）
    pages = ["资本化研发投入 0.30 亿元\n研发投入合计 10,000 万元\n"
             "费用化研发投入 0 万元"]
    data = E.extract_rd_capitalization(pages)
    assert data["status"] != "ok"
    # 占比行（费用化研发投入占比）不得作为"费用化金额"续行
    ratio_row = E.extract_rd_capitalization(
        ["研发投入合计 10,000\n费用化研发投入占比（%）\n60"])
    assert ratio_row["status"] != "ok"


def test_rd_capitalization_percent_continuation():
    # 百分比值在下一行（pypdf 常见版式）：支持续行解析
    zero = E.extract_rd_capitalization(["研发投入资本化的比重（%）\n0.00"])
    assert zero["status"] == "ok" and zero["ratio"] == pytest.approx(0.0)
    warn = E.extract_rd_capitalization(
        ["资本化研发投入占比（%）\n35\n研发投入合计 100"])
    assert warn["status"] == "ok" and warn["ratio"] == pytest.approx(0.35)
    assert R.rule_6_2(warn).verdict in (MsVerdict.WARN, MsVerdict.FAIL)


def test_rd_capitalization_cap_equals_total_prefers_percent():
    # cap=合计且缺费用化时，若同行有明确百分比证据，应优先采用百分比
    pages = ["资本化研发投入 10.00 亿元\n研发投入合计 10.00 亿元\n"
             "研发投入资本化的比重（%） 0.00"]
    data = E.extract_rd_capitalization(pages)
    assert data["status"] == "ok"
    assert data["ratio"] == pytest.approx(0.0)
    assert R.rule_6_2(data).verdict is MsVerdict.PASS


def test_regulatory_section_headers_ignored():
    """年报章节模板标题（"十、…受到处罚及整改情况"）不得当作处罚事实。"""
    pages = ["十、上市公司及其董事、高级管理人员、控股股东、实际控制人涉嫌违法违规、"
             "受到处罚及整改情况\n□适用 √不适用"]
    data = E.extract_regulatory(pages)
    assert data["status"] == "clear"
    assert R.rule_5_9(data).verdict is MsVerdict.PASS

    paren = E.extract_regulatory(
        ["（五）近三年受证券监管机构处罚的情况说明\n□适用 √不适用"])
    assert paren["status"] == "clear"

    same_line = E.extract_regulatory(
        ["十、公司涉嫌违法违规、受到处罚及整改情况：□适用 √不适用"])
    assert same_line["status"] == "clear"

    # PDF 抽取把标题断行（…及整改\n情况）：仍应识别为标题
    split_line = E.extract_regulatory(
        ["十、上市公司及其董事、高级管理人员、控股股东、实际控制人涉嫌违法违规、"
         "受到处罚及整改\n情况"])
    assert split_line["status"] == "clear"


def test_regulatory_genuine_numbered_or_narrative_penalty_kept():
    # 正文中的真实处罚（阿拉伯数字编号）不得被标题跳过逻辑吞掉
    numbered = E.extract_regulatory(["1、公司收到证监局警示函。"])
    assert numbered["status"] == "warn"
    assert R.rule_5_9(numbered).verdict is MsVerdict.WARN
    # 中文序号但不是"情况/说明"标题的真实叙述仍应识别
    narrative = E.extract_regulatory(["十、公司控股股东被证监会立案调查。"])
    assert narrative["status"] == "fail"
    # 中文序号 + 处罚事实措辞（存在/受到处罚）不得按标题跳过
    fact = E.extract_regulatory(["十、公司存在受到处罚的情况。"])
    assert fact["status"] == "warn"
    # 中文序号 + "整改情况" + 具体处罚类型（通报批评）不得被标题守卫吞掉
    numbered_fact = E.extract_regulatory(
        ["（一）公司受到深交所通报批评，具体整改情况如下："])
    assert numbered_fact["status"] == "warn"
    # 年份中的 0 不得被当作"零次"否定（2020 年被立案 / 2024 年警示函）
    year_fail = E.extract_regulatory(["公司2020年被证监会立案调查。"])
    assert year_fail["status"] == "fail"
    year_warn = E.extract_regulatory(["公司2024年收到证监局警示函。"])
    assert year_warn["status"] == "warn"
    # "被处罚"（非"受到处罚"）也应识别为真实处罚
    beichufa = E.extract_regulatory(["十、公司被处罚款 10 万元。"])
    assert beichufa["status"] == "warn"
    # 真实处罚句含"不适用"时，不得按"不适用披露"跳过
    not_applicable = E.extract_regulatory(
        ["控股股东被立案调查，相关主体不适用豁免。"])
    assert not_applicable["status"] == "fail"


def test_regulatory_linebreak_and_commitment_false_positives():
    """2026-10-25 复核修正：跨行否定/承诺函/假设句/风险表述/表格字段不得误报。"""
    # PDF 在"被/监管"间断行：否定语境不得丢失（比亚迪 002594 实盘误报）
    byd = E.extract_regulatory(
        ["截至报告期末，公司治理的实际状况符合有关要求，未收到被\n"
         "监管部门采取行政监管措施的有关文件。"])
    assert byd["status"] == "clear"

    # 长定语否定句：否定词距"立案"约 30 字符（中芯 688981 承诺函实盘误报）
    smic_long_neg = E.extract_regulatory(
        ["本公司不存在因涉嫌犯罪正被司法机关立案侦查或涉嫌违法违规正被"
         "中国证监会立案调查的情形。"])
    assert smic_long_neg["status"] == "clear"

    # 承诺函假设句："如本次交易因…被立案调查的"不是已发生事实
    hypothetical = E.extract_regulatory(
        ["2. 如本次交易因本企业涉嫌所提供的信息存在虚假记载被司法机关立案侦查"
         "或者被中国证监会立案调查的，在案件调查结论明确以前，不转让股份。"])
    assert hypothetical["status"] == "clear"

    # 承诺函兜底条款中的"受到行政处罚产生的罚款"不是处罚事实（神华 601088 实盘误报）
    compensation = E.extract_regulatory(
        ["如因标的公司未取得必要业务资质导致中国神华或标的公司在本次交易完成后"
         "遭受损失（包括受到行政处罚产生的罚款等），国家能源集团公司应当予以补偿。"])
    assert compensation["status"] == "clear"

    # 风险描述（"行政处罚风险明显增加"）不是处罚事实
    risk = E.extract_regulatory(
        ["公司面临的合规监督和行政处罚风险明显增加。"])
    assert risk["status"] == "clear"

    # 表格披露字段���（"因环境问题受到行政处罚的情况…（如有）"）不是处罚事实
    table_field = E.extract_regulatory(
        ["报告期内募集资金所投向的绿色项目发生重大污染责任事故、因环境问题受到"
         "行政处罚的情况和其他环境违法事件等信息，及是否会对偿债产生重大影响（如有）"])
    assert table_field["status"] == "clear"

    # 长否定句末尾"…的情形"：否定覆盖全句（中芯 688981 p97）
    scope_neg = E.extract_regulatory(
        ["1. 截至本承诺函签署日，本公司不存在因涉嫌犯罪正被司法机关立案侦查或"
         "涉嫌违法违规正被中国证监会立案调查的情形。"])
    assert scope_neg["status"] == "clear"

    # "…诉讼、仲裁、行政处罚，亦不存在被监管部门…立案调查"（松发 603268 实盘误报）
    listed_neg = E.extract_regulatory(
        ["本公司不存在影响公司正常经营的抵押、质押、冻结、司法查封等权利受限情况"
         "或诉讼、仲裁、行政处罚，亦不存在被监管部门、司法机关立案调查、"
         "采取强制措施的情形。"])
    assert listed_neg["status"] == "clear"

    # 页眉（"…2025 年年度报告98 / 232"）不得与正文拼成假事实
    header = E.extract_regulatory(
        ["中芯国际集成电路制造有限公司2025 年年度报告98 / 232违反证券法律、"
         "行政法规、规章受到中国证监会的行政处罚的情形。"])
    assert header["status"] == "clear"


def test_regulatory_positive_detention_after_checkbox():
    """√适用 之后的真实"高管被实施留置"必须保留 WARN（茅台 600519 p39）。

    勾选项"√适用 □不适用"不得被当作否定语境（否则真实事件被静默吞掉）。
    """
    data = E.extract_regulatory(
        ["√适用 □不适用详见 2026 年 3月 14 日披露的《贵州茅台关于高级管理人员"
         "被实施留置的公告》（公告编号：临 2026-004）。"])
    assert data["status"] == "warn"
    assert R.rule_5_9(data).verdict is MsVerdict.WARN


# ---------- 2026-10-25 复核修正：1.3 运费语境/阶跃 / 5.4 换届 / 5.2 披露豁免 ----------

def test_freight_requires_expense_context_and_rejects_related_party_table():
    # 期间费用附注正例：销售费用中的运输费
    ok = E.extract_freight(["销售费用 运输费 12,000,000.00 10,000,000.00"])
    assert ok["status"] == "ok"
    assert R.rule_1_3(ok, 0.1).verdict in (MsVerdict.PASS, MsVerdict.WARN, MsVerdict.FAIL)
    # 关联方交易表（松发 603268 p209）：不得当运费附注
    related = E.extract_freight(
        ["关联方交易 恒力海运（大连）有限公司 接受劳务 运输费 "
         "1,247,973.74 183,486.24"])
    assert related["status"] != "ok"
    # 无费用/附注语境：宁漏勿错
    bare = E.extract_freight(["运输费 1,247,973.74 183,486.24"])
    assert bare["status"] != "ok"


def test_freight_rule_skips_on_revenue_step_change():
    # 营收 +7774%（借壳并表）→ 运费对比无意义，SKIP（松发 603268）
    freight = {"status": "ok", "current": 1_247_973.74,
               "previous": 183_486.24, "growth": 5.80}
    assert R.rule_1_3(freight, 77.74).verdict is MsVerdict.SKIP
    # 正常营收增速仍按原规则判定：运费下降 20% vs 营收 +10% → 比值 <25% → FAIL
    falling = {"status": "ok", "current": 80.0, "previous": 100.0, "growth": -0.2}
    assert R.rule_1_3(falling, 0.10).verdict is MsVerdict.FAIL


def test_independent_director_reshuffle_is_not_collective_resignation():
    # 松发 603268：借壳后提前换届 + 同日新聘，证据全是"离任"而非"辞职" → clear
    pages = [
        "独立董事邹健（离任）独立董事黄伟坤（离任）独立董事刘瑛（离任）",
        "公司第六届董事会原定任期终止日期为 2027 年 11 月，鉴于已实施重大资产置换"
        "及发行股份购买资产，决定提前进行换届选举。",
        "同日选举李志文、周波、许浩然为独立董事。",
    ]
    data = E.extract_independent_director(pages)
    assert data["status"] == "clear"
    assert R.rule_5_4(data).verdict is MsVerdict.PASS
    # 真实辞职仍能识别（两人同句）
    real = E.extract_independent_director(["独立董事张某辞职，独立董事李某辞任。"])
    assert real["status"] == "found" and real["count"] >= 2


def test_freight_cost_composition_table_accepted():
    """营业成本构成表（茅台 600519 p11）：表内无"销售费用"字样也应能提取。"""
    pages = [
        "主营业务成本构成\n直接材料 7,607,858,939.76 51.38 6,895,320,421.92 50.59\n"
        "直接人工 5,499,482,112.67 37.14 5,224,448,485.08 38.32\n"
        "制造费用 902,282,395.06 6.10 776,373,890.79 5.70\n"
        "运输费 326,866,386.45 2.21 311,524,380.58 2.29\n"
        "合计 14,805,900,139.59 100.00 13,629,995,812.89 100.00"
    ]
    data = E.extract_freight(pages)
    assert data["status"] == "ok"
    assert data["current"] == 326_866_386.45
    assert data["previous"] == 311_524_380.58


def test_regulatory_real_penalty_with_trailing_conditional():
    """真处罚句尾部带"如果/将面临处罚"不得被假设语境误杀。"""
    data = E.extract_regulatory(
        ["公司被中国证监会立案调查，如果最终认定违规，公司将面临处罚。"])
    assert data["status"] == "fail"
    # 无逗号的转折但书：否定不覆盖后一分句
    data2 = E.extract_regulatory(["公司不存在违规行为但控股股东被立案调查。"])
    assert data2["status"] == "fail"
    # 真处罚 + 赔偿责任（非"予以赔偿/补偿"承诺句式）保留
    data3 = E.extract_regulatory(["公司被证监会立案调查，可能需承担赔偿责任。"])
    assert data3["status"] == "fail"


def test_regulatory_page_header_merged_line_keeps_body():
    """页眉与正文被 pypdf 拼到同一行：剥页眉后正文真实处罚仍须识别。"""
    data = E.extract_regulatory(
        ["公司2025 年年度报告95 / 232公司被证监会立案调查。"])
    assert data["status"] == "fail"


def test_supplement_5_2_holder_event_override():
    pages = [
        "前十名股东情况 中国长江三峡集团有限公司部分持股减少，主要原因一是其持有"
        "的 978,728,709 股股份无偿划转至其全资子公司长江生态环保集团有限公司；"
        "二是其发行的可交换债券进入换股期，有投资者换股所致。"
    ]
    events = E.extract_holder_events(pages)
    assert events["status"] == "found"
    override = R.supplement_5_2(events)
    assert override is not None and override.verdict is MsVerdict.PASS
    # 无"持股减少"语境 → 不覆盖
    unrelated = E.extract_holder_events(["公司发行可交换公司债券的公告。"])
    assert R.supplement_5_2(unrelated) is None
