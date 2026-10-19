"""tushare 客户端：JSON 文件缓存 + 简单限速。所有接口调用必须经过这里。

缓存按 (接口名, 参数) 哈希落盘到 data_cache/，重复分析零 API 消耗；
--refresh 强制刷新。缓存文件含抓取日期，便于追溯数据口径。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pandas as pd

from sqa.config import PROJECT_ROOT


class SqaDataError(RuntimeError):
    """数据获取失败（网络/权限/字段缺失），调用方应降级为 SKIP 而非崩溃。"""


class TushareClient:
    def __init__(self, token: str | None = None, cache_dir: Path | None = None,
                 min_interval: float = 0.35, max_retries: int = 2,
                 retry_delay: float = 1.0) -> None:
        self._token = token
        self._pro = None
        self.cache_dir = cache_dir or PROJECT_ROOT / "data_cache"
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._last_call = 0.0

    @property
    def pro(self):
        if self._pro is None:
            import tushare as ts

            if not self._token:
                raise SqaDataError(
                    "TUSHARE_TOKEN 未设置（复制 .env.example 为 .env 并填写，"
                    "或设置环境变量）"
                )
            self._pro = ts.pro_api(self._token)
        return self._pro

    def query(self, api: str, refresh: bool = False, **params) -> list[dict]:
        """调用 tushare 接口并返回 records（list[dict]，NaN 已转 None）。"""
        cache_file = self._cache_path(api, params)
        if cache_file.exists() and not refresh:
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
            return payload["records"]

        df = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                df = getattr(self.pro, api)(**params)  # token 缺失由 pro property 抛错
                break
            except SqaDataError:
                raise  # 配置类错误不重试（token 未设置等）
            except Exception as exc:  # tushare 各类网络/权限异常统一重试
                if attempt >= self.max_retries:
                    raise SqaDataError(f"tushare {api} 调用失败: {exc}") from exc
                time.sleep(self.retry_delay * (2 ** attempt))

        if df is None or (hasattr(df, "empty") and df.empty):
            records: list[dict] = []
        else:
            cleaned = df.astype(object).where(pd.notna(df), None)
            records = cleaned.to_dict(orient="records")

        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(
                {"fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"), "api": api,
                 "params": {k: str(v) for k, v in params.items()}, "records": records},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return records

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.time()

    def _cache_path(self, api: str, params: dict) -> Path:
        key = json.dumps({k: str(v) for k, v in sorted(params.items())}, sort_keys=True)
        digest = hashlib.sha1(f"{api}:{key}".encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{api}_{digest}.json"
