"""JSON Schema contracts for annotations, separate from the translation schema.

`schema-export` writes these standard Draft 2020-12 schemas for external tools.
No remote references or schema downloads are used during validation.
"""
from functools import lru_cache

from jsonschema import Draft202012Validator, ValidationError

from .common import PipelineError

TEXT = {"type": "string", "minLength": 1}
HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
ID = {"type": "string", "pattern": "^[A-Za-z0-9_-]+$"}


def obj(properties, required=None):
    return {"type": "object", "properties": properties, "required": list(properties) if required is None else required, "additionalProperties": False}


def array(items, minimum=0):
    return {"type": "array", "items": items, "minItems": minimum}


def envelope(field, items):
    return obj({"schema_version": {"const": 1}, "paper_id": TEXT, field: array(items)})


PAIR_HASHES = {"source_sha256": HASH, "target_sha256": HASH}
OCCURRENCE = obj({"block_id": ID, "ja": TEXT, "source_context": TEXT, "target_context": TEXT, "source_location": {"type": "object"}, **PAIR_HASHES})
TERM_INPUT = obj({"en": TEXT, "sense": TEXT, "ja": TEXT, "alternatives": array(TEXT), "reason": TEXT, "severity": {"enum": ["minor", "major", "critical"]}, "block_ids": {**array(ID, 1), "uniqueItems": True}})
TERM_REVIEW = obj({"candidate_id": ID, "candidate_sha256": HASH, "decision": {"enum": ["approve", "correct", "defer"]}, "rationale": TEXT, "suggestions": {"type": "object", "additionalProperties": TEXT}}, ["candidate_id", "candidate_sha256", "decision", "rationale"])
STORED_REVIEW = obj({**TERM_REVIEW["properties"], "packet_id": ID, "reviewer": {"const": "GPT"}, "accepted_sha256": HASH, "applied": {"type": "boolean"}, "reviewed_at": TEXT, "application": array(obj({"block_id": ID, "rationale": TEXT, **PAIR_HASHES}))}, ["candidate_id", "candidate_sha256", "decision", "rationale", "packet_id", "reviewer", "accepted_sha256", "applied", "reviewed_at"])
TERM = obj({"id": ID, "en": TEXT, "sense": TEXT, "ja": TEXT, "alternatives": array(TEXT), "reasons": array(TEXT, 1), "severity": {"enum": ["minor", "major", "critical"]}, "status": {"enum": ["pending", "approved", "revision_requested", "deferred", "stale"]}, "occurrences": array(OCCURRENCE, 1), "review": {"anyOf": [{"type": "null"}, STORED_REVIEW]}})
EVIDENCE_INPUT = obj({"block_id": ID, "quote": TEXT, "source_sha256": HASH})
EVIDENCE = obj({**EVIDENCE_INPUT["properties"], "source_location": {"type": "object"}})
NOTE_FIELDS = {"block_id": ID, "kind": {"enum": ["logic", "assumption", "derivation", "notation", "relation", "question", "explanation"]}, "claim_type": {"enum": ["explicit", "derived", "speculative"]}, "content": TEXT, "certainty": {"enum": ["high", "medium", "low"]}, "agent": {"enum": ["Codex", "Claude Code"]}, **PAIR_HASHES, "evidence": array(EVIDENCE_INPUT, 1)}
NOTE_INPUT = obj({**NOTE_FIELDS, "id": ID}, list(NOTE_FIELDS))
NOTE = obj({**NOTE_FIELDS, "id": ID, "created_at": TEXT, "evidence": array(EVIDENCE, 1)})
QUESTION = obj({"id": ID, "block_id": ID, "question": TEXT, "context_sha256": HASH, "packet": TEXT, "created_at": TEXT, "block_hashes": {"type": "object", "additionalProperties": obj(PAIR_HASHES)}})

SCHEMAS = {
    "terminology-input": envelope("candidates", TERM_INPUT),
    "terminology-candidates": envelope("candidates", TERM),
    "terminology-result": obj({"schema_version": {"const": 1}, "paper_id": TEXT, "reviewer": {"const": "GPT"}, "packet_id": ID, "results": array(TERM_REVIEW)}),
    "terminology-apply": obj({"schema_version": {"const": 1}, "paper_id": TEXT, "candidate_id": ID, "occurrences": array(obj({"block_id": ID, "text": TEXT, "rationale": TEXT, **PAIR_HASHES}), 1)}),
    "notes-input": envelope("notes", NOTE_INPUT),
    "reading-notes": envelope("notes", NOTE),
    "questions": envelope("questions", QUESTION),
}

PAIR = obj({"block_id": ID, "kind": TEXT, "section": {"type": "integer"}, "en": TEXT, "ja": TEXT, "source_location": {"type": "object"}, **PAIR_HASHES, "inline_math": array({"type": "object"}), "asset": {"type": ["object", "null"]}})
TERM_VIEW = {k: v for k, v in TERM["properties"].items() if k not in {"review", "status"}}
TERM_VIEW["occurrences"] = array({"anyOf": [OCCURRENCE, obj({"block_id": ID, "missing": {"const": True}})]}, 1)
SCHEMAS["terminology-packet"] = obj({"schema_version": {"const": 1}, "paper_id": TEXT, "reviewer": {"const": "GPT"}, "instructions": TEXT, "packet_id": ID, "candidates": array(obj({**TERM_VIEW, "status": TERM["properties"]["status"], "candidate_sha256": HASH}))})
SCHEMAS["explain-context"] = obj({"schema_version": {"const": 1}, "paper_id": TEXT, "requested_id": ID, "block_id": ID, "target": PAIR, "before": array(PAIR), "after": array(PAIR), "related": array(obj({**PAIR["properties"], "relation": TEXT})), "outline": array(obj({"block_id": ID, "en": TEXT, "ja": {"type": "string"}})), "answer_policy": TEXT, "question": TEXT, "context_sha256": HASH}, ["schema_version", "paper_id", "requested_id", "block_id", "target", "before", "after", "related", "outline", "answer_policy", "context_sha256"])
SCHEMAS["reading-edition"] = obj({"schema_version": {"const": 1}, "paper_id": TEXT, "draft": {"type": "boolean"}, "faithful_sha256": HASH, "notes_sha256": HASH, "output_sha256": HASH, "block_ids": {**array(ID), "uniqueItems": True}})


@lru_cache(maxsize=None)
def schema(name):
    result = {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": name, **SCHEMAS[name]}
    Draft202012Validator.check_schema(result)
    return result


def validate_data(name, data):
    try:
        Draft202012Validator(schema(name)).validate(data)
    except ValidationError as exc:
        location = "/".join(map(str, exc.absolute_path)) or "<root>"
        raise PipelineError(f"{name}: {location}: {exc.message}") from exc
    return data
