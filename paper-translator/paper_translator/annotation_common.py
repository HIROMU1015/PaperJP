"""Shared current-document snapshots for annotations and reading questions."""
import json
import unicodedata
from pathlib import Path

from .annotation_schema import validate_data
from .common import PipelineError, load_blocks, read_json, save_json, sha
from .translate import translations


def digest(value):
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def normalized_term(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def paper_id(work):
    return read_json(Path(work) / "manifest.json")["paper_id"]


def document_pairs(work):
    source = load_blocks(work)
    target = translations(work)
    if len({b["id"] for b in source}) != len(source) or len({b["id"] for b in target}) != len(target):
        raise PipelineError("Duplicate block IDs; annotations require an unambiguous document")
    ja = {b["id"]: b["text"] for b in target}
    return {b["id"]: {"block_id": b["id"], "kind": b["kind"], "section": b["section"], "en": b["text"], "ja": ja.get(b["id"], ""), "source_location": b["source"], "source_sha256": sha(b["text"]), "target_sha256": sha(ja.get(b["id"], "")), "inline_math": b.get("inline_math", []), "asset": b.get("asset")} for b in source}


def require_pair(pairs, block_id):
    if block_id not in pairs:
        raise PipelineError(f"Unknown block_id: {block_id}")
    if not pairs[block_id]["ja"]:
        raise PipelineError(f"Translation missing for {block_id}")
    return pairs[block_id]


def matching_hashes(record, pair):
    return all(record.get(k) == pair[k] for k in ("source_sha256", "target_sha256"))


def checked_payload(work, schema_name, payload):
    validate_data(schema_name, payload)
    if payload["paper_id"] != paper_id(work):
        raise PipelineError("Annotation belongs to another paper/version")
    return payload


def load_store(work, filename, schema_name, field):
    path = Path(work) / filename
    value = read_json(path) if path.exists() else {"schema_version": 1, "paper_id": paper_id(work), field: []}
    return checked_payload(work, schema_name, value)


def save_store(work, filename, schema_name, value):
    checked_payload(work, schema_name, value)
    save_json(Path(work) / filename, value)


def initialize(work):
    for filename, name, field in (("terminology_candidates.json", "terminology-candidates", "candidates"), ("reading_notes.json", "reading-notes", "notes"), ("questions.json", "questions", "questions")):
        if not (Path(work) / filename).exists():
            save_store(work, filename, name, {"schema_version": 1, "paper_id": paper_id(work), field: []})
    return {"terminology": "terminology_candidates.json", "notes": "reading_notes.json", "questions": "questions.json"}
