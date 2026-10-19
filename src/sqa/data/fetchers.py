"""取数器：把 tushare 原始记录整理成规则可直接消费的年度序列。

口径统一：只取年报（end_date 以 1231 结尾）；同一报告期多条记录（调整口径）
按公告日期去重取最新。金额单位为元，股息率/分位为比率或百分数（见各函数）。
"""
from __future__ import annotations

from datetime import datetime

from sqa.classify.models import StockBasic
from sqa.data.tushare_client import SqaDataError, TushareClient


def resolve_ts_code(symbol: str) -> str:
    """6位代码 → tushare ts_code。6 沪市；0/3 深市；8/4/920 北交所。"""
    code = symbol.strip()
    if "." in code:
        return code.upper()
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    if code.startswith(("8", "4")) or code.startswith("920"):
        return f"{code}.BJ"
    raise SqaDataError(f"无法识别的代码: {symbol}")


def fetch_basic(client: TushareClient, ts_code: str, refresh: bool = False) -> StockBasic:
    rows = client.query("stock_basic", refresh=refresh, ts_code=ts_code,
                        fields="ts_code,symbol,name,industry,list_date")
    if not rows:
        # 历史回测会碰到已退市代码：stock_basic 默认只查 L，需回退 D
        rows = client.query("stock_basic", refresh=refresh, ts_code=ts_code,
                            list_status="D",
                            fields="ts_code,symbol,name,industry,list_date,delist_date")
    if not rows:
        raise SqaDataError(f"stock_basic 无记录: {ts_code}")
    r = rows[0]
    return StockBasic(
        symbol=r.get("symbol") or ts_code.split(".")[0],
        ts_code=ts_code,
        name=r.get("name") or "",
        industry=r.get("industry") or "",
        list_date=str(r.get("list_date") or ""),
    )


def _annual_rows(rows: list[dict]) -> dict[int, dict]:
    """过滤年报与合并报表口径、按公告日期去重取最新 → {year: row}。

    report_type: 1=合并报表(累计) 2=单季合并 3=调整单季 4=调整合并(累计)。
    不过滤会取到单季报表（金标准校准教训：神华 EPS 被单季口径污染成 0.6 元）。
    """
    valid_types = {"1", "4"}
    annual = [
        r for r in rows
        if str(r.get("end_date", "")).endswith("1231") and r.get("end_date")
        and str(r.get("report_type") or "1") in valid_types
    ]
    by_year: dict[int, dict] = {}
    for r in sorted(annual, key=lambda x: str(x.get("ann_date") or x.get("f_ann_date") or "")):
        by_year[int(str(r["end_date"])[:4])] = r
    return dict(sorted(by_year.items()))


def compute_fcf(cashflow_row: dict | None, indicator_row: dict | None = None) -> float | None:
    """统一 FCF 口径（排雷/质量/模块/分类共用）：

    优先 free_cashflow；缺失时 经营 CF − 购建长期资产（c_pay_acq_const_fiolta）；
    再缺失回退 fina_indicator 的 fcff。
    """
    row = cashflow_row or {}
    fcf = row.get("free_cashflow")
    if fcf is not None:
        return fcf
    ocf = row.get("n_cashflow_act")
    capex = row.get("c_pay_acq_const_fiolta")
    if ocf is not None and capex is not None:
        return ocf - capex
    if indicator_row:
        return indicator_row.get("fcff")
    return None


def fetch_annual_indicator(client: TushareClient, ts_code: str, years: int,
                           refresh: bool = False) -> dict[int, dict]:
    """年度财务指标 → {year: {gm, eps, profit_dedt}}（gm 单位 %）。"""
    start = f"{datetime.now().year - years}0101"
    rows = client.query("fina_indicator", refresh=refresh, ts_code=ts_code,
                        start_date=start,
                        fields="ts_code,end_date,ann_date,report_type,"
                               "grossprofit_margin,eps,profit_dedt,fcff")
    out: dict[int, dict] = {}
    for year, r in _annual_rows(rows).items():
        out[year] = {
            "gm": r.get("grossprofit_margin"),
            "eps": r.get("eps"),
            "profit_dedt": r.get("profit_dedt"),
            "fcff": r.get("fcff"),
        }
    return out


