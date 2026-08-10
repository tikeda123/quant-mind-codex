"""Bounded one-page PDF rendering for citation inspection."""

import hashlib
import threading
from collections import OrderedDict

import pymupdf
from pydantic import BaseModel, ConfigDict, Field

_MAX_CACHE_PAGES = 32
_MAX_CACHE_BYTES = 256 * 1024 * 1024


class PaperPreviewError(ValueError):
    """A PDF page could not be rendered safely for the UI."""


class RenderedPaperPage(BaseModel):
    """One bounded rasterized source page and visual-match metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number: int = Field(ge=1)
    page_count: int = Field(ge=1)
    png_bytes: bytes
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    highlight_found: bool
    highlight_count: int = Field(ge=0)


_CacheKey = tuple[str, int, str | None, float]
_CACHE: OrderedDict[_CacheKey, RenderedPaperPage] = OrderedDict()
_CACHE_BYTES = 0
_CACHE_LOCK = threading.RLock()


def _cache_get(key: _CacheKey) -> RenderedPaperPage | None:
    with _CACHE_LOCK:
        value = _CACHE.pop(key, None)
        if value is not None:
            _CACHE[key] = value
        return value


def _cache_put(key: _CacheKey, value: RenderedPaperPage) -> None:
    global _CACHE_BYTES
    with _CACHE_LOCK:
        previous = _CACHE.pop(key, None)
        if previous is not None:
            _CACHE_BYTES -= len(previous.png_bytes)
        _CACHE[key] = value
        _CACHE_BYTES += len(value.png_bytes)
        while _CACHE and (
            len(_CACHE) > _MAX_CACHE_PAGES or _CACHE_BYTES > _MAX_CACHE_BYTES
        ):
            _, evicted = _CACHE.popitem(last=False)
            _CACHE_BYTES -= len(evicted.png_bytes)


def render_cited_page(
    pdf_bytes: bytes,
    *,
    page_number: int,
    quote: str | None,
    scale: float = 1.5,
) -> RenderedPaperPage:
    """Render one 1-based page and overlay every exact visual quote match."""
    if page_number < 1:
        raise PaperPreviewError("page number must be 1-based")
    if not 0.5 <= scale <= 4.0:
        raise PaperPreviewError("preview scale must be between 0.5 and 4.0")
    source_hash = hashlib.sha256(pdf_bytes).hexdigest()
    quote_hash = (
        hashlib.sha256(quote.encode("utf-8")).hexdigest()
        if quote is not None
        else None
    )
    key = (source_hash, page_number, quote_hash, scale)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise PaperPreviewError("PDFを開けませんでした") from exc
    try:
        if page_number > document.page_count:
            raise PaperPreviewError(
                f"page {page_number} is outside 1-{document.page_count}"
            )
        page = document.load_page(page_number - 1)
        rectangles = page.search_for(quote) if quote else []
        for rectangle in rectangles:
            annotation = page.add_highlight_annot(rectangle)
            annotation.set_colors(stroke=(1.0, 0.85, 0.0))
            annotation.update()
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(scale, scale),
            alpha=False,
            annots=True,
        )
        value = RenderedPaperPage(
            page_number=page_number,
            page_count=document.page_count,
            png_bytes=pixmap.tobytes("png"),
            width=pixmap.width,
            height=pixmap.height,
            highlight_found=bool(rectangles),
            highlight_count=len(rectangles),
        )
    except PaperPreviewError:
        raise
    except Exception as exc:
        raise PaperPreviewError("PDF pageを描画できませんでした") from exc
    finally:
        document.close()
    _cache_put(key, value)
    return value


def _clear_preview_cache() -> None:
    global _CACHE_BYTES
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE_BYTES = 0


def _preview_cache_info() -> tuple[int, int]:
    with _CACHE_LOCK:
        return len(_CACHE), _CACHE_BYTES
