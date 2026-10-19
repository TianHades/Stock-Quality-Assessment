"""阈值配置加载：YAML 覆盖内置默认值，环境变量覆盖 YAML。

所有可调参数（docs/01 5.0 节）集中于此，业务代码禁止硬编码阈值。
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "thresholds.yaml"


@dataclass(frozen=True)
class Thresholds:
    # cycle
    gm_range_pp: float = 10.0
    np_drawdown: float = 0.5
    # dividend
    yield_floor: float = 3.0
    yield_rf_spread_pp: float = 1.5
    consecutive_years: int = 5
    payout_min: float = 0.25
    payout_warn: float = 0.90
    fcf_cover_max: float = 1.0
    # style
    growth_rev_cagr: float = 0.15
    growth_np_cagr: float = 0.20
    value_pctl: float = 0.30
    value_rev_cagr_max: float = 0.10
    val_pctl_window_years: int = 5
    # data
    history_years: int = 10
    min_years_for_quant: int = 5
    # minesweeper（02 号文档 §4 闸门 + L2.3 基准利率）
    ms_base_rate: float = 3.5
    ms_gate_extreme: int = 46
    ms_gate_high: int = 26
    ms_gate_mid: int = 11


_MINESWEEPER_KEY_MAP = {
    "base_rate": "ms_base_rate",
    "gate_extreme": "ms_gate_extreme",
    "gate_high": "ms_gate_high",
    "gate_mid": "ms_gate_mid",
}


def load_thresholds(path: Path | None = None) -> Thresholds:
    """加载阈值：内置默认值 <- YAML <- 环境变量（RISK_FREE_RATE 等少数项）。"""
    load_dotenv(PROJECT_ROOT / ".env")

    data: dict = {}
    cfg_path = path or CONFIG_PATH
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        for section_name, section in raw.items():
            if not isinstance(section, dict):
                continue
            for key, value in section.items():
                # YAML 使用简洁键名（base_rate/gate_*），dataclass 用 ms_ 前缀；
                # 此前不做映射导致闸门参数被静默忽略。
                if section_name == "minesweeper":
                    key = _MINESWEEPER_KEY_MAP.get(key, key)
                data[key] = value

    defaults = Thresholds().__dict__
    merged = {**defaults, **{k: v for k, v in data.items() if k in defaults}}
    return Thresholds(**merged)


def risk_free_rate(client=None) -> float:
    """10Y 国债收益率：环境变量静态值 > tushare yc_cb > akshare 实时 > 默认 2%。

    金标准教训：akshare 表格末行可能是空行（NaN 直接泄出，导致股息利差 nan）；
    且 max(3.0, nan) 在 Python 中偶然返回 3.0，掩盖了污染。必须做值域校验。
    tushare yc_cb（中债国债收益率曲线）比 akshare 更稳定，传入 client 时优先尝试。
    """
    from datetime import datetime, timedelta

    rf = os.environ.get("RISK_FREE_RATE", "")
    if rf:
        try:
            return float(rf)
        except ValueError:
            pass

    if client is not None:
        try:  # pragma: no cover - 依赖 tushare 权限与网络
            today = datetime.now()
            rows = client.query(
                "yc_cb", ts_code="1001.CB", curve_type="0", curve_term="10",
                start_date=(today - timedelta(days=30)).strftime("%Y%m%d"),
                end_date=today.strftime("%Y%m%d"),
                fields="trade_date,yield",
            )
            for row in reversed(rows or []):
                value = row.get("yield")
                if value is None:
                    continue
                value = float(value)
                if math.isfinite(value) and 0.0 < value < 10.0:  # 合理值域（%）
                    return value / 100.0
        except Exception:
            pass

    try:  # pragma: no cover - 依赖 akshare 可选
        import akshare as ak  # type: ignore

        df = ak.bond_zh_us_rate(start_date="20260101")
        col = "中国国债收益率10年"
        if col not in getattr(df, "columns", []):
            return 0.02
        for _, row in reversed(list(df.iterrows())):
            v = row.get(col)
            if v is None:
                continue
            value = float(v)
            if math.isfinite(value) and 0.0 < value < 10.0:  # 合理值域（%）
                return value / 100.0
        return 0.02
    except Exception:
        return 0.02
