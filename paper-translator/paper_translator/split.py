from __future__ import annotations

import re
from pathlib import Path

from .common import PipelineError, file_sha, load_blocks, read_json, render_blocks, save, save_json, sha
from .protect import protect


def split(work, target_tokens=2000):
    work = Path(work)
    blocks = load_blocks(work)
    chunks, current, size = [], [], 0
    for block in blocks:
        estimate = max(1, len(block["text"]) / 4)
        if current and ((block["kind"] == "heading" and size >= target_tokens / 2) or size + estimate > target_tokens):
            chunks.append(current)
            current, size = [], 0
        current.append(block)
        size += estimate
    if current:
        chunks.append(current)
    entries = []
    for i, chunk in enumerate(chunks, 1):
        name = f"{i:03d}-sec{chunk[0]['section']:02d}.md"
        save(work / "en" / name, render_blocks(chunk))
        entries.append({"file": name, "block_ids": [b["id"] for b in chunk], "sha256": file_sha(work / "en" / name)})
    result = {"schema_version": 1, "blocks_sha256": file_sha(work / "blocks.jsonl"), "target_tokens_estimate": target_tokens, "chunks": entries}
    save_json(work / "chunks.json", result)
    return result


def prepare(work):
    work = Path(work)
    from .validate import validate_extraction
    validation = validate_extraction(work)
    if validation["status"] != "PASS":
        raise PipelineError("Extraction gate failed. Inspect reports/extraction.md; diagnostic draft is still available.")
    from .annotation_common import initialize
    initialize(work)
    chunks = read_json(work / "chunks.json")
    blocks = load_blocks(work)
    lookup = {b["id"]: b for b in blocks}
    glossary_path = work / "glossary.json"
    if not glossary_path.exists():
        # Suggestions are extracted from this paper, never silently pretranslated.
        candidates = {}
        for block in blocks:
            if not block["translate"]:
                continue
            for term in re.findall(r"\*\*([^*]+)\*\*|\*([^*]+)\*|\b([A-Z]{2,8})\b", block["text"]):
                value = next((v for v in term if v), "")
                if value:
                    candidates.setdefault(value, []).append(block["id"])
        save_json(glossary_path, {"schema_version": 1, "status": "candidates_need_codex", "terms": [{"en": key, "ja": "", "block_ids": ids} for key, ids in sorted(candidates.items())]})
    for chunk in chunks["chunks"]:
        packet_blocks = []
        for block_id in chunk["block_ids"]:
            block = lookup[block_id]
            protected, mapping = protect(block["text"], block_id) if block["translate"] else (block["text"], {})
            packet_blocks.append({**block, "source_sha256": sha(block["text"]), "protected_text": protected, "placeholders": mapping})
        start = next(i for i, b in enumerate(blocks) if b["id"] == chunk["block_ids"][0])
        end = start + len(chunk["block_ids"])
        packet = {"schema_version": 1, "chunk": chunk["file"], "glossary_sha256": file_sha(glossary_path), "instructions": "Translate only blocks with translate=true. Preserve tokens exactly. Context is read-only. Non-translatable blocks must be copied exactly.", "blocks": packet_blocks, "context_before": blocks[max(0, start - 1):start], "context_after": blocks[end:end + 1]}
        save_json(work / "packets" / (Path(chunk["file"]).stem + ".json"), packet)
    return {"packets": len(chunks["chunks"]), "glossary": str(glossary_path)}
