"""Human-mediated GPT terminology review with occurrence-specific corrections."""
from pathlib import Path

from .annotation_common import checked_payload, digest, document_pairs, load_store, matching_hashes, normalized_term, paper_id, require_pair, save_store
from .common import PipelineError, issue, now, read_json, save_json

FILE = "terminology_candidates.json"


def store(work):
    return load_store(work, FILE, "terminology-candidates", "candidates")


def occurrence(pair, ja):
    return {"block_id": pair["block_id"], "ja": ja, "source_context": pair["en"], "target_context": pair["ja"], "source_location": pair["source_location"], "source_sha256": pair["source_sha256"], "target_sha256": pair["target_sha256"]}


def candidate_view(candidate, pairs):
    value = {k: candidate[k] for k in ("id", "en", "sense", "ja", "alternatives", "reasons", "severity")}
    value["occurrences"] = [occurrence(pairs[o["block_id"]], o["ja"]) if o["block_id"] in pairs else {"block_id": o["block_id"], "missing": True} for o in candidate["occurrences"]]
    return value


def effective_state(candidate, pairs):
    review = candidate["review"]
    if any(o["block_id"] not in pairs or not matching_hashes(o, pairs[o["block_id"]]) for o in candidate["occurrences"]):
        return "stale"
    if not review:
        return "pending"
    if review["accepted_sha256"] != digest(candidate_view(candidate, pairs)):
        return "stale"
    return {"approve": "approved", "defer": "deferred", "correct": "approved" if review["applied"] else "revision_requested"}[review["decision"]]


def register_candidates(work, payload, pairs=None, persist=True):
    checked_payload(work, "terminology-input", payload)
    pairs = document_pairs(work) if pairs is None else pairs
    data = store(work)
    levels = {"minor": 0, "major": 1, "critical": 2}
    for item in payload["candidates"]:
        identity = [normalized_term(item["en"]), normalized_term(item["sense"])]
        candidate_id = "term-" + digest(identity)[:20]
        candidate = next((c for c in data["candidates"] if c["id"] == candidate_id), None)
        if candidate is None:
            candidate = {"id": candidate_id, "en": item["en"], "sense": item["sense"], "ja": item["ja"], "alternatives": [], "reasons": [], "severity": item["severity"], "status": "pending", "occurrences": [], "review": None}
            data["candidates"].append(candidate)
        candidate["severity"] = max((item["severity"], candidate["severity"]), key=levels.get)
        candidate["alternatives"] = list(dict.fromkeys(candidate["alternatives"] + item["alternatives"]))
        candidate["reasons"] = list(dict.fromkeys(candidate["reasons"] + [item["reason"]]))
        for block_id in item["block_ids"]:
            pair = require_pair(pairs, block_id)
            if normalized_term(item["en"]) not in normalized_term(pair["en"]) or item["ja"] not in pair["ja"]:
                raise PipelineError(f"Term occurrence not found in the current source/translation: {block_id}")
            old = next((o for o in candidate["occurrences"] if o["block_id"] == block_id), None)
            new = occurrence(pair, item["ja"])
            if old is None:
                candidate["occurrences"].append(new)
            else:
                old.update(new)
        candidate["status"] = effective_state(candidate, pairs)
    if persist:
        save_store(work, FILE, "terminology-candidates", data)
    return data


def save_candidates(work, data):
    save_store(work, FILE, "terminology-candidates", data)


def export_pending(work):
    pairs = document_pairs(work)
    candidates = []
    for candidate in store(work)["candidates"]:
        state = effective_state(candidate, pairs)
        if state == "approved":
            continue
        view = candidate_view(candidate, pairs)
        candidates.append({**view, "status": state, "candidate_sha256": digest(view)})
    payload = {"schema_version": 1, "paper_id": paper_id(work), "reviewer": "GPT", "instructions": "Confirm terminology occurrence by occurrence. Return approve/correct/defer with rationale. correct must provide suggestions keyed by every block_id. Do not rewrite paper text.", "candidates": candidates}
    packet_id = "terms-" + digest(payload)[:20]
    payload["packet_id"] = packet_id
    checked_payload(work, "terminology-packet", payload)
    path = Path(work) / "terminology" / f"{packet_id}.json"
    save_json(path, payload)
    return path


