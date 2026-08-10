"""Tests for cited paper annotations and their aggregate invariants."""

import unittest
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from quantmind.knowledge import (
    PaperAnnotatedResult,
    PaperAnnotationDraft,
    PaperAnnotationKind,
    PaperAnnotationSet,
    PaperArtifact,
    PaperCitationDraft,
    PaperCitationValidationError,
)
from tests.paper_helpers import (
    build_annotated_paper_result,
    build_paper_result,
)


class PaperAnnotationTests(unittest.TestCase):
    def test_annotations_are_typed_cited_and_deterministic(self) -> None:
        first = build_annotated_paper_result()
        second = build_annotated_paper_result()

        self.assertEqual(first, second)
        self.assertEqual(
            tuple(item.kind for item in first.annotation_set.annotations),
            (
                PaperAnnotationKind.SOURCE_FACT,
                PaperAnnotationKind.CODEX_INTERPRETATION,
            ),
        )
        self.assertTrue(
            all(item.citations for item in first.annotation_set.annotations)
        )
        revived = PaperAnnotatedResult.model_validate_json(
            first.model_dump_json()
        )
        self.assertEqual(
            revived.model_dump(mode="json"),
            first.model_dump(mode="json"),
        )
        self.assertEqual(
            TypeAdapter(PaperArtifact).validate_python(
                first.annotation_set.model_dump(mode="json")
            ),
            first.annotation_set,
        )

    def test_changed_draft_changes_summary_and_annotation_ids(self) -> None:
        first = build_annotated_paper_result(draft_marker="first")
        changed = build_annotated_paper_result(draft_marker="changed")

        self.assertEqual(first.source_revision.id, changed.source_revision.id)
        self.assertEqual(first.chunk_set.id, changed.chunk_set.id)
        self.assertNotEqual(first.global_summary.id, changed.global_summary.id)
        self.assertNotEqual(first.annotation_set.id, changed.annotation_set.id)
        self.assertNotEqual(
            first.annotation_set.annotations[0].annotation_id,
            changed.annotation_set.annotations[0].annotation_id,
        )

    def test_annotation_requires_resolvable_citation(self) -> None:
        semantic = build_paper_result()
        valid = build_annotated_paper_result()
        producer = valid.annotation_set.producer.model_copy(
            update={"input_chunk_set_id": semantic.chunk_set.id}
        )

        for citation in (
            PaperCitationDraft(chunk_index=-1, page_number=1),
            PaperCitationDraft(chunk_index=0, page_number=2),
            PaperCitationDraft(
                chunk_index=0,
                page_number=1,
                quote="not found in the chunk",
            ),
        ):
            with self.subTest(citation=citation):
                with self.assertRaises(PaperCitationValidationError):
                    PaperAnnotationSet.from_draft(
                        semantic.chunk_set,
                        producer=producer,
                        annotations=(
                            PaperAnnotationDraft(
                                kind=PaperAnnotationKind.USER_NOTE,
                                text="A grounded note.",
                                citations=(citation,),
                            ),
                        ),
                    )

    def test_annotation_set_rejects_tampered_member_identity(self) -> None:
        result = build_annotated_paper_result()
        annotation = result.annotation_set.annotations[0]
        payload = result.annotation_set.model_dump(mode="json")
        payload["annotations"][0]["annotation_id"] = str(uuid4())

        with self.assertRaisesRegex(ValidationError, "annotation ID"):
            PaperAnnotationSet.model_validate(payload)

        self.assertNotIn("embedding", annotation.model_dump())
        self.assertFalse(hasattr(annotation, "store"))

    def test_aggregate_rejects_cross_artifact_chunk_set(self) -> None:
        result = build_annotated_paper_result()
        annotation_set = result.annotation_set.model_copy(
            update={
                "producer": result.annotation_set.producer.model_copy(
                    update={"input_chunk_set_id": uuid4()}
                )
            }
        )

        with self.assertRaisesRegex(ValidationError, "producer hash mismatch"):
            PaperAnnotatedResult(
                source_revision=result.source_revision,
                chunk_set=result.chunk_set,
                global_summary=result.global_summary,
                annotation_set=annotation_set,
            )


if __name__ == "__main__":
    unittest.main()
