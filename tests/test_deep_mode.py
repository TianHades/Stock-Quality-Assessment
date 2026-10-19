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
