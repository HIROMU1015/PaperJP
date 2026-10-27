from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .common import PipelineError, now, save
from .review import DEFAULT_MODEL, DEFAULT_EFFORT, EFFORT_LEVELS


def main(argv=None):
    parser = argparse.ArgumentParser(description="Academic paper translation: deterministic Python stages + Codex translation + Claude Code review")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("url")
    fetch.add_argument("--root", default="workspace")
    acquire = sub.add_parser("acquire", help="Fetch, extract, split and validate (resumable); no AI calls")
    acquire.add_argument("url")
    acquire.add_argument("--root", default="workspace")
    acquire.add_argument("--main")
    papers = sub.add_parser("papers", help="List paper IDs and current status without modifying workspaces")
    papers.add_argument("--root", type=Path, default=Path("workspace"))
    schemas = sub.add_parser("schema-export", help="Export Draft 2020-12 annotation schemas")
    schemas.add_argument("directory", type=Path)
    for name in ["term-share", "term-share-import"]:
        p = sub.add_parser(name, help="Preview/export a GPT request" if name == "term-share" else "Import a GPT response from the shared paper directory")
        p.add_argument("work", type=Path)
        p.add_argument("input", type=Path)
        p.add_argument("--root", type=Path, default=Path("../gpt-review"))
        if name == "term-share":
            p.add_argument("--write", action="store_true", help="Explicitly copy the previewed request into Git-managed gpt-review")
    for name in ["annotations-init", "term-add", "term-export", "term-import", "term-apply", "notes-import", "reading", "explain", "context-search", "question-prepare", "annotations-check"]:
        p = sub.add_parser(name)
        p.add_argument("work", type=Path)
        if name in {"term-add", "term-import", "term-apply", "notes-import"}:
            p.add_argument("input", type=Path)
        if name == "reading":
            p.add_argument("--draft", action="store_true")
        if name in {"explain", "question-prepare"}:
            p.add_argument("block_id")
            p.add_argument("--related", action="append", default=[])
            p.add_argument("--radius", type=int, default=1)
            p.add_argument("question" if name == "question-prepare" else "--question")
        if name == "context-search":
            p.add_argument("query")
    for name in ["extract", "split", "validate-extraction", "prepare", "validate-translation", "review-prepare", "review-run", "merge", "status", "import-translation", "import-review"]:
        p = sub.add_parser(name)
        p.add_argument("work", type=Path)
        if name == "extract":
            p.add_argument("--main")
        if name == "split":
            p.add_argument("--target-tokens", type=int, default=2000)
        if name in {"review-prepare", "review-run"}:
            p.add_argument("--all", action="store_true")
            p.add_argument("--max-blocks", type=int, default=40)
            p.add_argument("--max-chars", type=int, default=60000)
        if name == "review-run":
            p.add_argument("--packet", type=Path)
            p.add_argument("--claude-exe")
            p.add_argument("--model", default=DEFAULT_MODEL, help=f"Claude model ID or alias (default: {DEFAULT_MODEL})")
            p.add_argument("--effort", choices=EFFORT_LEVELS, default=DEFAULT_EFFORT, help=f"Claude reasoning effort (default: {DEFAULT_EFFORT})")
        if name == "merge":
            p.add_argument("--draft", action="store_true")
        if name in {"import-translation", "import-review"}:
            p.add_argument("response", type=Path)
    args = parser.parse_args(argv)
    try:
        result = dispatch(args)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 2 if isinstance(result, dict) and result.get("status") == "FAIL" else 0
    except (PipelineError, OSError, ValueError, KeyError) as exc:
        if hasattr(args, "work"):
            path = args.work / "reports" / "errors.jsonl"
            previous = path.read_text(encoding="utf-8") if path.exists() else ""
            save(path, previous + json.dumps({"at": now(), "command": args.command, "status": "failed", "error": str(exc)}, ensure_ascii=False) + "\n")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def dispatch(args):
    from .extract import extract
    from .fetch import fetch
    from .merge import merge, status
    from .review import import_review, prepare_reviews, run_review
    from .split import prepare, split
    from .translate import import_translation
    from .validate import validate_extraction, validate_translation
    command = args.command
    if command == "papers":
        from .library import list_papers
        return list_papers(args.root)
    if command == "schema-export":
        from .annotation_schema import SCHEMAS, schema
        from .common import save_json
        for name in SCHEMAS:
            save_json(args.directory / f"{name}.schema.json", schema(name))
        return {"directory": str(args.directory), "schemas": list(SCHEMAS)}
    if command in {"fetch", "acquire"}:
        work = fetch(args.url, args.root)
        if command == "acquire":
            extract(work, args.main)
            split(work)
            result = validate_extraction(work)
            return {"work": str(work), **result}
        return {"work": str(work)}
    work = args.work
    if command in {"term-share", "term-share-import"}:
        from .sharing import import_shared_terms, share_terms
        if command == "term-share":
            return share_terms(work, args.input, args.root, args.write)
        return import_shared_terms(work, args.input, args.root)
    if command in {"annotations-init", "term-add", "term-export", "term-import", "term-apply", "notes-import", "reading", "explain", "context-search", "question-prepare", "annotations-check"}:
        from .annotation_common import initialize
        from .common import read_json
        from .context import explain_context, question_prepare, search_context
        from .reading import annotations_check, import_notes, reading_edition
        from .terminology import apply_correction, export_pending, import_result, register_candidates
        if command == "annotations-init":
            return initialize(work)
        if command == "term-add":
            return register_candidates(work, read_json(args.input))
        if command == "term-export":
            return {"output": str(export_pending(work))}
        if command == "term-import":
            return import_result(work, read_json(args.input))
        if command == "term-apply":
            return apply_correction(work, read_json(args.input))
        if command == "notes-import":
            return import_notes(work, read_json(args.input))
        if command == "reading":
            return {"output": str(reading_edition(work, args.draft))}
        if command == "explain":
            return explain_context(work, args.block_id, args.related, args.radius, args.question)
        if command == "context-search":
            return search_context(work, args.query)
        if command == "question-prepare":
            return {"output": str(question_prepare(work, args.block_id, args.question, args.related, args.radius))}
        if command == "annotations-check":
            return annotations_check(work)
    if command == "extract":
        blocks = extract(work, args.main)
        return {"block_count": len(blocks)}
    if command == "split":
        if args.target_tokens < 50:
            raise PipelineError("--target-tokens must be at least 50")
        return split(work, args.target_tokens)
    if command == "validate-extraction":
        return validate_extraction(work)
    if command == "prepare":
        return prepare(work)
    if command == "import-translation":
        return import_translation(work, args.response)
    if command == "validate-translation":
        return validate_translation(work)
    if command == "review-prepare":
        paths = prepare_reviews(work, args.all, args.max_blocks, args.max_chars)
        return {"packets": list(map(str, paths)), **({"packet": str(paths[0])} if len(paths) == 1 else {})}
    if command == "review-run":
        return run_review(work, args.packet, args.claude_exe, args.all, args.max_blocks, args.max_chars, model=args.model, effort=args.effort)
    if command == "import-review":
        return import_review(work, args.response)
    if command == "merge":
        return {"output": str(merge(work, args.draft))}
    if command == "status":
        return status(work)
    raise PipelineError(f"Unknown command: {command}")
