"""Compact paper identity display."""

from pathlib import PurePosixPath

import streamlit as st

from apps.paper_library.components.status import health_label
from quantmind.library import PaperCatalogEntry


def display_title(entry: PaperCatalogEntry, override: str | None = None) -> str:
    """Apply the explicit personal/canonical/URI/hash title priority."""
    if override:
        return override
    if entry.title:
        return entry.title
    filename = PurePosixPath(entry.source_uri).name
    if filename:
        return filename
    return f"タイトル未取得 · {str(entry.source_revision_id)[:12]}"


def render_paper_card(entry: PaperCatalogEntry) -> None:
    """Render one compact source-revision identity card."""
    st.subheader(display_title(entry))
    st.caption(
        f"{', '.join(entry.authors) or '著者未取得'} · "
        f"{entry.page_count} pages · {health_label(entry.health)}"
    )