def fetch_annual_income(client: TushareClient, ts_code: str, years: int,
                        refresh: bool = False) -> dict[int, dict]:
    """年度利润表 → {year: {revenue, n_income}}（元）。"""
    start = f"{datetime.now().year - years}0101"
    rows = client.query("income", refresh=refresh, ts_code=ts_code,
                        start_date=start,
                        fields="ts_code,end_date,ann_date,f_ann_date,report_type,revenue,n_income_attr_p")
    out: dict[int, dict] = {}
    for year, r in _annual_rows(rows).items():
        out[year] = {
            "revenue": r.get("revenue"),
            "n_income": r.get("n_income_attr_p"),
        }
    return out


def fetch_annual_cashflow(client: TushareClient, ts_code: str, years: int,
                          refresh: bool = False) -> dict[int, dict]:
    """年度现金流量表 → {year: {ocf, fcf}}（元）。

    FCF 优先取接口的 free_cashflow 字段（tushare 实测存在），缺失时用
    经营现金流 − 购建长期资产支出估算。购建支出字段真实名为
    ``c_pay_acq_const_fiolta``（历史字段 ``buy_fixed_assets_oth`` 已不存在）。
    """
    start = f"{datetime.now().year - years}0101"
    rows = client.query("cashflow", refresh=refresh, ts_code=ts_code,
                        start_date=start,
                        fields="ts_code,end_date,ann_date,report_type,n_cashflow_act,"
                               "free_cashflow,c_pay_acq_const_fiolta")
    out: dict[int, dict] = {}
    for year, r in _annual_rows(rows).items():
        out[year] = {"ocf": r.get("n_cashflow_act"), "fcf": compute_fcf(r)}
    return out


def fetch_dividend_records(client: TushareClient, ts_code: str,
                           refresh: bool = False) -> dict[int, float]:
    """分红记录 → {财年: 该财年每股现金分红合计(元，税前)}。

    口径（2026-10 校准）：
    - 只保留 ``div_proc`` 含"实施"的行（排除预案/股东大会通过，避免同笔分红
      被预案+实施重复计；历史字段名保留兼容：div_proc 缺失时按旧口径处理）；
    - 用 ``cash_div_tax``（税前，与 01 号文档"神华 2.26"口径一致），缺失回退
      ``cash_div``；
    - 对 ``(end_date, 每股金额)`` 精确去重：tushare 偶发同一报告期出现两条
      同值"实施"公告（600519 2024/2025 实测），不去重会重复累计；
    - 中期分红（end_date=0630/0930）并入其所属财年。
    """
    rows = client.query("dividend", refresh=refresh, ts_code=ts_code,
                        fields="ts_code,end_date,ann_date,div_proc,cash_div,cash_div_tax")
    seen: set[tuple[str, float]] = set()
    per_year: dict[int, float] = {}
    for r in rows:
        end_date = str(r.get("end_date") or "")
        if not end_date:
            continue
        proc = str(r.get("div_proc") or "")
        if proc and "实施" not in proc:
            continue
        cash = r.get("cash_div_tax")
        if cash is None:
            cash = r.get("cash_div")
        if cash is None or float(cash) == 0.0:
            continue
        key = (end_date, round(float(cash), 6))
        if key in seen:
            continue
        seen.add(key)
        year = int(end_date[:4])
        per_year[year] = per_year.get(year, 0.0) + float(cash)
    return dict(sorted(per_year.items()))


def fetch_daily_basic(client: TushareClient, ts_code: str, years: int,
                      refresh: bool = False) -> list[dict]:
    """每日指标（PE/PB/PS/股息率/总股本）按日期升序 → 分位数与年末股息率。"""
    start = f"{datetime.now().year - years}0101"
    rows = client.query("daily_basic", refresh=refresh, ts_code=ts_code,
                        start_date=start,
                        fields="ts_code,trade_date,close,pe_ttm,pb,ps_ttm,dv_ratio,total_share")
    return sorted(rows, key=lambda r: str(r.get("trade_date") or ""))
