"""同行中位数取数（质量评分的行业相对锚点 + 排雷 3.5 共用）。

口径：同行业（tushare industry 字符串精确匹配）上市公司在最近完整财年
年报的毛利率/资产负债率/总资产周转率/应收周转率中位数；排雷 3.5 额外取
同行 income.credit_impa_loss + balancesheet.accounts_receiv 的坏账计提比例中位数。

实现约束（2026-10 校准）：当前 token 积分档不支持不带 ts_code 的全市场
fina_indicator 调用（报"必填参数 ts_code"），改为——
  同行业清单（stock_basic，一次缓存）→ 按总市值取前 60 家（daily_basic
  全市场快照，一次缓存）→ 20 个代码一组拼 ts_code 分块调用。
income/balancesheet 不支持多代码批量，3.5 只取同行市值前 8 家单票查询。
样本 <3 时返回 None 或对应键 None，调用方降级 SKIP——绝不硬造同行数据。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqa.data.tushare_client import SqaDataError, TushareClient
from sqa.quality.metrics import median

MAX_PEERS = 60
CHUNK = 20


def fetch_peer_medians(client: TushareClient, industry: str,
                       refresh: bool = False,
                       include_bad_debt: bool = False) -> dict | None:
    """同行业年报中位数：毛利率/资产负债率/总资产周转率/应收周转率（+坏账计提）。

    ``include_bad_debt=True`` 时额外取同行 income(credit_impa_loss) +
    balancesheet(accounts_receiv) 计算坏账计提比例中位数，供排雷 3.5 使用；
    同行数据不足返回 None 或对应键为 None，调用方降级 SKIP。
    """
    if not industry:
        return None
    try:
        peers = _industry_peers(client, industry, refresh)
        if len(peers) < 3:
            return None
        if len(peers) > MAX_PEERS:
            peers = _top_by_mv(client, peers, refresh)

        # 固定 year-1 在每年 1–4 月年报未披露时会取空；回退 year-2，并按
        # ts_code 去重后再算中位数（同一代码可能返回多条报告）。
        period, by_code = "", {}
        for offset in (1, 2):
            candidate = f"{datetime.now().year - offset}1231"
            rows_all: list[dict] = []
            for i in range(0, len(peers), CHUNK):
                codes = ",".join(peers[i:i + CHUNK])
                rows_all.extend(client.query(
                    "fina_indicator", refresh=refresh, ts_code=codes,
                    period=candidate,
                    fields="ts_code,report_type,grossprofit_margin,debt_to_assets,"
                           "assets_turn,ar_turn"))
            if rows_all:
                period = candidate
                for r in rows_all:
                    if r.get("report_type") not in (None, "1"):
                        continue  # 只要合并报表口径
                    code = str(r.get("ts_code") or "")
                    if code and code not in by_code:
                        by_code[code] = r
                break
        gms = [float(r["grossprofit_margin"]) for r in by_code.values()
               if r.get("grossprofit_margin") is not None]
        debts = [float(r["debt_to_assets"]) for r in by_code.values()
                 if r.get("debt_to_assets") is not None]
        assets_turns = [float(r["assets_turn"]) for r in by_code.values()
                        if r.get("assets_turn") is not None]
        ar_turns = [float(r["ar_turn"]) for r in by_code.values()
                    if r.get("ar_turn") is not None]
    except SqaDataError:
        return None

    if len(gms) < 3:
        return None

    bad_debt_ratio_median = None
    n_bad_debt_peers = 0
    if include_bad_debt and period:
        bad_ratios: list[float] = []
        # income/balancesheet 不支持多 ts_code 批量查询，单票查询代价高；
        # 坏账对比只取同行市值前 8 家，样本足够算中位数且控制冷启动耗时。
        try:
            bad_peers = (peers if len(peers) <= 8
                         else _top_by_mv(client, peers, refresh, limit=8))
        except SqaDataError:
            bad_peers = peers[:8]
        for code in bad_peers:
            try:
                inc_rows = client.query(
                    "income", refresh=refresh, ts_code=code, period=period,
                    fields="ts_code,report_type,credit_impa_loss")
                bal_rows = client.query(
                    "balancesheet", refresh=refresh, ts_code=code, period=period,
                    fields="ts_code,report_type,accounts_receiv,total_assets")
            except SqaDataError:
                continue
            loss = next((abs(float(r["credit_impa_loss"])) for r in inc_rows
                         if r.get("report_type") in (None, "1")
                         and r.get("credit_impa_loss") is not None), None)
            ar = next((float(r["accounts_receiv"]) for r in bal_rows
                       if r.get("report_type") in (None, "1")
                       and r.get("accounts_receiv") is not None), None)
            if loss is not None and ar and ar > 0:
                bad_ratios.append(loss / ar * 100.0)
        if len(bad_ratios) >= 3:
            bad_debt_ratio_median = median(bad_ratios)
            n_bad_debt_peers = len(bad_ratios)

    return {
        "gm_median": median(gms),
        "debt_median": median(debts) if debts else None,
        "assets_turn_median": median(assets_turns) if assets_turns else None,
        "ar_turn_median": median(ar_turns) if ar_turns else None,
        "bad_debt_ratio_median": bad_debt_ratio_median,
        "n_bad_debt_peers": n_bad_debt_peers,
        "n_peers": len(gms),
        "period": period,
    }


def _industry_peers(client: TushareClient, industry: str, refresh: bool) -> list[str]:
    basics = client.query("stock_basic", refresh=refresh, list_status="L",
                          fields="ts_code,industry")
    return [r["ts_code"] for r in basics if r.get("industry") == industry]


def _top_by_mv(client: TushareClient, codes: list[str], refresh: bool,
               limit: int = MAX_PEERS) -> list[str]:
    """按总市值取前 limit 家（大市值公司报表代表性更强）。快照失败则按原序截断。"""
    snapshot = _latest_market_mv(client, refresh)
    if snapshot:
        ranked = sorted(codes, key=lambda c: snapshot.get(c, 0.0), reverse=True)
        return ranked[:limit]
    return codes[:limit]


def _latest_market_mv(client: TushareClient, refresh: bool) -> dict[str, float]:
    """最近一个交易日的全市场总市值快照（逐日回退最多 7 天找有效交易日）。"""
    day = datetime.now() - timedelta(days=1)
    for _ in range(7):
        trade_date = day.strftime("%Y%m%d")
        try:
            rows = client.query("daily_basic", refresh=refresh, trade_date=trade_date,
                                fields="ts_code,total_mv")
        except SqaDataError:
            rows = []
        if rows:
            return {r["ts_code"]: float(r.get("total_mv") or 0.0) for r in rows}
        day -= timedelta(days=1)
    return {}
