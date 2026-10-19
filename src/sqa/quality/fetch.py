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
BAD_DEBT_PEER_LIMIT = 12   # 坏账对比样本：前 12 家，降低小样本中位数抖动
CHUNK = 20


def fetch_peer_medians(client: TushareClient, industry: str,
                       refresh: bool = False,
                       include_bad_debt: bool = False,
                       exclude_code: str = "",
                       as_of: str | None = None) -> dict | None:
    """同行业年报中位数：毛利率/资产负债率/总资产周转率/应收周转率（+坏账计提）。

    ``exclude_code`` 用于排除目标公司自身，避免"自己和自己比"。
    ``as_of``（YYYYMMDD）用于历史时点：只取该日期前已公告的同行报表；
    ``include_bad_debt=True`` 时额外取同行 income(credit_impa_loss) +
    balancesheet(accounts_receiv) 计算坏账计提比例中位数，供排雷 3.5 使用；
    同行数据不足返回 None 或对应键为 None，调用方降级 SKIP。
    """
    if not industry:
        return None
    try:
        peers = _industry_peers(client, industry, refresh, as_of=as_of)
        if exclude_code:
            peers = [code for code in peers if code != exclude_code]
        if len(peers) < 3:
            return None
        if len(peers) > MAX_PEERS:
            # 历史时点不能用当前市值挑同行（未来函数）；用代码序前 60 家，
            # 当前分析仍按市值取前 60 家。
            peers = peers[:MAX_PEERS] if as_of else _top_by_mv(client, peers, refresh)

        # 固定 year-1 在每年 1–4 月年报未披露时会取空；回退 year-2，并按
        # ts_code 去重后再算中位数（同一代码可能返回多条报告）。
        base_year = int(as_of[:4]) if as_of else datetime.now().year
        period, by_code = "", {}
        for offset in (1, 2):
            candidate = f"{base_year - offset}1231"
            rows_all: list[dict] = []
            for i in range(0, len(peers), CHUNK):
                codes = ",".join(peers[i:i + CHUNK])
                rows_all.extend(client.query(
                    "fina_indicator", refresh=refresh, ts_code=codes,
                    period=candidate,
                    fields="ts_code,ann_date,report_type,grossprofit_margin,"
                           "debt_to_assets,assets_turn,ar_turn"))
            for r in rows_all:
                if r.get("report_type") not in (None, "1"):
                    continue  # 只要合并报表口径
                if as_of and str(r.get("ann_date") or "") > as_of:
                    continue  # 历史时点：较晚公告的同行年报不纳入
                code = str(r.get("ts_code") or "")
                if code and code not in by_code:
                    by_code[code] = r
            if by_code:
                period = candidate
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
    bad_debt_ratio_agg = None
    n_bad_debt_peers = 0
    if include_bad_debt and period:
        bad_pairs: list[tuple[float, float]] = []  # (信用减值损失, 应收账款)
        # income/balancesheet 不支持多 ts_code 批量查询，单票查询代价高；
        # 坏账对比只取同行前 12 家；历史时点按代码序，不用当前市值（避免未来函数）。
        try:
            bad_peers = (peers if len(peers) <= BAD_DEBT_PEER_LIMIT
                         else (peers[:BAD_DEBT_PEER_LIMIT] if as_of
                               else _top_by_mv(client, peers, refresh,
                                               limit=BAD_DEBT_PEER_LIMIT)))
        except SqaDataError:
            bad_peers = peers[:BAD_DEBT_PEER_LIMIT]
        for code in bad_peers:
            try:
                inc_rows = client.query(
                    "income", refresh=refresh, ts_code=code, period=period,
                    fields="ts_code,ann_date,report_type,credit_impa_loss")
                bal_rows = client.query(
                    "balancesheet", refresh=refresh, ts_code=code, period=period,
                    fields="ts_code,ann_date,report_type,accounts_receiv,total_assets")
            except SqaDataError:
                continue

            def asof_ok(row: dict) -> bool:
                return not as_of or str(row.get("ann_date") or "") <= as_of

            loss = next((abs(float(r["credit_impa_loss"])) for r in inc_rows
                         if r.get("report_type") in (None, "1")
                         and asof_ok(r)
                         and r.get("credit_impa_loss") is not None), None)
            ar = next((float(r["accounts_receiv"]) for r in bal_rows
                       if r.get("report_type") in (None, "1")
                       and asof_ok(r)
                       and r.get("accounts_receiv") is not None), None)
            total_assets = next((float(r["total_assets"]) for r in bal_rows
                                 if r.get("report_type") in (None, "1")
                                 and asof_ok(r)
                                 and r.get("total_assets") is not None), None)
            # 与公司口径一致：只有应收账款具备重要性（≥总资产1%）的同行才入样，
            # 避免应收极小公司把"计提/应收"比例放大成离群值。
            if (loss is not None and ar and total_assets and total_assets > 0
                    and ar / total_assets >= 0.01):
                bad_pairs.append((loss, ar))
        if bad_pairs:
            bad_ratios = [loss / ar * 100.0 for loss, ar in bad_pairs]
            n_bad_debt_peers = len(bad_pairs)
            # 中位数保留作展示；规则使用"行业加权计提率"（总损失/总应收），
            # 对行业中少数小应收/一次性计提造成的极端比例更稳健（C 步校准）。
            bad_debt_ratio_median = median(bad_ratios)
            bad_debt_ratio_agg = (sum(loss for loss, _ in bad_pairs)
                                  / sum(ar for _, ar in bad_pairs) * 100.0)

    return {
        "gm_median": median(gms),
        "debt_median": median(debts) if debts else None,
        "assets_turn_median": median(assets_turns) if assets_turns else None,
        "ar_turn_median": median(ar_turns) if ar_turns else None,
        "bad_debt_ratio_median": bad_debt_ratio_median,
        "bad_debt_ratio_agg": bad_debt_ratio_agg,
        "n_bad_debt_peers": n_bad_debt_peers,
        "n_peers": len(gms),
        "period": period,
    }


def _industry_peers(client: TushareClient, industry: str, refresh: bool,
                    as_of: str | None = None) -> list[str]:
    basics = client.query("stock_basic", refresh=refresh, list_status="L",
                          fields="ts_code,industry,list_date")
    rows = [r for r in basics if r.get("industry") == industry]
    if as_of:
        # 历史时点不能把形成日之后才上市的公司算作同行
        rows = [r for r in rows
                if str(r.get("list_date") or "") <= as_of]
    return [r["ts_code"] for r in rows]


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
