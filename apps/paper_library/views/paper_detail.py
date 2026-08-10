"""Evidence-first paper detail and reading management."""

import json
from pathlib import Path
from typing import cast
from uuid import UUID

import streamlit as st

from apps.paper_library.components.citation import (
    annotation_kind_label,
    render_citation,
)
from apps.paper_library.models import (
    ReadingStatus,
    TranslationReviewStatus,
    VisualAnnotationReviewStatus,
)
from apps.paper_library.pdf_preview import (
    PaperPreviewError,
    render_cited_page,
)
from apps.paper_library.service import PaperLibraryAppService


def _select_evidence(page: int, quote: str | None) -> None:
    st.session_state["selected_citation_page"] = page
    st.session_state["selected_citation_quote"] = quote


_VISUAL_REVIEW_LABELS: dict[VisualAnnotationReviewStatus, str] = {
    "unreviewed": "未確認",
    "attention": "要確認",
    "verified": "原典と照合済み",
}

_TRANSLATION_REVIEW_LABELS: dict[TranslationReviewStatus, str] = {
    "unreviewed": "未確認",
    "attention": "要確認",
    "verified": "原文と照合済み",
}


def render(service: PaperLibraryAppService) -> None:
    """Render canonical evidence and separately editable personal state."""
    st.title("論文詳細")
    selected = st.session_state.get("selected_source_revision_id")
    if not selected:
        st.info("蔵書または検索結果から論文を選択してください。")
        return
    source_id = UUID(str(selected))
    details = service.get_paper_details(source_id)
    source = details.source
    state = service.state.get_state(source_id)
    st.header(state.display_title or source.title or "タイトル未取得")
    st.caption(
        f"{', '.join(source.authors) or '著者未取得'} · "
        f"{len(source.parsed.pages)} pages · {details.health} · "
        f"source {str(source.id)[:12]}"
    )
    source_uri = source.source.uri
    if source_uri and source_uri.startswith(("https://", "http://")):
        st.link_button("原典URL", source_uri)
    raw_asset = service.get_paper_asset(source.id, source.raw_asset_id)
    st.download_button(
        "原典PDFを保存",
        data=raw_asset.content,
        file_name=raw_asset.filename,
        mime=raw_asset.media_type,
    )
    registration = details.registrations[0] if details.registrations else None
    summary = next(
        (
            item
            for item in details.summaries
            if registration is not None and item.id == registration.summary_id
        ),
        details.summaries[0] if details.summaries else None,
    )
    annotation_set = next(
        (
            item
            for item in details.annotation_sets
            if registration is not None
            and item.id == registration.annotation_set_id
        ),
        details.annotation_sets[0] if details.annotation_sets else None,
    )
    (
        overview,
        annotations,
        translation_tab,
        visual_annotations,
        original,
        artifacts,
        history,
        reading,
    ) = st.tabs(
        [
            "概要",
            "注釈",
            "日本語訳",
            "画像注釈",
            "原典",
            "Artifact",
            "登録履歴",
            "読書管理",
        ]
    )
    with overview:
        if summary is None:
            st.warning("要約artifactがありません。")
        else:
            st.write(summary.summary)
            for index, citation in enumerate(summary.citations):
                render_citation(
                    citation,
                    key=f"summary-citation-{index}",
                    on_open=_select_evidence,
                )
            with st.expander("生成情報"):
                st.json(summary.producer.model_dump(mode="json"))
                st.code(str(summary.id))
    with annotations:
        if annotation_set is None:
            st.warning("注釈artifactがありません。")
        else:
            for annotation in annotation_set.annotations:
                st.subheader(annotation_kind_label(annotation.kind.value))
                st.write(annotation.text)
                for index, citation in enumerate(annotation.citations):
                    render_citation(
                        citation,
                        key=f"annotation-{annotation.annotation_id}-{index}",
                        on_open=_select_evidence,
                    )
        st.divider()
        st.subheader("個人メモ・根拠未検証")
        st.write(state.personal_memo or "個人メモはありません。")
    with translation_tab:
        st.info(
            "日本語訳は読解支援用です。引用・根拠確認には、同じページ番号の"
            "英語原文と原典PDFを使用してください。"
        )
        translation = None
        if details.translations:
            translation_by_id = {
                str(item.id): item for item in details.translations
            }
            latest_translation_id = next(
                (
                    str(record.translation_id)
                    for record in details.translation_registrations
                    if str(record.translation_id) in translation_by_id
                ),
                next(iter(translation_by_id)),
            )
            selected_translation_id = st.selectbox(
                "翻訳バージョン",
                list(translation_by_id),
                index=list(translation_by_id).index(latest_translation_id),
                format_func=lambda value: value[:12],
            )
            translation = translation_by_id[selected_translation_id]
            reviews = service.state.list_translation_page_reviews(
                translation.id
            )
            verified_count = sum(
                review.review_status == "verified" for review in reviews
            )
            attention_count = sum(
                review.review_status == "attention" for review in reviews
            )
            st.caption(
                f"翻訳済み {len(translation.pages)}/{len(source.parsed.pages)} "
                f"pages · 原文照合済み {verified_count}/{len(translation.pages)} "
                f"· 要確認 {attention_count}"
            )
            page_number = int(
                st.number_input(
                    "翻訳ページ",
                    min_value=1,
                    max_value=len(translation.pages),
                    value=min(
                        int(
                            st.session_state.get(
                                "selected_citation_page",
                                state.last_opened_page or 1,
                            )
                        ),
                        len(translation.pages),
                    ),
                    step=1,
                    key=f"translation-page-{translation.id}",
                )
            )
            page = translation.pages[page_number - 1]
            mode = st.radio(
                "表示",
                ["日本語のみ", "原文対訳"],
                horizontal=True,
                key=f"translation-mode-{translation.id}",
            )
            if mode == "原文対訳":
                original_column, japanese_column = st.columns(2)
                with original_column:
                    st.subheader(f"English · page {page.page_number}")
                    st.text(page.source_text or "（空白ページ）")
                with japanese_column:
                    st.subheader(f"日本語 · page {page.page_number}")
                    st.write(page.translated_text or "（空白ページ）")
            else:
                st.subheader(f"日本語 · page {page.page_number}")
                st.write(page.translated_text or "（空白ページ）")
            if st.button(
                "同じ原典ページを確認",
                key=f"translation-source-{translation.id}-{page.page_number}",
            ):
                _select_evidence(page.page_number, None)
                st.success("「原典」タブの表示ページを更新しました。")
            review = service.state.get_translation_page_review(
                translation.id,
                page.page_number,
            )
            current_label = _TRANSLATION_REVIEW_LABELS[review.review_status]
            with st.expander("このページの原文照合"):
                with st.form(
                    f"translation-review-{translation.id}-{page.page_number}"
                ):
                    labels = list(_TRANSLATION_REVIEW_LABELS.values())
                    review_label = st.selectbox(
                        "確認状態",
                        labels,
                        index=labels.index(current_label),
                    )
                    review_note = st.text_area(
                        "確認メモ",
                        value=review.review_note,
                        max_chars=2_000,
                    )
                    if st.form_submit_button("確認状態を保存"):
                        review_status = cast(
                            TranslationReviewStatus,
                            next(
                                key
                                for key, value in _TRANSLATION_REVIEW_LABELS.items()
                                if value == review_label
                            ),
                        )
                        try:
                            service.update_translation_page_review(
                                translation.id,
                                page.page_number,
                                expected_version=review.version,
                                review_status=review_status,
                                review_note=review_note,
                            )
                        except (ValueError, RuntimeError) as exc:
                            st.error(str(exc))
                        else:
                            st.success("翻訳ページの確認状態を更新しました。")
                            st.rerun()
        else:
            st.warning("この論文には日本語訳がまだ登録されていません。")

        st.divider()
        with st.expander("Codex対話で日本語訳を作成・登録", expanded=False):
            st.write(
                "準備は原文とページhashを固定するだけです。UIはCodexや外部LLMを"
                "呼びません。準備後、このCodex対話でJSON作成を依頼してください。"
            )
            prepare_key = f"translation-manifest-{source.id}"
            result_key = f"translation-result-{source.id}"
            if st.button("翻訳用ファイルを準備", key=f"prepare-{source.id}"):
                try:
                    _, manifest_path = service.prepare_translation(source.id)
                except (ValueError, RuntimeError, OSError) as exc:
                    st.error(str(exc))
                else:
                    st.session_state[prepare_key] = str(manifest_path)
                    st.session_state.pop(result_key, None)
            manifest_value = st.session_state.get(prepare_key)
            if manifest_value:
                manifest_path = Path(str(manifest_value))
                draft_path = manifest_path.parent / "translation_draft.json"
                st.code(
                    "Codexへの依頼例:\n"
                    f"{manifest_path} と同じフォルダの source.pdf を読み、\n"
                    "contexts/usage/codex-paper-translation-v1.md に従って\n"
                    f"{draft_path} を作成してください。外部LLM APIは使わないでください。",
                    language=None,
                )
                st.caption(f"待機先: {draft_path}")
                if st.button(
                    "translation_draft.jsonを検証",
                    disabled=not draft_path.exists(),
                    key=f"validate-translation-{source.id}",
                ):
                    try:
                        result = service.finalize_translation(
                            manifest_path,
                            draft_path,
                        )
                        if result.source_revision.id != source.id:
                            raise ValueError(
                                "translation source does not match this paper"
                            )
                    except (ValueError, RuntimeError, OSError) as exc:
                        st.error(str(exc))
                        st.session_state.pop(result_key, None)
                    else:
                        st.session_state[result_key] = result
                validated = st.session_state.get(result_key)
                if validated is not None:
                    st.success(
                        f"検証済み: {len(validated.translation.pages)} pages"
                    )
                    if st.button(
                        "確認して日本語訳を登録",
                        type="primary",
                        key=f"register-translation-{source.id}",
                    ):
                        try:
                            service.register_translation(validated)
                        except (ValueError, RuntimeError) as exc:
                            st.error(str(exc))
                        else:
                            st.session_state.pop(result_key, None)
                            st.success("日本語訳をcanonical DBに登録しました。")
                            st.rerun()
    with visual_annotations:
        st.info(
            "画像注釈は説明を助ける個人用資料です。原論文の引用証拠ではなく、"
            "semantic searchにも使用されません。"
        )
        annotation_options: dict[str, UUID | None] = {"論文全体": None}
        if annotation_set is not None:
            for annotation in annotation_set.annotations:
                label = (
                    f"{annotation_kind_label(annotation.kind.value)} · "
                    f"{str(annotation.annotation_id)[:8]}"
                )
                annotation_options[label] = annotation.annotation_id
        with st.expander("説明画像を追加", expanded=False):
            with st.form("visual-annotation-add"):
                upload = st.file_uploader(
                    "画像",
                    type=["png", "jpg", "jpeg", "webp"],
                    help="PNG/JPEG/WebP、20 MB以下、40メガピクセル以下",
                )
                caption = st.text_input(
                    "見出し",
                    max_chars=1_000,
                    placeholder="例: 効率的フロンティアの解説図",
                )
                alt_text = st.text_area(
                    "代替テキスト",
                    max_chars=1_000,
                    help="画像を見なくても内容が分かる説明を入力します。",
                )
                creator = st.text_input("作成者・ツール（任意）", max_chars=200)
                provenance = st.text_area(
                    "入手元・作成条件（任意）", max_chars=2_000
                )
                linked_label = st.selectbox(
                    "関連する文章注釈（任意）",
                    list(annotation_options),
                )
                review_label = st.selectbox(
                    "確認状態",
                    list(_VISUAL_REVIEW_LABELS.values()),
                    help="原典と照合するまでは「未確認」または「要確認」にします。",
                )
                review_note = st.text_area("確認メモ（任意）", max_chars=2_000)
                if st.form_submit_button("画像注釈として保存"):
                    if upload is None:
                        st.error("画像を選択してください。")
                    else:
                        review_status = cast(
                            VisualAnnotationReviewStatus,
                            next(
                                key
                                for key, value in _VISUAL_REVIEW_LABELS.items()
                                if value == review_label
                            ),
                        )
                        try:
                            service.add_visual_annotation(
                                source.id,
                                image_content=upload.getvalue(),
                                original_filename=upload.name,
                                media_type=upload.type,
                                caption=caption,
                                alt_text=alt_text,
                                creator=creator,
                                provenance=provenance,
                                review_status=review_status,
                                review_note=review_note,
                                linked_annotation_id=annotation_options[
                                    linked_label
                                ],
                            )
                        except (ValueError, KeyError) as exc:
                            st.error(str(exc))
                        else:
                            st.success("画像注釈をsidecarに保存しました。")
                            st.rerun()
        visuals = service.list_visual_annotations(source.id)
        if not visuals:
            st.caption("画像注釈はまだありません。")
        for visual in visuals:
            st.divider()
            status_label = _VISUAL_REVIEW_LABELS[visual.review_status]
            if visual.review_status == "attention":
                st.warning(
                    f"{status_label}: {visual.review_note or '確認が必要です。'}"
                )
            elif visual.review_status == "verified":
                st.success(status_label)
            else:
                st.warning(status_label)
            st.subheader(visual.caption)
            st.image(
                visual.image_content,
                caption=visual.caption,
                use_container_width=True,
            )
            st.write(f"代替テキスト: {visual.alt_text}")
            st.caption(
                f"{visual.original_filename} · {visual.media_type} · "
                f"{visual.width}×{visual.height} · "
                f"{visual.byte_size / (1024 * 1024):.1f} MB · "
                f"sha256 {visual.content_hash[:12]}…"
            )
            if visual.creator:
                st.write(f"作成者・ツール: {visual.creator}")
            if visual.provenance:
                st.write(f"入手元・作成条件: {visual.provenance}")
            if visual.linked_annotation_id is not None:
                st.write(f"関連文章注釈: {visual.linked_annotation_id}")
            with st.expander("確認状態を更新"):
                with st.form(f"visual-review-{visual.visual_annotation_id}"):
                    labels = list(_VISUAL_REVIEW_LABELS.values())
                    new_label = st.selectbox(
                        "確認状態",
                        labels,
                        index=labels.index(status_label),
                        key=f"visual-status-{visual.visual_annotation_id}",
                    )
                    new_note = st.text_area(
                        "確認メモ",
                        value=visual.review_note,
                        max_chars=2_000,
                        key=f"visual-note-{visual.visual_annotation_id}",
                    )
                    if st.form_submit_button("確認状態を保存"):
                        new_status = cast(
                            VisualAnnotationReviewStatus,
                            next(
                                key
                                for key, value in _VISUAL_REVIEW_LABELS.items()
                                if value == new_label
                            ),
                        )
                        try:
                            service.update_visual_annotation_review(
                                visual.visual_annotation_id,
                                expected_version=visual.version,
                                review_status=new_status,
                                review_note=new_note,
                            )
                        except (ValueError, RuntimeError) as exc:
                            st.error(str(exc))
                        else:
                            st.success("確認状態を更新しました。")
                            st.rerun()
    with original:
        selected_page = int(
            st.session_state.get(
                "selected_citation_page", state.last_opened_page or 1
            )
        )
        selected_page = st.number_input(
            "page",
            min_value=1,
            max_value=len(source.parsed.pages),
            value=min(selected_page, len(source.parsed.pages)),
            step=1,
        )
        quote = st.session_state.get("selected_citation_quote")
        try:
            rendered = render_cited_page(
                raw_asset.content,
                page_number=int(selected_page),
                quote=quote,
            )
            st.image(rendered.png_bytes, use_container_width=True)
            if quote:
                st.code(quote, language=None, wrap_lines=True)
                if not rendered.highlight_found:
                    st.caption(
                        "visual highlightなし。保存済みexact quoteを表示しています。"
                    )
        except PaperPreviewError as exc:
            st.error(str(exc))
        with st.expander("全文PDFを開く"):
            st.pdf(raw_asset.content)
    with artifacts:
        for label, values in (
            ("Chunk sets", details.chunk_sets),
            ("Summaries", details.summaries),
            ("Annotation sets", details.annotation_sets),
            ("Translations", details.translations),
        ):
            st.subheader(label)
            for value in values:
                st.code(str(value.id))
                with st.expander("read-only JSON"):
                    payload = value.model_dump(mode="json")
                    st.json(payload)
                    st.download_button(
                        "JSONを保存",
                        json.dumps(payload, ensure_ascii=False, indent=2),
                        file_name=f"{value.id}.json",
                        mime="application/json",
                        key=f"download-{value.id}",
                    )
    with history:
        for record in details.registrations:
            st.subheader(record.registered_at.isoformat())
            st.code(str(record.registration_id))
            st.write(
                f"{record.embedding_model} · {record.embedding_dimensions}次元 · "
                f"parser {record.parser_name} {record.parser_version}"
            )
            st.write(" / ".join(record.passed_checks))
        for record in details.translation_registrations:
            st.subheader(f"日本語訳 · {record.registered_at.isoformat()}")
            st.code(str(record.registration_id))
            st.write(
                f"{record.source_language} → {record.target_language} · "
                f"{record.page_count} pages"
            )
            st.write(" / ".join(record.passed_checks))
    with reading:
        st.info(
            "ここでの変更は個人用sidecarだけに保存されます。canonical dataは不変です。"
        )
        collections = service.state.list_collections()
        collection_by_name = {
            item.name: item.collection_id for item in collections
        }
        with st.form("reading-state"):
            display_title = st.text_input(
                "個人表示名", value=state.display_title or ""
            )
            reading_status = st.selectbox(
                "読書状態",
                ["inbox", "reading", "read", "archived"],
                index=["inbox", "reading", "read", "archived"].index(
                    state.reading_status
                ),
            )
            starred = st.checkbox("重要", value=state.starred)
            memo = st.text_area(
                "個人メモ・根拠未検証",
                value=state.personal_memo,
                max_chars=20_000,
            )
            tags_text = st.text_input(
                "タグ（カンマ区切り）", value=", ".join(state.tags)
            )
            selected_collections = st.multiselect(
                "コレクション",
                list(collection_by_name),
                default=[
                    name
                    for name in state.collections
                    if name in collection_by_name
                ],
            )
            if st.form_submit_button("保存"):
                service.state.update_state(
                    source_id,
                    expected_version=state.version,
                    display_title=display_title,
                    reading_status=cast(ReadingStatus, reading_status),
                    starred=starred,
                    personal_memo=memo,
                    last_opened_page=int(selected_page),
                    page_count=len(source.parsed.pages),
                )
                service.state.set_tags(
                    source_id,
                    tuple(
                        value.strip()
                        for value in tags_text.split(",")
                        if value.strip()
                    ),
                )
                service.state.set_collections(
                    source_id,
                    tuple(
                        collection_by_name[name]
                        for name in selected_collections
                    ),
                )
                st.success("個人用の読書状態を保存しました。")
        with st.expander("コレクションを作成"):
            with st.form("create-collection"):
                new_name = st.text_input("名称")
                description = st.text_area("説明", max_chars=2_000)
                if st.form_submit_button("作成"):
                    service.state.create_collection(new_name, description)
                    st.success(
                        "コレクションを作成しました。再実行後に選択できます。"
                    )
