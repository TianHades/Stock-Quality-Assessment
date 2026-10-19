"""PDF 转文本（深度模式）：优先 pdftotext（poppler），回退 pypdf。

- pdftotext 抽取质量更好（-layout 保留表格列），但 Windows 默认没有安装；
- pypdf 是纯 Python 依赖，通过 pyproject 的 `pdf` 可选依赖提供；
- 抽取结果缓存到 data_cache/pdf_text/（按文件 sha1 索引），同 PDF 不重复解析；
- 扫描版 PDF（无文本层）抽取为空时抛 PdfTextError，调用方降级 SKIP。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sqa.config import PROJECT_ROOT

_MIN_TEXT_CHARS = 500  # 少于此字符数视为抽取失败（扫描版/加密）
_MIN_PAGE_COVERAGE = 0.10  # 非空页占比过低视为抽取失败（避免仅封面/页眉导致假 PASS）
_CHUNK_PAGES = 200     # 文本缓存按页分块，避免单文件过大


class PdfTextError(RuntimeError):
    """PDF 文本抽取失败。"""


@dataclass
class PdfDocument:
    path: Path
    pages: list[str]
    backend: str

    @property
    def text(self) -> str:
        return "\n".join(self.pages)

    @property
    def page_count(self) -> int:
        return len(self.pages)


def _pdf_sha1(pdf_path: Path) -> str:
    h = hashlib.sha1()
    with pdf_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _cache_path(cache_dir: Path, sha1: str, idx: int) -> Path:
    return cache_dir / f"{sha1}_{idx:03d}.json"


def _load_cache(cache_dir: Path, sha1: str) -> tuple[list[str], str] | None:
    if not cache_dir.exists():
        return None
    pages: list[str] = []
    backend = ""
    idx = 0
    while True:
        path = _cache_path(cache_dir, sha1, idx)
        if not path.exists():
            break
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        pages.extend(payload.get("pages") or [])
        backend = backend or str(payload.get("backend") or "")
        idx += 1
    return (pages, backend) if pages else None


def _save_cache(cache_dir: Path, sha1: str, pages: list[str], backend: str) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        for idx, start in enumerate(range(0, len(pages), _CHUNK_PAGES)):
            _cache_path(cache_dir, sha1, idx).write_text(
                json.dumps({"backend": backend,
                            "pages": pages[start:start + _CHUNK_PAGES]},
                           ensure_ascii=False),
                encoding="utf-8",
            )
    except OSError:
        pass  # 缓存失败不影响主流程


def _pdftotext_pages(pdf_path: Path) -> list[str]:
    """pdftotext -layout 输出按换页符 \f 分页（保留空页以对齐物理页码）。"""
    exe = shutil.which("pdftotext")
    if not exe:
        return []
    try:
        proc = subprocess.run(
            [exe, "-layout", "-enc", "UTF-8", str(pdf_path), "-"],
            capture_output=True, timeout=180, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    text = proc.stdout.decode("utf-8", errors="replace")
    pages = [p.strip() for p in text.split("\f")]
    while pages and not pages[-1]:
        pages.pop()  # 结尾换页符产生的空页不计
    return pages


def _pypdf_pages(pdf_path: Path) -> list[str]:
    try:
        from pypdf import PdfReader  # 可选依赖：pip install -e ".[pdf]"
    except ImportError as exc:
        raise PdfTextError(
            "未安装 pypdf，无法抽取 PDF 文本（pip install -e \".[pdf]\"，"
            "或安装 poppler 提供 pdftotext）"
        ) from exc
    try:
        reader = PdfReader(str(pdf_path))
        pages: list[str] = []
        for page in reader.pages:
            try:
                pages.append((page.extract_text() or "").strip())
            except Exception:  # noqa: BLE001 - 单页失败不放弃整本
                pages.append("")
        # 保留空页占位：evidence 的 page 号才等于 PDF 物理页号
        return pages
    except Exception as exc:  # noqa: BLE001 - pypdf 异常统一包装
        raise PdfTextError(f"pypdf 解析失败: {exc}") from exc


def extract_pdf(pdf_path: str | Path, refresh: bool = False) -> PdfDocument:
    """抽取 PDF 全部页文本（带磁盘缓存）。"""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise PdfTextError(f"PDF 不存在: {pdf_path}")
    try:
        sha1 = _pdf_sha1(pdf_path)
    except OSError as exc:
        raise PdfTextError(f"PDF 读取失败: {exc}") from exc

    cache_dir = PROJECT_ROOT / "data_cache" / "pdf_text"
    if not refresh:
        cached = _load_cache(cache_dir, sha1)
        if cached and sum(len(p) for p in cached[0]) >= _MIN_TEXT_CHARS:
            return PdfDocument(pdf_path, cached[0], cached[1] or "cache")

    pdftotext_error: PdfTextError | None = None
    try:
        dt_pages = _pdftotext_pages(pdf_path)
    except PdfTextError as exc:  # 保守兼容
        pdftotext_error, dt_pages = exc, []

    if sum(len(p) for p in dt_pages) >= _MIN_TEXT_CHARS:
        pages, backend = dt_pages, "pdftotext"
    else:
        # pdftotext 抽到的文本过短（封面/页眉/字体问题）时再尝试 pypdf，取更长的结果
        try:
            py_pages = _pypdf_pages(pdf_path)
        except PdfTextError as exc:
            if not dt_pages:
                raise exc
            py_pages = []
        if sum(len(p) for p in py_pages) >= sum(len(p) for p in dt_pages):
            pages, backend = py_pages, "pypdf"
        else:
            pages, backend = dt_pages, "pdftotext"
    total = sum(len(p) for p in pages)
    if total < _MIN_TEXT_CHARS:
        detail = f"（{pdftotext_error}）" if pdftotext_error else ""
        raise PdfTextError(
            f"PDF 文本抽取为空或过短（{total} 字符），可能是扫描版/加密 PDF{detail}"
        )
    non_empty = sum(1 for p in pages if p.strip())
    if len(pages) >= 10 and non_empty / len(pages) < _MIN_PAGE_COVERAGE:
        raise PdfTextError(
            f"PDF 文本页覆盖率过低（{non_empty}/{len(pages)}），"
            "可能大部分页面为扫描图，避免据此给出假 PASS"
        )
    _save_cache(cache_dir, sha1, pages, backend)
    return PdfDocument(pdf_path, pages, backend)
