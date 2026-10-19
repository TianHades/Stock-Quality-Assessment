"""年报 PDF 获取（深度模式）：本地路径 / URL 下载 / 巨潮资讯网自动搜索。

来源与许可：
- URL 白名单、%PDF- magic 校验、重试退避逻辑移植自
  financial-report-minesweeper（scripts/download_report.py，已并入本工程）；
- 巨潮资讯网搜索模式参考龟龟投研（Turtle_investment，MIT）的 A 股部分并简化，
  仅保留 A 股年度报告，去除港股 AES 加密逻辑。

仅支持 A 股（本工程定位）。下载文件落在 data_cache/pdf/（已被 .gitignore 忽略）。
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from sqa.config import PROJECT_ROOT

PDF_MAGIC = b"%PDF-"
MIN_FILE_SIZE_WARNING = 100 * 1024  # 100KB
DOWNLOAD_TIMEOUT = 120
DEFAULT_MAX_RETRIES = 3
BACKOFF_BASE = 3  # seconds

# 只允许精确域名或其子域，避免 evilcninfo.com.cn 之类的仿冒域
ALLOWED_PDF_HOSTS = ("cninfo.com.cn", "stockn.xueqiu.com", "10jqka.com.cn")

CNINFO_SEARCH_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_PDF_BASE = "http://static.cninfo.com.cn"

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,application/octet-stream,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 年度报告标题排除词（摘要、审计报告等非正文材料）
CNINFO_EXCLUDE_KEYWORDS = (
    "摘要", "审计报告", "公告", "利润分配", "可持续发展", "股东大会", "ESG",
    "更正", "补充", "意见", "内部控制", "问询", "回复", "说明",
    "英文", "English", "H股", "港股",
)


def get_headers(url: str) -> dict:
    """按下载域名设置 Referer（巨潮/雪球/同花顺防盗链）。"""
    headers = dict(BASE_HEADERS)
    if "cninfo.com.cn" in url:
        headers["Referer"] = "https://www.cninfo.com.cn/"
    elif "10jqka.com.cn" in url:
        headers["Referer"] = "https://10jqka.com.cn/"
    else:
        headers["Referer"] = "https://xueqiu.com/"
    return headers


def validate_url(url: str) -> tuple[bool, str]:
    """校验 URL：受支持域名的精确匹配/子域 + .pdf 后缀。"""
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return False, "URL 格式非法"
    if parsed.scheme not in ("http", "https"):
        return False, "URL 必须是 http/https"
    host = (parsed.hostname or "").lower()
    host_ok = any(host == domain or host.endswith("." + domain)
                  for domain in ALLOWED_PDF_HOSTS)
    if not host_ok or not parsed.path.lower().endswith(".pdf"):
        return False, (
            "URL 必须是 cninfo.com.cn / stockn.xueqiu.com / 10jqka.com.cn "
            "域名下的 .pdf 链接"
        )
    return True, ""


def looks_like_pdf(path: Path) -> tuple[bool, str]:
    """检查文件头 magic bytes 与最小体积（文件系统异常也返回失败而不抛）。"""
    try:
        if not path.exists() or not path.is_file():
            return False, f"文件不存在或不是文件: {path}"
        with path.open("rb") as fh:
            head = fh.read(5)
        size = path.stat().st_size
    except OSError as exc:
        return False, f"PDF 读取失败: {exc}"
    if not head.startswith(PDF_MAGIC):
        return False, "PDF 校验失败：文件头不是 %PDF-"
    if size < MIN_FILE_SIZE_WARNING:
        return True, f"文件偏小（{size} 字节，<100KB），可能是摘要或损坏文件"
    return True, f"PDF {size / 1024 / 1024:.1f} MB"


def build_filename(stock_code: str, year: int, report_type: str = "年报") -> str:
    return f"{stock_code}_{report_type}_{year}.pdf"


def download_pdf(url: str, save_path: Path,
                 max_retries: int = DEFAULT_MAX_RETRIES) -> dict:
    """流式下载 PDF：临时文件落盘 → magic 校验 → 原子改名；失败重试退避。"""
    save_path = Path(save_path)
    try:
        save_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"success": False, "message": f"缓存目录不可写: {exc}",
                "path": "", "filesize": 0}
    last_error = ""
    for attempt in range(1, max_retries + 1):
        tmp = save_path.with_suffix(save_path.suffix + ".tmp")
        try:
            resp = requests.get(url, headers=get_headers(url),
                                timeout=DOWNLOAD_TIMEOUT, stream=True)
            resp.raise_for_status()
            total = 0
            first_chunk = True
            with tmp.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    if first_chunk:
                        if not chunk[:5].startswith(PDF_MAGIC):
                            tmp.unlink(missing_ok=True)
                            return {"success": False,
                                    "message": "PDF 校验失败：文件头不是 %PDF-",
                                    "path": "", "filesize": 0}
                        first_chunk = False
                    fh.write(chunk)
                    total += len(chunk)
            if save_path.exists():
                save_path.unlink()
            tmp.replace(save_path)
            ok, msg = looks_like_pdf(save_path)
            if not ok:
                save_path.unlink(missing_ok=True)
                return {"success": False, "message": msg,
                        "path": "", "filesize": 0}
            return {"success": True, "message": msg,
                    "path": str(save_path.resolve()), "filesize": total}
        except requests.exceptions.RequestException as exc:
            last_error = str(exc)
            tmp.unlink(missing_ok=True)
            if attempt < max_retries:
                time.sleep(BACKOFF_BASE * attempt)  # 3s / 6s
        except OSError as exc:  # 磁盘写入失败不重试
            return {"success": False, "message": f"写入失败: {exc}",
                    "path": "", "filesize": 0}
    return {"success": False,
            "message": f"下载失败（{max_retries} 次重试）: {last_error}",
            "path": "", "filesize": 0}


def _cninfo_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": BASE_HEADERS["User-Agent"],
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
        "X-Requested-With": "XMLHttpRequest",
    })
    return session


def search_cninfo_report(symbol: str, year: int, company_name: str = "",
                         timeout: int = 15) -> dict | None:
    """在巨潮资讯网检索某财年的年度报告正文 PDF。

    year 为财年（如 2025 表示 2025 年度报告）；窗口取次年 1–6 月。
    Returns {"url", "title", "publish_date"} 或 None。
    """
    clean_code = re.sub(r"^(SH|SZ)", "", str(symbol), flags=re.IGNORECASE)
    se_date = f"{year + 1}-01-01~{year + 1}-06-30"
    session = _cninfo_session()
    for searchkey in [k for k in (company_name, clean_code) if k]:
        payload = {
            "pageNum": 1, "pageSize": 30, "column": "szse",
            "tabName": "fulltext", "plate": "sz;sh",
            "searchkey": searchkey, "secid": "",
            "category": "category_ndbg_szsh",
            "trade": "", "seDate": se_date,
            "sortName": "code", "sortType": "asc", "isHLtitle": "false",
        }
        try:
            resp = session.post(CNINFO_SEARCH_URL, data=payload, timeout=timeout)
            resp.raise_for_status()
            announcements = resp.json().get("announcements") or []
        except Exception:  # noqa: BLE001 - 搜索失败由调用方降级 SKIP
            continue

        candidates = []
        for ann in announcements:
            if not isinstance(ann, dict):
                continue
            title = re.sub(r"<[^>]+>", "", str(ann.get("announcementTitle") or ""))
            sec_code = str(ann.get("secCode") or "")
            adjunct = str(ann.get("adjunctUrl") or "")
            if sec_code != clean_code or not adjunct:
                continue
            if not any(k in title for k in ("年度报告", "年报")):
                continue
            if any(ex in title for ex in CNINFO_EXCLUDE_KEYWORDS):
                continue
            compact = title.replace(" ", "")
            if f"{year}年" not in compact and f"{year}年度" not in compact:
                continue
            candidates.append({
                "url": f"{CNINFO_PDF_BASE}/{adjunct.lstrip('/')}",
                "title": title,
                "publish_date": str(ann.get("announcementTime") or ""),
            })
        if candidates:
            # “年度报告”优先于“年报”
            candidates.sort(key=lambda c: 0 if "年度报告" in c["title"] else 1)
            return candidates[0]
    return None


def resolve_pdf(symbol: str, company_name: str, year: int,
                pdf_path: str | Path | None = None,
                pdf_url: str | None = None,
                report_type: str = "年报",
                refresh: bool = False) -> dict:
    """获取年报 PDF：本地路径 > 指定 URL > 缓存 > 巨潮自动搜索（含上一年兜底）。

    返回 {"status": "ok"|"skipped"|"error", "path", "source", "message", "url", "title"}。
    ``refresh=True`` 时绕过缓存、重新下载；网络失败时若缓存仍可用则回退并注明。
    """
    if pdf_path:
        path = Path(pdf_path)
        ok, msg = looks_like_pdf(path)
        if not ok:
            return {"status": "error", "message": msg, "path": "",
                    "source": "local", "url": "", "title": ""}
        return {"status": "ok", "path": str(path.resolve()), "source": "local",
                "message": msg, "url": "", "title": ""}

    cache_dir = PROJECT_ROOT / "data_cache" / "pdf"
    if pdf_url:
        valid, err = validate_url(pdf_url)
        if not valid:
            return {"status": "error", "message": err, "path": "",
                    "source": "url", "url": pdf_url, "title": ""}
        target = cache_dir / build_filename(symbol, year, report_type)
        if refresh:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        result = download_pdf(pdf_url, target)
        return {"status": "ok" if result["success"] else "error",
                "path": result["path"], "source": "url",
                "message": result["message"], "url": pdf_url, "title": ""}

    def _cached(year_value: int) -> dict | None:
        target = cache_dir / build_filename(symbol, year_value, report_type)
        ok, msg = looks_like_pdf(target)
        if ok:
            return {"status": "ok", "path": str(target.resolve()),
                    "source": "cache", "message": f"{msg}（使用缓存）",
                    "url": "", "title": ""}
        return None

    # 先查缓存：断网/接口故障时也能复用已下载的年报
    if not refresh:
        for y in (year, year - 1):
            cached = _cached(y)
            if cached:
                return cached

    # 自动搜索：本财年找不到时回退上一年（年报披露滞后/数据年份边界）
    for y in (year, year - 1):
        found = search_cninfo_report(symbol, y, company_name)
        if not found:
            continue
        target = cache_dir / build_filename(symbol, y, report_type)
        if refresh:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        result = download_pdf(found["url"], target)
        if result["success"]:
            return {"status": "ok", "path": result["path"], "source": "cninfo",
                    "message": result["message"], "url": found["url"],
                    "title": found["title"]}

    # 网络失败时回退到已有缓存（refresh 模式下也允许）
    for y in (year, year - 1):
        cached = _cached(y)
        if cached:
            cached["message"] = "巨潮检索/下载失败，" + cached["message"]
            return cached
    return {"status": "skipped",
            "message": f"巨潮未检索到 {year} 年年报 PDF（可用 --pdf/--pdf-url 指定）",
            "path": "", "source": "cninfo", "url": "", "title": ""}
