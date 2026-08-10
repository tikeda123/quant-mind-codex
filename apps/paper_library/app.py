"""Streamlit entrypoint for the local-only QuantMind paper library."""

# ruff: noqa: E402

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from apps.paper_library.components.status import render_local_badge
from apps.paper_library.service import (
    PaperLibraryAppService,
    validate_loopback_address,
)
from apps.paper_library.views import (
    audit,
    dashboard,
    intake,
    library,
    paper_detail,
    search,
)


def _configured_path(name: str, default: Path) -> Path:
    value = Path(os.environ.get(name, str(default))).expanduser()
    if not value.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return value.resolve()


@st.cache_resource
def _service() -> PaperLibraryAppService:
    root = Path.cwd().resolve() / ".quantmind"
    return PaperLibraryAppService(
        knowledge_db_path=_configured_path(
            "QUANTMIND_LIBRARY_DB", root / "paper_library.sqlite3"
        ),
        sidecar_db_path=_configured_path(
            "QUANTMIND_UI_DB", root / "paper_library_ui.sqlite3"
        ),
        model_cache_path=_configured_path(
            "QUANTMIND_MODEL_CACHE", root / "models"
        ),
        intake_work_root=_configured_path(
            "QUANTMIND_INTAKE_ROOT", root / "intake"
        ),
    )


def main() -> None:
    """Configure and run the six-page local Streamlit application."""
    validate_loopback_address(
        os.environ.get("STREAMLIT_SERVER_ADDRESS", "127.0.0.1")
    )
    st.set_page_config(
        page_title="QuantMind Paper Library",
        page_icon="📚",
        layout="wide",
    )
    service = _service()
    st.sidebar.title("QuantMind Paper Library")
    render_local_badge()
    stats = service.inspect_library()
    st.sidebar.caption(
        f"{service.knowledge_db_path.name} · ready {stats.search_ready_count} · "
        f"attention {stats.attention_count} · broken {stats.broken_count}"
    )
    with st.sidebar.expander("設定path"):
        st.code(
            f"Knowledge DB: {service.knowledge_db_path}\n"
            f"Sidecar DB: {service.sidecar_db_path}\n"
            f"Model cache: {service.model_cache_path}\n"
            f"Intake root: {service.intake_work_root}"
        )
    detail_page = st.Page(
        lambda: paper_detail.render(service),
        title="論文詳細",
        url_path="paper-detail",
    )
    st.session_state["_detail_page"] = detail_page
    pages = {
        "活用": [
            st.Page(
                lambda: dashboard.render(service),
                title="ダッシュボード",
                url_path="dashboard",
                default=True,
            ),
            st.Page(
                lambda: library.render(service),
                title="蔵書",
                url_path="library",
            ),
            detail_page,
            st.Page(
                lambda: search.render(service),
                title="検索",
                url_path="search",
            ),
        ],
        "運用": [
            st.Page(
                lambda: intake.render(service),
                title="取り込み",
                url_path="intake",
            ),
            st.Page(
                lambda: audit.render(service),
                title="監査",
                url_path="audit",
            ),
        ],
    }
    st.navigation(pages).run()


if __name__ == "__main__":
    main()
