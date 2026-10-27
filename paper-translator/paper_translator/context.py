"""Read-only paragraph context retrieval and separately recorded user questions."""
import re
from pathlib import Path

from .annotation_common import checked_payload, digest, document_pairs, load_store, matching_hashes, paper_id, require_pair, save_store
from .common import PipelineError, now, read_json, safe_path, save_json


def explain_context(work, block_id, related=(), radius=1, question=None):
    if not 0 <= radius <= 5:
        raise PipelineError("Context radius must be between 0 and 5")
    pairs = document_pairs(work)
    ids = list(pairs)
    inline_owner = next((key for key, pair in pairs.items() if any(m["id"] == block_id for m in pair["inline_math"])), None)
    target_id = inline_owner or block_id
    target = require_pair(pairs, target_id)
    index = ids.index(target_id)
    neighbors = ids[max(0, index - radius):index] + ids[index + 1:index + radius + 1]
    reasons = {key: "explicitly_requested" for key in related}
    anchors = {}
    for key, pair in pairs.items():
        for anchor in re.findall(r'<a id="([^"]+)"', pair["en"]):
            anchors[anchor] = key
    for key in [target_id, *neighbors, *related]:
        pair = require_pair(pairs, key)
        for anchor in re.findall(r"\]\(#([^)]+)\)", pair["en"]):
            if anchor in anchors:
                reasons.setdefault(anchors[anchor], "document_reference")
    for i, key in enumerate(ids):
        pair = pairs[key]
        if pair["section"] == target["section"] and pair["kind"] in {"equation", "figure", "table", "caption"} and abs(i - index) <= 6:
            reasons.setdefault(key, "nearby_equation_figure_or_table")
    related_pairs = [{**require_pair(pairs, key), "relation": reason} for key, reason in reasons.items() if key != target_id and key not in neighbors]
    result = {"schema_version": 1, "paper_id": paper_id(work), "requested_id": block_id, "block_id": target_id, "target": target, "before": [require_pair(pairs, key) for key in ids[max(0, index - radius):index]], "after": [require_pair(pairs, key) for key in ids[index + 1:index + radius + 1]], "related": related_pairs, "outline": [{"block_id": key, "en": p["en"], "ja": p["ja"]} for key, p in pairs.items() if p["kind"] in {"title", "heading"}], "answer_policy": "Separate explicit source statements, logical derivation (show assumptions/calculation), and AI speculation/questions. Never edit faithful translations to answer a reading question."}
    if question:
        result["question"] = question
    result["context_sha256"] = digest(result)
    checked_payload(work, "explain-context", result)
    return result


def search_context(work, query):
    if not query.strip():
        raise PipelineError("Search query cannot be empty")
    terms = query.casefold().split()
    scored = []
    for pair in document_pairs(work).values():
        haystack = (pair["en"] + "\n" + pair["ja"]).casefold()
        score = sum(term in haystack for term in terms)
        if score:
            scored.append({"block_id": pair["block_id"], "kind": pair["kind"], "score": score, "en": pair["en"][:240], "ja": pair["ja"][:240]})
    return sorted(scored, key=lambda row: -row["score"])[:20]


def question_prepare(work, block_id, question, related=(), radius=1):
    if not question.strip():
        raise PipelineError("Question cannot be empty")
    context = explain_context(work, block_id, related, radius, question)
    question_id = "question-" + context["context_sha256"][:20]
    path = Path(work) / "questions" / f"{question_id}.json"
    pairs = document_pairs(work)
    included = [context["target"], *context["before"], *context["after"], *context["related"], *(pairs[p["block_id"]] for p in context["outline"] if pairs[p["block_id"]]["ja"])]
    record = {"id": question_id, "block_id": context["block_id"], "question": question, "context_sha256": context["context_sha256"], "packet": path.relative_to(work).as_posix(), "created_at": now(), "block_hashes": {p["block_id"]: {key: p[key] for key in ("source_sha256", "target_sha256")} for p in included}}
    data = load_store(work, "questions.json", "questions", "questions")
    if not any(q["id"] == question_id for q in data["questions"]):
        data["questions"].append(record)
    save_json(path, context)
    save_store(work, "questions.json", "questions", data)
    return path


def question_states(work):
    pairs = document_pairs(work)
    data = load_store(work, "questions.json", "questions", "questions")
    result = {}
    for q in data["questions"]:
        path = safe_path(work, q["packet"])
        if not path.is_file():
            result[q["id"]] = "orphan"
            continue
        try:
            packet = checked_payload(work, "explain-context", read_json(path))
            actual_hash = digest({k: v for k, v in packet.items() if k != "context_sha256"})
            if packet["context_sha256"] != q["context_sha256"] or actual_hash != q["context_sha256"]:
                result[q["id"]] = "modified"
            else:
                result[q["id"]] = "current" if all(key in pairs and matching_hashes(hashes, pairs[key]) for key, hashes in q["block_hashes"].items()) else "stale"
        except (PipelineError, ValueError, KeyError):
            result[q["id"]] = "modified"
    return result
