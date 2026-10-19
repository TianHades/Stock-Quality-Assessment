"""取数契约与字段名回归测试（离线，不联网）。

背景：此前纯函数测试用合成字段名（operate_tax_add/inv_turn），真实 tushare
不返回这些默认列，导致规则永久 SKIP；本文件锁定：
- fetch_annual_full 必须显式传 fields（含可选列）；
- 分红只取"实施"、税前口径、同值行去重；
- 规则使用真实字段名；4.4 对总净利（少数股东口径）。
"""
from __future__ import annotations

import pytest

from sqa.data.fetchers import (
    compute_fcf, fetch_annual_cashflow, fetch_dividend_records,
)
from sqa.minesweeper import rules_financial as fin
from sqa.minesweeper.fetch import fetch_annual_full
from sqa.minesweeper.models import MsVerdict
from sqa.minesweeper import rules_nonfinancial as nonfin


class FakeClient:
    """最小 TushareClient 替身：记录调用参数，按 api 返回预设记录。"""

    def __init__(self, rows: dict[str, list[dict]] | None = None) -> None:
        self.rows = rows or {}
        self.calls: list[tuple[str, dict]] = []

    def query(self, api: str, refresh: bool = False, **params):
        self.calls.append((api, params))
        return self.rows.get(api, [])


def _income_row(**overrides) -> dict:
    row = {
        "ts_code": "600519.SH", "end_date": "20251231", "ann_date": "20260401",
        "report_type": "1", "revenue": 1500.0, "oper_cost": 100.0,
        "biz_tax_surchg": 180.0, "sell_exp": 50.0, "admin_exp": 60.0,
        "fin_exp": -130.0, "n_income": 1000.0, "n_income_attr_p": 750.0,
        "oth_b_income": 30.0, "assets_impair_loss": None,
        "credit_impa_loss": None,
    }
    row.update(overrides)
    return row


# ---------- 取数契约 ----------

def test_fetch_annual_full_requests_optional_fields():
    client = FakeClient({"income": [_income_row()]})
    out = fetch_annual_full(client, "income", "600519.SH", 10)
    api, params = client.calls[0]
    assert api == "income"
    fields = params["fields"]
    for name in ("biz_tax_surchg", "credit_impa_loss", "inv_turn", "n_income"):
        if name == "inv_turn":
            continue  # income 无此字段；下面单独校验 indicator
        assert name in fields
    assert out[2025]["biz_tax_surchg"] == 180.0

    client2 = FakeClient({"fina_indicator": [{"end_date": "20251231",
                                             "ann_date": "20260401",
                                             "report_type": "1", "inv_turn": 2.5}]})
    fetch_annual_full(client2, "fina_indicator", "600519.SH", 10)
    assert "inv_turn" in client2.calls[0][1]["fields"]


def test_fetch_annual_cashflow_uses_capex_field():
    rows = [{"end_date": "20251231", "ann_date": "20260401", "report_type": "1",
             "n_cashflow_act": 1000.0, "free_cashflow": None,
             "c_pay_acq_const_fiolta": 300.0}]
    client = FakeClient({"cashflow": rows})
    out = fetch_annual_cashflow(client, "600519.SH", 10)
    assert out[2025]["fcf"] == pytest.approx(700.0)
    assert "c_pay_acq_const_fiolta" in client.calls[0][1]["fields"]


def test_compute_fcf_shared_contract():
    assert compute_fcf({"free_cashflow": 100.0}) == 100.0
    assert compute_fcf({"n_cashflow_act": 1000.0,
                        "c_pay_acq_const_fiolta": 300.0}) == 700.0
    assert compute_fcf({}, {"fcff": 50.0}) == 50.0
    assert compute_fcf({}, None) is None


# ---------- 分红：实施过滤 + 去重 + 税前口径 ----------

def test_dividend_filters_implemented_dedupes_and_uses_pretax():
    rows = [
        # 2024 年度：同值"实施"公告重复两次（600519 实测形态）
        {"end_date": "20241231", "ann_date": "20250403", "div_proc": "实施",
         "cash_div": 26.0, "cash_div_tax": 27.673},
        {"end_date": "20241231", "ann_date": "20250520", "div_proc": "实施",
         "cash_div": 27.673, "cash_div_tax": 27.673},
        # 2024 中期：并入 FY2024
        {"end_date": "20240930", "ann_date": "20241128", "div_proc": "实施",
         "cash_div": 23.882, "cash_div_tax": 23.882},
        # 预案行：不计
        {"end_date": "20251231", "ann_date": "20260417", "div_proc": "预案",
         "cash_div": 0.0, "cash_div_tax": 28.02423},
    ]
    client = FakeClient({"dividend": rows})
    divs = fetch_dividend_records(client, "600519.SH")
    assert divs[2024] == pytest.approx(27.673 + 23.882)
    assert 2025 not in divs
    assert "div_proc" in client.calls[0][1]["fields"]


# ---------- 规则真实字段 ----------

def test_rule_1_4_uses_real_other_business_income_field():
    row = _income_row(oth_b_income=90.0)
    prev = _income_row(end_date="20241231", ann_date="20250401",
                       oth_b_income=10.0)
    result = fin.rule_1_4([prev, row])
    assert result.verdict is not MsVerdict.SKIP
    assert "其他业务收入占比" in result.detail


def test_rule_3_4_uses_real_long_term_deferred_field():
    balance = [
        {"lt_amor_exp": 100.0},
        {"lt_amor_exp": 160.0},
    ]
    result = fin.rule_3_4(balance, [1000.0, 1000.0])
    assert result.verdict is MsVerdict.WARN  # +60% > 50%
    assert result.verdict is not MsVerdict.SKIP


def test_rule_4_4_uses_business_tax_and_total_net_income():
    row = _income_row()  # core=1240, n_income=1000 → 24% WARN；attr=750 会是 65% FAIL
    result = fin.rule_4_4(row)
    assert result.verdict is MsVerdict.WARN
    # 缺少税金字段 → SKIP（宁缺毋滥）
    no_tax = dict(row)
    no_tax.pop("biz_tax_surchg")
    assert fin.rule_4_4(no_tax).verdict is MsVerdict.SKIP


def test_rule_s2_zero_debt_pass_and_new_field():
    assert nonfin.rule_s2({"money_cap": 100.0, "st_borr": 0.0,
                           "total_assets": 1000.0}).verdict is MsVerdict.PASS
    # 一年内到期非流动负债：正确字段 non_cur_liab_due_1y 参与计算
    r = nonfin.rule_s2({"money_cap": 100.0, "st_borr": 10.0,
                        "non_cur_liab_due_1y": 400.0, "total_assets": 1000.0})
    assert r.verdict is MsVerdict.FAIL
    assert "0.24" in r.detail