def import_result(work, payload):
    checked_payload(work, "terminology-result", payload)
    packet = read_json(Path(work) / "terminology" / f"{payload['packet_id']}.json")
    checked_payload(work, "terminology-packet", packet)
    if packet["paper_id"] != paper_id(work):
        raise PipelineError("Term review packet belongs to another paper")
    expected = {c["id"]: c for c in packet["candidates"]}
    ids = [r["candidate_id"] for r in payload["results"]]
    if len(ids) != len(set(ids)) or any(cid not in expected for cid in ids):
        raise PipelineError("Duplicate/unknown candidate in GPT result")
    data, pairs = store(work), document_pairs(work)
    lookup = {c["id"]: c for c in data["candidates"]}
    for result in payload["results"]:
        candidate = lookup[result["candidate_id"]]
        current_hash = digest(candidate_view(candidate, pairs))
        if result["candidate_sha256"] != expected[candidate["id"]]["candidate_sha256"] or result["candidate_sha256"] != current_hash:
            raise PipelineError(f"Stale terminology review: {candidate['id']}")
        suggestions = result.get("suggestions", {})
        occurrences = candidate["occurrences"]
        if result["decision"] == "correct" and set(suggestions) != {o["block_id"] for o in occurrences}:
            raise PipelineError("Correction must specify a translation for every occurrence")
        if result["decision"] == "approve":
            for old in occurrences:
                pair = require_pair(pairs, old["block_id"])
                if old["ja"] not in pair["ja"] or normalized_term(candidate["en"]) not in normalized_term(pair["en"]):
                    raise PipelineError("Changed/absent term must be corrected or deferred, not approved")
        candidate["occurrences"] = [occurrence(require_pair(pairs, o["block_id"]), o["ja"]) for o in occurrences]
        candidate["review"] = {**result, "packet_id": payload["packet_id"], "reviewer": "GPT", "accepted_sha256": current_hash, "applied": False, "reviewed_at": now()}
        candidate["status"] = effective_state(candidate, pairs)
    save_json(Path(work) / "terminology" / ("result-" + digest(payload)[:20] + ".json"), payload)
    save_candidates(work, data)
    return terminology_status(work)


def apply_correction(work, payload):
    checked_payload(work, "terminology-apply", payload)
    data, pairs = store(work), document_pairs(work)
    candidate = next((c for c in data["candidates"] if c["id"] == payload["candidate_id"]), None)
    if candidate is None or effective_state(candidate, pairs) != "revision_requested":
        raise PipelineError("A current GPT correction plan is required before applying a term change")
    expected = candidate["review"]["suggestions"]
    incoming = payload["occurrences"]
    if len(incoming) != len(expected) or {o["block_id"] for o in incoming} != set(expected):
        raise PipelineError("Apply must explicitly cover every occurrence exactly once")
    for change in incoming:
        pair = require_pair(pairs, change["block_id"])
        if not matching_hashes(change, pair):
            raise PipelineError(f"Stale occurrence correction: {change['block_id']}")
        if expected[change["block_id"]] not in change["text"]:
            raise PipelineError(f"Suggested term missing in revised paragraph: {change['block_id']}")
    # Whole paragraphs and occurrence-specific rationales are supplied by Codex.
    # This code never performs a global find-and-replace.
    from .translate import replace_translation_blocks
    replace_translation_blocks(work, incoming)
    current = document_pairs(work)
    candidate["occurrences"] = [occurrence(current[o["block_id"]], expected[o["block_id"]]) for o in candidate["occurrences"]]
    candidate["ja"] = candidate["occurrences"][0]["ja"]
    candidate["review"]["applied"] = True
    candidate["review"]["application"] = [{"block_id": c["block_id"], "rationale": c["rationale"], "source_sha256": current[c["block_id"]]["source_sha256"], "target_sha256": current[c["block_id"]]["target_sha256"]} for c in incoming]
    candidate["review"]["accepted_sha256"] = digest(candidate_view(candidate, current))
    candidate["status"] = "approved"
    save_candidates(work, data)
    save_json(Path(work) / "terminology" / ("application-" + digest(payload)[:20] + ".json"), payload)
    from .review import review_state
    return {"terminology": terminology_status(work), "review": review_state(work), "next": "validate-translation, review-prepare, Claude Code review-run/import-review, merge"}


def terminology_status(work):
    pairs = document_pairs(work)
    return {c["id"]: {"en": c["en"], "sense": c["sense"], "severity": c["severity"], "state": effective_state(c, pairs), "block_ids": [o["block_id"] for o in c["occurrences"]]} for c in store(work)["candidates"]}


def terminology_issues(work):
    if not (Path(work) / FILE).exists():
        return []
    try:
        states = terminology_status(work)
        return [issue("terminology_unresolved", f"{cid}: {value['en']} ({value['state']})") for cid, value in states.items() if value["severity"] in {"major", "critical"} and value["state"] != "approved"]
    except (PipelineError, KeyError, ValueError) as exc:
        return [issue("terminology_invalid", str(exc))]
