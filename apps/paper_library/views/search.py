"""Local semantic search with source-level sidecar filters."""

from typing import cast
from uuid import UUID

import streamlit as st

from apps.paper_library.models import ReadingStatus
from apps.paper_library.service import PaperLibraryAppService
from quantmind.knowledge import PaperArtifactKind
from quantmind.library import SemanticQuery


def render(service: PaperLibraryAppService) -> None:
    """Render fixed-model semantic search and source evidence links."""
    st.title("検索")
    collections = service.state.list_collections()
    collection_by_name = {item.name: item.collection_id for item in collections}
    with st.form("semantic-search"):
        query_text = st.text_input("質問・検索語")
        top_k = st.selectbox("表示件数", [5, 10, 20])
        target = st.multiselect(
            "検索対象",
            ["summary", "chunk"],
            default=["summary", "chunk"],
        )
        reading_status = st.selectbox(
            "読書状態", ["指定なし", "inbox", "reading", "read"]
        )
        starred = st.checkbox("重要論文のみ")
        tags = st.multiselect("タグ", service.state.list_tags())
        collection_name = st.selectbox(
            "コレクション", ["指定なし", *collection_by_name]
        )
        submitted = st.form_submit_button("ローカル意味検索", type="primary")
    if not submitted:
        st.caption(
            "固定multilingual-e5-smallのquery embeddingと保存済みvectorを照合します。"
        )
        return
    kinds = []
    if "summary" in target:
        kinds.append(PaperArtifactKind.GLOBAL_SUMMARY)
    if "chunk" in target:
        kinds.append(PaperArtifactKind.CHUNK_SET)
    source_ids: tuple[UUID, ...] | None = None
    if (
        reading_status != "指定なし"
        or starred
        or tags
        or collection_name != "指定なし"
    ):
        source_ids = service.state.filter_source_ids(
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
        )
    try:
        hits = service.search(
            SemanticQuery(
                text=query_text,
                artifact_kinds=kinds,
                source_revision_ids=source_ids,
                top_k=top_k,
            )
        )
    except Exception as exc:
        st.error(f"検索を実行できませんでした: {type(exc).__name__}")
        st.caption("model cacheと検索準備状態を監査画面で確認してください。")
        return
    history = st.session_state.setdefault("query_history", [])
    history.append(query_text)
    if not hits:
        st.info("該当なし")
        st.write(
            "queryの言い換え、filter解除、検索準備statusを確認してください。"
        )
        return
    for index, hit in enumerate(hits):
        source_id = hit.locator.source_revision_id
        st.subheader(f"{hit.item_type} · 類似度 {hit.score:.3f}")
        st.write(hit.matched_text)
        for citation in hit.citations:
            st.caption(f"原典 p.{citation.page}")
            if citation.quote:
                st.code(citation.quote, language=None, wrap_lines=True)
        with st.expander("検索projection"):
            st.json(hit.projection.model_dump(mode="json"))
        if source_id is not None and st.button(
            "論文詳細を開く", key=f"search-detail-{index}"
        ):
            st.session_state["selected_source_revision_id"] = str(source_id)
            if hit.citations:
                st.session_state["selected_citation_page"] = hit.citations[
                    0
                ].page
                st.session_state["selected_citation_quote"] = hit.citations[
                    0
                ].quote
            st.switch_page(st.session_state["_detail_page"])
