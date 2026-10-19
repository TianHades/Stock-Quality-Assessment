"""配置加载与代码/阈值契约的离线测试。"""
from __future__ import annotations

import textwrap
from pathlib import Path

from sqa.config import load_thresholds
from sqa.data.fetchers import resolve_ts_code


def test_yaml_minesweeper_keys_are_mapped(tmp_path: Path):
    """YAML 的 base_rate/gate_* 简写必须映射到 ms_* 字段（此前被静默忽略）。"""
    cfg = tmp_path / "thresholds.yaml"
    cfg.write_text(textwrap.dedent("""\
        minesweeper:
          base_rate: 9.9
          gate_extreme: 99
          gate_high: 88
          gate_mid: 77
        data:
          history_years: 7
        """), encoding="utf-8")
    thresholds = load_thresholds(cfg)
    assert thresholds.ms_base_rate == 9.9
    assert thresholds.ms_gate_extreme == 99
    assert thresholds.ms_gate_high == 88
    assert thresholds.ms_gate_mid == 77
    assert thresholds.history_years == 7


def test_beijing_920_code_supported():
    assert resolve_ts_code("920202") == "920202.BJ"
    assert resolve_ts_code("830799") == "830799.BJ"
