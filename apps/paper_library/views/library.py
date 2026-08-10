"""Filterable source-revision catalog."""

from typing import Literal, cast

import streamlit as st

from apps.paper_library.components.paper_card import (
    display_authors,
    display_publication,
    display_title,
)
from apps.paper_library.components.status import health_label
from apps.paper_library.models import ReadingStatus
from apps.paper_library.service import PaperLibraryAppService
from quantmind.library import PaperCatalogQuery


def render(service: PaperLibraryAppService) -> None:
    """Render filterable source revisions and a single-row detail action."""
    st.title("蔵書")
    collections = service.state.list_collections()
    collection_by_name = {item.name: item.collection_id for item in collections}
    with st.form("catalog_filters"):
        keyword = st.text_input("キーワード（title・author・URI）")
        source_kinds = st.multiselect("原典種別", ["arxiv", "http", "local"])
        health = st.selectbox(
            "整合性", ["すべて", "ready", "attention", "broken"]
        )
        sort = st.selectbox(
            "並び順",
            ["registered_desc", "published_desc", "title_asc"],
        )
        reading_status = st.selectbox(
            "読書状態", ["指定なし", "inbox", "reading", "read", "archived"]
        )
        starred = st.checkbox("重要論文のみ")
        tags = st.multiselect("タグ", service.state.list_tags())
        collection_name = st.selectbox(
            "コレクション", ["指定なし", *collection_by_name]
        )
        include_archived = st.checkbox("アーカイブを表示")
        submitted = st.form_submit_button("絞り込む")
    if submitted:
        st.session_state.pop("catalog_cursor", None)
    sidecar_filter = any(
        (
            reading_status != "指定なし",
            starred,
            bool(tags),
            collection_name != "指定なし",
            include_archived,
        )
    )
    allowed_ids = (
        set(
            service.state.filter_source_ids(
                candidate_source_ids=service.list_source_ids(),
                reading_status=(
                    None
                    if reading_status == "指定なし"
                    else cast(ReadingStatus, reading_status)
                ),
                starred=True if starred else None,
                tags=tuple(tags),
                collection_id=(
                    None
                    if collection_name == "指定なし"
                    else collection_by_name[collection_name]
                ),
                include_archived=include_archived,
            )
        )
        if sidecar_filter
        else None
    )
    page = service.list_papers(
        PaperCatalogQuery(
            text=keyword or None,
            source_kinds=tuple(source_kinds),
            health=(
                None
                if health == "すべて"
                else cast(Literal["ready", "attention", "broken"], health)
            ),
            sort=cast(
                Literal["registered_desc", "published_desc", "title_asc"],
                sort,
            ),
            limit=50,
            cursor=st.session_state.get("catalog_cursor"),
        )
    )
    if not page.entries:
        st.info("条件に一致する論文はありません。")
        return
    rows = []
    for entry in page.entries:
        if (
            allowed_ids is not None
            and entry.source_revision_id not in allowed_ids
        ):
            continue
        state = service.state.get_state(entry.source_revision_id)
        if state.reading_status == "archived" and not include_archived:
            continue
        rows.append(
            {
                "★": "★" if state.starred else "",
                "タイトル": display_title(entry, state.display_title),
                "著者": display_authors(
                    entry.authors,
                    state.display_authors,
                ),
                "公開日": display_publication(
                    entry.published_at,
                    state.display_publication,
                ),
                "種別": entry.source_kind,
                "pages": entry.page_count,
                "注釈": entry.annotation_count,
                "日本語訳": entry.translation_count,
                "検索状態": entry.embedding_model or "未準備",
                "整合性": health_label(entry.health),
                "読書": state.reading_status,
                "登録": (
                    entry.latest_registered_at.isoformat()
                    if entry.latest_registered_at
                    else "なし"
                ),
                "source_revision_id": str(entry.source_revision_id),
            }
        )
    if not rows:
        st.info(
            "現在の個人用フィルターに一致する論文はこのページにありません。"
        )
    event = st.dataframe(
        rows,
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
    )
    selection = event.get("selection", {})
    selected_rows = selection.get("rows", [])
    if selected_rows:
        selected = rows[selected_rows[0]]
        if st.button("詳細を開く", type="primary"):
            st.session_state["selected_source_revision_id"] = selected[
                "source_revision_id"
            ]
            st.switch_page(st.session_state["_detail_page"])
    previous, next_page = st.columns(2)
    if previous.button(
        "先頭ページ", disabled=st.session_state.get("catalog_cursor") is None
    ):
        st.session_state.pop("catalog_cursor", None)
        st.rerun()
    if next_page.button("次のページ", disabled=page.next_cursor is None):
        st.session_state["catalog_cursor"] = page.next_cursor
        st.rerun()
    st.caption(
        f"canonical条件 {page.total_count}件 · このページの表示 {len(rows)}件 "
        "· 1 page最大50件"
    )
