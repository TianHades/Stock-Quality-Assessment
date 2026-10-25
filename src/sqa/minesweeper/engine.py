"""排雷引擎（02 号文档）：取数 → 快筛规则 →（可选）深度模式 PDF 规则 → 计分 → 风险闸门。"""
from __future__ import annotations

import os
from datetime import datetime

from sqa.config import Thresholds, load_thresholds
from sqa.data.fetchers import fetch_basic, ocf_minus_capex, resolve_ts_code
from sqa.data.tushare_client import SqaDataError, TushareClient
from sqa.minesweeper.fetch import (
    fetch_annual_full, fetch_audit, fetch_holder_series, fetch_pledge_ratio,
    holder_ratio_series,
)
from sqa.minesweeper.models import MsResult, assess
from sqa.minesweeper import rules_financial as fin
from sqa.minesweeper import rules_nonfinancial as nonfin
from sqa.quality.fetch import fetch_peer_medians

# 规则版本锚点：原 financial-report-minesweeper 仓库 main @ 483d3fc（2026-04-16）
# 规则全文已内联到 docs/reference/minesweeper-checklist-rules.md（2026-10-18 迁移）。
SOURCE_VERSION = "checklist-rules.md@483d3fc（原 financial-report-minesweeper，2026-04-16 归档）"


