"""排雷引擎取数：审计/质押/股东 + 显式字段年报三表（字段名集中管理）。"""
from __future__ import annotations

from datetime import datetime

from sqa.data.fields import ANNUAL_FULL_FIELDS
from sqa.data.tushare_client import TushareClient
from sqa.data.fetchers import _annual_rows  # 同一套年报过滤/去重口径


def fetch_annual_full(client: TushareClient, api: str, ts_code: str, years: int,
                      refresh: bool = False,
                      end_date: str | None = None) -> dict[int, dict]:
    """年报报表（显式 fields，含可选列；字段名集中见 sqa.data.fields）。

    ``end_date`` 用于历史时点回测：只取该日期之前披露的报表（YYYYMMDD），
    默认 None 表示最新。不传 fields 时 tushare 只返回默认列，会缺
    credit_impa_loss/inv_turn 等可选字段（金标准教训：多条规则曾因字段缺失静默 SKIP）。
    """
    end_year = int(end_date[:4]) if end_date else datetime.now().year
    start = f"{end_year - years}0101"
    params = dict(ts_code=ts_code, start_date=start)
    if end_date:
        params["end_date"] = end_date
    fields = ANNUAL_FULL_FIELDS.get(api)
    if fields:
        params["fields"] = fields
    rows = client.query(api, refresh=refresh, **params)
    if end_date:
        # 历史时点必须按“公告日”截断，而不是按报告期：例如 2022 年报在
        # 2023-04-30 之后才披露，就不应出现在 as_of=20230430 的快照里。
        rows = [
            r for r in rows
            if str(r.get("ann_date") or r.get("f_ann_date") or r.get("end_date") or "")
            <= end_date
        ]
    return _annual_rows(rows)


def fetch_audit(client: TushareClient, ts_code: str, refresh: bool = False,
                end_date: str | None = None) -> list[dict]:
    """审计记录（按 end_date 升序，年报口径）→ 供 L0.1/L0.2/L5.1。"""
    rows = client.query("fina_audit", refresh=refresh, ts_code=ts_code,
                        fields="ts_code,end_date,ann_date,audit_result,audit_agency")
    annual = [r for r in rows if str(r.get("end_date", "")).endswith("1231")]
    if end_date:
        annual = [
            r for r in annual
            if str(r.get("ann_date") or r.get("end_date") or "") <= end_date
        ]
    return sorted(annual, key=lambda r: str(r.get("end_date")))


def fetch_pledge_ratio(client: TushareClient, ts_code: str,
                       refresh: bool = False,
                       as_of: str | None = None) -> float | None:
    """股权质押比例（%，pledge_stat 全公司口径）；as_of 时取该日之前最近一期。"""
    from sqa.data.tushare_client import SqaDataError

    try:
        rows = client.query("pledge_stat", refresh=refresh, ts_code=ts_code)
    except SqaDataError:
        return None
    if not rows:
        return None
    if as_of:
        rows = [r for r in rows if str(r.get("end_date") or "") <= as_of]
        if not rows:
            return None
    latest = max(rows, key=lambda r: str(r.get("end_date") or ""))
    ratio = latest.get("total_ratio") or latest.get("pledge_ratio")
    return float(ratio) if ratio is not None else None


# 名义持有人账户（H 股代理/沪深股通托管/中央结算）：不是"第一大股东"，
# 其持股比例变化由 H 股配售/送转/托管迁移驱动，不能作为大股东减持信号。
_NOMINEE_KEYWORDS = ("HKSCC", "NOMINEES", "香港中央结算", "中央结算",
                     "名义持有人", "代理人", "GLOBAL CUSTODIAN")


def _is_nominee_holder(name: str) -> bool:
    upper = (name or "").upper()
    return any(keyword.upper() in upper for keyword in _NOMINEE_KEYWORDS)


def fetch_holder_series(client: TushareClient, ts_code: str,
                        periods: int = 4, refresh: bool = False,
                        as_of: str | None = None) -> list[dict]:
    """前十大股东逐期明细（年报报告期，旧→新）。

    返回 ``[{"end_date": str, "holders": [{name, ratio, amount, nominee}]}]``，
    供 5.2 / S4 / 质量治理维度区分名义持有人、判断"减持 vs 稀释/划转"。
    ``as_of`` 时只保留该日期之前的报告期，避免历史回测未来函数。
    """
    from sqa.data.tushare_client import SqaDataError

    try:
        rows = client.query("top10_holders", refresh=refresh, ts_code=ts_code,
                            fields="ts_code,end_date,holder_name,hold_amount,hold_ratio")
    except SqaDataError:
        return []

    by_period: dict[str, list[dict]] = {}
    for r in rows:
        end_date = str(r.get("end_date") or "")
        ratio = r.get("hold_ratio")
        if not end_date.endswith("1231") or ratio is None:
            continue
        if as_of and end_date > as_of:
            continue
        name = str(r.get("holder_name") or "")
        amount = r.get("hold_amount")
        by_period.setdefault(end_date, []).append({
            "name": name,
            "ratio": float(ratio),
            "amount": float(amount) if amount is not None else None,
            "nominee": _is_nominee_holder(name),
        })
    ordered = sorted(by_period)
    if periods > 0:
        ordered = ordered[-periods:]
    return [{"end_date": p, "holders": by_period[p]} for p in ordered]


def holder_ratio_series(periods: list[dict]) -> list[float | None]:
    """逐期"第一大股东（剔除名义持有人）"持股比例序列。"""
    out: list[float | None] = []
    for period in periods:
        holders = period.get("holders") or []
        candidates = [h for h in holders if not h.get("nominee")] or holders
        out.append(max(h["ratio"] for h in candidates) if candidates else None)
    return out


def fetch_holder_ratio_series(client: TushareClient, ts_code: str,
                              periods: int = 4, refresh: bool = False,
                              as_of: str | None = None) -> list[float | None]:
    """第一大股东持股比例时间序列（年报报告期，旧→新）。

    2026-10-25 复核修正：原实现取每期 ``max(hold_ratio)``，会把
    HKSCC NOMINEES LIMITED / 香港中央结算有限公司等名义持有人当成第一大股东
    （比亚迪 H 股代理 40.4%、中芯 56.0%），导致 5.2 与质量治理 holder_change
    度量的是名义账户变化；现改为剔除名义持有人后取最大持股比例。
    """
    return holder_ratio_series(fetch_holder_series(
        client, ts_code, periods=periods, refresh=refresh, as_of=as_of))
