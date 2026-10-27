from __future__ import annotations

import json
import re
from pathlib import Path

from .common import PipelineError, file_sha, load_blocks, parse_markdown, read_json, render_blocks, save, save_json, sha
from .protect import restore, localize_dates


def import_translation(work, response_file):
    work = Path(work)
    response = read_json(response_file)
    chunk = response.get("chunk", "")
    spec = next((c for c in read_json(work / "chunks.json")["chunks"] if c["file"] == chunk), None)
    if not spec:
        raise PipelineError("Unknown translation chunk")
    packet = read_json(work / "packets" / (Path(chunk).stem + ".json"))
    if packet.get("paper_id") not in {None, read_json(work / "manifest.json")["paper_id"]}:
        raise PipelineError("Translation packet belongs to another paper")
    if packet.get("glossary_sha256") != file_sha(work / "glossary.json"):
        raise PipelineError("Translation packet has a stale glossary; run prepare again")
    if "packet_sha256" in packet and packet["packet_sha256"] != sha(json.dumps({k: v for k, v in packet.items() if k != "packet_sha256"}, sort_keys=True, ensure_ascii=False)):
        raise PipelineError("Translation packet content hash mismatch; run prepare again")
    if "packet_sha256" in packet and (response.get("paper_id") != packet["paper_id"] or response.get("packet_sha256") != packet["packet_sha256"]):
        raise PipelineError("Translation response belongs to another paper or an old packet/glossary; translate the current packet")
    incoming = response.get("blocks", [])
    if [b.get("id") for b in incoming] != spec["block_ids"]:
        raise PipelineError("Translation IDs missing, duplicated or reordered")
    lookup = {b["id"]: b for b in load_blocks(work)}
    output = []
    for src, dest in zip(packet["blocks"], incoming, strict=True):
        if sha(lookup[src["id"]]["text"]) != src["source_sha256"]:
            raise PipelineError("Translation packet is stale; run prepare again")
        value = dest.get("text", "")
        if not isinstance(value, str) or not value.strip():
            raise PipelineError(f"Empty translation: {src['id']}")
        value = restore(value, src["placeholders"]) if src["translate"] else value
        if src["translate"]:
            value = localize_dates(value)
        if not src["translate"] and value != src["text"]:
            raise PipelineError(f"Non-translatable block was changed: {src['id']}")
        output.append({"id": src["id"], "text": value})
    candidates = None
    if response.get("terminology_candidates"):
        from .annotation_common import document_pairs, paper_id
        from .terminology import register_candidates
        pairs = document_pairs(work)
        for block in output:
            pairs[block["id"]]["ja"] = block["text"]
            pairs[block["id"]]["target_sha256"] = sha(block["text"])
        for term in response["terminology_candidates"]:
            if any(key not in spec["block_ids"] for key in term["block_ids"]):
                raise PipelineError("A translation response can register terms only in its own chunk")
        candidates = register_candidates(work, {"schema_version": 1, "paper_id": paper_id(work), "candidates": response["terminology_candidates"]}, pairs=pairs, persist=False)
    save(work / "ja_draft" / chunk, render_blocks(output))
    save_json(work / "translations" / (Path(chunk).stem + ".json"), response)
    if candidates is not None:
        from .terminology import save_candidates
        save_candidates(work, candidates)
    from .validate import validate_translation
    return validate_translation(work)


def translations(work):
    work = Path(work)
    result = []
    for chunk in read_json(work / "chunks.json")["chunks"]:
        path = work / "ja_draft" / chunk["file"]
        if path.exists():
            result.extend(parse_markdown(path.read_text(encoding="utf-8-sig")))
    return result


def replace_translation_blocks(work, changes):
    """Validate complete occurrence-specific paragraphs before writing any chunk."""
    from .validate import validate_translation
    work = Path(work)
    old = translations(work)
    incoming = {c["block_id"]: c["text"].strip() for c in changes}
    source = {b["id"]: b for b in load_blocks(work)}
    if len(incoming) != len(changes) or any(key not in source or not source[key]["translate"] for key in incoming):
        raise PipelineError("Unknown, duplicate or immutable correction block")
    staged = [{"id": b["id"], "text": incoming.get(b["id"], b["text"])} for b in old]
    result = validate_translation(work, staged, write_report=False)
    if result["status"] != "PASS":
        raise PipelineError(f"Correction failed machine validation: {result['issues']}")
    by_id = {b["id"]: b for b in staged}
    for chunk in read_json(work / "chunks.json")["chunks"]:
        if any(key in incoming for key in chunk["block_ids"]):
            save(work / "ja_draft" / chunk["file"], render_blocks([by_id[key] for key in chunk["block_ids"]]))
    return validate_translation(work)
