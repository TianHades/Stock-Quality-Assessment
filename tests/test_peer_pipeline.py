"""同行中位数管道 + 排雷 3.5 的离线测试（不联网）。"""
from __future__ import annotations

import pytest

from sqa.minesweeper import rules_financial as fin
from sqa.minesweeper.models import MsVerdict
from sqa.quality.fetch import fetch_peer_medians


class FakePeerClient:
    """按 api 返回同行数据；income/balancesheet 模拟 tushare 单票查询。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def query(self, api: str, refresh: bool = False, **params):
        self.calls.append((api, params))
        codes = ["600001.SH", "600002.SH", "600003.SH"]
        if api == "stock_basic":
            return [{"ts_code": c, "industry": "煤炭开采",
                     "list_date": "20100101"} for c in codes]
        if api == "daily_basic":
            return []
        if api == "fina_indicator":
            period = params.get("period", "")
            rows = []
            for i, code in enumerate(codes):
                rows.append({
                    "ts_code": code, "end_date": period, "report_type": "1",
                    "grossprofit_margin": 20.0 + i * 10,
                    "debt_to_assets": 40.0 + i * 10,
                    "assets_turn": 0.5 + i * 0.1,
                    "ar_turn": 5.0 + i * 5,
                })
            return rows
        if api == "income":
            code = params.get("ts_code")
            loss = {"600001.SH": -10.0, "600002.SH": -20.0, "600003.SH": -30.0}[code]
            return [{"ts_code": code, "report_type": "1",
                     "credit_impa_loss": loss}]
        if api == "balancesheet":
            code = params.get("ts_code")
            ar = {"600001.SH": 100.0, "600002.SH": 200.0, "600003.SH": 300.0}[code]
            return [{"ts_code": code, "report_type": "1",
                     "accounts_receiv": ar, "total_assets": 1000.0}]
        return []


def test_peer_medians_include_turnover_and_bad_debt():
    client = FakePeerClient()
    peers = fetch_peer_medians(client, "煤炭开采", include_bad_debt=True)
    assert peers is not None
    assert peers["gm_median"] == pytest.approx(30.0)
    assert peers["debt_median"] == pytest.approx(50.0)
    assert peers["assets_turn_median"] == pytest.approx(0.6)
    assert peers["ar_turn_median"] == pytest.approx(10.0)
    # 三家坏账率均为 10%，加权计提率也是 10%
    assert peers["bad_debt_ratio_median"] == pytest.approx(10.0)
    assert peers["bad_debt_ratio_agg"] == pytest.approx(10.0)
    assert peers["n_bad_debt_peers"] == 3
    assert any(api == "income" for api, _ in client.calls)
    assert any(api == "balancesheet" for api, _ in client.calls)


def test_peer_medians_exclude_self():
    client = FakePeerClient()
    # 行业只有 3 家，排除自身后剩 2 家 → 样本不足，不硬算
    assert fetch_peer_medians(client, "煤炭开采", exclude_code="600001.SH") is None


def test_peer_medians_historical_period_selection():
    client = FakePeerClient()
    peers = fetch_peer_medians(client, "煤炭开采", as_of="20210430")
    assert peers is not None and peers["period"] == "20201231"
    assert any(call[1].get("period") == "20201231" for call in client.calls
               if call[0] == "fina_indicator")


def test_rule_3_5_bands_and_skips():
    income = {"credit_impa_loss": -10.0}
    balance = {"accounts_receiv": 100.0, "total_assets": 1000.0}
    # 10% vs 中位数 8% → PASS
    assert fin.rule_3_5(income, balance,
                        {"bad_debt_ratio_median": 8.0, "n_bad_debt_peers": 5}
                        ).verdict is MsVerdict.PASS
    # 加权率优先于中位数：10% vs 加权 20% → WARN（中位数 8% 会被忽略）
    assert fin.rule_3_5(income, balance,
                        {"bad_debt_ratio_median": 8.0,
                         "bad_debt_ratio_agg": 20.0, "n_bad_debt_peers": 5}
                        ).verdict is MsVerdict.WARN
    # 10% vs 15% → WARN
    assert fin.rule_3_5(income, balance,
                        {"bad_debt_ratio_median": 15.0, "n_bad_debt_peers": 5}
                        ).verdict is MsVerdict.WARN
    # 10% < 中位数 25% ×50% → FAIL
    assert fin.rule_3_5(income, balance,
                        {"bad_debt_ratio_median": 25.0, "n_bad_debt_peers": 5}
                        ).verdict is MsVerdict.FAIL
    # 应收账款极小 → SKIP
    assert fin.rule_3_5(income, {"accounts_receiv": 5.0, "total_assets": 1000.0},
                        {"bad_debt_ratio_median": 10.0, "n_bad_debt_peers": 5}
                        ).verdict is MsVerdict.SKIP
    # 同行不足 → SKIP
    assert fin.rule_3_5(income, balance,
                        {"bad_debt_ratio_median": 10.0, "n_bad_debt_peers": 2}
                        ).verdict is MsVerdict.SKIP
