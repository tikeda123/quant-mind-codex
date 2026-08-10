#!/usr/bin/env python3
"""Stage one PDF and deterministic manifest for an interactive cited draft."""

import argparse
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from quantmind.preprocess.fetch import fetch_url, read_local_file
from quantmind.preprocess.format import parse_pdf

_POLICY_VERSION = "cited-paper-draft-v1"
_INSTRUCTIONS_PATH = (
    Path(__file__).resolve().parents[1]
    / "contexts"
    / "usage"
    / "codex-paper-draft-v1.md"
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


async def prepare_codex_paper(
    input_value: str,
    workdir: Path,
    *,
    replace_workdir: bool = False,
) -> tuple[Path, Path]:
    """Create ``source.pdf`` and ``manifest.json`` without calling an LLM."""
    requested_workdir = workdir.expanduser()
    if not requested_workdir.is_absolute():
        raise ValueError("workdir must be an absolute path")
    if requested_workdir.is_symlink():
        raise ValueError("workdir must not be a symbolic link")
    workdir = requested_workdir.resolve()
    source_path = workdir / "source.pdf"
    manifest_path = workdir / "manifest.json"
    draft_path = workdir / "draft.json"
    protected_paths = (source_path, manifest_path, draft_path)
    if any(path.is_symlink() for path in protected_paths):
        raise ValueError("staged files must not be symbolic links")
    if draft_path.exists():
        raise FileExistsError(
            "workdir already contains draft.json; use a new workdir so the "
            "draft cannot be paired with different source bytes"
        )
    if not replace_workdir and any(
        path.exists() for path in (source_path, manifest_path)
    ):
        raise FileExistsError(
            "workdir already contains source.pdf, manifest.json, or draft.json; "
            "use --replace-workdir to replace the staged source and manifest"
        )
    workdir.mkdir(parents=True, exist_ok=True)

    parsed_input = urlsplit(input_value)
    observed_at = datetime.now(timezone.utc)
    if parsed_input.scheme:
        if parsed_input.scheme.lower() != "https":
            raise ValueError("URL input must be a public HTTPS URL")
        fetched = await fetch_url(input_value, public_only=True)
        source_kind = "http"
        requested_uri = input_value
        resolved_uri = fetched.resolved_url or input_value
        fetched_at = fetched.fetched_at or observed_at
    else:
        local_path = Path(input_value).expanduser().resolve()
        fetched = await read_local_file(local_path)
        source_kind = "local"
        requested_uri = local_path.as_uri()
        resolved_uri = requested_uri
        fetched_at = observed_at

    media_type = (fetched.content_type or "").lower()
    if not media_type.startswith("application/pdf"):
        raise ValueError("prepare_codex_paper requires application/pdf content")
    raw_bytes = fetched.bytes
    if not raw_bytes.startswith(b"%PDF-"):
        raise ValueError("input does not have a PDF file signature")

    parsed = await parse_pdf(
        raw_bytes,
        artifact_dir=str(workdir / "parser-assets"),
    )
    if tuple(page.page_number for page in parsed.pages) != tuple(
        range(1, len(parsed.pages) + 1)
    ):
        raise RuntimeError("parser pages are not contiguous and 1-based")
    instructions = _INSTRUCTIONS_PATH.read_bytes()
    source_hash = _sha256(raw_bytes)
    manifest = {
        "schema_version": "1",
        "source": {
            "kind": source_kind,
            "requested_uri": requested_uri,
            "resolved_uri": resolved_uri,
            "media_type": "application/pdf",
            "fetched_at": fetched_at.astimezone(timezone.utc).isoformat(),
            "available_at": fetched_at.astimezone(timezone.utc).isoformat(),
            "published_at": None,
            "arxiv_id": None,
            "title": None,
            "authors": [],
        },
        "pdf": {
            "path": str(source_path),
            "sha256": source_hash,
            "size_bytes": len(raw_bytes),
        },
        "parser": {
            "name": parsed.parser_name,
            "version": parsed.parser_version,
            "cleanup_policy": parsed.cleanup_version,
        },
        "pages": [
            {
                "page_number": page.page_number,
                "text": page.text,
                "text_sha256": _sha256(page.text.encode("utf-8")),
                "is_empty": not page.text.strip(),
            }
            for page in parsed.pages
        ],
        "draft_policy": {
            "version": _POLICY_VERSION,
            "instructions_sha256": _sha256(instructions),
        },
    }

    source_path.write_bytes(raw_bytes)
    _write_json(manifest_path, manifest)
    if _sha256(source_path.read_bytes()) != source_hash:
        raise RuntimeError("staged PDF failed write-after-read hash validation")
    reloaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    if reloaded != manifest:
        raise RuntimeError("staged manifest failed write-after-read validation")
    return source_path, manifest_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Stage a local PDF or public HTTPS PDF for an interactive Codex "
            "cited draft. This command does not call Codex or another LLM."
        )
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--workdir", required=True, type=Path)
    parser.add_argument("--replace-workdir", action="store_true")
    return parser


async def _main() -> int:
    args = _parser().parse_args()
    source_path, manifest_path = await prepare_codex_paper(
        args.input,
        args.workdir,
        replace_workdir=args.replace_workdir,
    )
    print(f"source_pdf={source_path}")
    print(f"manifest={manifest_path}")
    print(f"draft_instructions={_INSTRUCTIONS_PATH}")
    print(
        "next_step=Use Codex interactively to create draft.json; no API call ran."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
