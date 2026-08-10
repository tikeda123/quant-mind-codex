"""Explicit prepare → interactive draft → validate → register workflow."""

import json
from pathlib import Path

import streamlit as st

from apps.paper_library.models import IntakeSnapshot
from apps.paper_library.service import PaperLibraryAppService


def _snapshot() -> IntakeSnapshot:
    payload = st.session_state.get("intake_snapshot")
    return (
        IntakeSnapshot.model_validate(payload)
        if payload is not None
        else IntakeSnapshot(stage="unprepared")
    )


def _save(snapshot: IntakeSnapshot) -> None:
    st.session_state["intake_snapshot"] = snapshot.model_dump(mode="json")


def render(service: PaperLibraryAppService) -> None:
    """Render the user-driven intake state machine without polling or Codex API."""
    st.title("取り込み")
    st.info(
        "この画面はCodexを呼び出しません。Prepare後、Codexとの対話でdraft.jsonを作成し、明示的に検証・登録します。"
    )
    snapshot = _snapshot()
    st.write(f"現在の状態: **{snapshot.stage}**")
    if snapshot.stage == "unprepared":
        method = st.radio("入力", ["PDF upload", "公開HTTPS URL"])
        upload = (
            st.file_uploader("PDF", type=["pdf"], accept_multiple_files=False)
            if method == "PDF upload"
            else None
        )
        url = (
            st.text_input("公開HTTPS PDF URL") if method != "PDF upload" else ""
        )
        if st.button("Prepare", type="primary"):
            try:
                workdir = service.create_intake_workdir()
                if upload is not None:
                    uploaded_path = service.save_uploaded_pdf(
                        workdir, upload.getvalue()
                    )
                    input_value = str(uploaded_path)
                elif url:
                    input_value = url
                else:
                    raise ValueError("PDFまたはURLを指定してください")
                _, manifest_path = service.prepare_input(input_value, workdir)
                _save(
                    IntakeSnapshot(
                        stage="draft_waiting",
                        workdir=str(workdir),
                        manifest_path=str(manifest_path),
                        draft_path=str(workdir / "draft.json"),
                    )
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Prepare失敗: {type(exc).__name__}: {exc}")
        return
    assert snapshot.workdir and snapshot.manifest_path and snapshot.draft_path
    workdir = Path(snapshot.workdir)
    manifest_path = Path(snapshot.manifest_path)
    draft_path = Path(snapshot.draft_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    st.subheader("準備済み")
    st.code(f"source.pdf: {workdir / 'source.pdf'}\nmanifest: {manifest_path}")
    st.write(
        f"hash {manifest['pdf']['sha256'][:12]} · "
        f"{manifest['pdf']['size_bytes']:,} bytes · "
        f"{len(manifest['pages'])} pages"
    )
    st.code(
        "Codexへ: source.pdf、manifest.json、"
        "contexts/usage/codex-paper-draft-v1.mdを読み、draft.jsonだけを保存してください。",
        language=None,
    )
    uploaded_draft = st.file_uploader(
        "Codexが作成したdraft.json",
        type=["json"],
        accept_multiple_files=False,
    )
    if uploaded_draft is not None and st.button("draft.jsonを明示保存"):
        service.save_draft(workdir, uploaded_draft.getvalue())
        st.success("draft.jsonを保存しました。")
    if st.button("再読込して検証"):
        try:
            result = service.finalize_draft(manifest_path, draft_path)
            st.session_state["validated_result"] = result
            _save(
                snapshot.model_copy(
                    update={"stage": "validated", "error": None}
                )
            )
            st.rerun()
        except Exception as exc:
            _save(
                snapshot.model_copy(
                    update={
                        "stage": "validation_failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            )
            st.error(f"検証失敗: {type(exc).__name__}: {exc}")
    if snapshot.error:
        st.error(snapshot.error)
    result = st.session_state.get("validated_result")
    if snapshot.stage == "validated" and result is not None:
        st.subheader("Finalize preview")
        st.write(result.global_summary.summary[:500])
        st.write(
            f"pages {len(result.source_revision.parsed.pages)} · "
            f"annotations {len(result.annotation_set.annotations)} · "
            f"vectors予定 {len(result.chunk_set.chunks) + 1}"
        )
        confirmed = st.checkbox("検証済みbundleをcanonical DBへ登録します")
        if st.button("登録する", type="primary", disabled=not confirmed):
            try:
                record = service.register(result)
                _save(
                    snapshot.model_copy(
                        update={
                            "stage": "registered",
                            "registration_id": record.registration_id,
                        }
                    )
                )
                st.success(f"登録完了: {record.registration_id}")
            except Exception as exc:
                st.error(f"登録失敗: {type(exc).__name__}")
    if st.button("新しい取り込みを開始"):
        st.session_state.pop("validated_result", None)
        _save(IntakeSnapshot(stage="unprepared"))
        st.rerun()
