"""Read-only audit view; no repair, delete, or re-embed controls."""

import json

import streamlit as st

from apps.paper_library.service import PaperLibraryAppService


def render(service: PaperLibraryAppService) -> None:
    """Render read-only library, registration, and sidecar evidence."""
    st.title("監査")
    stats = service.inspect_library()
    sidecar = service.state.inspect()
    st.subheader("Library summary")
    st.json(stats.model_dump(mode="json"))
    st.subheader("Embedding")
    st.write(
        "固定: intfloat/multilingual-e5-small@"
        "fd1525a9fd15316a2d503bf26ab031a61d056e98 · 384 dimensions"
    )
    st.caption("通常実行時のdownloadなし。cache missing時はfail closed。")
    st.subheader("Registrations")
    registrations = service.list_registrations(limit=100)
    for record in registrations:
        st.write(
            f"{record.registered_at.isoformat()} · "
            f"{str(record.registration_id)[:12]} · "
            f"{', '.join(record.passed_checks)}"
        )
    st.download_button(
        "registration JSON export",
        json.dumps(
            [record.model_dump(mode="json") for record in registrations],
            ensure_ascii=False,
            indent=2,
        ),
        file_name="paper-registrations.json",
        mime="application/json",
    )
    st.subheader("Integrity (fast check)")
    page = service.list_papers()
    report = [entry.model_dump(mode="json") for entry in page.entries]
    for entry in page.entries:
        st.write(
            f"{entry.health} · {entry.title or 'タイトル未取得'} · "
            f"{', '.join(entry.health_reasons) or 'reasonなし'}"
        )
    st.download_button(
        "health report JSON export",
        json.dumps(report, ensure_ascii=False, indent=2),
        file_name="paper-health.json",
        mime="application/json",
    )
    if st.button("読み取り専用deep checkを実行"):
        deep_report = []
        with st.spinner("原典bytesとartifactを再検証中..."):
            for entry in page.entries:
                try:
                    details = service.get_paper_details(
                        entry.source_revision_id
                    )
                    deep_report.append(
                        {
                            "source_revision_id": str(entry.source_revision_id),
                            "health": details.health,
                            "reasons": details.health_reasons,
                        }
                    )
                except Exception as exc:
                    deep_report.append(
                        {
                            "source_revision_id": str(entry.source_revision_id),
                            "health": "broken",
                            "reasons": [type(exc).__name__],
                        }
                    )
        st.session_state["deep_audit_report"] = deep_report
    deep_report = st.session_state.get("deep_audit_report")
    if deep_report is not None:
        st.subheader("Integrity (deep check)")
        st.json(deep_report)
        st.download_button(
            "deep report JSON export",
            json.dumps(deep_report, ensure_ascii=False, indent=2),
            file_name="paper-deep-health.json",
            mime="application/json",
        )
    known_ids = {entry.source_revision_id for entry in page.entries}
    st.subheader("Sidecar")
    st.json(sidecar.model_dump(mode="json"))
    orphans = service.state.orphaned_source_ids(known_ids)
    if orphans:
        st.warning(
            "orphaned sidecar references: " + ", ".join(map(str, orphans))
        )
    st.subheader("Environment")
    st.write(
        "loopback only · network-free browse · API key値は読込/表示/保存しません"
    )
    st.caption("修復・削除・再埋め込み・VACUUM操作はこのUIにありません。")
