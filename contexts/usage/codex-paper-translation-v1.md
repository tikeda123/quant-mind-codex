# Paper translation draft policy v1

## Quick Summary

- **Purpose**: Define the exact English-to-Japanese page translation handoff
  authored interactively by Codex.
- **Read when**: Creating or correcting `translation_draft.json` for a
  prepared PDF.
- **Boundary**: Translate every staged physical page without changing the
  source, inventing evidence, or calling another model API.

## Contents

- [Translation JSON](#translation-json)
- [Translation Rules](#translation-rules)

## Translation JSON

Read `source.pdf` and `translation_manifest.json` from the work directory.
Create only `translation_draft.json`; do not edit the PDF or manifest and do
not call an external LLM API.

The JSON must have exactly these fields:

```json
{
  "schema_version": "1",
  "source_content_hash": "<manifest pdf sha256>",
  "source_language": "en",
  "target_language": "ja",
  "generator": {
    "kind": "codex-interactive",
    "model_label": null,
    "draft_policy_version": "paper-translation-draft-v1",
    "instructions_sha256": "<manifest instructions_sha256>"
  },
  "pages": [
    {
      "page_number": 1,
      "translated_text": "<page 1 Japanese translation>"
    }
  ]
}
```

## Translation Rules

Include every page exactly once in ascending, contiguous order. Translate all
meaningful English prose into clear Japanese without summarizing or adding an
interpretation. Preserve headings, paragraph order, list structure, symbols,
equations, variable names, table values, figure labels, citations, footnotes,
and bibliography entries as faithfully as possible. Do not repair or silently
guess unreadable OCR; mark an unreadable fragment as `［判読不能］`. Keep a
blank source page blank. The registered English source remains the evidence;
the Japanese text is a reading aid and must not be quoted as source evidence.
