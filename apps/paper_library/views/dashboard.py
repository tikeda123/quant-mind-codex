"""Paper library dashboard."""

import streamlit as st

from apps.paper_library.service import PaperLibraryAppService


def render(service: PaperLibraryAppService) -> None:
    """Render source-level KPIs and the next useful actions."""
    st.title("ダッシュボード")
    stats = service.inspect_library()
    sidecar = service.state.inspect()
    columns = st.columns(7)
    values = (
        ("保存論文", stats.source_revision_count),
        ("検索可能", stats.search_ready_count),
        ("要確認", stats.attention_count),
        ("破損", stats.broken_count),
        ("未読", sidecar.inbox_count),
        ("注釈", stats.total_annotations),
        ("日本語訳", stats.total_translations),
    )
    for column, (label, value) in zip(columns, values, strict=True):
        column.metric(label, value)
    st.caption(
        f"画像注釈 {sidecar.visual_annotation_count}件 · "
        f"うち要確認 {sidecar.attention_visual_annotation_count}件"
    )
    page = service.list_papers()
    if not page.entries:
        st.info(
            "保存済み論文はありません。『取り込み』からPDFを準備してください。"
        )
        return
    st.subheader("最近登録した論文")
    for entry in page.entries[:10]:
        title = entry.title or "タイトル未取得"
        st.write(
            f"- {title} · {entry.page_count} pages · {entry.health} · "
            f"{str(entry.source_revision_id)[:12]}"
        )
    st.subheader("確認が必要な項目")
    attention = [entry for entry in page.entries if entry.health != "ready"]
    if not attention:
        st.success("現在、要確認または破損として検出された論文はありません。")
    for entry in attention:
        st.warning(
            f"{entry.title or 'タイトル未取得'}: "
            f"{', '.join(entry.health_reasons)}"
        )
    active = []
    starred = []
    for entry in page.entries:
        state = service.state.get_state(entry.source_revision_id)
        title = state.display_title or entry.title or "タイトル未取得"
        if state.reading_status == "reading":
            active.append(title)
        if state.starred:
            starred.append(title)
    first, second = st.columns(2)
    with first:
        st.subheader("読書中")
        st.write("\n".join(f"- {title}" for title in active[:5]) or "該当なし")
    with second:
        st.subheader("重要論文")
        st.write("\n".join(f"- {title}" for title in starred[:5]) or "該当なし")
    st.caption(
        f"DB size: {stats.database_size_bytes or 0:,} bytes · "
        "fast check（全文PDF再hashは詳細表示時）"
    )
