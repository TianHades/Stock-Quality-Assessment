"""金标准集成测试：需要网络与 TUSHARE_TOKEN。

默认跳过；开启方式:
    SQA_INTEGRATION=1 pytest tests/test_golden_set.py -v
或直接: sqa golden
"""
import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("SQA_INTEGRATION") != "1",
        reason="需要网络与 token，设置 SQA_INTEGRATION=1 开启",
    ),
]


def test_golden_set_passes():
    pytest.importorskip("tushare")
    from sqa.golden import run_golden

    results = run_golden()
    failed = [r for r in results if not r["pass"]]
    assert not failed, "金标准未全过:\n" + "\n".join(
        f"{r['symbol']} {r['name']}: 预期{r['expected_style']}/{r['expected_badges']}"
        f"/{r['expected_tag']} 实际{r['actual_style']}/{r['actual_badges']}/{r['actual_tag']}"
        for r in failed
    )
