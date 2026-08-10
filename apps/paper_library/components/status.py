"""Text-first status labels that never rely on color alone."""

from typing import Literal

import streamlit as st

_HEALTH_LABELS = {
    "ready": "✅ 検索可能",
    "attention": "⚠️ 要確認",
    "broken": "⛔ 破損",
}


def health_label(value: Literal["ready", "attention", "broken"]) -> str:
    """Return a text-and-icon label for one health state."""
    return _HEALTH_LABELS[value]


def render_local_badge() -> None:
    """Render the always-visible local-only execution boundary."""
    st.caption("🔒 ローカル限定 · 外部LLM API呼び出しなし")