def run_minesweeper(symbol: str, cfg: Thresholds | None = None,
                    client: TushareClient | None = None,
                    refresh: bool = False, mode: str = "quick",
                    pdf_path: str | None = None,
                    pdf_url: str | None = None,
                    as_of: str | None = None) -> dict:
    """主入口：股票代码 → 02 号文档 §6 的 JSON 结构。

    mode="deep" 时额外下载/解析年报 PDF，补齐 1.3/5.3/5.4/5.5/5.6/5.9/6.2 七条
    依赖 PDF 的规则，并用 5.8 的 PDF 补充检查覆盖快筛结果；PDF 获取失败时自动
    降级为快筛结果（deep 字段记录失败原因），不影响闸门判定。

    ``as_of``（YYYYMMDD）用于历史时点回测：只取该日期前披露的年报/审计记录；
    质押、股东、同行等当前快照数据会置空（S1/S4/5.2/3.5 等 SKIP），避免未来函数。
    """
    cfg = cfg or load_thresholds()
    if client is None:
        client = TushareClient(token=os.environ.get("TUSHARE_TOKEN", ""))

    ts_code = resolve_ts_code(symbol)
    basic = fetch_basic(client, ts_code, refresh)
    historical = bool(as_of)

    # ---- 取数（全字段年报三表 + 审计/质押/股东）----
    ind = fetch_annual_full(client, "fina_indicator", ts_code, cfg.history_years,
                            refresh, end_date=as_of)
    income = fetch_annual_full(client, "income", ts_code, cfg.history_years,
                               refresh, end_date=as_of)
    balance = fetch_annual_full(client, "balancesheet", ts_code, cfg.history_years,
                                refresh, end_date=as_of)
    cashflow = fetch_annual_full(client, "cashflow", ts_code, cfg.history_years,
                                 refresh, end_date=as_of)
    try:
        audit_rows = fetch_audit(client, ts_code, refresh, end_date=as_of)
    except SqaDataError:
        audit_rows = []
    if historical:
        pledge_ratio = fetch_pledge_ratio(client, ts_code, refresh, as_of=as_of)
        holder_periods = fetch_holder_series(client, ts_code, refresh=refresh,
                                             as_of=as_of)
        holders = holder_ratio_series(holder_periods)
        try:
            peers = fetch_peer_medians(client, basic.industry, refresh,
                                       include_bad_debt=True, exclude_code=ts_code,
                                       as_of=as_of)
        except SqaDataError:
            peers = None
    else:
        pledge_ratio = fetch_pledge_ratio(client, ts_code, refresh)
        holder_periods = fetch_holder_series(client, ts_code, refresh=refresh)
        holders = holder_ratio_series(holder_periods)
        try:
            peers = fetch_peer_medians(client, basic.industry, refresh,
                                       include_bad_debt=True, exclude_code=ts_code)
        except SqaDataError:
            peers = None

    # ---- 序列准备（按年份对齐，缺口 None）----
    years = sorted(set(ind) | set(income) | set(balance) | set(cashflow))

    def series(source: dict, field: str) -> list[float | None]:
        return [source.get(y, {}).get(field) for y in years]

    gm = series(ind, "grossprofit_margin")
    inv_turn = series(ind, "inv_turn")
    revenue = series(income, "revenue")
    np_ = series(income, "n_income_attr_p")
    ar = series(balance, "accounts_receiv")
    ap = series(balance, "acct_payable")
    ta = series(balance, "total_assets")
    cip = series(balance, "cip")
    fix = series(balance, "fix_assets")
    ocf = series(cashflow, "n_cashflow_act")
    capex = series(cashflow, "c_pay_acq_const_fiolta")
    cash_recv = series(cashflow, "c_fr_sale_sg")

    def fcf_of(y: int) -> float | None:
        # 4.5 口径：FCF = 经营 CF − Capex（与规则文本一致）；缺失时回退 fcff。
        # 不用 compute_fcf 的 free_cashflow 优先口径（会与规则定义混用）。
        value = ocf_minus_capex(cashflow.get(y))
        if value is not None:
            return value
        return (ind.get(y) or {}).get("fcff")

    fcf = [fcf_of(y) for y in years]

    income_rows = [income[y] for y in sorted(income)]
    balance_rows = [balance[y] for y in sorted(balance)]
    income_latest = income_rows[-1] if income_rows else {}
    balance_latest = balance_rows[-1] if balance_rows else {}
    # 2.3 的"隐含利率"必须用利息支出（int_exp），不能用财务费用净额：
    # 美的 fin_exp=-59 亿（利息净收入）会算出 9% 假高利率；int_exp 仅 27.5 万。
    interest_exp_latest = income_latest.get("int_exp")
    if interest_exp_latest is None:
        fin_exp_latest = income_latest.get("fin_exp")
        interest_exp_latest = fin_exp_latest if fin_exp_latest and fin_exp_latest > 0 else None

    # S4 口径（02 §3）：近 2 年大股东持股比例降幅（原实现只比最近两期 ≈1 年）
    positive = [h for h in holders if h is not None]
    if len(positive) >= 3:
        holder_drop = positive[-3] - positive[-1]
    elif len(positive) >= 2:
        holder_drop = positive[0] - positive[-1]
    else:
        holder_drop = None

    # ---- 规则执行（快筛模式；深度模式 PDF 规则暂以 SKIP 占位）----
    results: list[MsResult] = [
        nonfin.rule_0_1(audit_rows),
        nonfin.rule_0_2(audit_rows),
        fin.rule_1_1(gm),
        fin.rule_1_2(gm, ar, ap, revenue),
        fin.rule_1_3(),
        fin.rule_1_4(income_rows),
        fin.rule_1_5(income_rows),
        fin.rule_1_6(income_rows),
        fin.rule_2_1(ocf, capex),
        fin.rule_2_2(ocf),
        fin.rule_2_3(balance_latest, interest_exp_latest, cfg.ms_base_rate),
        fin.rule_3_1(ar, revenue),
        fin.rule_3_2(inv_turn, gm),
        fin.rule_3_3(cip, fix, ta),
        fin.rule_3_4(balance_rows, ta),
        fin.rule_3_5(income_latest, balance_latest, peers),
        fin.rule_4_1(ocf, np_),
        fin.rule_4_2(cash_recv, revenue),
        fin.rule_4_3(ta, revenue, np_),
        fin.rule_4_4(income_latest),
        fin.rule_4_5(np_, fcf),
        nonfin.rule_5_1(audit_rows),
        nonfin.rule_5_2(holder_periods),
        nonfin.rule_5_3(), nonfin.rule_5_4(), nonfin.rule_5_5(), nonfin.rule_5_6(),
        nonfin.rule_5_7(balance_latest),
        nonfin.rule_5_8(balance_rows, ta),
        nonfin.rule_5_9(),
        nonfin.rule_6_1(basic.industry),
        nonfin.rule_6_2(),
        nonfin.rule_s1(pledge_ratio),
        nonfin.rule_s2(balance_latest),
        nonfin.rule_s3(income_latest),
        nonfin.rule_s4(pledge_ratio, holder_drop),
    ]

    # ---- 深度模式：年报 PDF 规则（按需；失败降级不阻断）----
    deep_info = None
    if mode == "deep" and not historical:
        from sqa.minesweeper.deep import run_deep

        rev_growth = None
        if len(revenue) >= 2 and revenue[-1] is not None and revenue[-2]:
            rev_growth = revenue[-1] / revenue[-2] - 1
        report_year = years[-1] if years else datetime.now().year - 1
        try:
            deep = run_deep(symbol=basic.symbol, company_name=basic.name,
                            year=report_year, revenue_growth=rev_growth,
                            pdf_path=pdf_path, pdf_url=pdf_url, refresh=refresh)
        except Exception as exc:  # noqa: BLE001 - 深度模式任何异常都不得击穿快筛结果
            deep = {"status": "error",
                    "message": f"深度模式异常: {type(exc).__name__}: {exc}",
                    "pdf": {"path": "", "source": "", "title": "", "url": "",
                            "pages": 0, "backend": ""},
                    "rules": [], "override_5_8": None, "evidence": {}}
        if deep.get("status") == "ok":
            replaced = {r.rule: r for r in deep.get("rules", [])}
            results = [replaced.get(r.rule, r) for r in results]
            override = deep.get("override_5_8")
            if override is not None:
                results = [override if r.rule == "5.8" else r for r in results]
            # 5.2 深度模式补充：仅当快筛判 WARN/FAIL 且年报明确披露"划转/EB 换股
            # 导致持股减少"时覆盖为 PASS（避免无披露时放宽真实减持）
            override_52 = deep.get("override_5_2")
            if override_52 is not None:
                results = [
                    override_52 if (r.rule == "5.2"
                                    and r.verdict.value in ("WARN", "FAIL")) else r
                    for r in results
                ]
        deep_info = {k: v for k, v in deep.items()
                     if k not in ("rules", "override_5_8", "override_5_2", "evidence")}
        deep_info["evidence"] = deep.get("evidence", {})
        if deep.get("status") == "ok":
            deep_info["verdicts"] = {r.rule: r.verdict.value
                                     for r in deep.get("rules", [])}
            if deep.get("override_5_8") is not None:
                deep_info["verdicts"]["5.8"] = deep["override_5_8"].verdict.value
            if deep.get("override_5_2") is not None:
                deep_info["verdicts"]["5.2"] = deep["override_5_2"].verdict.value

    gate = assess(results, cfg.ms_gate_extreme, cfg.ms_gate_high, cfg.ms_gate_mid)

    return {
        "symbol": basic.symbol,
        "name": basic.name,
        "mode": mode,
        **gate,
        "rules": [
            {"id": r.rule, "layer": r.layer, "verdict": r.verdict.value, "detail": r.detail}
            for r in results
        ],
        "source_version": SOURCE_VERSION,
        "deep": deep_info,
        "as_of": as_of,
    }
