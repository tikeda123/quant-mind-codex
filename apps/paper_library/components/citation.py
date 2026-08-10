"""Evidence-first citation cards for summaries and annotations."""

from collections.abc import Callable

import streamlit as st

from quantmind.knowledge import PaperCitation


def render_citation(
    citation: PaperCitation,
    *,
    key: str,
    on_open: Callable[[int, str | None], None] | None = None,
) -> None:
    """Render one exact page citation and an optional navigation action."""
    st.caption(f"原典 p.{citation.page_number}")
    if citation.quote:
        st.code(citation.quote, language=None, wrap_lines=True)
    if on_open is not None and st.button("原典で確認", key=key):
        on_open(citation.page_number, citation.quote)


def annotation_kind_label(kind: str) -> str:
    """Return a text label that keeps source fact and interpretation distinct."""
    return {
        "source_fact": "原典事実",
        "codex_interpretation": "Codex解釈",
        "user_note": "根拠付きユーザーノート",
    }.get(kind, kind)
