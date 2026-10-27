from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from .common import PipelineError, file_sha, load_blocks, now, parse_markdown, read_json, render_blocks, report, save, save_json
from .review import review_state
from .translate import translations
from .validate import validate_extraction, validate_translation


def merge(work, draft=False):
    work = Path(work)
    extraction = validate_extraction(work)
    validation = validate_translation(work)
    states = review_state(work) if (work / "glossary.json").exists() else {}
    from .terminology import terminology_issues
    problems = terminology_issues(work)
    if extraction["status"] != "PASS":
        problems.append({"severity": "error", "code": "extraction_gate", "message": "Extraction validation failed"})
    if validation["status"] != "PASS":
        problems.append({"severity": "error", "code": "translation_gate", "message": "Translation validation failed"})
    if not states or any(s != "PASS" for s in states.values()):
        problems.append({"severity": "error", "code": "review_gate", "message": str(dict(Counter(states.values())))})
    if problems and not draft:
        report(work, "final", problems, official=False, review_states=dict(Counter(states.values())))
        raise PipelineError("Official final blocked: unresolved validation/review issues. Use merge --draft for diagnosis.")
    blocks = translations(work)
    text = render_blocks(blocks).replace("](../assets/", "](assets/")
    name = "paper_ja.draft.md" if draft else "paper_ja.md"
    if draft:
        text = "<!-- DRAFT: 未検証・未承認の内容を含む可能性があります。正式版ではありません。 -->\n\n" + text
    # Reparse the integrated document to verify actual emitted IDs and content.
    parsed = parse_markdown(text[text.index("<!-- block:"):])
    if [b["id"] for b in parsed] != [b["id"] for b in blocks] or [b["text"] for b in parsed] != [b["text"].replace("](../assets/", "](assets/") for b in blocks]:
        raise PipelineError("Post-merge content/ID mismatch")
    for path in re.findall(r"!\[[^]]*\]\(([^)]+)\)", text):
        if not (work / path).is_file():
            raise PipelineError(f"Post-merge broken image: {path}")
    save(work / name, text)
    if not draft:
        for chunk in read_json(work / "chunks.json")["chunks"]:
            save(work / "ja_reviewed" / chunk["file"], (work / "ja_draft" / chunk["file"]).read_bytes())
        save_json(work / "finalization.json", {"schema_version": 1, "created_at": now(), "output_sha256": file_sha(work / name), "blocks_sha256": file_sha(work / "blocks.jsonl"), "glossary_sha256": file_sha(work / "glossary.json"), "translations": {c["file"]: file_sha(work / "ja_draft" / c["file"]) for c in read_json(work / "chunks.json")["chunks"]}})
    report(work, "draft" if draft else "final", problems, official=not draft, output=name, output_sha256=file_sha(work / name), block_count=len(parsed), review_states=dict(Counter(states.values())))
    if not draft:
        from .reading import reading_edition
        try:
            reading_edition(work)
        except (PipelineError, ValueError, KeyError, OSError) as exc:
            # Notes do not participate in faithful-translation coverage/approval.
            report(work, "reading-generation", [{"severity": "error", "code": "reading_edition_failed", "message": str(exc)}], faithful_output=name, faithful_output_preserved=True)
    return work / name


def status(work, write_reports=True):
    work = Path(work)
    result = {"source": "available" if (work / "manifest.json").exists() else "not_run", "extraction": "not_run", "translation": "not_run", "review": {}, "official": "not_generated"}
    if (work / "chunks.json").exists():
        result["extraction"] = validate_extraction(work, write_report=write_reports)["status"]
    if (work / "glossary.json").exists():
        result["translation"] = validate_translation(work, write_report=write_reports)["status"]
        result["review"] = dict(Counter(review_state(work).values()))
    marker = work / "finalization.json"
    from .terminology import terminology_issues, terminology_status
    terminology_problems = terminology_issues(work)
    result["terminology"] = {"blocking_issues": terminology_problems}
    if (work / "terminology_candidates.json").exists() and not any(p["code"] == "terminology_invalid" for p in terminology_problems):
        result["terminology"]["candidates"] = terminology_status(work)
    if marker.exists():
        recorded = read_json(marker)
        current = (work / "paper_ja.md").exists() and recorded["output_sha256"] == file_sha(work / "paper_ja.md") and recorded["blocks_sha256"] == file_sha(work / "blocks.jsonl") and recorded["glossary_sha256"] == file_sha(work / "glossary.json")
        current = current and all((work / "ja_draft" / name).is_file() and file_sha(work / "ja_draft" / name) == digest for name, digest in recorded["translations"].items())
        current = current and not terminology_problems and result["extraction"] == "PASS" and result["translation"] == "PASS" and all(s == "PASS" for s in review_state(work).values())
        result["official"] = "VALID" if current else "STALE_OR_INVALID"
    return result
