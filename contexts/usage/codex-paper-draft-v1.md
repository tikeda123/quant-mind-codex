# Cited paper draft policy v1

## Quick Summary

- **Purpose**: Define the exact JSON handoff authored interactively by Codex.
- **Read when**: Creating or correcting `draft.json` for a prepared PDF.
- **Boundary**: Quote only staged physical pages; never invent canonical IDs or
  call another model API.

## Contents

- [Draft JSON](#draft-json)
- [Evidence Rules](#evidence-rules)

## Draft JSON

Read `source.pdf` and `manifest.json` from the work directory. Create only
`draft.json`; do not edit the PDF or manifest and do not call an external LLM
API. Preserve the language of the paper when quoting it.

The JSON must have exactly these fields:

```json
{
  "schema_version": "1",
  "source_content_hash": "<manifest pdf sha256>",
  "generator": {
    "kind": "codex-interactive",
    "model_label": null,
    "draft_policy_version": "cited-paper-draft-v1",
    "instructions_sha256": "<manifest instructions_sha256>"
  },
  "summary": {
    "text": "<whole-paper summary>",
    "citations": [
      {"page_number": 1, "quote": "<8-500 exact source characters>"}
    ]
  },
  "annotations": [
    {
      "kind": "source_fact",
      "text": "<fact stated by the source>",
      "citations": [
        {"page_number": 1, "quote": "<8-500 exact source characters>"}
      ]
    },
    {
      "kind": "codex_interpretation",
      "text": "<interpretation explicitly separated from source fact>",
      "citations": [
        {"page_number": 1, "quote": "<8-500 exact source characters>"}
      ]
    }
  ]
}
```

## Evidence Rules

Use at least three summary citations spanning at least two physical pages.
Every annotation, including `user_note`, requires at least one citation. Each
quote must be copied exactly from the cited page text in `manifest.json`, must
fit inside one short passage, and must be unique enough to resolve to one
chunk. Do not invent chunk indexes, UUIDs, page numbers, or quotes. If
finalization reports no match or an ambiguous match, shorten or replace that
quote with a distinctive exact passage from the same page.
